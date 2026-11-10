"""목표 비중 → 주문 계획 (docs/broker-sandbox.md 16절).

Claude가 정하는 것은 목표 비중이고, 이 파일이 그것을 요청(주문·이체)으로 바꿔 가상 증권사(market/broker.py)에 넣는다.
**증권사의 공개 요청(order·transfer·mark)과 조회만 쓴다** — 받을지·얼마에 체결될지·잔고는 증권사가 정하고, 이 파일은 기록 줄을
만들지 않는다(그래서 증권사 파일과 따로 두었다: '요청하는 쪽'이 규칙을 건너뛸 길이 없다). 계좌 넷이 같은 코드를 쓴다(P6).

수량 규칙은 옛 장부(ledger.Account.plan)와 같다: 차이가 계좌의 문턱(band)보다 작으면 거래하지 않고, 세 대상은 내림,
종목은 가까운 주 수(그 종목이 상한 + ledger.STOCK_SLACK을 넘으면 내림), 걸린 주문은 든 것으로 치고 차이를 잰다.

순서(판단 때)
1. 비트코인 팔기: 지금 주문.
2. 한국 거래소 팔기: 장이 닫혀 있으면 다음 장 시작 시장가, 장중이면 지금 주문.
3. 돈 옮기기: 사려는 주머니에 돈이 모자라면 은행 → 그 주머니, 그래도 모자라면 다른 주머니의 뺄 수 있는 돈을 은행을 거쳐.
   업비트에서 빼는 돈은 MOVE_MIN_FROM_UPBIT 이상일 때만(건당 출금 수수료를 푼돈에 쓰지 않게).
4. 비트코인 사기: 지금 주문(업비트 원화 안에서).
5. 한국 거래소 사기: **지금 주문 가능 금액 안에서만** — 장이 닫혀 있으면 장 시작 지정가(한도 = rules.default_open_limit),
   장중이면 지금 주문. 같은 09:00에 팔리는 돈은 아직 없으므로 그 몫의 사기는 내지 않는다(거절 줄이 아니라 계획이 안 냄).
   남은 사기는 장 시작 뒤 단계(daily open)나 다음 날 판단 때 나간다.
6. 그날의 목표 비중을 plan 줄로 남긴다(장 시작 뒤 단계가 읽는다).
계획은 거절이 나지 않게 수량을 주문 가능 금액에 맞춰 잡지만, 그래도 증권사가 거절하면 그대로 둔다(다시 줄여 내지 않는다 —
거절 줄이 남고, 다음 실행이 목표를 다시 본다).

시계: 요청마다 시각이 달라야 방금 체결된 돈을 다음 요청에 쓸 수 있다(증권사의 T1). 진짜 기록 폴더에서는 요청하는 그 순간의
실제 시각(지금 가격의 시각과 견주므로 판단을 시작한 시각을 그대로 쓰면 '시계가 어긋났다'로 거절된다), 흉내 내기에서는
판단 시각에서 요청마다 1초씩.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from . import broker as B
from . import config as C
from . import ledger as L
from . import rules as RU

BAND = 0.02                      # 차이가 계좌의 이만큼보다 작으면 거래하지 않는다(옛 장부와 같다)
# 업비트 → 은행은 건당 수수료(rules.upbit_krw_withdraw_fee, 1,000원)를 낸다. 수수료가 옮기는 돈의 1%를 넘지 않게 아래 끝을 둔다
MOVE_MIN_FROM_UPBIT = 100_000
NOW_CUSHION = 0.003              # 지금 주문은 계획 때 본 값과 주문 순간의 값이 다를 수 있어 살 수 있는 수량을 이만큼 여유 있게 잡는다


class Clock:
    """요청 시각. live면 실제 시각(앞 요청보다 적어도 1초 뒤), 아니면 시작 시각에서 요청마다 1초씩."""

    def __init__(self, at: dt.datetime, live: bool = False):
        self.last = at.astimezone(C.KST).replace(microsecond=0)
        self.live = live

    def now(self) -> dt.datetime:
        nxt = self.last + dt.timedelta(seconds=1)
        self.last = max(C.now_kst().replace(microsecond=0), nxt) if self.live else nxt
        return self.last


@dataclass
class Plan:
    lines: list[tuple[str, dict]] = field(default_factory=list)   # 증권사가 만든 줄(종류, 줄) — 낸 순서
    notes: list[str] = field(default_factory=list)                # 계획이 내지 않은 것과 까닭(사람 말)
    short: list[str] = field(default_factory=list)                # 돈이 모자라 목표보다 적게 샀거나 못 산 대상
    warnings: list[str] = field(default_factory=list)

    def of(self, kind: str, typ: str | None = None) -> list[dict]:
        return [d for k, d in self.lines if k == kind and (typ is None or d.get("type") == typ)]


def total_value(b: B.Broker, prices: dict[str, float]) -> float:
    """계좌 평가액(세 주머니 원화 + 든 것 × 가격). 가격이 없는 대상은 빠진다."""
    return sum(b.pockets().values()) + sum(n * prices[a] / C.ASSETS[a].unit for a, n in b.holdings().items() if a in prices)


def cancellable(p: B.Pending, at: dt.datetime) -> bool:
    """걸린 주문을 지금 취소할 수 있나 — 증권사의 cancel이 받는 조건과 같다(체결 시각 전). 장 시작에 시가 ≥ 한도라 그날 장중에
    걸린 사기는 체결 시각(09:00)이 지나 취소할 수 없다(일봉만으로는 언제 체결될지 몰라서 — docs/broker-sandbox.md 18절)."""
    first = B.first_fill_time(p)
    return first is None or at < first


def rebalance(b: B.Broker, targets: dict[str, float | None], prices: dict[str, float], clock: Clock,
              band: float = BAND, reasons: dict[str, str] | None = None, phase: str = "decide") -> Plan:
    """목표 비중(대상 → 0~1, None은 그대로 둠)으로 요청을 낸다. prices = 수량을 잡을 가격(판단 때 가격).
    phase: "decide"(판단 때 — 끝에 plan 줄을 남긴다) / "open"(장 시작 뒤 — 한국 거래소는 장중일 때 지금 주문만, plan 줄 없음).
    차례는 입력 dict의 차례와 상관없다(2026-11-10 2회차): 걸린 주문 다시 짜기·팔기는 대상 id 순, 사기와 돈 옮기기는 목표와의
    차이(원)가 큰 것부터(같으면 id 순)."""
    plan = Plan()
    reasons = reasons or {}

    def ask(res: B.Result) -> B.Result:
        plan.lines += res.lines
        plan.warnings += res.warnings
        return res

    at = clock.now()
    ask(b.sync(at))
    market = b.market(at)
    krx_kind = "now" if market == "now" else ("open" if market == "open" and phase == "decide" else "")
    total = total_value(b, prices)
    live = sorted(a for a, w in targets.items() if w is not None and a in prices and prices[a] > 0)

    def gap(a: str, have: dict[str, int]) -> float:
        return targets[a] * total - have.get(a, 0) * prices[a] / C.ASSETS[a].unit

    # 0. 걸린 주문 다시 짜기: 걸린 주문까지 친 보유가 목표와 문턱 밖이면 그 대상의 취소할 수 있는 걸린 주문을 취소한다(다시 낼 수 있을
    #    때만 — 한국 거래소 주문을 지금 못 내는 시간이면 건드리지 않는다). 문턱 안이면 그대로 둔다(날마다 취소하고 다시 내지 않게).
    #    취소 못 하는 걸린 주문(그날 장중에 걸린 사기)은 그대로 두고 든 것으로 친다. 취소가 거절되면 그 주문도 든 것으로 친다.
    have = b.expected_units()
    for a in live:
        if abs(gap(a, have)) < band * total or (C.on_exchange(a) and not krx_kind):
            continue
        for p in sorted(b.pending(), key=lambda p: p.id):
            if p.asset == a:
                t = clock.now()
                if cancellable(p, t):
                    ask(b.cancel(t, p.id, f"목표 비중이 바뀌어 다시 낸다(목표 {targets[a]:.4f})"))
    have = b.expected_units()

    sells, buys = [], []
    for a in live:
        diff = gap(a, have)
        if abs(diff) < band * total:
            continue
        (buys if diff > 0 else sells).append((a, diff))
    buys.sort(key=lambda x: (-x[1], x[0]))      # 차이가 큰 것부터 — 돈이 모자란 날 누가 먼저 사는지를 코드가 정한다
    if not krx_kind and any(C.on_exchange(a) for a, _ in sells + buys):
        plan.notes.append("한국 거래소 주문은 내지 않았다 — " + ("장 시작 뒤 단계인데 장중이 아니다" if phase == "open" else
                                                          "지금은 장 시작 주문도 지금 주문도 받지 않는 시간이다(09:00 정각·15:20–15:30)"))
    mn = RU.value("upbit_min_order")

    # 1–2. 팔기 (비트코인은 지금, 한국 거래소는 장 시작 시장가나 지금) — 대상 id 순(다 내므로 차례는 결과를 바꾸지 않는다)
    for a, diff in sells:
        spec = C.ASSETS[a]
        n = -diff / prices[a] * spec.unit
        q = min(b.sellable(a, at), round(n) if C.is_stock(a) else math.floor(n))
        if q <= 0:
            continue
        if spec.kind == "coin":
            if q / spec.unit * prices[a] < mn:
                plan.notes.append(f"{a} 팔기는 내지 않았다 — 업비트 최소 주문 금액({mn:,}원)보다 작다")
                continue
            ask(b.order(clock.now(), a, "sell", "now", qty=q, reason=reasons.get(a, "")))
        elif krx_kind:
            ask(b.order(clock.now(), a, "sell", krx_kind, qty=q, reason=reasons.get(a, "")))

    def cost(a: str, q: int, unit: float) -> int:
        v = math.ceil(q * unit)
        return v + RU.fee(a, v)

    # 3. 돈 옮기기: 그 대상의 주머니에 모자란 만큼, 은행 먼저 → 다른 주머니의 뺄 수 있는 돈(은행을 거쳐). 사는 차례대로 한 대상씩
    #    부른다 — 앞 대상이 먼저 돈을 받는다(고치기 전에는 증권 계좌가 자기 살 몫을 먼저 남겨 두어 비트코인이 덜 샀다).
    def top_up(pocket: str, need: int, least: int) -> None:
        """need = 그 대상을 다 사는 데 드는 돈, least = 한 단위라도 사는 데 드는 돈(한 주, 업비트 최소 주문). 모아도 least에 못 미치면
        옮기지 않는다 — 못 살 대상에 돈을 옮겨 두었다가 다음 대상이 도로 빼 가는 헛걸음을 막는다."""
        t = clock.now()
        lack = need - b.orderable(pocket, t)
        if lack <= 0:
            return
        other = "upbit" if pocket == "sec" else "sec"
        fee = RU.value("upbit_krw_withdraw_fee") if other == "upbit" else 0
        spare = b.withdrawable(other, t) - fee
        floor_ = MOVE_MIN_FROM_UPBIT if other == "upbit" else 1
        reach = b.orderable(pocket, t) + b.withdrawable("bank", t) + (spare if min(lack, spare) >= floor_ else 0)
        if reach < least:
            return
        from_bank = min(lack, b.withdrawable("bank", t))
        if from_bank > 0:
            ask(b.transfer(t, "bank", pocket, from_bank, f"{pocket}에서 살 돈"))
            lack -= from_bank
        if lack <= 0:
            return
        spare = b.withdrawable(other, t) - fee
        amount = min(lack, spare)
        if amount < floor_:
            if spare > 0 and other == "upbit":
                plan.notes.append(f"업비트의 남는 돈 {max(amount, 0):,}원은 옮기지 않았다 — {MOVE_MIN_FROM_UPBIT:,}원보다 작으면 "
                                  f"출금 수수료({fee:,}원)가 아깝다")
            return
        if ask(b.transfer(t, other, "bank", amount, f"{pocket}에서 살 돈")).ok:
            ask(b.transfer(t, "bank", pocket, amount, f"{pocket}에서 살 돈"))

    # 4–5. 사기: 차이가 큰 것부터, 그 대상의 주머니에 돈을 대어 준 뒤 그 주머니의 주문 가능 금액 안에서만
    for a, diff in buys:
        spec = C.ASSETS[a]
        if spec.kind == "coin":
            q = math.floor(diff)
            if q >= mn:
                top_up("upbit", q + RU.fee(a, q), mn + RU.fee(a, mn))
            t = clock.now()
            avail = b.orderable("upbit", t)
            amt = min(q, int(avail / (1 + spec.fee)))
            while amt > 0 and amt + RU.fee(a, amt) > avail:
                amt -= 1
            if amt < q:
                plan.short.append(a)
            if amt < mn:
                if q >= mn:
                    plan.notes.append(f"{a} 사기는 내지 않았다 — 업비트에 쓸 수 있는 돈이 {avail:,}원뿐이다")
                continue
            ask(b.order(t, a, "buy", "now", amount=amt, reason=reasons.get(a, "")))
            continue
        if not krx_kind:
            continue
        q = math.floor(diff / (prices[a] * (1 + spec.fee)))
        if C.is_stock(a):   # 가까운 주 수 — 그 종목이 상한 + 여유를 넘지 않을 때만(옛 장부와 같다)
            near = round(diff / prices[a])
            after = (have.get(a, 0) + near) * prices[a] / total if total else 1.0
            if near > q and after <= C.STOCK_MAX_WEIGHT + L.STOCK_SLACK:
                q = near
        if krx_kind == "open":
            limit = RU.default_open_limit(a, prices[a])
            unit = float(limit) if limit else None
        else:
            limit, up = None, RU.step(a, prices[a], 1)
            unit = up * (1 + NOW_CUSHION) if up else None
        if q <= 0 or not unit:
            continue
        top_up("sec", cost(a, q, unit), cost(a, 1, unit))
        t = clock.now()
        avail = b.orderable("sec", t)
        n = min(q, int(avail // (unit * (1 + spec.fee))))
        while n > 0 and cost(a, n, unit) > avail:
            n -= 1
        if n < q:
            plan.short.append(a)
            plan.notes.append(f"{a} 사기 {q}주 가운데 {n}주만 냈다 — 지금 주문 가능 금액이 {avail:,}원이다"
                              + ("(같은 장 시작에 팔리는 돈은 아직 쓸 수 없고, 장 시작 사기는 한도 가격만큼 돈을 잡아 둔다)"
                                 if krx_kind == "open" else ""))
        if n > 0:
            ask(b.order(t, a, "buy", krx_kind, qty=n, limit=limit, reason=reasons.get(a, "")))

    # 6. 그날의 목표 비중
    if phase == "decide":
        data = {"targets": {a: w for a, w in targets.items() if w is not None}, "band": band,
                "prices": {a: prices[a] for a in targets if a in prices}, "short": sorted(set(plan.short)),
                "reasons": {a: reasons[a] for a in targets if reasons.get(a)}}
        ask(b.mark(clock.now(), "plan", data))
    return plan


def describe(plan: Plan, name=lambda a: a) -> list[str]:
    """계획이 한 일을 사람 말로(매일 실행의 출력 — 메인이 읽고 일지에 옮긴다)."""
    out = []
    fills = {f["order"]: f for f in plan.of("fill")}
    for o in plan.of("order"):
        side = "사기" if o["side"] == "buy" else "팔기"
        f = fills.get(o["id"])
        if f:
            out.append(f"바로 체결: {name(o['asset'])} {side} {f['value']:,}원어치(수수료 {f['fee']:,}원"
                       + (f", 세금 {f['tax']:,}원" if f.get("tax") else "") + ")" + (f" — {f['note']}" if f.get("note") else ""))
        else:
            when = "장 시작(09:00) 가격" if o["order_kind"] == "open" else "장 마감 가격"
            lim = f", 한도 {o['limit']:,.0f}원" if o.get("limit") else ""
            out.append(f"주문 넣음: {name(o['asset'])} {side} {o['qty_units']}주 — {o.get('session')} {when}에 치른다{lim}")
    done = {f["order"] for f in plan.of("fill")} - {o["id"] for o in plan.of("order")}
    for oid in sorted(done):
        f = fills[oid]
        out.append(f"지난 주문 체결: {name(f['asset'])} {'사기' if f['side'] == 'buy' else '팔기'} {f['qty_units']}단위 @ "
                   f"{f['price']:,.0f}원 ({f['at'][:16]})")
    for t in plan.of("broker", "transfer"):
        x = t["data"]
        out.append(f"이체: {x['src']} → {x['dst']} {x['amount']:,}원" + (f"(수수료 {x['fee']:,}원)" if x.get("fee") else ""))
    for r in plan.of("broker", "reject"):
        x = r["data"]
        what = f"{name(x.get('asset', ''))} {x.get('side', '')} {x.get('kind', '')}" if x.get("type") == "order" else x.get("type")
        out.append(f"거절됨: {what} — {r['note']}")
    for e in plan.of("broker", "expire"):
        out.append(f"체결 못 하고 끝난 주문: {e['data'].get('order')} — {e['note']}")
    for c in plan.of("broker", "cancel"):
        out.append(f"걸어 둔 주문 취소: {c['data'].get('order')} — {c['note']}")
    out += [f"내지 않음: {n}" for n in plan.notes]
    out += [f"경고: {w}" for w in plan.warnings]
    return out
