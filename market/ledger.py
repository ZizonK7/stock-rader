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

    def record(self) -> dict:
        return {"id": self.id, "run": self.run.isoformat(), "account": self.account, "asset": self.asset,
                "side": self.side, "qty_units": self.qty_units, "ref_price": self.ref_price, "reason": self.reason}


@dataclass
class Account:
    id: str
    cash: int = C.START_CASH
    units: dict[str, int] = field(default_factory=lambda: {a: 0 for a in C.ASSET_IDS})
    pending: list[Order] = field(default_factory=list)

    # ---------- 평가 ----------
    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.units[a] * prices[a] / C.ASSETS[a].unit for a in C.ASSET_IDS if a in prices)

    def expected(self, prices: dict[str, float]) -> tuple[dict[str, int], float]:
        """체결 안 된 주문까지 더한 (예상 보유, 예상 현금). 가격은 판단 때 알려진 마지막 종가."""
        units = dict(self.units)
        cash = float(self.cash)
        for o in self.pending:
            a = C.ASSETS[o.asset]
            amount = o.qty_units * prices.get(o.asset, o.ref_price) / a.unit
            if o.side == "buy":
                units[o.asset] += o.qty_units
                cash -= amount * (1 + a.fee)
            else:
                units[o.asset] -= o.qty_units
                cash += amount * (1 - a.fee)
        return units, cash

    def record(self, run: dt.date, prices: dict[str, tuple[dt.date, float]]) -> dict:
        p = {a: v for a, (_, v) in prices.items()}
        return {"id": f"{self.id}:{run.isoformat()}", "run": run.isoformat(), "account": self.id, "cash": self.cash,
                "positions": dict(self.units), "prices": {a: [d.isoformat(), v] for a, (d, v) in prices.items()},
                "value": round(self.value(p), 2), "pending": [o.id for o in self.pending]}

    # ---------- 주문 ----------
    def plan(self, run: dt.date, targets: dict[str, float | None], prices: dict[str, float],
             band: float = 0.02, reasons: dict[str, str] | None = None) -> list[Order]:
        """목표 비중(대상 → 0~1, None은 그대로 둠)으로 주문을 낸다. 차이가 계좌의 band보다 작으면 거래하지 않는다."""
        units, cash = self.expected(prices)
        total = cash + sum(units[a] * prices[a] / C.ASSETS[a].unit for a in C.ASSET_IDS if a in prices)
        orders: list[Order] = []
        sells, buys = [], []
        for a, w in targets.items():
            if w is None or a not in prices or prices[a] <= 0:
                continue
            spec = C.ASSETS[a]
            now = units[a] * prices[a] / spec.unit
            diff = w * total - now
            if abs(diff) < band * total:
                continue
            (buys if diff > 0 else sells).append((a, diff))
        for a, diff in sells:
            spec = C.ASSETS[a]
            q = min(units[a], math.floor(-diff / prices[a] * spec.unit))
            if q > 0:
                orders.append(Order(f"{self.id}:{run.isoformat()}:{a}", run, self.id, a, "sell", q, prices[a],
                                    (reasons or {}).get(a, "")))
                cash += q * prices[a] / spec.unit * (1 - spec.fee)
        for a, diff in buys:
            spec = C.ASSETS[a]
            spend = min(diff, max(cash, 0.0))
            q = math.floor(spend / (prices[a] * (1 + spec.fee)) * spec.unit)
            if q > 0:
                orders.append(Order(f"{self.id}:{run.isoformat()}:{a}", run, self.id, a, "buy", q, prices[a],
                                    (reasons or {}).get(a, "")))
                cash -= q * prices[a] / spec.unit * (1 + spec.fee)
        self.pending += orders
        return orders

    # ---------- 정산 ----------
    def settle(self, hists: dict[str, History], run: dt.date) -> list[dict]:
        """판단 시각에 알려진 일봉으로 체결할 수 있는 주문을 시간 순서대로 체결한다. 체결 기록 목록."""
        cutoff = settle_cutoff(hists)
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
            if q > self.units[o.asset]:
                q, note = self.units[o.asset], "가진 만큼만 팖"
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
            self.units[o.asset] += q
            self.cash -= value + fee
        return {"id": f"fill:{o.id}", "run": run.isoformat(), "account": self.id, "order": o.id, "asset": o.asset,
                "side": o.side, "fill_date": row.date.isoformat(), "price": price, "qty_units": q, "value": value,
                "fee": fee, "cash_after": self.cash, "note": note}


def fill_row(o: Order, hist: History) -> Row | None:
    """주문이 체결되는 일봉 (아직 모르면 None). ETF: 주문일과 같거나 뒤인 첫 거래일, 코인: 주문일 일봉."""
    if C.ASSETS[o.asset].kind == "etf":
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
