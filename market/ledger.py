"""계좌: 주문 내기, 체결(정산), 평가.

체결 규칙 (docs/design.md "체결 규칙")
- 판단은 날짜 D의 07:37. ETF 주문은 D와 같거나 뒤인 첫 거래일의 종가(15:30), 코인 주문은 D 일봉(09:00 시작)의 시가에 체결.
- 수수료: 금액 × 비율, 원 단위 올림. ETF 1주 단위, 코인 1e-8 단위. 현금은 음수가 될 수 없다(사기 금액은 현금 안으로 줄인다).
  빚·공매도 없음(팔기는 가진 만큼만).
- 금액은 원 단위 정수: 사는 금액은 올림, 파는 금액은 내림(계좌에 불리한 쪽).
- 정산은 시간 순서대로 한다: 아직 값을 모르는 체결보다 뒤의 체결은 기다린다. 판단 시각에 알려진 일봉(끝난 일봉)만 쓰므로
  코인 체결(D 09:00)은 D+2 실행, ETF 체결(D 15:30)은 대개 D+3 실행에서 정산된다(코인 일봉이 다음 날 09:00에 끝나서). 그사이 판단은 '체결 안 된 주문을 더한 예상 보유'로 한다.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from . import config as C
from .prices import History, Row


# 종목 1주 반올림 때 상한을 넘어도 되는 폭(계좌 대비). 1주가 비싼 종목을 아예 못 사는 일과, 상한을 크게 넘는 일 사이의 타협
STOCK_SLACK = 0.05


@dataclass
class Order:
    id: str
    run: dt.date
    account: str
    asset: str
    side: str          # buy / sell
    qty_units: int
    ref_price: float   # 판단 때 알려진 마지막 종가
    reason: str = ""
    # 판단한 실제 시각(2단계 진짜 계좌). 있으면 이 시각 **뒤에** 정해지는 첫 공식 가격에 체결한다(늦은 실행에서 이미 나온
    # 가격으로 사는 일을 막는다). 없으면(1단계 재생) 판단 시각 07:37 기준의 옛 규칙 그대로.
    decided_at: dt.datetime | None = None

    def record(self) -> dict:
        d = {"id": self.id, "run": self.run.isoformat(), "account": self.account, "asset": self.asset,
             "side": self.side, "qty_units": self.qty_units, "ref_price": self.ref_price, "reason": self.reason}
        if self.decided_at is not None:
            d["decided_at"] = self.decided_at.isoformat(timespec="seconds")
        return d

    @classmethod
    def from_record(cls, d: dict) -> "Order":
        at = d.get("decided_at")
        return cls(d["id"], dt.date.fromisoformat(d["run"]), d["account"], d["asset"], d["side"], d["qty_units"],
                   d["ref_price"], d.get("reason", ""), dt.datetime.fromisoformat(at) if at else None)


@dataclass
class Account:
    id: str
    cash: int = C.START_CASH
    units: dict[str, int] = field(default_factory=lambda: {a: 0 for a in C.ASSET_IDS})
    pending: list[Order] = field(default_factory=list)

    # ---------- 평가 ----------
    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(n * prices[a] / C.ASSETS[a].unit for a, n in self.units.items() if a in prices)

    def held(self) -> set[str]:
        """세 대상이 아닌 것 중 들고 있거나 체결을 기다리는 것(개별 종목)."""
        return ({a for a, n in self.units.items() if n and a not in C.CORE}
                | {o.asset for o in self.pending if o.asset not in C.CORE})

    def expected(self, prices: dict[str, float]) -> tuple[dict[str, int], float]:
        """체결 안 된 주문까지 더한 (예상 보유, 예상 현금). 가격은 판단 때 알려진 마지막 종가."""
        units = dict(self.units)
        cash = float(self.cash)
        for o in self.pending:
            a = C.ASSETS[o.asset]
            amount = o.qty_units * prices.get(o.asset, o.ref_price) / a.unit
            if o.side == "buy":
                units[o.asset] = units.get(o.asset, 0) + o.qty_units
                cash -= amount * (1 + a.fee)
            else:
                units[o.asset] = units.get(o.asset, 0) - o.qty_units
                cash += amount * (1 - a.fee)
        return units, cash

    def record(self, run: dt.date, prices: dict[str, tuple[dt.date, float]]) -> dict:
        # 보유 칸: 세 대상은 늘 적고(0이어도), 종목은 들고 있을 때만. 평가 가격: 세 대상 + 들고 있거나 기다리는 종목
        keep = set(C.ASSET_IDS) | self.held()
        prices = {a: v for a, v in prices.items() if a in keep}
        p = {a: v for a, (_, v) in prices.items()}
        pos = {a: n for a, n in self.units.items() if a in C.CORE or n}
        return {"id": f"{self.id}:{run.isoformat()}", "run": run.isoformat(), "account": self.id, "cash": self.cash,
                "positions": pos, "prices": {a: [d.isoformat(), v] for a, (d, v) in prices.items()},
                "value": round(self.value(p), 2), "pending": [o.id for o in self.pending]}

    # ---------- 주문 ----------
    def plan(self, run: dt.date, targets: dict[str, float | None], prices: dict[str, float],
             band: float = 0.02, reasons: dict[str, str] | None = None,
             decided_at: dt.datetime | None = None) -> list[Order]:
        """목표 비중(대상 → 0~1, None은 그대로 둠)으로 주문을 낸다. 차이가 계좌의 band보다 작으면 거래하지 않는다."""
        units, cash = self.expected(prices)
        total = cash + sum(n * prices[a] / C.ASSETS[a].unit for a, n in units.items() if a in prices)
        orders: list[Order] = []
        sells, buys = [], []
        for a, w in targets.items():
            if w is None or a not in prices or prices[a] <= 0:
                continue
            spec = C.ASSETS[a]
            now = units.get(a, 0) * prices[a] / spec.unit
            diff = w * total - now
            if abs(diff) < band * total:
                continue
            (buys if diff > 0 else sells).append((a, diff))
        for a, diff in sells:
            spec = C.ASSETS[a]
            n = -diff / prices[a] * spec.unit
            q = min(units.get(a, 0), round(n) if C.is_stock(a) else math.floor(n))
            if q > 0:
                orders.append(Order(f"{self.id}:{run.isoformat()}:{a}", run, self.id, a, "sell", q, prices[a],
                                    (reasons or {}).get(a, ""), decided_at))
                cash += q * prices[a] / spec.unit * (1 - spec.fee)
        for a, diff in buys:
            spec = C.ASSETS[a]
            spend = min(diff, max(cash, 0.0))
            q = math.floor(spend / (prices[a] * (1 + spec.fee)) * spec.unit)
            if C.is_stock(a):
                # 종목은 1주 값이 계좌의 몇 %나 되기도 한다(1주 150만 원 = 약 15%). 내림만 하면 목표 10%로는 영영 못 산다.
                # 그래서 가까운 주 수로 맞춘다. 다만 그 종목이 상한 + 5%p를 넘게 되거나 현금이 모자라면 내림 그대로.
                near = round(diff / prices[a])
                after = (units.get(a, 0) + near) * prices[a] / total if total else 1.0
                afford = math.floor(max(cash, 0.0) / (prices[a] * (1 + spec.fee)))
                if near > q and near <= afford and after <= C.STOCK_MAX_WEIGHT + STOCK_SLACK:
                    q = near
            if q > 0:
                orders.append(Order(f"{self.id}:{run.isoformat()}:{a}", run, self.id, a, "buy", q, prices[a],
                                    (reasons or {}).get(a, ""), decided_at))
                cash -= q * prices[a] / spec.unit * (1 + spec.fee)
        self.pending += orders
        return orders

    # ---------- 정산 ----------
    def settle(self, hists: dict[str, History], run: dt.date) -> list[dict]:
        """판단 시각에 알려진 일봉으로 체결할 수 있는 주문을 시간 순서대로 체결한다. 체결 기록 목록."""
        # 시간 순서를 지킬 대상: 세 대상 + 이 계좌가 체결을 기다리는 것(종목). 다른 종목의 시세가 늦어도 이 계좌는 기다리지 않는다
        need = set(C.ASSET_IDS) | {o.asset for o in self.pending}
        cutoff = settle_cutoff({a: h for a, h in hists.items() if a in need})
        ready = []
        for o in self.pending:
            row = fill_row(o, hists[o.asset])
            if row is None:
                continue
            when = C.fill_dt(C.ASSETS[o.asset], row.date)
            if cutoff is not None and when <= cutoff:
                ready.append((when, 0 if o.side == "sell" else 1, o, row))
        fills = []
        for when, _, o, row in sorted(ready, key=lambda x: (x[0], x[1])):
            fills.append(self._fill(o, row, run))
            self.pending.remove(o)
        return fills

    def _fill(self, o: Order, row: Row, run: dt.date) -> dict:
        spec = C.ASSETS[o.asset]
        price = getattr(row, spec.fill_field)
        q, note = o.qty_units, ""
        if o.side == "sell":
            if q > self.units.get(o.asset, 0):
                q, note = self.units.get(o.asset, 0), "가진 만큼만 팖"
            value = math.floor(q * price / spec.unit)
            fee = math.ceil(value * spec.fee)
            self.units[o.asset] -= q
            self.cash += value - fee
        else:
            def cost(n: int) -> tuple[int, int]:
                v = math.ceil(n * price / spec.unit)
                return v, math.ceil(v * spec.fee)
            value, fee = cost(q)
            if value + fee > self.cash:
                q = math.floor(self.cash / (price * (1 + spec.fee)) * spec.unit)
                while q > 0 and sum(cost(q)) > self.cash:
                    q -= 1
                value, fee = cost(q)
                note = "현금이 모자라 수량을 줄임"
            self.units[o.asset] = self.units.get(o.asset, 0) + q
            self.cash -= value + fee
        return {"id": f"fill:{o.id}", "run": run.isoformat(), "account": self.id, "order": o.id, "asset": o.asset,
                "side": o.side, "fill_date": row.date.isoformat(), "price": price, "qty_units": q, "value": value,
                "fee": fee, "cash_after": self.cash, "note": note}


def fill_row(o: Order, hist: History) -> Row | None:
    """주문이 체결되는 일봉 (아직 모르면 None). ETF: 주문일과 같거나 뒤인 첫 거래일, 코인: 주문일 일봉.
    판단 시각(decided_at)이 있으면: 주문일과 같거나 뒤이고 체결 가격 시각이 판단 시각 뒤인 첫 일봉."""
    if o.decided_at is not None:
        spec = C.ASSETS[o.asset]
        for r in hist.rows:
            if r.date >= o.run and C.fill_dt(spec, r.date) > o.decided_at:
                return r
        return None
    if C.on_exchange(o.asset):
        return hist.on_or_after(o.run)
    return hist.on(o.run)


def settle_cutoff(hists: dict[str, History]) -> dt.datetime | None:
    """모든 대상의 체결 가격을 알고 있는 마지막 시각 (대상마다 '마지막으로 아는 체결 시각' 중 가장 이른 것)."""
    times = []
    for a, h in hists.items():
        if h.last is None:
            return None
        times.append(C.fill_dt(C.ASSETS[a], h.last.date))
    return min(times) if times else None


def last_prices(hists: dict[str, History]) -> dict[str, tuple[dt.date, float]]:
    return {a: (h.last.date, h.last.close) for a, h in hists.items() if h.last}


def target_weight(p: float | None) -> float | None:
    """R_weights: 1/3 × clamp((p − 0.40) ÷ 0.20, 0, 1). 모름이면 None(그대로 둠)."""
    if p is None:
        return None
    return (1 / 3) * min(max((p - 0.40) / 0.20, 0.0), 1.0)


def rebuild(account_id: str, accounts: list[dict], orders: list[dict]) -> Account:
    """기록에서 계좌를 되살린다: 마지막 계좌 상태(현금·보유·체결 안 된 주문) + 그날 그 뒤에 낸 주문.
    계좌 기록이 없으면 시작 금액의 빈 계좌."""
    mine = [a for a in accounts if a["account"] == account_id]
    acc = Account(account_id)
    if not mine:
        return acc
    last = mine[-1]
    acc.cash = last["cash"]
    acc.units = {a: int(last["positions"].get(a, 0)) for a in C.ASSET_IDS}
    acc.units.update({a: int(n) for a, n in last["positions"].items() if a not in C.CORE and n})
    by_id = {o["id"]: o for o in orders if o["account"] == account_id}
    pend = [by_id[i] for i in last["pending"] if i in by_id]
    pend += [o for o in by_id.values() if o["run"] == last["run"] and o["id"] not in last["pending"]]
    acc.pending = [Order.from_record(o) for o in pend]
    return acc
