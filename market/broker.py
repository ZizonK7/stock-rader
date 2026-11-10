"""가상 증권사 — 받기·거절·체결·잔고를 정하는 유일한 곳 (docs/broker-sandbox.md 14–15절, 1회차: 핵심과 시험).

Claude(매일 실행)는 "무엇을 사고판다·옮긴다"만 요청하고, 이 프로그램이 규칙(market/rules.py)대로 받거나 거절하며 체결·잔고를 정한다.
**주문·체결·증권사 사건 기록 줄을 만드는 코드는 여기뿐이다.** 증권사는 줄(dict)을 만들어 들고 있다가 부른 쪽이 `write`로
한 번에 덧붙인다(모두 검사한 뒤 한 번에 — records.write).

계좌 하나의 상태
- 주머니 셋: bank(은행 원화), sec(증권 계좌 원화 + ETF·종목), upbit(업비트 원화 + 코인).
- 증권 계좌 원화는 날짜가 붙은 흐름으로 센다: 체결 돈은 결제일(체결 + 2영업일)에, 이체·시작 잔고는 바로.
  주문 가능 금액 = 흐름 전부의 합 − 걸린 사기 주문이 잡은 돈 − 같은 시각에 들어온 체결 돈(T1).
  출금 가능 금액 = 지금 뺄 수 있는 돈(결제일 09:00 뒤의 체결 돈 + 바로 들어온 돈)부터 앞으로 날마다 더해 간 잔고의 가장 작은 값
  − 걸린 사기 주문이 잡은 돈(판 돈은 결제일 전에는 못 뺀다).
- 보유(최소 단위 개수)와 산 값의 합(이동 평균 — 해외 지수 ETF 이익 세금), 걸린 주문.
- 상태는 기록 줄을 **seq 순서**(증권사가 줄을 만든 순서 = 그 줄을 알게 된 순서)로 다시 돌려 얻는다. 요청(주문·이체·취소)의
  시각은 앞 줄들의 시각보다 이를 수 없으므로(지난 시각 주문 거절) 요청 사이에서는 seq 순서 = 시각 순서다. 늦게 알게 된 체결
  (09:00 체결을 다음 실행에서 넣음)은 seq로는 뒤지만 그 줄의 시각(at)을 같이 적고, 돈·수량은 더하기라 순서와 상관없이 같다.

미래를 못 보는 구조
- 증권사가 가격을 보는 길은 quotes 공급자의 now·book·day(+ known_days)뿐이고, 모두 시각 at을 넘긴다. 공급자는 at에 알 수 있는
  값만 준다(quotes.BarQuotes: 그날 09:00 전엔 그날 봉 없음, 끝나기 전엔 시가만). sync(at)·order(at)는 at 뒤의 체결을 만들지 않는다.
- 같은 시각에 일어난 체결의 돈·수량은 같은 시각의 요청에 못 쓴다(T1, State.fresh).

쓰는 법
    b = Broker.load(folder, "live:hand", quotes.NetQuotes())   # 또는 Broker("acc", quotes, lines)
    b.start(at, bank=10_000_000)                                # 처음 한 번
    r = b.order(at, "069500", "buy", "open", qty=10, limit=80_000)
    if not r.ok: print(r.reason)                               # 거절도 broker 줄(type reject)로 남는다
    b.write(folder)                                             # 새 줄을 한 번에 덧붙임
검산: check(lines, bars) / check_folder(folder) — records check가 부른다.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import config as C
from . import prices as PR
from . import quotes as Q
from . import records as R
from . import rules as RU

POCKETS = R.POCKETS                                  # 은행 / 증권 계좌 / 업비트
ORDER_KINDS = R.ORDER_KINDS                          # 장 시작 / 장 마감 / 지금
FILL_KINDS = R.PRICE_KINDS                           # 체결 줄 price_kind (limit = 장 시작에 못 사고 장중 한도 가격에)
BROKER_TYPES = R.BROKER_TYPES
REQUEST_TYPES = ("start", "transfer", "cancel", "plan", "phase")   # 요청(시각이 앞 줄보다 이를 수 없음)
ROUTES = {("bank", "sec"), ("bank", "upbit"), ("sec", "bank"), ("upbit", "bank")}
KINDS = ("order", "fill", "broker", "account")       # 증권사가 만드는 기록 종류(쓰는 순서)
LOOKBACK_DAYS = 12                                    # 전 거래일 종가·뒤 봉을 찾을 때 거슬러 보는 날 수
ADJUST_DAYS = 10                                      # 진행 중인 봉으로 넣은 체결을 끝난 봉과 견주는 기간(일)


def pocket_of(asset: str) -> str:
    return "upbit" if C.ASSETS[asset].kind == "coin" else "sec"


def _t(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(C.KST).isoformat(timespec="seconds")


def _ceil(x: float) -> int:
    return math.ceil(round(x, 6))


def _floor(x: float) -> int:
    return math.floor(round(x, 6))


def _at(t: dt.datetime) -> dt.datetime:
    if t.tzinfo is None:
        raise ValueError("시각에 시간대가 없다 — config.KST를 붙인다")
    return t.astimezone(C.KST).replace(microsecond=0)


def _kst(day: dt.date, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, t, C.KST)


@dataclass
class Pending:
    id: str
    asset: str
    side: str
    kind: str
    qty: int
    limit: float | None
    amount: int | None
    reserved: int
    at: dt.datetime
    session: dt.date | None
    pocket: str


@dataclass
class Result:
    ok: bool
    reason: str = ""
    order: str | None = None
    lines: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------- 상태 (기록 줄을 다시 돌려 만든다) ----------
class State:
    def __init__(self, account: str):
        self.account = account
        self.started = False
        self.bank = 0
        self.upbit = 0
        self.flows: list[tuple[dt.date, int, bool]] = []   # 증권 계좌: (날짜, 금액, 바로 = 이체·시작)
        self.units: dict[str, int] = {}
        self.cost: dict[str, float] = {}                    # 대상 → 산 값의 합(수수료 뺌, 이동 평균)
        self.pending: dict[str, Pending] = {}
        self.seq = 0
        self.last_at: dt.datetime | None = None
        self.fresh: dict[dt.datetime, dict[str, int]] = {}  # 시각 → {주머니·대상: 그 시각 체결로 들어온 돈·수량}
        self.unverified: dict[str, dict] = {}               # 진행 중인 봉 값으로 넣은 장 시작 체결(끝난 봉과 아직 안 견줌)
        self.adjusted: set[str] = set()
        self.orders: dict[str, dict] = {}
        self.fills: dict[str, dict] = {}

    # ----- 조회 -----
    def sec_total(self) -> int:
        return sum(a for _, a, _ in self.flows)

    def pocket_cash(self, pocket: str) -> int:
        return {"bank": self.bank, "sec": self.sec_total(), "upbit": self.upbit}[pocket]

    def reserved(self, pocket: str) -> int:
        return sum(p.reserved for p in self.pending.values() if p.pocket == pocket and p.side == "buy")

    def _fresh(self, at: dt.datetime, key: str) -> int:
        return self.fresh.get(at, {}).get(key, 0)

    def orderable(self, pocket: str, at: dt.datetime) -> int:
        """주문 가능 금액. 증권 계좌: 판 돈은 결제 전이어도 들어간다(krx_rebuy). 같은 시각의 체결 돈은 빼고(T1)."""
        return self.pocket_cash(pocket) - self.reserved(pocket) - self._fresh(at, pocket)

    def withdrawable(self, pocket: str, at: dt.datetime) -> int:
        """출금 가능 금액(이체에 쓴다). 증권 계좌는 결제 전 돈을 못 뺀다."""
        if pocket != "sec":
            return self.pocket_cash(pocket) - self.reserved(pocket) - self._fresh(at, pocket)
        now = 0
        later: dict[dt.date, int] = {}
        for day, amt, instant in self.flows:
            if (instant and day <= at.date()) or (not instant and RU.settled_by(day, at)):
                now += amt
            else:
                later[day] = later.get(day, 0) + amt
        low = cum = now
        for day in sorted(later):
            cum += later[day]
            low = min(low, cum)
        return max(0, low - self.reserved("sec"))

    def sellable(self, asset: str, at: dt.datetime) -> int:
        held = sum(p.qty for p in self.pending.values() if p.asset == asset and p.side == "sell")
        return self.units.get(asset, 0) - held - self._fresh(at, asset)

    def last_event(self) -> dt.datetime | None:
        return self.last_at

    def sec_low(self) -> int:
        """증권 계좌 원화가 날마다 가장 낮을 때(모든 흐름을 날짜 순서로 더해 감) — 음수면 안 된다."""
        by: dict[dt.date, int] = {}
        for day, amt, _ in self.flows:
            by[day] = by.get(day, 0) + amt
        low = cum = 0
        for day in sorted(by):
            cum += by[day]
            low = min(low, cum)
        return low

    def positions(self) -> dict[str, int]:
        pos = {a: self.units.get(a, 0) for a in C.ASSET_IDS}
        pos.update({a: n for a, n in self.units.items() if a not in C.CORE and n})
        return pos

    # ----- 줄 하나 적용 -----
    def apply(self, kind: str, d: dict) -> list[str]:
        """줄 하나를 상태에 더한다. 받을 때 조건이 안 맞았던 줄이면 오류 글 목록(상태는 그래도 더한다)."""
        w = f"{self.account} {kind} {d.get('id')}"
        errs: list[str] = []
        seq = d.get("seq")
        if not isinstance(seq, int) or seq <= self.seq:
            errs.append(f"{w}: seq {seq}가 앞 줄({self.seq})보다 크지 않음")
        else:
            self.seq = seq
        try:
            at = _t(d["decided_at"] if kind == "order" else d["at"])
        except (KeyError, TypeError, ValueError):
            return errs + [f"{w}: 시각 칸이 없거나 틀림"]
        typ = d.get("type") if kind == "broker" else None
        request = kind == "order" or typ in REQUEST_TYPES
        if request and self.last_at is not None and at < self.last_at:
            errs.append(f"{w}: 요청 시각 {_iso(at)}가 앞 사건({_iso(self.last_at)})보다 이름 — 지난 시각으로 요청할 수 없다")
        if typ != "reject":
            self.last_at = at if self.last_at is None else max(self.last_at, at)
        if not self.started and typ != "start":
            if typ != "reject":
                errs.append(f"{w}: 시작 잔고(start) 전의 줄")
            return errs
        handler = {"order": self._order, "fill": self._fill, "account": self._account}.get(kind)
        if kind == "broker":
            handler = getattr(self, f"_b_{typ}", None)
            if handler is None:
                return errs + [f"{w}: 모르는 사건 종류 '{typ}'"]
        if handler is None:
            return errs + [f"{w}: 모르는 기록 종류"]
        try:
            errs += [f"{w}: {e}" for e in handler(d, at)]
        except (KeyError, TypeError, ValueError) as exc:   # 칸이 빠졌거나 틀린 줄(손으로 적은 줄 등)
            return errs + [f"{w}: 줄 모양이 틀림 ({exc!r})"]
        if self.bank < 0 or self.upbit < 0 or self.sec_low() < 0:
            errs.append(f"{w}: 주머니가 음수가 됨 (은행 {self.bank}, 업비트 {self.upbit}, 증권 가장 낮을 때 {self.sec_low()})")
        if any(n < 0 for n in self.units.values()):
            errs.append(f"{w}: 보유 수량이 음수가 됨")
        return errs

    def _b_start(self, d: dict, at: dt.datetime) -> list[str]:
        if self.started:
            return ["시작 잔고가 두 번"]
        self.started = True
        data = d.get("data", {})
        pk = data.get("pockets", {})
        self.bank, self.upbit = int(pk.get("bank", 0)), int(pk.get("upbit", 0))
        if pk.get("sec", 0):
            self.flows.append((at.date(), int(pk["sec"]), True))
        self.units = {a: int(n) for a, n in data.get("units", {}).items()}
        self.cost = {a: float(v) for a, v in data.get("cost", {}).items()}
        return [] if min(self.bank, self.upbit, int(pk.get("sec", 0))) >= 0 else ["시작 잔고가 음수"]

    def _b_reject(self, d, at):
        return []

    def _b_plan(self, d, at):
        return []

    def _b_phase(self, d, at):
        return []

    def _b_transfer(self, d: dict, at: dt.datetime) -> list[str]:
        x = d.get("data", {})
        src, dst, amt, fee = x.get("src"), x.get("dst"), x.get("amount"), x.get("fee", 0)
        errs = []
        if (src, dst) not in ROUTES:
            return [f"이체 길 {src} → {dst}이 없음 — 주머니 사이는 은행을 거친다"]
        if not isinstance(amt, int) or amt <= 0:
            return ["이체 금액은 양의 정수(원)"]
        want_fee = RU.value("upbit_krw_withdraw_fee") if src == "upbit" else RU.value("bank_transfer")["fee"]
        if fee != want_fee:
            errs.append(f"이체 수수료 {fee} ≠ 규칙 {want_fee}")
        if amt + fee > self.withdrawable(src, at):
            errs.append(f"이체 {amt + fee}원 > {src} 출금 가능 {self.withdrawable(src, at)}원")
        self._move(src, -(amt + fee), at.date())
        self._move(dst, amt, at.date())
        return errs

    def _move(self, pocket: str, amt: int, day: dt.date, settle: dt.date | None = None) -> None:
        if pocket == "bank":
            self.bank += amt
        elif pocket == "upbit":
            self.upbit += amt
        else:
            self.flows.append((settle or day, amt, settle is None))

    def _b_cancel(self, d: dict, at: dt.datetime) -> list[str]:
        oid = d.get("data", {}).get("order")
        p = self.pending.pop(oid, None)
        if p is None:
            return [f"취소할 주문 {oid}이 걸려 있지 않음"]
        first = first_fill_time(p)
        return [] if first is None or at < first else [f"체결 시각({_iso(first)}) 뒤의 취소"]

    def _b_expire(self, d: dict, at: dt.datetime) -> list[str]:
        oid = d.get("data", {}).get("order")
        return [] if self.pending.pop(oid, None) else [f"끝낼 주문 {oid}이 걸려 있지 않음"]

    def _b_adjust(self, d: dict, at: dt.datetime) -> list[str]:
        x = d.get("data", {})
        f = self.fills.get(x.get("fill"))
        if f is None:
            return [f"바로잡을 체결 {x.get('fill')}이 없음"]
        settle = dt.date.fromisoformat(f["settle"]) if f.get("settle") else None
        self._move(x.get("pocket", f.get("pocket")), int(x.get("cash", 0)), at.date(), settle)
        self.cost[f["asset"]] = self.cost.get(f["asset"], 0.0) + float(x.get("cost", 0))
        self.adjusted.add(f["id"])
        self.unverified.pop(f["id"], None)
        return []

    def _order(self, d: dict, at: dt.datetime) -> list[str]:
        errs = []
        a, side, kind, q = d.get("asset"), d.get("side"), d.get("order_kind"), d.get("qty_units", 0)
        pocket = d.get("pocket")
        if pocket != pocket_of(a):
            errs.append(f"주머니 {pocket}가 대상의 주머니 {pocket_of(a)}와 다름")
        res = int(d.get("reserved", 0))
        if side == "buy":
            if res <= 0:
                errs.append("사기 주문이 잡은 돈(reserved)이 없음")
            if res > self.orderable(pocket, at):
                errs.append(f"사기 주문 {res}원 > 주문 가능 금액 {self.orderable(pocket, at)}원 (받을 때 돈이 없었다)")
            lim = d.get("limit")
            if kind in ("open", "close") and lim is not None and res != _ceil(q * lim) + RU.fee(a, _ceil(q * lim)):
                errs.append("잡은 돈이 한도 × 수량 + 수수료와 다름")
            if pocket == "upbit" and d.get("amount") is not None and res != d["amount"] + RU.fee(a, d["amount"]):
                errs.append("잡은 돈이 금액 + 수수료와 다름")
        else:
            if q <= 0 or q > self.sellable(a, at):
                errs.append(f"팔기 {q} > 팔 수 있는 수량 {self.sellable(a, at)} (가진 것 − 걸린 팔기)")
        self.orders[d["id"]] = d
        session = dt.date.fromisoformat(d["session"]) if d.get("session") else None
        self.pending[d["id"]] = Pending(d["id"], a, side, kind, q, d.get("limit"), d.get("amount"), res, at, session, pocket)
        return errs

    def _fill(self, d: dict, at: dt.datetime) -> list[str]:
        errs = []
        p = self.pending.pop(d.get("order"), None)
        if p is None:
            return [f"체결할 주문 {d.get('order')}이 걸려 있지 않음(손으로 적은 체결?)"]
        a, q, price, value, fee, tax = p.asset, d["qty_units"], d["price"], d["value"], d["fee"], d.get("tax", 0)
        unit = C.ASSETS[a].unit
        kind = d.get("price_kind")
        if d.get("asset") != a or d.get("side") != p.side:
            errs.append("체결의 대상·방향이 주문과 다름")
        if p.pocket == "sec":
            if q != p.qty:
                errs.append(f"체결 수량 {q} ≠ 주문 {p.qty} (한국 거래소 주문은 전부 체결되거나 안 된다)")
            want = _ceil(q * price) if p.side == "buy" else _floor(q * price)
            if value != want:
                errs.append(f"체결 금액 {value} ≠ 수량 × 가격 {want}")
            if p.side == "buy" and p.limit is not None:
                if kind in ("open", "close") and not price < p.limit:
                    errs.append(f"한도 {p.limit} 이상의 값 {price}에 산 체결")
                if kind == "limit" and price != p.limit:
                    errs.append(f"장중 한도 체결의 값 {price} ≠ 한도 {p.limit}")
            if kind not in ({p.kind, "limit"} if p.kind == "open" else {p.kind}):
                errs.append(f"체결 가격 종류 {kind}가 주문 종류 {p.kind}와 맞지 않음")
        else:
            if q <= 0 or (p.side == "sell" and q > p.qty):
                errs.append(f"업비트 체결 수량 {q}이 틀림")
            if abs(value - q * price / unit) > 1.0:
                errs.append(f"업비트 체결 금액 {value} ≠ 수량 × 평균값 {q * price / unit:.0f}")
            if p.side == "buy" and p.amount is not None and value > p.amount:
                errs.append(f"업비트 사기 {value}원 > 주문 금액 {p.amount}원")
        if fee != RU.fee(a, value):
            errs.append(f"수수료 {fee} ≠ 규칙 {RU.fee(a, value)}")
        settle = dt.date.fromisoformat(d["settle"]) if d.get("settle") else None
        if (p.pocket == "sec") != (settle is not None):
            errs.append("증권 계좌 체결에만 결제일이 있다")
        held = self.units.get(a, 0)
        if p.side == "buy":
            if tax:
                errs.append("사기에 세금")
            self._move(p.pocket, -(value + fee), at.date(), settle)
            self.units[a] = held + q
            self.cost[a] = self.cost.get(a, 0.0) + value
        else:
            basis = self.cost.get(a, 0.0) * q / held if held else 0.0
            gain = value - basis
            if tax != RU.sell_tax(a, value, gain):
                errs.append(f"세금 {tax} ≠ 규칙 {RU.sell_tax(a, value, gain)}")
            self._move(p.pocket, value - fee - tax, at.date(), settle)
            self.units[a] = held - q
            self.cost[a] = self.cost.get(a, 0.0) - basis
            if self.units[a] == 0:
                self.cost[a] = 0.0
        if d.get("cash_after") != self.pocket_cash(p.pocket):
            errs.append(f"cash_after {d.get('cash_after')} ≠ 다시 센 {p.pocket} 원화 {self.pocket_cash(p.pocket)}")
        got = self.fresh.setdefault(at, {})
        if p.side == "sell":
            got[p.pocket] = got.get(p.pocket, 0) + value - fee - tax
        else:
            got[a] = got.get(a, 0) + q
        self.fills[d["id"]] = d
        src = d.get("source") or {}
        if kind == "open" and src.get("final") is False:
            self.unverified[d["id"]] = d
        return errs

    def _account(self, d: dict, at: dt.datetime) -> list[str]:
        errs = []
        pk = {k: self.pocket_cash(k) for k in POCKETS}
        if d.get("pockets") != pk:
            errs.append(f"주머니 {d.get('pockets')} ≠ 다시 센 {pk}")
        if d.get("cash") != sum(pk.values()):
            errs.append(f"cash {d.get('cash')} ≠ 세 주머니 합 {sum(pk.values())}")
        if d.get("positions") != self.positions():
            errs.append(f"보유 {d.get('positions')} ≠ 다시 센 {self.positions()}")
        if sorted(d.get("pending", [])) != sorted(self.pending):
            errs.append("걸린 주문 목록이 다시 센 것과 다름")
        return errs


def first_fill_time(p: Pending) -> dt.datetime | None:
    """걸린 주문이 체결될 수 있는 가장 이른 시각(취소는 이 전에만)."""
    if p.session is None:
        return None
    h = RU.hours()
    return _kst(p.session, h["open"] if p.kind == "open" else h["close"])


# ---------- 증권사 ----------
class Broker:
    def __init__(self, account: str, quotes: Q.Quotes, lines: dict[str, list[dict]] | None = None,
                 bar_days: Iterable[dt.date] | None = None):
        self.account = account
        self.quotes = quotes
        self.bar_days = frozenset(bar_days) if bar_days else None
        self.state = State(account)
        self.new: dict[str, list[dict]] = {k: [] for k in KINDS}
        self.problems: list[str] = []
        self._verified: set[str] = set()
        self._account_ids: set[str] = set()
        self._broker_lines: list[dict] = []
        for kind, d in mine(lines or {}, account):
            self.problems += self.state.apply(kind, d)
            if kind == "account":
                self._account_ids.add(d["id"])
            elif kind == "broker":
                self._broker_lines.append(d)

    @classmethod
    def load(cls, folder: Path, account: str, quotes: Q.Quotes, bar_days: Iterable[dt.date] | None = None) -> "Broker":
        return cls(account, quotes, {k: R.read(folder, k) for k in KINDS}, bar_days)

    # ----- 쓰기 -----
    def write(self, folder: Path) -> int:
        """새 줄을 모두 검사한 뒤 종류별로 덧붙인다(하나라도 틀리면 아무것도 쓰지 않는다). 쓴 줄 수."""
        errs = [e for k in KINDS for i, it in enumerate(self.new[k])
                for e in R.line_errors(k, {"kind": k, **it}, f"{k}+{i}")]
        if errs:
            raise ValueError("기록 칸 오류:\n" + "\n".join(errs))
        n = 0
        for k in KINDS:
            if self.new[k]:
                R.write(folder, k, self.new[k])
                n += len(self.new[k])
                self.new[k] = []
        return n

    def lines(self) -> dict[str, list[dict]]:
        return {k: list(v) for k, v in self.new.items()}

    def _emit(self, res: Result, kind: str, line: dict) -> dict:
        line["seq"] = self.state.seq + 1
        errs = self.state.apply(kind, line)
        if errs:  # 증권사가 검사한 뒤 만든 줄이 상태 검사에 걸리면 증권사 코드의 잘못이다
            raise RuntimeError("증권사 내부 오류:\n" + "\n".join(errs))
        self.new[kind].append(line)
        if kind == "broker":
            self._broker_lines.append(line)
        res.lines.append((kind, line))
        return line

    def _broker_line(self, res: Result, at: dt.datetime, typ: str, data: dict, note: str = "",
                     run: dt.datetime | None = None) -> dict:
        """run = 줄을 만든 호출의 시각(기본 at) — 늦게 넣는 만료 줄은 사건 시각(at)이 앞 날이어도 run은 오늘이다."""
        seq = self.state.seq + 1
        return self._emit(res, "broker", {"id": f"{self.account}:{at:%Y-%m-%d}:{typ}:{seq}",
                                          "run": (run or at).date().isoformat(),
                                          "account": self.account, "at": _iso(at), "type": typ, "data": data,
                                          "note": note})

    def _reject(self, res: Result, at: dt.datetime, request: dict, reason: str) -> Result:
        res.ok, res.reason = False, reason
        self._broker_line(res, at, "reject", {k: v for k, v in request.items() if v is not None}, reason)
        return res

    # ----- 조회 -----
    def _days(self, at: dt.datetime) -> frozenset[dt.date] | None:
        known = self.quotes.known_days(at)
        if self.bar_days:
            return frozenset(d for d in self.bar_days if d < at.date()) | (known or frozenset())
        return known

    def orderable(self, pocket: str, at: dt.datetime) -> int:
        return self.state.orderable(pocket, _at(at))

    def withdrawable(self, pocket: str, at: dt.datetime) -> int:
        return self.state.withdrawable(pocket, _at(at))

    def holdings(self) -> dict[str, int]:
        return {a: n for a, n in self.state.units.items() if n}

    def avg_cost(self, asset: str) -> float | None:
        n = self.state.units.get(asset, 0)
        return self.state.cost.get(asset, 0.0) / n if n else None

    def pending(self) -> list[Pending]:
        return list(self.state.pending.values())

    def pockets(self) -> dict[str, int]:
        return {k: self.state.pocket_cash(k) for k in POCKETS}

    def sellable(self, asset: str, at: dt.datetime) -> int:
        return self.state.sellable(asset, _at(at))

    def expected_units(self) -> dict[str, int]:
        """든 수량에 걸린 주문을 더한 것(걸린 사기는 든 것으로, 걸린 팔기는 판 것으로) — 주문 계획이 이것으로 차이를 잰다."""
        out = {a: n for a, n in self.state.units.items() if n}
        for p in self.state.pending.values():
            out[p.asset] = out.get(p.asset, 0) + (p.qty if p.side == "buy" else -p.qty)
        return out

    def market(self, at: dt.datetime) -> str:
        """한국 거래소에 지금 낼 수 있는 주문: "open"(장이 닫혀 있음 → 다음 장 시작 주문) / "now"(장중 → 지금 주문) /
        ""(둘 다 못 냄 — 09:00 정각, 장 마감 동시호가 15:20–15:30)."""
        at = _at(at)
        h = RU.hours()
        if not RU.is_business_day(at.date(), self._days(at)):
            return "open"
        t = at.time()
        if h["open"] < t < h["close_auction"]:
            return "now"
        return "open" if t < h["open"] or t >= h["close"] else ""

    def events(self, typ: str) -> list[dict]:
        """이 계좌의 증권사 사건 줄(지난 것 + 이번에 만든 것) 중 그 종류."""
        return [d for d in self._broker_lines if d.get("type") == typ]

    # ----- 시작·사건 -----
    def start(self, at: dt.datetime, bank: int = 0, sec: int = 0, upbit: int = 0,
              units: dict[str, int] | None = None, cost: dict[str, float] | None = None, note: str = "") -> Result:
        at = _at(at)
        res = Result(True)
        if self.state.started:
            return self._reject(res, at, {"type": "start"}, "이미 시작 잔고가 있다")
        if min(bank, sec, upbit) < 0 or any(int(n) < 0 for n in (units or {}).values()):
            return self._reject(res, at, {"type": "start"}, "시작 잔고가 음수")
        data = {"pockets": {"bank": int(bank), "sec": int(sec), "upbit": int(upbit)},
                "units": {a: int(n) for a, n in (units or {}).items() if n},
                "cost": {a: float(v) for a, v in (cost or {}).items() if (units or {}).get(a)}}
        self._broker_line(res, at, "start", data, note)
        return res

    def mark(self, at: dt.datetime, typ: str, data: dict, note: str = "") -> Result:
        """그날의 목표 비중(plan)·단계 표시(phase) 줄."""
        at = _at(at)
        res = Result(True)
        if typ not in ("plan", "phase"):
            raise ValueError(f"mark는 plan/phase만 — {typ}")
        if self._past(at):
            return self._reject(res, at, {"type": typ}, self._past(at))
        self._broker_line(res, at, typ, data, note)
        return res

    def _past(self, at: dt.datetime) -> str:
        last = self.state.last_event()
        if last is not None and at < last:
            return f"지난 시각 {_iso(at)} — 이 계좌의 마지막 사건({_iso(last)})보다 이르다"
        return ""

    # ----- 맞추기 -----
    def sync(self, at: dt.datetime) -> Result:
        """시각 at에 알 수 있는 체결·만료를 장부에 넣고, 진행 중인 봉 값으로 넣은 체결을 끝난 봉과 견준다(어긋나면 adjust)."""
        at = _at(at)
        res = Result(True)
        if self._past(at):
            res.ok, res.reason = False, self._past(at)
            return res
        if not self.state.started:
            return res
        events = []
        for p in sorted(self.state.pending.values(), key=lambda p: (p.at, p.id)):
            if p.pocket == "sec":
                ev = self._due(p, at)
                if ev is not None:
                    events.append(ev)
        for ev in sorted(events, key=lambda e: (e[0], e[1].at, e[1].id)):
            when, p, what, price, day, src = ev
            if what == "expire":
                self._broker_line(res, when, "expire", {"order": p.id}, price, run=at)
            else:
                self._fill_krx(res, p, when, price, what, day, src, run=at)
        self._verify(res, at)
        return res

    def _due(self, p: Pending, at: dt.datetime):
        """걸린 한국 거래소 주문의 (시각, 주문, 'expire'|가격 종류, 가격|이유, 봉 날짜, 출처) — 아직 모르면 None."""
        h = RU.hours()
        d = p.session
        close_at = _kst(d, h["close"])
        if at < _kst(d, h["open"]) or (p.kind == "close" and C.final_dt(C.ASSETS[p.asset], d) > at):
            return None  # 그 장의 값이 아직 없다 — 받으러 가지 않는다(실제 주소를 헛되이 부르지 않게)
        day = self.quotes.day(p.asset, d, at)
        if day is None:
            if C.final_dt(C.ASSETS[p.asset], d) <= at and self._later_bar(p.asset, d, at):
                return close_at, p, "expire", "그날 봉이 없음(휴장·거래 정지로 봄)", d, None
            return None
        src = {"from": day.source, "time": _iso(at), "final": day.final}
        if day.open <= 0:
            return (close_at, p, "expire", "거래 정지(시가 0)", d, None) if day.final else None
        if p.kind == "open":
            if p.side == "sell" or day.open < p.limit:
                return _kst(d, h["open"]), p, "open", day.open, d, src
            if not day.final:
                return None
            if day.low is not None and day.low < p.limit:
                return close_at, p, "limit", p.limit, d, src
            return close_at, p, "expire", "시가가 한도 이상이고 장중 저가도 한도 이상", d, None
        if not day.final:
            return None
        if p.side == "sell" or day.close < p.limit:
            return close_at, p, "close", day.close, d, src
        return close_at, p, "expire", "종가가 한도 이상", d, None

    def _later_bar(self, asset: str, day: dt.date, at: dt.datetime) -> bool:
        d = day + dt.timedelta(days=1)
        for _ in range(LOOKBACK_DAYS):
            if d > at.date():
                return False
            if self.quotes.day(asset, d, at) is not None:
                return True
            d += dt.timedelta(days=1)
        return False

    def _fill_krx(self, res: Result, p: Pending, when: dt.datetime, price: float, kind: str, day: dt.date,
                  src: dict | None, note: str = "", run: dt.datetime | None = None) -> dict:
        """한국 거래소 체결 줄. when = 체결 시각, run = 줄을 만든 호출의 시각(늦게 넣으면 when보다 뒤)."""
        a, q = p.asset, p.qty
        settle = RU.settle_date(day, self._days(when))
        line = {"id": f"fill:{p.id}", "run": (run or when).date().isoformat(),
                "account": self.account, "order": p.id, "asset": a, "side": p.side, "fill_date": day.isoformat(),
                "price": float(price), "price_kind": kind, "qty_units": q, "at": _iso(when), "pocket": "sec",
                "settle": settle.isoformat(), "source": src or {}, "note": note}
        if p.side == "buy":
            value = _ceil(q * price)
            fee, tax = RU.fee(a, value), 0
            delta = -(value + fee)
        else:
            value = _floor(q * price)
            held = self.state.units.get(a, 0)
            gain = value - (self.state.cost.get(a, 0.0) * q / held if held else 0.0)
            fee, tax = RU.fee(a, value), RU.sell_tax(a, value, gain)
            delta = value - fee - tax
            line["gain"] = round(gain, 2)
        line.update(value=value, fee=fee, tax=tax, cash_after=self.state.sec_total() + delta)
        return self._emit(res, "fill", line)

    def _verify(self, res: Result, at: dt.datetime) -> None:
        """진행 중인 봉의 시가로 넣은 장 시작 체결을 끝난 봉의 시가와 견준다. 다르면 adjust 줄과 경고(지난 줄은 고치지 않는다)."""
        for fid, f in sorted(self.state.unverified.items()):
            if fid in self._verified:
                continue
            day = dt.date.fromisoformat(f["fill_date"])
            if (at.date() - day).days > ADJUST_DAYS or C.final_dt(C.ASSETS[f["asset"]], day) > at:
                continue
            got = self.quotes.day(f["asset"], day, at)
            if got is None or not got.final:
                continue
            if got.open == f["price"]:
                self._verified.add(fid)
                continue
            a, q, side = f["asset"], f["qty_units"], f["side"]
            if side == "buy":
                v = _ceil(q * got.open)
                cash, cost = -((v + RU.fee(a, v)) - (f["value"] + f["fee"])), v - f["value"]
            else:
                v = _floor(q * got.open)
                gain = f.get("gain", 0.0) + (v - f["value"])
                cash = (v - RU.fee(a, v) - RU.sell_tax(a, v, gain)) - (f["value"] - f["fee"] - f.get("tax", 0))
                cost = 0
            msg = (f"{fid}: 진행 중인 봉에서 받은 시가 {f['price']}가 끝난 봉의 시가 {got.open}와 다름 — "
                   f"차액 {cash}원을 adjust 줄로 바로잡음")
            lim = (self.state.orders.get(f.get("order")) or {}).get("limit")
            if side == "buy" and lim is not None and got.open >= lim:
                msg += (f". 끝난 봉의 시가가 한도 {lim} 이상이다 — 실제로는 그 시각(09:00)에 체결되지 않았을 주문이다"
                        f"(체결은 되돌리지 않고 값만 바로잡았다)")
            self._broker_line(res, at, "adjust", {"fill": fid, "asset": a, "pocket": f["pocket"], "price_was": f["price"],
                                                  "price_final": got.open, "cash": cash, "cost": cost}, msg)
            res.warnings.append(msg)

    # ----- 요청 -----
    def order(self, at: dt.datetime, asset: str, side: str, kind: str, qty: int | None = None,
              limit: float | None = None, amount: int | None = None, reason: str = "") -> Result:
        """주문 하나. kind: open(장 시작) / close(장 마감) / now(지금). 안 되면 거절(reject 줄) — 수량·값을 바꿔 주지 않는다."""
        at = _at(at)
        res = self.sync(at)
        res.ok, res.reason = True, ""
        req = {"type": "order", "asset": asset, "side": side, "kind": kind, "qty": qty, "limit": limit, "amount": amount,
               "reason": reason}
        why = self._past(at) or ("" if self.state.started else "시작 잔고(start) 전")
        if why:
            return self._reject(res, at, req, why)
        try:
            spec = C.ASSETS[asset]
        except KeyError:
            return self._reject(res, at, req, f"모르는 대상 {asset}")
        if side not in ("buy", "sell") or kind not in ORDER_KINDS:
            return self._reject(res, at, req, f"side는 buy/sell, kind는 {list(ORDER_KINDS)}")
        if spec.kind == "coin":
            return self._order_upbit(res, at, req, asset, side, kind, qty, amount, reason)
        return self._order_krx(res, at, req, asset, side, kind, qty, limit, amount, reason)

    def _prev_close(self, asset: str, session: dt.date, at: dt.datetime) -> float | None:
        """session 전 거래일의 공식 종가(시각 at에 끝난 봉만). 모르면 None."""
        d = session - dt.timedelta(days=1)
        for _ in range(LOOKBACK_DAYS):
            day = self.quotes.day(asset, d, at)
            if day is not None:
                return day.close if day.final and day.close and day.close > 0 else None
            d -= dt.timedelta(days=1)
        return None

    def _limit_error(self, asset: str, price: float, prev: float | None) -> str:
        if not RU.on_tick(asset, price):
            return f"가격 {price}이 호가 단위({RU.tick(asset, price)}원)에 맞지 않음"
        if prev is None:
            return "전 거래일 종가를 몰라 가격 제한 폭을 알 수 없음"
        lo, hi = RU.price_limits(asset, prev)
        if not lo <= price <= hi:
            return f"가격 {price}이 가격 제한 폭 {lo}–{hi}(전 거래일 종가 {prev} ± 30%) 밖"
        return ""

    def _order_line(self, at, asset, side, kind, qty, ref, reason, reserved, limit=None, amount=None, session=None) -> dict:
        seq = self.state.seq + 1
        line = {"id": f"{self.account}:{at:%Y-%m-%d}:{asset}:{kind}:{side}:{seq}", "run": at.date().isoformat(),
                "account": self.account, "asset": asset, "side": side, "qty_units": int(qty), "ref_price": float(ref),
                "reason": reason, "decided_at": _iso(at), "fill_rule": kind, "pocket": pocket_of(asset),
                "order_kind": kind, "reserved": int(reserved)}
        if limit is not None:
            line["limit"] = float(limit)
        if amount is not None:
            line["amount"] = int(amount)
        if session is not None:
            line["session"] = session.isoformat()
        return line

    def _quote_error(self, t: dt.datetime, at: dt.datetime) -> str:
        age = (at - t).total_seconds()
        if age > RU.value("now_quote_max_age"):
            return f"받은 가격이 {age:.0f}초 묵었음(> {RU.value('now_quote_max_age')}초)"
        if -age > RU.value("quote_clock_skew"):
            return f"받은 가격의 시각이 주문 시각보다 {-age:.0f}초 늦음 — 시계가 어긋났다"
        return ""

    def _order_krx(self, res, at, req, asset, side, kind, qty, limit, amount, reason) -> Result:
        h = RU.hours()
        if amount is not None or not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return self._reject(res, at, req, "한국 거래소 주문은 수량(주, 양의 정수)으로 낸다")
        if side == "sell" and limit is not None:
            return self._reject(res, at, req, "팔기는 시장가 — 한도 가격을 받지 않는다")
        days = self._days(at)
        today_open = RU.is_business_day(at.date(), days)
        tm = at.time()
        if kind == "now":
            if not today_open or not h["open"] < tm < h["close_auction"]:
                return self._reject(res, at, req, "지금 주문은 영업일 장중(09:00 뒤–15:20 전)에만")
            if limit is not None:
                return self._reject(res, at, req, "지금 주문은 한도 가격을 받지 않는다(가격은 증권사가 받은 실제 값)")
            got = self.quotes.now(asset, at)
            if got is None:
                return self._reject(res, at, req, "지금 가격을 받지 못함")
            if not got.open:
                return self._reject(res, at, req, "장이 닫혀 있음(받은 장 상태)")
            bad = self._quote_error(got.time, at)
            if bad:
                return self._reject(res, at, req, bad)
            price = RU.step(asset, got.price, 1 if side == "buy" else -1)
            if price is None:
                return self._reject(res, at, req, "호가 단위를 모름")
            bad = self._limit_error(asset, price, self._prev_close(asset, at.date(), at))
            if bad:
                return self._reject(res, at, req, bad)
            value = _ceil(qty * price)
            cost = value + RU.fee(asset, value) if side == "buy" else 0
            bad = self._money_error(at, asset, side, qty, cost)
            if bad:
                return self._reject(res, at, req, bad)
            o = self._emit(res, "order", self._order_line(at, asset, side, kind, qty, got.price, reason, cost))
            res.order = o["id"]
            p = self.state.pending[o["id"]]
            self._fill_krx(res, p, at, price, "now", at.date(), {"from": got.source, "time": _iso(got.time)},
                           f"마지막 체결가 {got.price}에서 {'+' if side == 'buy' else '−'}1칸")
            return res
        if kind == "open":
            if today_open and h["open"] <= tm < h["close"]:
                return self._reject(res, at, req, "장중에는 장 시작 주문을 낼 수 없다(지금 주문을 쓴다)")
            session = at.date() if today_open and tm < h["open"] else RU.next_business_day(at.date(), days)
        else:
            if not today_open or tm >= h["close_auction"]:
                return self._reject(res, at, req, "장 마감 주문은 영업일 15:20 전에만")
            session = at.date()
        prev = self._prev_close(asset, session, at)
        if side == "buy":
            if limit is None:
                return self._reject(res, at, req, "사기는 한도 가격이 있어야 한다")
            bad = self._limit_error(asset, limit, prev)
            if bad:
                return self._reject(res, at, req, bad)
            value = _ceil(qty * limit)
            reserved = value + RU.fee(asset, value)
        else:
            reserved = 0
        bad = self._money_error(at, asset, side, qty, reserved)
        if bad:
            return self._reject(res, at, req, bad)
        # ref_price = 주문 수량을 잡은 가격(전 거래일 종가). 한도는 limit 칸에 따로 — 바깥 그래프 코드가 걸린 주문을
        # 수량 × ref_price로 어림하므로 여유(5%)가 든 한도를 넣지 않는다
        ref = prev or limit or 0.0
        o = self._emit(res, "order", self._order_line(at, asset, side, kind, qty, ref, reason, reserved, limit,
                                                       session=session))
        res.order = o["id"]
        return res

    def _money_error(self, at, asset, side, qty, cost) -> str:
        pocket = pocket_of(asset)
        if side == "buy":
            have = self.state.orderable(pocket, at)
            if cost > have:
                return f"돈이 모자람: 필요 {cost:,}원 > 주문 가능 금액 {have:,}원 ({pocket})"
        else:
            have = self.state.sellable(asset, at)
            if qty > have:
                return f"가진 것보다 많이 팖: {qty} > 팔 수 있는 {have} (가진 것 − 걸린 팔기)"
        return ""

    def _order_upbit(self, res, at, req, asset, side, kind, qty, amount, reason) -> Result:
        unit = C.ASSETS[asset].unit
        if kind != "now":
            return self._reject(res, at, req, "업비트는 지금 주문만")
        if side == "buy" and (qty is not None or not isinstance(amount, int) or isinstance(amount, bool)):
            return self._reject(res, at, req, "업비트 사기는 금액(원, 정수)으로 낸다")
        if side == "sell" and (amount is not None or not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0):
            return self._reject(res, at, req, "업비트 팔기는 수량(1e-8 단위 정수)으로 낸다")
        book = self.quotes.book(asset, at)
        if book is None:
            return self._reject(res, at, req, "지금 호가를 받지 못함")
        bad = self._quote_error(book.time, at)
        if bad:
            return self._reject(res, at, req, bad)
        mn = RU.value("upbit_min_order")
        src = {"from": book.source, "time": _iso(book.time)}
        if side == "buy":
            if amount < mn:
                return self._reject(res, at, req, f"최소 주문 금액 {mn:,}원 미만")
            reserved = amount + RU.fee(asset, amount)
            bad = self._money_error(at, asset, side, 0, reserved)
            if bad:
                return self._reject(res, at, req, bad)
            rem, units_f = float(amount), 0.0
            for p, s in book.asks:
                take = min(rem, p * s)
                units_f += take / p
                rem -= take
                if rem <= 1e-9:
                    break
            q = _floor(units_f * unit)
            if q <= 0:
                return self._reject(res, at, req, "팔려는 호가가 비었음")
            value = min(amount, _ceil(amount - max(rem, 0.0)))
            note = "" if rem <= 1e-6 else f"호가가 모자라 {amount - value:,}원어치는 체결 안 함"
            ref = book.asks[0][0]
            o = self._emit(res, "order", self._order_line(at, asset, side, kind, 0, ref, reason, reserved, amount=amount))
            fee = RU.fee(asset, value)
            delta = -(value + fee)
        else:
            if not book.bids or qty / unit * book.bids[0][0] < mn:
                return self._reject(res, at, req, f"최소 주문 금액 {mn:,}원 미만(또는 사려는 호가가 비었음)")
            bad = self._money_error(at, asset, side, qty, 0)
            if bad:
                return self._reject(res, at, req, bad)
            rem, value_f = qty, 0.0
            for p, s in book.bids:
                take = min(rem, _floor(s * unit))
                value_f += take / unit * p
                rem -= take
                if rem <= 0:
                    break
            q = qty - rem
            value = _floor(value_f)
            note = "" if rem == 0 else f"호가가 모자라 {rem}단위는 체결 안 함"
            ref = book.bids[0][0]
            o = self._emit(res, "order", self._order_line(at, asset, side, kind, qty, ref, reason, 0))
            fee = RU.fee(asset, value)
            delta = value - fee
        res.order = o["id"]
        line = {"id": f"fill:{o['id']}", "run": at.date().isoformat(), "account": self.account, "order": o["id"],
                "asset": asset, "side": side, "fill_date": Q.coin_bar_date(at).isoformat(),
                "price": value / (q / unit), "price_kind": "now", "qty_units": q, "value": value, "fee": fee, "tax": 0,
                "cash_after": self.state.upbit + delta, "at": _iso(at), "pocket": "upbit", "settle": None,
                "source": src, "note": note}
        self._emit(res, "fill", line)
        return res

    def cancel(self, at: dt.datetime, order_id: str, reason: str = "") -> Result:
        at = _at(at)
        res = self.sync(at)
        res.ok, res.reason = True, ""
        req = {"type": "cancel", "order": order_id, "reason": reason}
        why = self._past(at) or ("" if self.state.started else "시작 잔고(start) 전")
        if why:
            return self._reject(res, at, req, why)
        p = self.state.pending.get(order_id)
        if p is None:
            return self._reject(res, at, req, f"걸린 주문 {order_id}이 없음(이미 체결·취소·만료)")
        first = first_fill_time(p)
        if first is not None and at >= first:
            return self._reject(res, at, req, f"체결 시각({_iso(first)}) 뒤에는 취소할 수 없다 — 장중에 걸린 주문은 언제 체결될지 몰라 취소하지 않는다")
        self._broker_line(res, at, "cancel", {"order": order_id}, reason)
        res.order = order_id
        return res

    def transfer(self, at: dt.datetime, src: str, dst: str, amount: int, reason: str = "") -> Result:
        at = _at(at)
        res = self.sync(at)
        res.ok, res.reason = True, ""
        req = {"type": "transfer", "src": src, "dst": dst, "amount": amount, "reason": reason}
        why = self._past(at) or ("" if self.state.started else "시작 잔고(start) 전")
        if why:
            return self._reject(res, at, req, why)
        if (src, dst) not in ROUTES:
            return self._reject(res, at, req, f"{src} → {dst} 길은 없다 — 주머니 사이는 은행을 거친다(같은 시각 두 번 이체)")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return self._reject(res, at, req, "이체 금액은 양의 정수(원)")
        fee = RU.value("upbit_krw_withdraw_fee") if src == "upbit" else RU.value("bank_transfer")["fee"]
        have = self.state.withdrawable(src, at)
        if amount + fee > have:
            extra = " (판 돈은 결제일 09:00 뒤부터 뺄 수 있다)" if src == "sec" else ""
            return self._reject(res, at, req, f"옮길 수 있는 돈이 모자람: {amount + fee:,}원 > {have:,}원{extra}")
        self._broker_line(res, at, "transfer", {"src": src, "dst": dst, "amount": amount, "fee": fee}, reason)
        return res

    # ----- 평가 -----
    def snapshot(self, at: dt.datetime, run: dt.date | None = None,
                 prices: dict[str, tuple[float, dt.datetime]] | None = None) -> Result:
        """그 시각의 계좌 줄(옛 account 모양 + pockets·priced_at·at·seq). 평가 가격은 그 시각에 받을 수 있는 가장 새 실제 값
        (한국 거래소는 장이 닫혀 있으면 마지막 공식 종가). 같은 날 두 번은 만들지 않는다(id가 계좌:날짜).
        prices(대상 → (값, 그 값의 시각))를 주면 그 값으로 치고, 없는 대상만 공급자에게 묻는다(매일 실행이 받아 둔 값을 넘긴다)."""
        at = _at(at)
        res = self.sync(at)
        res.ok, res.reason = True, ""
        run = run or at.date()
        aid = f"{self.account}:{run.isoformat()}"
        if aid in self._account_ids:
            res.ok, res.reason = False, f"{aid} 계좌 줄이 이미 있다"
            return res
        line, warns = self.account_line(at, run, prices)
        res.warnings += warns
        self._emit(res, "account", line)
        self._account_ids.add(aid)
        return res

    def account_line(self, at: dt.datetime, run: dt.date | None = None,
                     prices: dict[str, tuple[float, dt.datetime]] | None = None) -> tuple[dict, list[str]]:
        """계좌 줄을 만들기만 한다(기록에 넣지 않는다 — 미리 보기용). (줄, 경고)."""
        at = _at(at)
        run = run or at.date()
        aid = f"{self.account}:{run.isoformat()}"
        res = Result(True)
        st = self.state
        keep = list(C.ASSET_IDS) + sorted({a for a, n in st.units.items() if n and a not in C.CORE}
                                          | {p.asset for p in st.pending.values() if p.asset not in C.CORE})
        prices, priced_at, value = {}, {}, 0.0
        pk = self.pockets()
        for a in keep:
            got = (prices or {}).get(a) or self._price(a, at)
            if got is None:
                if st.units.get(a):
                    res.warnings.append(f"{a}: 평가 가격을 받지 못함 — 평가액에서 빠짐")
                continue
            p, t = got
            prices[a] = [t.date().isoformat(), p]
            priced_at[a] = _iso(t)
            value += st.units.get(a, 0) * p / C.ASSETS[a].unit
        line = {"id": aid, "run": run.isoformat(), "account": self.account, "cash": sum(pk.values()),
                "positions": st.positions(), "prices": prices, "value": round(sum(pk.values()) + value, 2),
                "pending": sorted(st.pending), "pockets": pk, "priced_at": priced_at, "at": _iso(at)}
        return line, res.warnings

    def _price(self, asset: str, at: dt.datetime) -> tuple[float, dt.datetime] | None:
        got = self.quotes.now(asset, at)
        if got is not None and got.time <= at + dt.timedelta(seconds=RU.value("quote_clock_skew")):
            return got.price, got.time
        d = at.date()
        for _ in range(LOOKBACK_DAYS):
            day = self.quotes.day(asset, d, at)
            if day is not None and day.final and day.close:
                end = _kst(d, RU.hours()["close"]) if C.on_exchange(asset) else C.final_dt(C.ASSETS[asset], d)
                return day.close, end
            d -= dt.timedelta(days=1)
        return None


# ---------- 기록 고르기·검산 ----------
def mine(lines: dict[str, list[dict]], account: str | None = None) -> list[tuple[str, dict]]:
    """증권사가 만든 줄(seq가 있는 order·fill·account와 모든 broker 줄)을 seq 순서로. account가 있으면 그 계좌만."""
    out = []
    for k in KINDS:
        for d in lines.get(k, []):
            if (k == "broker" or "seq" in d) and (account is None or d.get("account") == account):
                out.append((k, d))
    return sorted(out, key=lambda x: (x[1].get("seq", 0) if isinstance(x[1].get("seq"), int) else 0))


def _bar(rows: dict[str, list], asset: str, day: dt.date):
    return next((r for r in rows.get(asset, ()) if r.date == day), None)


def _order_rule_errors(o: dict, rows: dict[str, list]) -> list[str]:
    """받을 때 조건 중 시각·호가 단위·가격 제한 폭·최소 금액(돈·수량은 State가 본다)."""
    errs, w = [], f"{o.get('account')} order {o.get('id')}"
    at, kind, a = _t(o["decided_at"]), o.get("order_kind"), o.get("asset")
    h = RU.hours()
    if kind not in ORDER_KINDS:
        return [f"{w}: order_kind '{kind}'"]
    if C.ASSETS[a].kind == "coin":
        if kind != "now":
            errs.append(f"{w}: 업비트는 지금 주문만")
        amt = o.get("amount")
        if o.get("side") == "buy" and (amt is None or amt < RU.value("upbit_min_order")):
            errs.append(f"{w}: 업비트 최소 주문 금액 미만")
        return errs
    biz = RU.is_business_day(at.date())
    if kind == "now" and not (biz and h["open"] < at.time() < h["close_auction"]):
        errs.append(f"{w}: 지금 주문을 장중 밖에 냄")
    if kind == "close" and not (biz and at.time() < h["close_auction"]):
        errs.append(f"{w}: 장 마감 주문을 15:20 뒤에 냄")
    if kind == "open" and biz and h["open"] <= at.time() < h["close"]:
        errs.append(f"{w}: 장 시작 주문을 장중에 냄")
    lim = o.get("limit")
    if kind != "now" and lim is not None:
        if not RU.on_tick(a, lim):
            errs.append(f"{w}: 한도 {lim}이 호가 단위에 안 맞음")
        session = dt.date.fromisoformat(o["session"]) if o.get("session") else None
        before = [r for r in rows.get(a, ()) if session and r.date < session and r.close > 0]
        if before:
            lo, hi = RU.price_limits(a, before[-1].close)
            if not lo <= lim <= hi:
                errs.append(f"{w}: 한도 {lim}이 가격 제한 폭 {lo}–{hi} 밖")
    return errs


def _fill_price_errors(f: dict, order: dict | None, adjust: dict | None, rows: dict[str, list]) -> list[str]:
    """체결가 검산(P8-4): 장 시작·장 마감은 끝난 일봉의 시가·종가와 같고(바로잡은 값이 있으면 그 값), 장중 한도는 저가 < 한도,
    지금 체결은 그날 저가 − 한 칸 ≤ 값 ≤ 고가 + 한 칸."""
    w = f"{f.get('account')} fill {f.get('id')}"
    a = f.get("asset")
    rs = rows.get(a, ())
    if not rs:
        return []
    day = dt.date.fromisoformat(f["fill_date"])
    if day > rs[-1].date:
        return []  # 아직 끝난 봉이 없다 — 다음 검사에서
    bar = _bar(rows, a, day)
    if bar is None:
        return [f"{w}: 체결 날짜 {day}의 일봉이 시세 파일에 없음"]
    kind, price = f.get("price_kind"), f.get("price")
    if kind in ("open", "close"):
        want = bar.open if kind == "open" else bar.close
        eff = adjust["data"]["price_final"] if adjust else price
        if eff != want:
            return [f"{w}: 체결가 {price}{f'(바로잡은 값 {eff})' if adjust else ''} ≠ 끝난 일봉의 {kind} {want}"]
    elif kind == "limit":
        if not bar.low < price:
            return [f"{w}: 장중 한도 체결 {price}인데 그날 저가 {bar.low}가 한도보다 낮지 않음"]
    elif kind == "now":
        t = RU.tick(a, price) or 0
        if not bar.low - t <= price <= bar.high + t:
            return [f"{w}: 지금 체결가 {price}이 그날 저가–고가({bar.low}–{bar.high}) ± 한 칸 밖"]
    return []


def check(lines: dict[str, list[dict]], bars: dict[str, Iterable] | None = None) -> tuple[list[str], list[str]]:
    """기록 전체를 다시 돌려 어긋남을 찾는다 (오류, 경고). ① 주머니 음수 없음 ② 판 수량 ≤ 가진 수량 ③ 받은 주문마다 받을 때 조건
    ④ account 줄 = 다시 센 값, 그리고 체결가 검산(bars = 대상 → 끝난 일봉 묶음, 없으면 건너뜀). 손으로 적은 줄은 여기서 걸린다."""
    rows = {a: tuple(sorted(rs, key=lambda r: r.date)) for a, rs in (bars or {}).items()}
    errs: list[str] = []
    warns: list[str] = []
    accounts = sorted({d.get("account") for _, d in mine(lines)})
    for acc in accounts:
        items = mine(lines, acc)
        seqs = [d.get("seq") for _, d in items]
        if len(set(seqs)) != len(seqs):
            errs.append(f"{acc}: seq가 겹침")
        st = State(acc)
        for kind, d in items:
            errs += st.apply(kind, d)
            if kind == "order":
                errs += _order_rule_errors(d, rows)
        adjusts = {d["data"].get("fill"): d for k, d in items if k == "broker" and d.get("type") == "adjust"}
        for kind, d in items:
            if kind == "fill":
                errs += _fill_price_errors(d, st.orders.get(d.get("order")), adjusts.get(d["id"]), rows)
        for fid, f in st.unverified.items():   # 끝난 봉이 시세 파일에 있으면 위 체결가 검산이 이미 견줬다
            if _bar(rows, f["asset"], dt.date.fromisoformat(f["fill_date"])) is None:
                warns.append(f"{acc} fill {fid}: 진행 중인 봉의 시가로 넣었고 아직 끝난 봉과 견주지 않음")
    return errs, warns


def check_folder(folder: Path, prices_folder: Path | None = None) -> tuple[list[str], list[str]]:
    """기록 폴더의 증권사 줄 검산. 시세는 prices_folder(기본 data/prices)에서 줄에 나온 대상만 읽는다."""
    lines = {k: R.read(folder, k) for k in KINDS}
    assets = {d.get("asset") for k in ("order", "fill") for d in lines[k] if "seq" in d}
    bars = {a: PR.load(a, prices_folder) for a in assets if a}
    return check(lines, bars)


# ---------- 상태 보기 (읽기 전용 명령) ----------
class _NoQuotes(Q.Quotes):
    """가격을 주지 않는 공급자 — 상태만 볼 때 쓴다(아무것도 체결하지 않는다)."""

    def now(self, asset, at):
        return None

    def book(self, asset, at):
        return None

    def day(self, asset, date, at):
        return None


def status(root: Path, at: dt.datetime | None = None) -> tuple[list[str], int]:
    """계좌마다 주머니·보유·걸린 주문·주문 가능/출금 가능 금액과 검산 결과를 사람 말로. 기록을 쓰지 않는다. (줄들, 오류 수)"""
    at = _at(at or C.now_kst())
    folder = root / "records"
    lines = {k: R.read(folder, k) for k in KINDS}
    accounts = sorted({d.get("account") for _, d in mine(lines)})
    out = [f"# 가상 증권사 상태 ({folder}, {_iso(at)} 기준)"]
    if not accounts:
        out.append("증권사 기록이 없다(아직 갈아타기 전) — 옛 장부(market/ledger.py)로 돈다.")
    brokers = {acc: Broker(acc, _NoQuotes(), lines) for acc in accounts}
    # 흉내 내기 폴더의 기록은 실제 시계보다 앞날일 수 있다 — 마지막 사건보다 이른 시각으로는 보지 않는다
    at = max([at] + [b.state.last_at for b in brokers.values() if b.state.last_at])
    out[0] = f"# 가상 증권사 상태 ({folder}, {_iso(at)} 기준)"
    for acc, b in brokers.items():
        pk = b.pockets()
        out += ["", f"## {acc}",
                f"- 원화: 은행 {pk['bank']:,}원 · 증권 계좌 {pk['sec']:,}원 · 업비트 {pk['upbit']:,}원 (합 {sum(pk.values()):,}원)",
                f"- 주문 가능: 증권 계좌 {b.orderable('sec', at):,}원 · 업비트 {b.orderable('upbit', at):,}원 / "
                f"출금 가능: 증권 계좌 {b.withdrawable('sec', at):,}원 · 업비트 {b.withdrawable('upbit', at):,}원",
                "- 보유: " + (", ".join(f"{a} {n}단위(평균 산 값 {b.avg_cost(a) * C.ASSETS[a].unit:,.0f}원)"
                                      for a, n in sorted(b.holdings().items())) or "없음")]
        later = sorted((d, a) for d, a, instant in b.state.flows if not instant and not RU.settled_by(d, at))
        if later:
            out.append("- 결제를 기다리는 돈: " + ", ".join(f"{d} {a:+,}원" for d, a in later))
        for p in b.pending():
            what = f"{p.qty}단위" + (f" 한도 {p.limit:,.0f}원" if p.limit else "")
            out.append(f"- 걸린 주문 {p.id}: {p.asset} {'사기' if p.side == 'buy' else '팔기'} {what}, "
                       f"{p.session} {'장 시작' if p.kind == 'open' else '장 마감'}, 잡아 둔 돈 {p.reserved:,}원")
        if not b.pending():
            out.append("- 걸린 주문: 없음")
        for e in b.problems:
            out.append(f"- **기록이 어긋남**: {e}")
    errs, warns = check_folder(folder, root / "data" / "prices")
    out += ["", f"## 검산 (오류 {len(errs)}건, 경고 {len(warns)}건)"]
    out += [f"- 오류: {e}" for e in errs[:50]] + [f"- 경고: {w}" for w in warns[:50]]
    return out, len(errs)


def main(argv: list[str]) -> int:
    """python -m market.broker status [--root <폴더>] — 계좌 상태와 검산(읽기 전용)."""
    if not argv or argv[0] != "status":
        print(main.__doc__)
        return 2
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv else C.ROOT
    out, n = status(root)
    print("\n".join(out))
    return 1 if n else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
