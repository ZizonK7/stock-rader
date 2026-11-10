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
- 두 경우: (가) "once" 하루 한 번만 움직임 (나) "open" 그날 09:05에 장 시작 뒤 단계(daily.open_accounts)를 한 번 더 — 한국 거래소
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
                          **{c: cases[c].values[(day, aid)]["value"] for c in CASES}})
        result["daily"][aid] = rows_
        end = rows_[-1]
        result["accounts"][aid] = {k: end[k] for k in ("recorded", "revalued", *CASES)}
        ev, booked = old_exec_value(aid, days[-1], recs["account"], recs["order"], rows, marks[-1][1], marks[-1][2])
        result["accounts"][aid]["old_exec"] = round(ev, 2) if ev is not None else None
        result["accounts"][aid]["old_exec_added"] = booked
        old = attribute(flows_old(recs["fill"], aid), [], marks)
        parts = {"old": _short(old)}
        for c in CASES:
            lines = {k: [d for b in cases[c].brokers.values() for d in b.new[k]] for k in B.KINDS}
            fl, mv = flows_new(lines, aid)
            new = attribute(fl, mv, cases[c].marks)
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
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        with (out / "values.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "account", "recorded", "revalued", *CASES])
            for aid, rs in result["daily"].items():
                for x in rs:
                    w.writerow([x["day"], aid, x["recorded"], x["revalued"], *(x[c] for c in CASES)])
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
    out += ["", "| 계좌 | 기록된 값 | 같은 가격으로 다시 친 값 | (가) 하루 한 번 | (나) 09:05 한 번 더 |", "|---|---|---|---|---|"]
    for aid, a in res["accounts"].items():
        cell = lambda v: f"{v:,.0f} ({(v / C.START_CASH - 1) * 100:+.2f}%)" if v is not None else "–"
        out.append(f"| {aid} | {cell(a['recorded'])} | {cell(a['revalued'])} | {cell(a['once'])} | {cell(a['open'])} |")
    out.append("")
    for aid, a in res["accounts"].items():
        if a.get("old_exec") is not None:
            out.append(f"- {aid} 옛 값을 체결 시각 기준으로(이미 체결됐지만 옛 장부가 아직 안 넣은 주문 {len(a['old_exec_added'])}건을 "
                       f"옛 규칙대로 넣어) 같은 가격으로 치면: {a['old_exec']:,.0f}원")
    for aid in ("live:hand",):
        p = res["accounts"][aid]["parts"]
        out += ["", f"## {aid} 차이 가르기 (대입 − 다시 친 옛 값, 원)"]
        for c in CASES:
            v = p[c]["vs_old"]
            out.append(f"- {CASES[c]}: 보유 몫 {v['hold']:+,.0f} / 치른 값 몫 {v['paid']:+,.0f} / 수수료 {v['fee']:+,.0f} / "
                       f"이체 수수료 {v['transfer_fee']:+,.0f} / 세금 {v['tax']:+,.0f} / 그 밖 {v['rest']:+,.0f}")
            out.append(f"  - 보유 몫 대상별: {v['hold_by']}")
            out.append(f"  - 치른 값 몫 대상별: {v['paid_by']}")
        out.append(f"- 맞는지(끝 값 − 가른 몫의 합, 0이어야): 옛 {p['old']['identity']:.2f}, "
                   + ", ".join(f"{c} {p[c]['identity']:.2f}" for c in CASES))
        out += ["", "| 날 | 다시 친 옛 값 | (가) | (나) |", "|---|---|---|---|"]
        for x in res["daily"][aid]:
            out.append(f"| {x['day']} | {x['revalued']:,.0f} | {x['once']:,.0f} | {x['open']:,.0f} |")
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
    return 1 if any(res["cases"][c]["check_errors"] for c in CASES) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
