"""지난 결정을 가상 증권사에 대입해 보기 (2026-11-10, 사용자 "그럼 지금까지의 결정들을 프로그램을 썼다고 치고 대입할 수는 없나?").

사용법 (저장소 루트):
  python -m market.whatif [--coin-prices <json>] [--out <폴더>]
  (시험용) --records <기록 폴더> --prices <시세 폴더>

2026-10-21~ 매일 실행이 옛 장부(market/ledger.py)로 내린 결정을, 처음부터 가상 증권사(market/broker.py·rebalance.py)를 썼다면
계좌가 어떻게 됐을지 날짜 순서대로 다시 계산한다. **'다시 해 본 것'이지 성적이 아니다** — 그때의 결정은 옛 장부의 잔고를 보고
내린 것이라, 프로그램을 썼다면 결정 자체가 달라졌을 수 있다.

읽기만 한다: 진짜 기록(records/*.jsonl)과 시세(data/prices/). 결과는 --out(기본 out/whatif/, git 제외)에만 쓰고, records·reports·
data 아래로는 쓰지 않는다(거절). 이 결과를 진짜 기록이나 갈아타는 날의 시작 잔고에 쓰는 길은 없다.

방법 (지시서 docs/worklog/2026-11-10-broker-whatif.md '대입하는 방법')
- '결정' = 그날 계좌가 향하던 배분. 그날의 입력 파일은 저장소에 없어서 기록에서 되살린다(old_targets): 그날의 옛 계좌 줄 +
  그날 낸 옛 주문 = '주문까지 더한 예상 보유'(ledger.rebuild → Account.expected, 옛 장부가 그날 본 것과 같은 셈). 가격은 그날
  주문 수량을 잡은 가격(주문의 ref_price), 주문이 없는 대상은 그날 계좌 줄의 평가 가격, 비트코인은 실행 기록의 판단 때 가격.
  모든 대상의 비중을 그날의 목표로 넘긴다(주문이 없던 대상도 — 못 산 몫이 다음 날 이어서 사져야 하므로). 문턱 2%와 종목
  수량 규칙은 rebalance 그대로. 계산기 계좌도 같은 방법, 셋 나눠 사 두기는 daily.equal_targets 그대로, 현금 계좌는 아무것도 안 함.
- 시각: 날마다 그날 실행 기록의 판단 시각(run.steps.decided_at)에 맞추기(sync) → 주문 계획(rebalance) → 계좌 줄(snapshot).
  매일 실행(daily.compute·record)과 같은 순서·같은 함수(value_prices, equal_targets, open_accounts)를 부른다.
- 가격: 한국 거래소는 시세 파일의 봉으로 만든 BarQuotes(시각에 정직 — 09:00 전에는 그날 봉이 안 보인다). 비트코인 '지금 가격'은
  판단 분의 실제 1분봉 종가(--coin-prices의 decision), 없는 날은 실행 기록의 판단 때 가격(판단 6–16분 전 값)을 판단 때 값으로 쓴다
  (어느 쪽인지 결과에 적는다). 업비트 호가는 그 값 둘레로 만든다(BarQuotes가 하는 식). 그 값은 정해 둔 창(판단 시각부터 10분,
  (나)의 09:05부터 10분) 안에서만 준다 — 창 밖의 비트코인 지금 가격은 없다(값을 지어내지 않게).
- **그대로 대입**(2026-11-10 3회, carry — 갈아타는 날의 잔고도 이 계산이다): 옛 주문 줄(무엇을·몇 주·언제)을 그대로 그때의 공식
  값으로 넣는다. 아래 '배분 되살림' 두 경우는 비교용으로 남긴다.
- 배분 되살림 두 경우: (가) "once" 하루 한 번만 움직임 (나) "open" 그날 09:05에 장 시작 뒤 단계(daily.open_accounts)를 한 번 더 — 한국 거래소
  지금 가격은 그날 시가(BarQuotes가 장중에 주는 값), 비트코인은 --coin-prices의 after_open. 마지막 날(평가하는 날)에는 돌리지 않는다.
- 시작: 첫 실행의 판단 시각에 계좌 넷 start(은행 1,000만 원). 끝: 마지막 실행의 판단 시각에 평가(그날 계좌 줄).
- 견줄 값: 옛 장부의 그날 계좌 줄(보유·현금 그대로, 체결 대기 주문은 옛 기록대로)을 대입과 **같은 평가 가격**으로 다시 친 값.
- 차이 가르기(attribution): 같은 평가 시점(날마다 판단 때)·같은 가격으로, 체결이 실제로 일어난 시각 기준 보유를 쓰면
  끝 값 − 시작 값 = 보유 몫(들고 있던 양 × 다음 평가까지 값 변화) + 치른 값 몫(체결 수량 × (다음 평가 가격 − 치른 값)) − 수수료 −
  세금이 정확히 맞는다. 두 계좌의 각 몫을 빼면 차이를 가른다.

미래를 못 보게: 판단 시각 at의 계산은 시세를 PR.view(at)(그 시각에 끝난 봉)와 BarQuotes(at)(그 시각에 알 수 있는 값)로만 보고,
목표 비중은 run ≤ 그날인 기록 줄만 읽는다(시험 test_whatif가 뒤 날짜 봉을 바꿔도 그날까지 결과가 같은지 본다).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import broker as B
from . import config as C
from . import daily as D
from . import ledger as L
from . import prices as PR
from . import quotes as Q
from . import rebalance as RB
from . import records as R
from . import rules as RU
from . import stocks as ST

CASES = {"once": "(가) 하루 한 번", "open": "(나) 09:05에 한 번 더"}
AFTER_OPEN = dt.time(9, 5)
COIN_WINDOW = dt.timedelta(minutes=10)
DEFAULT_OUT = C.OUT / "whatif"
FORBIDDEN = ("records", "reports", "data")   # 이 아래로는 쓰지 않는다


# ---------- 가격 공급자 ----------
class WhatIfQuotes(Q.BarQuotes):
    """BarQuotes(한국 거래소, 시각에 정직) + 비트코인 지금 가격은 정해 둔 창 안에서만(창 = (시작 시각, 값, 출처)).
    값의 시각은 요청 시각으로 준다 — 창이 판단 분(1분봉 종가)이나 09:05 분을 덮는다."""

    def __init__(self, rows: dict, coin: list[tuple[dt.datetime, float, str]]):
        super().__init__(rows)
        self.coin = sorted(coin)

    def now(self, asset: str, at: dt.datetime) -> Q.Now | None:
        if C.ASSETS[asset].kind != "coin":
            return super().now(asset, at)
        t = at.astimezone(C.KST)
        for start, price, src in self.coin:
            if start <= t < start + COIN_WINDOW:
                return Q.Now(asset, price, t, True, src)
        return None


def _kst(text: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(text)
    return (t if t.tzinfo else t.replace(tzinfo=C.KST)).astimezone(C.KST)


def coin_points(runs: list[dict], coin: dict | None, case: str) -> tuple[list[tuple[dt.datetime, float, str]], dict]:
    """비트코인 지금 가격 창과 날마다 어느 값을 썼는지."""
    pts, used = [], {}
    for r in runs:
        day = r["run"]
        at = _kst(r["steps"]["decided_at"])
        m = (coin or {}).get("decision", {}).get(day)
        if m and m.get("close"):
            pts.append((at, float(m["close"]), f"1분봉 종가 {m['kst']}"))
            used[day] = {"decision": "minute", "price": float(m["close"]), "kst": m["kst"], "exact": bool(m.get("exact"))}
        else:
            dp = ((r["steps"].get("decision_price") or {}).get("KRW-BTC") or {})
            if dp.get("price"):
                pts.append((at, float(dp["price"]), f"판단 때 가격 {dp.get('time')}(판단 전 값)"))
                used[day] = {"decision": "decision_price", "price": float(dp["price"]), "time": dp.get("time")}
            else:
                used[day] = {"decision": "none"}
        if case == "open":
            a = (coin or {}).get("after_open", {}).get(day)
            start = dt.datetime.combine(dt.date.fromisoformat(day), AFTER_OPEN, C.KST)
            if a and a.get("close"):
                pts.append((start, float(a["close"]), f"1분봉 종가 {a['kst']}"))
                used[day]["after_open"] = {"price": float(a["close"]), "kst": a["kst"], "exact": bool(a.get("exact"))}
            else:
                used[day]["after_open"] = None
    return pts, used


# ---------- 결정 되살리기 ----------
def day_prices(aid: str, day: str, accounts: list[dict], orders: list[dict], run: dict | None) -> dict[str, float]:
    """그날 옛 장부가 주문 수량을 잡은 가격: 계좌 줄의 평가 가격 → 비트코인은 판단 때 가격 → 그날 주문의 ref_price."""
    line = next((a for a in accounts if a["account"] == aid and a["run"] == day), None)
    prices = {a: float(v[1]) for a, v in ((line or {}).get("prices") or {}).items()}
    dp = (((run or {}).get("steps") or {}).get("decision_price") or {}).get("KRW-BTC") or {}
    if dp.get("price"):
        prices["KRW-BTC"] = float(dp["price"])
    for o in orders:
        if o["account"] == aid and o["run"] == day:
            prices[o["asset"]] = float(o["ref_price"])
    return prices


def old_targets(aid: str, day: str, accounts: list[dict], orders: list[dict], run: dict | None
                ) -> tuple[dict[str, float], dict]:
    """그날 옛 계좌가 향하던 배분(대상 → 비중)과 그 셈(예상 보유·예상 현금·가격). 그날 계좌 줄이 없으면 빈 dict.
    run ≤ day인 줄만 읽는다(그날 판단 때 이미 있던 기록)."""
    accs = [a for a in accounts if a["account"] == aid and a["run"] <= day]
    ords = [o for o in orders if o["account"] == aid and o["run"] <= day]
    if not accs or accs[-1]["run"] != day:
        return {}, {}
    acc = L.rebuild(aid, accs, ords)
    prices = day_prices(aid, day, accs, ords, run)
    units, cash = acc.expected(prices)
    held = {a: max(n, 0) for a, n in units.items() if a in prices}
    worth = {a: n * prices[a] / C.ASSETS[a].unit for a, n in held.items()}
    total = cash + sum(worth.values())
    w = {a: (v / total if total > 0 else 0.0) for a, v in worth.items()}
    for a in C.ASSET_IDS:
        w.setdefault(a, 0.0)
    missing = sorted(a for a, n in units.items() if n and a not in prices)
    return w, {"units": {a: n for a, n in units.items() if n}, "cash": round(cash, 2), "total": round(total, 2),
               "prices": prices, "missing_price": missing}


def target_order(aid: str, day: str, orders: list[dict], names: set[str]) -> list[str]:
    """목표를 넘기는 차례: 세 대상 → 그날 옛 주문에 나온 종목(줄 차례) → 나머지 종목(코드 순) — 그날 입력(weights)의 차례를 되살린 것.
    2026-11-10 2회차부터 rebalance가 사는 차례를 '목표와 차이가 큰 것부터'로 스스로 정해 **이 차례는 결과를 바꾸지 않는다**
    (시험 test_target_order_does_not_matter). 1회차에는 돈이 모자란 날 입력의 차례대로 사서 결과가 바뀌었고, 집합(set)의 차례에
    맡긴 처음 구현은 실행마다 결과가 달랐다(test_same_result_in_another_process)."""
    seen = [o["asset"] for o in orders if o["account"] == aid and o["run"] == day]
    seq = [*C.ASSET_IDS, *seen, *sorted(names)]
    return [a for a in dict.fromkeys(seq) if a in names or a in C.ASSET_IDS]


# ---------- 대입 ----------
@dataclass
class Case:
    name: str
    brokers: dict
    values: dict = field(default_factory=dict)      # (day, aid) → 계좌 줄
    marks: list = field(default_factory=list)       # (day, 평가 시각, 평가 가격)
    notes: list = field(default_factory=list)       # (day, aid, 단계, 사람 말)
    targets: dict = field(default_factory=dict)     # (day, aid) → 목표


def simulate(case: str, runs: list[dict], recs: dict, rows: dict, coin: dict | None) -> tuple[Case, dict]:
    pts, used = coin_points(runs, coin, case)
    quotes = WhatIfQuotes(rows, pts)
    bar_days = [r.date for r in rows["069500"]]
    brokers = {aid: B.Broker(aid, quotes, {}, bar_days) for aid in C.LIVE_ACCOUNTS}
    K = Case(case, brokers)
    last = runs[-1]["run"]
    for i, r in enumerate(runs):
        day = r["run"]
        dday = dt.date.fromisoformat(day)
        at = _kst(r["steps"]["decided_at"])
        hists = {a: PR.view(a, rs, at) for a, rs in rows.items()}
        if i == 0:
            for aid, b in brokers.items():
                res = b.start(at, bank=C.START_CASH, note="대입 시작: 은행 1,000만 원")
                if not res.ok:
                    raise RuntimeError(f"{aid} start 거절: {res.reason}")
        for aid, b in brokers.items():   # daily.compute: 맞추기
            res = b.sync(at)
            if not res.ok:
                raise RuntimeError(f"{day} {aid} 맞추기 실패: {res.reason}")
            K.notes += [(day, aid, "판단", f"경고: {w}") for w in res.warnings]
        if any(b.problems for b in brokers.values()):
            raise RuntimeError(f"{day} 증권사 기록이 어긋남: {[e for b in brokers.values() for e in b.problems]}")
        mine = set(C.ASSET_IDS) | {a for b in brokers.values() for a in b.expected_units()}
        vp = D.value_prices(hists, quotes, at, {}, brokers["live:hand"].market(at) == "now", mine)
        prices = {a: v for a, (v, _) in vp.items()}
        jobs = []
        for aid in ("live:hand", "live:calc"):
            w, info = old_targets(aid, day, recs["account"], recs["order"], r)
            names = set(w) | set(brokers[aid].expected_units())
            tg = {a: w.get(a, 0.0) for a in target_order(aid, day, recs["order"], names)}
            K.targets[(day, aid)] = {"weights": {a: round(v, 4) for a, v in tg.items()}, **info}
            jobs.append((aid, tg, RB.BAND, {a: "옛 기록에서 되살린 비중" for a in tg}))
        skipped = r["steps"].get("skipped") or {}
        predictable = [a for a in C.ASSET_IDS if a not in skipped]
        orders_so_far = [o for b in brokers.values() for o in b.new["order"]]
        eq = D.equal_targets(orders_so_far, brokers["baseline:equal_hold"], prices, predictable)
        if eq:
            jobs.append(("baseline:equal_hold", eq, 0.0, {a: D.EQUAL_NOTE for a in eq}))
            K.targets[(day, "baseline:equal_hold")] = {"weights": eq}
        clock = RB.Clock(at, False)   # daily.record: 같은 시계, 계좌 차례대로
        for aid, tg, band, rs in jobs:
            plan = RB.rebalance(brokers[aid], tg, prices, clock, band, rs)
            K.notes += [(day, aid, "판단", x) for x in RB.describe(plan, lambda a: C.ASSETS[a].name if a else "")]
        for aid, b in brokers.items():
            res = b.snapshot(clock.now(), dday, vp)
            if not res.ok:
                raise RuntimeError(f"{day} {aid} 계좌 줄: {res.reason}")
            K.values[(day, aid)] = b.new["account"][-1]
        # 평가 시점 = 계좌 줄을 만든 시각(판단 때 바로 체결된 비트코인까지 든 상태 — 계좌 줄과 같은 시점), 가격 = 판단 때 평가 가격
        K.marks.append((day, clock.last, dict(vp)))
        if case == "open" and day != last:
            at2 = dt.datetime.combine(dday, AFTER_OPEN, C.KST)
            hists2 = {a: PR.view(a, rs, at2) for a, rs in rows.items()}
            said = D.open_accounts(brokers, day, at2, quotes, hists2, False)
            if isinstance(said, int):
                raise RuntimeError(f"{day} 장 시작 뒤 단계가 멈춤(종료 코드 {said})")
            for x in said:
                aid, _, text = x.partition("] ")
                K.notes.append((day, aid.lstrip("["), "09:05", text))
    return K, used


# ---------- 그대로 대입 (2026-11-10 3회 — 갈아타는 날의 잔고) ----------
# 사용자 결정(2026-11-10): "판단 자체는 기록에 남아 있으니 그때 가격으로 그대로 사고 하면 되는 거 아니야?" — 옛 장부의 계좌는 실제로
# 안 되는 거래로 만들어져 이어 쓰지 않고 비교용으로만 남긴다. 기록에 남은 옛 주문(무엇을, 몇 주, 언제)을 그대로 그때의 공식 값으로
# 가상 증권사에 넣고, 그 순간 돈이나 주식이 없어 안 되는 주문은 거절된 채로 둔다("체결된 줄 알았는데 안 된 것").
MORNING = dt.time(8, 30)                    # 그 시각에 받지 않는 종류의 옛 주문(주말에 낸 종가 주문 등)을 내는 그 장의 아침
COIN_DELAY = dt.timedelta(seconds=5)
CARRY_TAIL = dt.timedelta(minutes=1)        # whatif: 마지막 판단 시각 뒤 이만큼까지 넣는다(그날 옛 주문을 다 넣게)        # 비트코인은 그 09:00 몇 초 뒤(같은 09:00의 한국 거래소 체결과 섞이지 않게)
BOOK_DEPTH = 1_000_000.0
# 2회차(메인 결정): 이 계좌는 판단이 아니라 규칙('처음 살 수 있는 날 1/3씩 사서 그대로 둔다')이라, 돈이 모자란 사기는 그때 쓸 수 있는
# 돈 안에서 살 수 있는 만큼 산다(매일 실행의 주문 계획과 같은 뜻). 손 계좌·계산기 계좌는 판단을 내린 계좌라 통째로 받거나 거절한다
SHRINK_ACCOUNTS = B.SHRINK_ACCOUNTS                    # 다시 넣기의 업비트 호가 수량(코인 개수) — 기록된 수량이 그 값에 다 치러지게


class CarryQuotes(Q.BarQuotes):
    """그대로 넣기의 가격 공급자. until(갈아타는 시각) 전의 시각에는 시세 파일의 봉(끝난 공식 값)을 시각에 정직하게 준다(BarQuotes) —
    파일에 없는 봉(갈아타는 날 아직 끝나지 않은 어제 09:00 비트코인 봉 등)은 src에 until 시각으로 한 번 물어 받은 값만 쓰고, 그
    값도 시각에 정직하게(끝나기 전에는 시가만). until 뒤의 시각은 모두 src에 그대로 묻는다(갈아탄 뒤 매일 실행의 공급자).
    비트코인 지금 가격·호가는 그 시각이 속한 봉의 시가 — 옛 규칙이 치른 값(09:00 시가) 그대로에 치러지게 사는 값·파는 값을 같게 둔다."""

    def __init__(self, rows: dict, src: Q.Quotes | None, until: dt.datetime):
        # 시세 파일에서는 until에 끝나 있던 봉만 쓴다(흉내 내기 폴더의 시세 파일에는 뒤 날짜 봉이 있을 수 있다 — 진짜 폴더에는 끝난
        # 봉만 있다). 그 밖의 봉(진행 중이던 봉)은 src에 until 시각으로 물어 받은 값만
        super().__init__({a: [r for r in rs if C.final_dt(C.ASSETS[a], r.date) <= until] for a, rs in rows.items()})
        self.src = src if src is not None else Q.BarQuotes(rows)
        self.until = until
        self._live: dict[tuple[str, dt.date], Q.Day | None] = {}

    def _asked(self, asset: str, date: dt.date) -> Q.Day | None:
        key = (asset, date)
        if key not in self._live:
            self._live[key] = self.src.day(asset, date, self.until)
        return self._live[key]

    def day(self, asset: str, date: dt.date, at: dt.datetime) -> Q.Day | None:
        if at >= self.until:
            return self.src.day(asset, date, at)
        if self._row(asset, date) is not None:
            return super().day(asset, date, at)
        if C.fill_dt(C.ASSETS[asset], date, C.OPEN_TIME) > at:
            return None
        got = self._asked(asset, date)
        if got is None:
            return None
        if got.final and C.final_dt(C.ASSETS[asset], date) <= at:
            return got
        return Q.Day(asset, date, got.open, None, None, None, False, got.source)

    def now(self, asset: str, at: dt.datetime) -> Q.Now | None:
        if at >= self.until:
            return self.src.now(asset, at)
        if C.ASSETS[asset].kind != "coin":
            return super().now(asset, at)
        t = at.astimezone(C.KST)
        got = self.day(asset, Q.coin_bar_date(t), t)
        return None if got is None or not got.open or got.open <= 0 else Q.Now(asset, got.open, t, True, f"carry:{got.source}:open")

    def book(self, asset: str, at: dt.datetime) -> Q.Book | None:
        if at >= self.until:
            return self.src.book(asset, at)
        n = self.now(asset, at) if C.ASSETS[asset].kind == "coin" else None
        if n is None:
            return None
        side = ((float(n.price), BOOK_DEPTH),)
        return Q.Book(asset, side, side, n.time, n.source)

    def known_days(self, at: dt.datetime) -> frozenset[dt.date] | None:
        return self.src.known_days(at) if at >= self.until else super().known_days(at)


@dataclass
class Carry:
    brokers: dict                                   # 계좌 → 증권사(그대로 넣은 줄이 new에 있다)
    until: dt.datetime                              # 갈아타는 시각(이 시각에 맞추기와 phase 'carry' 줄)
    steps: list = field(default_factory=list)       # 옛 주문마다 한 일(계좌·id·날·대상·방향·수량·종류·낸 시각·결과·까닭)
    waiting: list = field(default_factory=list)     # 아직 낼 수 없는 옛 주문(그 09:00이 아직 안 왔거나 값을 못 받음)
    values: dict = field(default_factory=dict)      # (날, 계좌) → 그날 판단 때 값(marks를 줬을 때)
    notes: list = field(default_factory=list)


def old_session(o: L.Order, rows: dict, until: dt.datetime, bar_days) -> tuple[dt.date, bool]:
    """옛 주문이 옛 규칙으로 치러지는(치러졌을) 봉의 날짜와 그 봉을 until에 아는지. 아는 봉(시세 파일, until에 끝난 것)에서 먼저
    찾고(ledger.fill_row — 시가·종가 0인 봉은 건너뜀), 없으면 달력으로 센다(주말·알려진 봉 날짜·휴장일 표)."""
    if o.asset in rows:
        row = L.fill_row(o, PR.view(o.asset, rows[o.asset], until))
        if row is not None:
            return row.date, True
    spec = C.ASSETS[o.asset]
    _, when = o.rule()
    d = max(o.run, o.decided_at.astimezone(C.KST).date())
    days = frozenset(x for x in bar_days if x < until.date())
    while (C.on_exchange(o.asset) and not RU.is_business_day(d, days)) or C.fill_dt(spec, d, when) <= o.decided_at:
        d += dt.timedelta(days=1)
    return d, False


def krx_session(kind: str, t: dt.datetime, bar_days) -> dt.date | None:
    """그 시각에 한국 거래소 주문(open·close)을 내면 증권사가 정할 장의 날짜, 받지 않는 시각이면 None(broker._order_krx와 같은 셈)."""
    h = RU.hours()
    days = frozenset(x for x in bar_days if x < t.date())
    biz = RU.is_business_day(t.date(), days)
    tm = t.time()
    if kind == "open":
        if biz and h["open"] <= tm < h["close"]:
            return None
        return t.date() if biz and tm < h["open"] else RU.next_business_day(t.date(), days)
    return t.date() if biz and tm < h["close_auction"] else None


def coin_amount(qty: int, price: float, unit: int) -> int:
    """기록된 수량을 그 값에 사는 금액(원, 정수): 금액 ÷ 값을 1e-8 단위로 내린 수량이 기록과 같아지는 가장 큰 금액(원 단위라 1 단위가
    1원보다 작으면 꼭 같아지지 않을 수 있다 — 검산은 한 단위까지 같은 것으로 본다)."""
    amt = math.ceil(qty * price / unit)
    while amt > 0 and math.floor(round(amt / price * unit, 6)) > qty:
        amt -= 1
    return amt


def buy_limit(asset: str, q: int, prev: float | None, have: int) -> tuple[float | None, str]:
    """장 시작·장 마감 사기의 한도: 기본 한도(전 거래일 종가 × 1.05)를 잡을 돈(have)이 있으면 그대로, 없으면 그 수량을 살 수 있는
    가장 높은 호가로 낮춘다. 전 거래일 종가보다 낮아지면 낮추지 않는다(기본 한도로 내고 증권사가 거절한다). 낼 때 아는 값만 쓴다."""
    if prev is None:
        return None, ""
    top = RU.default_open_limit(asset, prev)

    def need(lim: float) -> int:
        v = math.ceil(round(q * lim, 6))
        return v + RU.fee(asset, v)
    if top is None or need(top) <= have:
        return top, ""
    lim = RU.floor_tick(asset, have / (1 + RU.fee_rate(asset)) / q) if q > 0 else None
    while lim is not None and lim > 0 and need(lim) > have:
        lim = RU.step(asset, lim, -1)
    if lim is None or lim < prev:
        return top, f"전 거래일 종가({prev:,.0f}원)로 낮춰도 돈이 모자라 기본 한도로 냄"
    return float(lim), f"돈에 맞춰 한도를 {top:,.0f}원 → {lim:,.0f}원으로 낮춤(전 거래일 종가 {prev:,.0f}원 이상)"


def carry(recs: dict, rows: dict, until: dt.datetime, src: Q.Quotes | None = None, book_day: dt.date | None = None,
          marks: list | None = None, accounts=C.LIVE_ACCOUNTS) -> Carry:
    """옛 주문을 그대로 가상 증권사에 넣어 다시 계산한다(읽기만 — 증권사 줄은 돌려주는 brokers의 new에만 있다).
    - 시작: 첫 옛 실행(run 줄 mode live, book_day 전)의 판단 시각에 은행 1,000만 원. 옛 실행이 없으면 until.
    - 옛 주문(pocket 칸이 없는 주문 줄)을 계좌마다 낼 시각 순서대로(같으면 기록 차례): 한국 거래소 = fill_rule "open"이면 장 시작,
      없으면 장 마감 주문을 기록된 decided_at에(그 시각에 받지 않거나 다른 장이 되면 옛 규칙의 장의 08:30에), 비트코인 = 옛 규칙의
      그 09:00 + 5초에 지금 주문(그 봉의 시가). 수량·대상·방향은 기록 그대로, 거절은 그대로 둔다(줄여 다시 내지 않는다).
    - 사기 전에 그 주문에 필요한 만큼만 돈을 옮긴다(rebalance.top_up — 은행 먼저, 그다음 다른 주머니의 뺄 수 있는 돈). 장 시작·
      장 마감 사기의 한도는 buy_limit.
    - until에 맞추기(sync)와 phase 'carry' 줄. 아직 낼 수 없는 옛 주문(그 09:00이 until 뒤인 비트코인 주문, 값을 못 받은 것)은
      waiting에 — 갈아타기는 이것이 있으면 그날 옛 장부로 돈다.
    - marks(날, 시각, 대상 → (값, 시각))를 주면 그 날의 판단 시각(run 줄)에 맞춘 뒤 그 가격으로 계좌 값을 낸다(whatif 견주기용)."""
    book_day = book_day or until.astimezone(C.KST).date()
    lives = sorted((r for r in recs.get("run", []) if r.get("mode") == "live"), key=lambda r: r["run"])
    runs = [r for r in lives if r["run"] < book_day.isoformat()]
    start = _kst(runs[0]["steps"]["decided_at"]) if runs else until
    q = CarryQuotes(rows, src, until)
    bar_days = [r.date for r in rows.get("069500", ())]
    brokers = {}
    K = Carry(brokers, until)
    old = [o for o in recs.get("order", []) if "seq" not in o and "pocket" not in o]
    mark_at = {r["run"]: _kst(r["steps"]["decided_at"]) for r in lives}
    for aid in accounts:
        b = brokers[aid] = B.Broker(aid, q, {}, bar_days)
        b.book_day = book_day
        res = b.start(start, bank=C.START_CASH, note="그대로 대입: 첫 옛 실행 시각의 은행 1,000만 원에서 옛 주문을 그대로 넣어 다시 계산")
        if not res.ok:
            raise RuntimeError(f"{aid} start 거절: {res.reason}")
        events = []   # (낼 시각, 순위, 차례, 종류, 내용)
        for i, d in enumerate(o for o in old if o["account"] == aid):
            o = L.Order.from_record(d)
            spec = C.ASSETS[o.asset]
            step = {"account": aid, "id": o.id, "run": o.run.isoformat(), "asset": o.asset, "side": o.side,
                    "qty": o.qty_units, "kind": B.old_kind(d), "ref": o.ref_price}
            if o.decided_at is None:
                raise RuntimeError(f"{o.id}: 판단 시각이 없는 옛 주문")
            s, _ = old_session(o, rows, until, bar_days)
            step["session"] = s.isoformat()
            if spec.kind == "coin":
                t = C.fill_dt(spec, s, C.OPEN_TIME) + COIN_DELAY
                if t >= until:
                    K.waiting.append({**step, "why": f"그 09:00({s})이 아직 오지 않았다 — 비트코인은 그때 낸다"})
                    continue
            else:
                t = o.decided_at.astimezone(C.KST)
                if krx_session(step["kind"], t, bar_days) != s:
                    t = dt.datetime.combine(s, MORNING, C.KST)
                    if t >= until:
                        t = until - dt.timedelta(minutes=1)
                    step["moved"] = f"그 시각에는 그 장의 주문을 낼 수 없어 {t:%m-%d %H:%M}에 냄"
            events.append((t, 1, i, "order", step))
        for day, t, vp in marks or ():
            if day in mark_at:
                events.append((mark_at[day], 0, -1, "mark", (day, vp)))
        last = start
        for t, _, _, what, x in sorted(events, key=lambda e: e[:3]):
            t = max(t.astimezone(C.KST).replace(microsecond=0), last + dt.timedelta(seconds=1))
            last = t
            if what == "mark":
                day, vp = x
                b.sync(t)
                K.values[(day, aid)] = sum(b.pockets().values()) + sum(
                    n * vp[a][0] / C.ASSETS[a].unit for a, n in b.holdings().items() if a in vp)
                missing = sorted(a for a in b.holdings() if a not in vp)
                if missing:
                    K.notes.append((day, aid, f"평가 가격이 없는 대상 {missing} — 값에서 빠짐"))
                continue
            K.steps.append(_carry_one(b, t, x, K))
        last = last + dt.timedelta(seconds=1) if last >= until else until
        b.sync(last)
        if not runs:   # 옛 실행이 없는 폴더(첫 실행): 다시 넣은 것이 없다 — 표시 줄도 남기지 않는다
            continue
        rejected = [s for s in K.steps if s["account"] == aid and s["result"] == "거절"]
        short = sorted({s["asset"] for s in K.steps if s["account"] == aid and s.get("shrunk")})
        b.mark(last, "phase", {"name": B.CARRY, "orders": sum(s["account"] == aid for s in K.steps),
                               "rejects": len(rejected), "waiting": sum(w["account"] == aid for w in K.waiting),
                               "short": short},
               "옛 주문을 그대로 넣어 다시 계산한 끝 — 이 시각부터 이 잔고에서 이어 간다")
    _outcomes(K)
    return K


def _carry_one(b: B.Broker, t: dt.datetime, step: dict, K: Carry) -> dict:
    """옛 주문 하나를 낸다(필요하면 돈을 먼저 옮긴다). step에 낸 시각·결과·까닭을 적어 돌려준다."""
    a, side, kind, qty = step["asset"], step["side"], step["kind"], step["qty"]
    spec = C.ASSETS[a]
    step = {**step, "at": t.isoformat(timespec="seconds")}
    moved: list[str] = []

    def ask(res: B.Result) -> B.Result:
        if not res.ok:
            moved.append(f"이체 거절: {res.reason}")
        return res
    reason = f"옛 주문 {step['id']}를 그대로({step['run']} 판단)"
    b.sync(t)   # 그 시각까지 치러진 것을 먼저 장부에(돈·한도를 그 시각의 잔고로 정한다)
    if spec.kind == "coin":
        if b.quotes.now(a, t) is None:   # 그 09:00 값을 아직 못 받음(갈아타는 날 진행 중인 봉) — 내지 않고 기다린다
            K.waiting.append({**step, "why": f"{step['session']} 09:00 값을 받지 못함"})
            return {**step, "result": "아직 못 냄", "why": "그 09:00 값을 받지 못함"}
        if side == "buy":
            amt = coin_amount(qty, b.quotes.now(a, t).price, spec.unit)
            need = amt + RU.fee(a, amt)
            if step["account"] in SHRINK_ACCOUNTS:
                have = RB.reach(b, t, "upbit", need - b.orderable("upbit", t))
                if need > have:
                    less = int(have / (1 + RU.fee_rate(a)))
                    while less > 0 and less + RU.fee(a, less) > have:
                        less -= 1
                    if less >= RU.value("upbit_min_order"):
                        step["shrunk"] = f"금액 {amt:,}원 → {less:,}원(그때 쓸 수 있는 돈 안에서)"
                        amt, need = less, less + RU.fee(a, less)
            RB.top_up(b, t, "upbit", need, need, ask, moved)
            res = b.order(t, a, "buy", "now", amount=amt, reason=reason, replay_of=step["id"])
            step["amount"] = amt
        else:
            res = b.order(t, a, "sell", "now", qty=qty, reason=reason, replay_of=step["id"])
    elif side == "buy":
        session = krx_session(kind, t, b.bar_days or ())
        prev = b.prev_close(a, session, t) if session else None
        unit_need = None
        top = RU.default_open_limit(a, prev) if prev else None
        if top:
            v = math.ceil(round(qty * top, 6))
            unit_need = v + RU.fee(a, v)
        have = RB.reach(b, t, "sec", (unit_need or 0) - b.orderable("sec", t))
        if step["account"] in SHRINK_ACCOUNTS and top and unit_need > have:
            n = qty
            while n > 0 and math.ceil(round(n * top, 6)) + RU.fee(a, math.ceil(round(n * top, 6))) > have:
                n -= 1
            if n >= 1:
                step["shrunk"] = f"{qty}주 → {n}주(기본 한도로 그때 쓸 수 있는 돈 안에서)"
                qty = n
            limit, how = top, ""
        else:
            limit, how = buy_limit(a, qty, prev, have)
        if how:
            step["limit_note"] = how
        if limit is not None:
            v = math.ceil(round(qty * limit, 6))
            need = v + RU.fee(a, v)
            if need <= have:
                RB.top_up(b, t, "sec", need, need, ask, moved)
        res = b.order(t, a, "buy", kind, qty=qty, limit=limit, reason=reason, replay_of=step["id"])
        step["limit"] = limit
    else:
        res = b.order(t, a, "sell", kind, qty=qty, reason=reason, replay_of=step["id"])
    if moved:
        step["moves"] = moved
    step["order"] = res.order
    step.setdefault("result", "받음" if res.ok else "거절")
    if not res.ok:
        step["why"] = res.reason
    return step


def _outcomes(K: Carry) -> None:
    """받아진 주문의 끝: 체결(값·금액) / 치러지지 못하고 끝남(expire) / 아직 걸려 있음."""
    for s in K.steps:
        if s["result"] != "받음":
            continue
        b = K.brokers[s["account"]]
        f = next((x for x in b.new["fill"] if x["order"] == s["order"]), None)
        if f is not None:
            s.update(result="체결", price=f["price"], value=f["value"], fill_at=f["at"], filled=f["qty_units"])
            continue
        e = next((x for x in b.new["broker"] if x["type"] == "expire" and x["data"].get("order") == s["order"]), None)
        if e is not None:
            s.update(result="끝남", why=e["note"])
        else:
            s["result"] = "걸려 있음"


# ---------- 차이 가르기 ----------
def exec_time(f: dict) -> dt.datetime:
    """옛 체결 줄의 체결 시각: 시가(open·코인) 09:00, 종가(close·옛 규칙의 ETF·종목) 15:30."""
    kind = f.get("price_kind") or ("open" if C.ASSETS[f["asset"]].kind == "coin" else "close")
    t = C.OPEN_TIME if kind == "open" else C.CLOSE_TIME
    return dt.datetime.combine(dt.date.fromisoformat(f["fill_date"]), t, C.KST)


def flows_old(fills: list[dict], aid: str) -> list[dict]:
    out = []
    for f in fills:
        if f["account"] != aid or not f["qty_units"]:
            continue
        sign = 1 if f["side"] == "buy" else -1
        out.append({"at": exec_time(f), "asset": f["asset"], "q": sign * int(f["qty_units"]),
                    "value": sign * int(f["value"]), "fee": int(f["fee"]), "tax": int(f.get("tax") or 0)})
    return out


def flows_new(lines: dict[str, list[dict]], aid: str) -> tuple[list[dict], list[tuple[dt.datetime, int]]]:
    out, moves = [], []
    for k, d in B.mine(lines, aid):
        if k == "fill":
            sign = 1 if d["side"] == "buy" else -1
            out.append({"at": _kst(d["at"]), "asset": d["asset"], "q": sign * int(d["qty_units"]),
                        "value": sign * int(d["value"]), "fee": int(d["fee"]), "tax": int(d.get("tax") or 0)})
        elif k == "broker" and d["type"] == "transfer" and d["data"].get("fee"):
            moves.append((_kst(d["at"]), int(d["data"]["fee"])))
    return out, moves


def attribute(flows: list[dict], moves: list[tuple[dt.datetime, int]], marks: list) -> dict:
    """정해진 평가 시점들(marks: (날, 시각, 대상 → (값, 시각)))에서 체결 시각 기준 보유로 가른다.
    끝 값 − 시작 값 = Σ보유 몫 + Σ치른 값 몫 − 수수료 − 세금 − 이체 수수료 (정확히 맞는다 — 'identity'로 확인)."""
    P = [{a: v for a, (v, _) in m[2].items()} for m in marks]
    T = [m[1] for m in marks]
    units: dict[str, int] = {}
    cash = float(C.START_CASH)
    hold_by, paid_by, fee_by, tax_by = {}, {}, {}, {}
    moved = 0
    gaps = []   # 날마다 대상별 평가액
    fl = sorted(flows, key=lambda f: f["at"])
    j = 0
    values = []
    for k in range(len(T)):
        if k > 0:
            for a, n in units.items():
                if n:
                    hold_by[a] = hold_by.get(a, 0.0) + n * (P[k][a] - P[k - 1][a]) / C.ASSETS[a].unit
        while j < len(fl) and fl[j]["at"] <= T[k]:
            f = fl[j]
            a = f["asset"]
            # 첫 평가 시점(첫날 계좌 줄) 안의 체결(첫날 바로 산 비트코인)은 시작 값(1,000만 원 현금)에서 그 시점까지의 '치른 값' 몫이다
            paid_by[a] = paid_by.get(a, 0.0) + f["q"] * P[k][a] / C.ASSETS[a].unit - f["value"]
            fee_by[a] = fee_by.get(a, 0) + f["fee"]
            tax_by[a] = tax_by.get(a, 0) + f["tax"]
            units[a] = units.get(a, 0) + f["q"]
            cash -= f["value"] + f["fee"] + f["tax"]
            j += 1
        for t, fee in moves:
            if (T[k - 1] if k else T[0] - dt.timedelta(days=1)) < t <= T[k]:
                moved += fee
                cash -= fee
        values.append(cash + sum(n * P[k][a] / C.ASSETS[a].unit for a, n in units.items() if n))
        gaps.append({a: n * P[k][a] / C.ASSETS[a].unit for a, n in units.items() if n})
    out = {"hold": sum(hold_by.values()), "paid": sum(paid_by.values()), "fee": sum(fee_by.values()),
           "tax": sum(tax_by.values()), "transfer_fee": moved, "end": values[-1], "start": float(C.START_CASH),
           "hold_by": hold_by, "paid_by": paid_by, "fee_by": fee_by, "tax_by": tax_by, "values": values, "worth": gaps}
    out["identity"] = out["end"] - (out["start"] + out["hold"] + out["paid"] - out["fee"] - out["tax"] - moved)
    return out


def revalue(line: dict, vp: dict) -> tuple[float, list[str]]:
    """옛 계좌 줄(현금·보유 그대로)을 대입과 같은 평가 가격으로 다시 친 값."""
    v, miss = float(line["cash"]), []
    for a, n in (line.get("positions") or {}).items():
        if not n:
            continue
        if a in vp:
            v += n * vp[a][0] / C.ASSETS[a].unit
        else:
            v += n * float(line["prices"][a][1]) / C.ASSETS[a].unit
            miss.append(a)
    return v, miss


def old_exec_value(aid: str, day: str, accounts: list[dict], orders: list[dict], rows: dict, at: dt.datetime,
                   vp: dict) -> tuple[float | None, list[str]]:
    """옛 장부의 그날 잔고에 '판단 시각 at 전에 이미 체결됐지만 옛 장부가 아직 장부에 안 넣은' 주문(옛 장부는 이틀 늦게 넣었다)을
    옛 규칙 그대로(그 봉의 시가·종가, 옛 장부의 체결 계산) 넣고, 같은 평가 가격 vp로 친 값 — '체결 시각 기준으로 견준 옛 값'.
    (값, 넣은 주문 id). 그날 계좌 줄이 없으면 None."""
    accs = [a for a in accounts if a["account"] == aid and a["run"] <= day]
    if not accs or accs[-1]["run"] != day:
        return None, []
    acc = L.rebuild(aid, accs, [o for o in orders if o["account"] == aid and o["run"] <= day])
    acc.pending = [o for o in acc.pending if o.run.isoformat() < day or (o.decided_at and o.decided_at < at)]
    ready = []
    for o in acc.pending:
        if o.asset not in rows:
            continue
        row = L.fill_row(o, PR.view(o.asset, rows[o.asset], at))
        if row is None:
            continue
        when = C.fill_dt(C.ASSETS[o.asset], row.date, o.rule()[1])
        if when <= at:
            ready.append((when, 0 if o.side == "sell" else 1, o, row))
    done = []
    for _, _, o, row in sorted(ready, key=lambda x: (x[0], x[1], x[2].id)):
        acc._fill(o, row, dt.date.fromisoformat(day))   # 옛 장부의 체결 계산 그대로(기록을 쓰지 않는다 — 계산만)
        done.append(o.id)
    value = float(acc.cash) + sum(n * vp[a][0] / C.ASSETS[a].unit for a, n in acc.units.items() if n and a in vp)
    return value, done


# ---------- 옛 장부의 '실제로는 못 하는 거래' ----------
def impossible(fills: list[dict], orders: list[dict], aid: str) -> dict:
    """① 같은 시각에 판 돈으로 산 금액(그 시각 전 현금을 넘는 사기) — 그중 증권 계좌에서 판 돈으로 같은 시각 비트코인을 산 몫
    ② 체결 때 수량을 줄인 주문. 옛 장부는 현금 하나라 체결 시각 순서(같은 시각엔 팔기 먼저)로 다시 센다."""
    fl = sorted((f for f in fills if f["account"] == aid), key=lambda f: (exec_time(f), f["side"] != "sell"))
    cash = C.START_CASH
    groups: dict[dt.datetime, list[dict]] = {}
    for f in fl:
        groups.setdefault(exec_time(f), []).append(f)
    same = []
    for t in sorted(groups):
        g = groups[t]
        before = cash
        sells = sum(f["value"] - f["fee"] for f in g if f["side"] == "sell" and f["qty_units"])
        buys = [f for f in g if f["side"] == "buy" and f["qty_units"]]
        cost = sum(f["value"] + f["fee"] for f in buys)
        funded = max(0, min(sells, cost - max(before, 0)))
        if funded > 0:
            coin_cost = sum(f["value"] + f["fee"] for f in buys if f["asset"] == "KRW-BTC")
            krx_sells = sum(f["value"] - f["fee"] for f in g if f["side"] == "sell" and f["qty_units"]
                            and C.on_exchange(f["asset"]))
            same.append({"at": t.isoformat(timespec="minutes"), "cash_before": before, "sold": sells, "bought": cost,
                         "funded_by_same_time_sale": funded,
                         "coin_from_krx_sale": min(coin_cost, krx_sells, funded),
                         "buys": [f"{f['asset']} {f['qty_units']}" for f in buys],
                         "sells": [f"{f['asset']} {f['qty_units']}" for f in g if f["side"] == "sell" and f["qty_units"]]})
        cash += sells - cost
    by_id = {o["id"]: o for o in orders}
    cut = []
    for f in fl:
        o = by_id.get(f["order"])
        if o and int(f["qty_units"]) != int(o["qty_units"]):
            cut.append({"at": exec_time(f).isoformat(timespec="minutes"), "order": f["order"], "asset": f["asset"],
                        "side": f["side"], "ordered": int(o["qty_units"]), "filled": int(f["qty_units"]),
                        "note": f.get("note", "")})
    return {"same_time": same, "cut": cut}


# ---------- 실행 ----------
def _guard(out: Path, root: Path) -> None:
    out = out.resolve()
    for name in FORBIDDEN:
        bad = (root / name).resolve()
        if out == bad or bad in out.parents:
            raise SystemExit(f"--out {out}는 {bad} 아래다 — 진짜 기록·일지·시세 폴더에는 쓰지 않는다")


def run(records: Path, prices: Path, coin: dict | None, out: Path | None, root: Path = C.ROOT,
        stocks: Path | None = None) -> dict:
    """대입 두 경우를 계산해 결과(dict)를 돌려주고, out이 있으면 그 폴더에 쓴다(records·reports·data 아래는 거절)."""
    if out is not None:
        _guard(out, root)
        _guard(out, records.parent)
    if stocks is not None and stocks.exists():
        ST.register(stocks)
    recs = {k: R.read(records, k) for k in ("run", "account", "order", "fill")}
    runs = [r for r in recs["run"] if r.get("mode") == "live"]
    if not runs:
        raise SystemExit(f"{records}에 실행 기록(run, mode live)이 없다")
    rows = {p.stem: PR.load(p.stem, prices) for p in sorted(prices.glob("*.csv"))}
    cases, used = {}, {}
    for case in CASES:
        cases[case], used[case] = simulate(case, runs, recs, rows, coin)
    marks = cases["once"].marks
    # 평가 가격은 두 경우가 같아야 한다(비트코인 판단 때 값·한국 거래소 마지막 종가)
    same_marks = all(m1[2] == m2[2] for m1, m2 in zip(marks, cases["open"].marks))
    days = [r["run"] for r in runs]
    # 그대로 대입(2026-11-10 3회): 마지막 판단 시각까지(그 뒤 1분), 장부에 넣은 날 = 마지막 실행 다음 날(갈아타는 날이라 치고)
    until = _kst(runs[-1]["steps"]["decided_at"]) + CARRY_TAIL
    V = carry(recs, rows, until, None, dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1), marks)
    result = {"made_at": C.now_kst().isoformat(timespec="seconds"), "days": days,
              "coin_source": used["once"], "coin_after_open": {d: used["open"][d].get("after_open") for d in days},
              "coin_file": bool(coin), "same_marks": same_marks, "accounts": {}, "daily": {}, "cases": {}}
    old_lines = {(a["run"], a["account"]): a for a in recs["account"]}
    for aid in C.LIVE_ACCOUNTS:
        rows_ = []
        for k, day in enumerate(days):
            line = old_lines.get((day, aid))
            rv, miss = revalue(line, marks[k][2]) if line else (None, [])
            rows_.append({"day": day, "recorded": line["value"] if line else None,
                          "revalued": round(rv, 2) if rv is not None else None, "revalue_missing": miss,
                          "verbatim": round(V.values[(day, aid)], 2),
                          **{c: cases[c].values[(day, aid)]["value"] for c in CASES}})
        result["daily"][aid] = rows_
        end = rows_[-1]
        result["accounts"][aid] = {k: end[k] for k in ("recorded", "revalued", "verbatim", *CASES)}
        ev, booked = old_exec_value(aid, days[-1], recs["account"], recs["order"], rows, marks[-1][1], marks[-1][2])
        result["accounts"][aid]["old_exec"] = round(ev, 2) if ev is not None else None
        result["accounts"][aid]["old_exec_added"] = booked
        old = attribute(flows_old(recs["fill"], aid), [], marks)
        parts = {"old": _short(old)}
        for c in ("verbatim", *CASES):
            brokers = V.brokers if c == "verbatim" else cases[c].brokers
            lines = {k: [d for b in brokers.values() for d in b.new[k]] for k in B.KINDS}
            fl, mv = flows_new(lines, aid)
            new = attribute(fl, mv, marks if c == "verbatim" else cases[c].marks)
            parts[c] = _short(new)
            parts[c]["vs_old"] = {
                "hold": new["hold"] - old["hold"], "paid": new["paid"] - old["paid"],
                "fee": -(new["fee"] - old["fee"]), "transfer_fee": -new["transfer_fee"], "tax": -new["tax"],
                "rest": (end[c] - (end["revalued"] or 0)) - ((new["hold"] - old["hold"]) + (new["paid"] - old["paid"])
                                                             - (new["fee"] - old["fee"]) - new["transfer_fee"] - new["tax"]),
                "hold_by": _diff(new["hold_by"], old["hold_by"]), "paid_by": _diff(new["paid_by"], old["paid_by"])}
            if aid == "live:hand":
                parts[c]["worth_gap"] = [{"day": days[k], **{a: round(new["worth"][k].get(a, 0) - old["worth"][k].get(a, 0))
                                                              for a in sorted(set(new["worth"][k]) | set(old["worth"][k]))}}
                                         for k in range(len(days))]
        result["accounts"][aid]["parts"] = parts
        result["accounts"][aid]["impossible"] = impossible(recs["fill"], recs["order"], aid)
    result["carry"] = carry_summary(V, recs, rows, marks)
    for c, K in cases.items():
        lines = {k: [d for b in K.brokers.values() for d in b.new[k]] for k in B.KINDS}
        bars = {a: rows[a] for a in {d.get("asset") for k in ("order", "fill") for d in lines[k]} if a in rows}
        errs, warns = B.check(lines, bars)
        info = {"check_errors": errs, "check_warnings": warns, "per_account": {}}
        for aid, b in K.brokers.items():
            mine_ = [d for d in lines["broker"] if d["account"] == aid]
            info["per_account"][aid] = {
                "orders": sum(o["account"] == aid for o in lines["order"]),
                "fills": sum(f["account"] == aid for f in lines["fill"]),
                "rejects": [{"at": d["at"], "data": d["data"], "note": d["note"]} for d in mine_ if d["type"] == "reject"],
                "transfers": [{"at": d["at"], **d["data"]} for d in mine_ if d["type"] == "transfer"],
                "expired": [{"at": d["at"], "note": d["note"], "order": d["data"].get("order")} for d in mine_
                            if d["type"] == "expire"],
                "adjust": sum(d["type"] == "adjust" for d in mine_),
                "not_sent": [{"day": n[0], "step": n[2], "why": why, "text": n[3][len("내지 않음: "):]} for n in K.notes
                             if n[1] == aid and (why := not_sent_kind(n[3]))],
                "pending_end": [{"id": p.id, "asset": p.asset, "side": p.side, "qty": p.qty, "session": str(p.session)}
                                for p in b.pending()],
                "taxes": sum(int(f.get("tax") or 0) for f in lines["fill"] if f["account"] == aid),
                "fees": sum(int(f["fee"]) for f in lines["fill"] if f["account"] == aid),
                "holdings_end": b.holdings(), "pockets_end": b.pockets()}
        info["targets_hand"] = {d: K.targets.get((d, "live:hand")) for d in days}
        info["notes"] = [{"day": n[0], "account": n[1], "step": n[2], "text": n[3]} for n in K.notes]
        result["cases"][c] = info
        if out is not None:
            folder = out / c / "records"
            if folder.exists():
                shutil.rmtree(folder)
            for b in K.brokers.values():
                b.write(folder)
    if out is not None:
        folder = out / "verbatim" / "records"
        if folder.exists():
            shutil.rmtree(folder)
        for b in V.brokers.values():
            b.write(folder)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        with (out / "values.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "account", "recorded", "revalued", "verbatim", *CASES])
            for aid, rs in result["daily"].items():
                for x in rs:
                    w.writerow([x["day"], aid, x["recorded"], x["revalued"], x["verbatim"], *(x[c] for c in CASES)])
        (out / "summary.txt").write_text("\n".join(describe(result)) + "\n", encoding="utf-8")
    return result


def not_sent_kind(text: str) -> str:
    """계획이 내지 않은 것의 갈래: money(돈이 모자라 덜 냈거나 못 냄) / closed(장이 닫혀 지금 가격이 없어 건너뜀 — 주말 09:05) /
    no_plan(그날 목표가 없는 계좌의 09:05 단계) / small(업비트 최소 금액·옮기기 아래 끝) / other. 내지 않음이 아니면 ''."""
    if not text.startswith("내지 않음: "):
        return ""
    t = text[len("내지 않음: "):]
    if "가운데" in t or "쓸 수 있는 돈" in t:
        return "money"
    if "지금 가격을 못 받은" in t or "장중이 아니다" in t or "받지 않는 시간" in t:
        return "closed"
    if "plan 줄)이 없다" in t:
        return "no_plan"
    if "최소 주문 금액" in t or "옮기지 않았다" in t:
        return "small"
    return "other"


def carry_summary(V: Carry, recs: dict, rows: dict, marks: list) -> dict:
    """그대로 대입의 결과: 검산(옛 주문 줄·run 줄과 함께 — 아직 못 넣은 옛 주문은 뺀다), 옛 주문마다 한 일, 받아지지 않은 주문
    (거절·치러지지 못하고 끝남)과 그 대상의 그때 값·끝 평가 가격, 계좌마다 끝 상태."""
    lines = {k: [d for b in V.brokers.values() for d in b.new[k]] for k in B.KINDS}
    waiting = {w["id"] for w in V.waiting}
    check_lines = {**lines, "order": [o for o in recs["order"] if o["id"] not in waiting] + lines["order"],
                   "run": recs["run"], "fill": [f for f in recs["fill"] if "seq" not in f] + lines["fill"],
                   "account": [a for a in recs["account"] if "seq" not in a]}
    errs, warns = B.check(check_lines, rows)
    old_fill = {f["order"]: f for f in recs["fill"]}
    end = marks[-1][2] if marks else {}
    missed = []
    for s in V.steps:
        if s["result"] not in ("거절", "끝남"):
            continue
        f = old_fill.get(s["id"])
        then = float(f["price"]) if f and f["qty_units"] else float(s["ref"])
        unit = C.ASSETS[s["asset"]].unit
        missed.append({**s, "then_price": then, "then_value": round(s["qty"] * then / unit),
                       "old_filled": int(f["qty_units"]) if f else None,
                       "end_price": end[s["asset"]][0] if s["asset"] in end else None,
                       "move_since": round(s["qty"] * (end[s["asset"]][0] - then) / unit) if s["asset"] in end else None})
    per = {}
    for aid, b in V.brokers.items():
        per[aid] = {"pockets_end": b.pockets(), "holdings_end": b.holdings(),
                    "avg_cost": {a: round(b.avg_cost(a) * C.ASSETS[a].unit, 2) for a in b.holdings()},
                    "pending_end": [{"id": p.id, "asset": p.asset, "side": p.side, "qty": p.qty, "session": str(p.session),
                                     "limit": p.limit} for p in b.pending()],
                    "orders": sum(s["account"] == aid for s in V.steps),
                    "results": {r: sum(s["account"] == aid and s["result"] == r for s in V.steps)
                                for r in ("체결", "거절", "끝남", "걸려 있음", "아직 못 냄")},
                    "transfers": [{"at": d["at"], **d["data"]} for d in lines["broker"] if d["account"] == aid
                                  and d["type"] == "transfer"],
                    "fees": sum(int(f["fee"]) for f in lines["fill"] if f["account"] == aid),
                    "taxes": sum(int(f.get("tax") or 0) for f in lines["fill"] if f["account"] == aid)}
    return {"until": V.until.isoformat(), "check_errors": errs, "check_warnings": warns, "steps": V.steps,
            "waiting": V.waiting, "missed": missed, "per_account": per, "notes": V.notes}


def _short(a: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in a.items()
            if k in ("hold", "paid", "fee", "tax", "transfer_fee", "end", "identity")}


def _diff(x: dict, y: dict) -> dict:
    return {a: round(x.get(a, 0.0) - y.get(a, 0.0)) for a in sorted(set(x) | set(y))}


def describe(res: dict) -> list[str]:
    """사람이 읽는 요약(표준 출력·summary.txt)."""
    days = res["days"]
    out = [f"# 지난 결정을 가상 증권사에 대입 ({days[0]} ~ {days[-1]}, {len(days)}번)", ""]
    src = res["coin_source"]
    n_min = sum(v.get("decision") == "minute" for v in src.values())
    out.append(f"비트코인 판단 때 값: 1분봉 종가 {n_min}일, 실행 기록의 판단 때 가격 {len(days) - n_min}일"
               f" / 평가 가격이 두 경우에 같음: {res['same_marks']}")
    out += ["", "| 계좌 | 기록된 값 | 옛 장부를 같은 가격으로 다시 친 값 | 옛 주문 그대로 대입 | (가) 배분 되살림 하루 한 번 | "
            "(나) 09:05 한 번 더 |", "|---|---|---|---|---|---|"]
    for aid, a in res["accounts"].items():
        cell = lambda v: f"{v:,.0f} ({(v / C.START_CASH - 1) * 100:+.2f}%)" if v is not None else "–"
        out.append(f"| {aid} | {cell(a['recorded'])} | {cell(a['revalued'])} | {cell(a['verbatim'])} | {cell(a['once'])} | "
                   f"{cell(a['open'])} |")
    out.append("")
    for aid, a in res["accounts"].items():
        if a.get("old_exec") is not None:
            out.append(f"- {aid} 옛 값을 체결 시각 기준으로(이미 체결됐지만 옛 장부가 아직 안 넣은 주문 {len(a['old_exec_added'])}건을 "
                       f"옛 규칙대로 넣어) 같은 가격으로 치면: {a['old_exec']:,.0f}원")
    for aid in ("live:hand",):
        p = res["accounts"][aid]["parts"]
        out += ["", f"## {aid} 차이 가르기 (대입 − 다시 친 옛 값, 원)"]
        for c in ("verbatim", *CASES):
            v = p[c]["vs_old"]
            out.append(f"- {CASES.get(c, '옛 주문 그대로 대입')}: 보유 몫 {v['hold']:+,.0f} / 치른 값 몫 {v['paid']:+,.0f} / 수수료 {v['fee']:+,.0f} / "
                       f"이체 수수료 {v['transfer_fee']:+,.0f} / 세금 {v['tax']:+,.0f} / 그 밖 {v['rest']:+,.0f}")
            out.append(f"  - 보유 몫 대상별: {v['hold_by']}")
            out.append(f"  - 치른 값 몫 대상별: {v['paid_by']}")
        out.append(f"- 맞는지(끝 값 − 가른 몫의 합, 0이어야): 옛 {p['old']['identity']:.2f}, "
                   + ", ".join(f"{c} {p[c]['identity']:.2f}" for c in ("verbatim", *CASES)))
        out += ["", "| 날 | 다시 친 옛 값 | 그대로 대입 | (가) | (나) |", "|---|---|---|---|---|"]
        for x in res["daily"][aid]:
            out.append(f"| {x['day']} | {x['revalued']:,.0f} | {x['verbatim']:,.0f} | {x['once']:,.0f} | {x['open']:,.0f} |")
    cr = res["carry"]
    out += ["", f"## 옛 주문 그대로 대입 ({cr['until'][:16]}까지): 검산 오류 {len(cr['check_errors'])}건, 경고 {len(cr['check_warnings'])}건"]
    out += [f"- 오류: {e}" for e in cr["check_errors"][:20]]
    for aid, x in cr["per_account"].items():
        out.append(f"- {aid}: 옛 주문 {x['orders']}건 → {x['results']}, 이체 {len(x['transfers'])}, 수수료 {x['fees']:,}원, "
                   f"세금 {x['taxes']:,}원, 끝 보유 {x['holdings_end']}, 주머니 {x['pockets_end']}")
    out.append(f"- 아직 못 넣은 옛 주문 {len(cr['waiting'])}건: " + (", ".join(f"{w['account']} {w['asset']} {w['side']} — {w['why']}"
                                                                for w in cr["waiting"]) or "없음"))
    out += ["", "| 계좌 | 판단한 날 | 대상 | 방향 | 수량 | 약 얼마 | 결과 | 까닭 | 그 뒤 값 변화 × 수량 |", "|---|---|---|---|--:|--:|---|---|--:|"]
    for m in cr["missed"]:
        out.append(f"| {m['account']} | {m['run']} | {C.ASSETS[m['asset']].name} | {m['side']} | {m['qty']} | {m['then_value']:,} | "
                   f"{m['result']} | {m.get('why', '')[:60]} | {m['move_since']:+,} |" if m["move_since"] is not None else
                   f"| {m['account']} | {m['run']} | {m['asset']} | {m['side']} | {m['qty']} | {m['then_value']:,} | {m['result']} | "
                   f"{m.get('why', '')[:60]} | – |")
    for c in CASES:
        info = res["cases"][c]
        out += ["", f"## {CASES[c]}: 검산 오류 {len(info['check_errors'])}건, 경고 {len(info['check_warnings'])}건"]
        out += [f"- 오류: {e}" for e in info["check_errors"][:20]]
        for aid, x in info["per_account"].items():
            kinds = {}
            for n in x["not_sent"]:
                kinds[n["why"]] = kinds.get(n["why"], 0) + 1
            out.append(f"- {aid}: 주문 {x['orders']}, 체결 {x['fills']}, 거절 {len(x['rejects'])}, 내지 않음 {kinds or 0}, "
                       f"이체 {len(x['transfers'])}, 끝난 주문 {len(x['expired'])}, 끝에 걸린 주문 {len(x['pending_end'])}, "
                       f"수수료 {x['fees']:,}원, 세금 {x['taxes']:,}원")
    return out


def main(argv: list[str]) -> int:
    opt, i = {}, 0
    while i < len(argv):
        if argv[i] in ("--coin-prices", "--out", "--records", "--prices") and i + 1 < len(argv):
            opt[argv[i]] = argv[i + 1]
            i += 2
        else:
            print(__doc__)
            return 2
    coin = None
    if "--coin-prices" in opt:
        coin = json.loads(Path(opt["--coin-prices"]).read_text(encoding="utf-8"))
    out = Path(opt.get("--out", DEFAULT_OUT))
    res = run(Path(opt.get("--records", C.ROOT / "records")), Path(opt.get("--prices", C.PRICES)), coin, out,
              stocks=C.ROOT / "stocks.toml")
    print("\n".join(describe(res)))
    print(f"\n→ {out}/ (summary.json, summary.txt, values.csv, <경우>/records/)")
    return 1 if any(res["cases"][c]["check_errors"] for c in CASES) or res["carry"]["check_errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
