"""매일 실행이 가상 증권사를 거친다 (2회차, docs/broker-sandbox.md 16–17절): 주문 계획(rebalance), prepare·record·open, 갈아타는 날,
일지. 임시 폴더에서 가짜 시세로 돈다(네트워크 없음). 시험 이름의 e1…e8은 2회차 지시서 'E. 시험'의 번호다.

흉내 내기(quotes.BarQuotes)에서만 되는 길로 통과시키지 않으려고, 실제 실행에서 값이 없을 수 있는 자리(지금 가격·호가를 못 받음,
진행 중인 봉을 못 받음)는 그런 공급자를 끼워 따로 시험한다(NoLive·NoRunningCoin)."""
import contextlib
import datetime as dt
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market import broker as B
from market import config as C
from market import daily as D
from market import ledger as L
from market import prices as PR
from market import quotes as Q
from market import rebalance as RB
from market import records as R
from market import report as RP
from market import rules as RU
from market.prices import Row
from tests.test_daily import TICK, at, fill, make_root

ROOT = Path(__file__).resolve().parent.parent
E, U, BTC = "069500", "360750", "KRW-BTC"


def day_run(root, day, weights=None, record_time=(8, 30), edit=None, quiet=True):
    """하루: prepare → 틀 채우기(비중은 weights로, 없는 대상은 0) → record. (종료 코드, record가 찍은 글)."""
    assert D.prepare(root, day, at(day, 7, 37), fetch=False, ticker=TICK, echo=False) == 0
    inp = fill(json.loads((root.day_dir(day) / "input.json").read_text(encoding="utf-8")))
    if weights is not None:
        for a in inp["weights"]:
            inp["weights"][a] = {"target": weights.get(a, 0.0), "reason": "가짜 비중"}
    if edit:
        edit(inp)
    p = root.day_dir(day) / "filled.json"
    p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = D.record(root, day, at(day, *record_time), p)
    return code, out.getvalue()


def quiet(fn, *a, **k):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        return fn(*a, **k), out.getvalue()


def recs(root):
    return {k: R.read(root.records, k) for k in ("run", "order", "fill", "broker", "account")}


def of(lines, account="live:hand", **want):
    return [x for x in lines if x["account"] == account and all(x.get(k) == v for k, v in want.items())]


def d(m, day):
    return dt.date(2025, m, day)


class NoLive(Q.BarQuotes):
    """지금 가격·호가를 못 받는 공급자(실제 실행에서 주소가 막혔을 때)."""

    def now(self, asset, at):
        return None

    def book(self, asset, at):
        return None


class NoRunningCoin(Q.BarQuotes):
    """진행 중인 코인 봉을 못 받는 공급자(갈아타는 날 비트코인 시가를 못 받았을 때)."""

    def day(self, asset, date, at):
        got = super().day(asset, date, at)
        return None if got is not None and not got.final and C.ASSETS[asset].kind == "coin" else got


def use(cls):
    return mock.patch.object(D, "quotes_for", lambda root: cls(
        {p.stem: PR.load(p.stem, root.prices) for p in sorted(root.prices.glob("*.csv"))}))


class Loop(unittest.TestCase):
    def test_e1_first_day_starts_and_yesterdays_open_fill_is_booked_the_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            for i in range(3):
                self.assertEqual(day_run(root, d(9, 2 + i))[0], 0)
            r = recs(root)
            starts = [x for x in r["broker"] if x["type"] == "start"]
            self.assertEqual([(x["account"], x["run"], x["data"]["pockets"]["bank"]) for x in starts],
                             [(a, "2025-09-02", C.START_CASH) for a in C.LIVE_ACCOUNTS])
            for acc in ("live:hand", "live:calc", "baseline:equal_hold"):
                first = [o for o in of(r["order"], acc) if o["run"] == "2025-09-02" and o["order_kind"] == "open"]
                self.assertTrue(first, acc)
                for o in first:   # 어제 09:00 체결이 오늘(이틀 뒤가 아니라) 실행에서 장부에 들어와 있다
                    f = next(x for x in r["fill"] if x["order"] == o["id"])
                    self.assertEqual((f["run"], f["at"], f["price_kind"]), ("2025-09-03", "2025-09-02T09:00:00+09:00", "open"))
                    self.assertEqual(f["price"], next(x.open for x in PR.load(o["asset"], root.prices) if x.date == d(9, 2)))
                    self.assertIs(f["source"]["final"], True)        # 다음 날 07:37엔 끝난 봉으로
                    # 바깥 그래프가 읽는 칸: ref_price = 주문 수량을 잡은 가격(전 거래일 종가), 한도는 limit에 따로
                    prev = [x.close for x in PR.load(o["asset"], root.prices) if x.date < d(9, 2)][-1]
                    self.assertEqual((o["ref_price"], o["limit"], o["fill_rule"]),
                                     (prev, float(RU.default_open_limit(o["asset"], prev)), "open"))
                    self.assertTrue({"asset", "side", "qty_units", "decided_at"} <= set(o))
                line = of(r["account"], acc, run="2025-09-03")[0]
                self.assertTrue(all(line["positions"][o["asset"]] >= o["qty_units"] for o in first))
            cash = of(r["account"], "baseline:cash")
            self.assertEqual([(x["value"], x["pockets"]["bank"], x["pending"]) for x in cash], [(C.START_CASH, C.START_CASH, [])] * 3)
            self.assertEqual(of(r["order"], "baseline:cash"), [])
            self.assertEqual([x for x in r["broker"] if x["type"] == "reject"], [])   # 계획이 거절을 만들지 않는다
            self.assertEqual([x["type"] for x in of(r["broker"], "baseline:equal_hold") if x["run"] == "2025-09-04"], [])
            self.assertEqual(R.check(root.path, [root.records]), [])    # 칸 + 증권사 검산(체결가 = 끝난 봉의 시가)
            out, n = B.status(root.path, at(d(9, 4), 12, 0))
            self.assertEqual(n, 0)
            self.assertIn("## live:hand", "\n".join(out))

    def test_e2_sell_to_buy_waits_for_the_open_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.95})[0], 0)
            code, said = day_run(root, d(9, 3), {U: 0.95})
            self.assertEqual(code, 0)
            r = recs(root)
            today = [o for o in of(r["order"]) if o["run"] == "2025-09-03"]
            sell = next(o for o in today if o["side"] == "sell")
            self.assertEqual((sell["asset"], sell["order_kind"], sell["session"]), (E, "open", "2025-09-03"))
            # 판단 때: 사는 쪽은 지금 계좌에 있는 돈만큼만(같은 09:00에 팔리는 돈은 아직 없다) — 거절 줄이 아니라 계획이 덜 낸다
            line = of(r["account"], run="2025-09-03")[0]
            early = [o for o in today if o["side"] == "buy"]
            self.assertLess(sum(o["reserved"] for o in early), 0.1 * line["value"])
            self.assertEqual([x for x in r["broker"] if x["type"] == "reject"], [])
            plan = [x for x in of(r["broker"], type="plan") if x["run"] == "2025-09-03"][-1]
            self.assertEqual((plan["data"]["targets"][U], plan["data"]["short"]), (0.95, [U]))
            self.assertIn("가운데", said)
            self.assertIn("주문 넣음: KODEX 200 팔기", said)
            # 같은 날 09:02 장 시작 뒤 단계: 팔린 돈으로 지금 산다
            (code, said), before = quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 2)), recs(root)
            self.assertEqual(code, 0)
            self.assertIn("바로 체결: TIGER 미국S&P500 사기", said)
            r = recs(root)
            sold = next(f for f in r["fill"] if f["order"] == sell["id"])
            self.assertEqual((sold["at"], sold["price_kind"], sold["source"]["final"]), ("2025-09-03T09:00:00+09:00", "open", False))
            now = [f for f in of(r["fill"], price_kind="now") if f["asset"] == U]
            self.assertEqual(len(now), 1)
            self.assertTrue(now[0]["at"].startswith("2025-09-03T09:02"))
            self.assertEqual(now[0]["price"], RU.step(U, next(x.open for x in PR.load(U, root.prices) if x.date == d(9, 3)), 1))
            self.assertGreater(now[0]["value"], 0.8 * line["value"])       # 판 돈의 거의 전부
            self.assertEqual(len(of(r["broker"], type="phase")), 1)
            self.assertEqual(len(r["account"]), len(before["account"]))   # 계좌 줄은 새로 쓰지 않는다(하루 한 줄)
            # 두 번 돌려도 한 번만
            (code, said), n = quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 30)), {k: len(v) for k, v in r.items()}
            self.assertEqual((code, {k: len(v) for k, v in recs(root).items()}), (0, n))
            self.assertIn("이미 돌았다", said)
            # 다음 날: 진행 중인 봉으로 넣은 시가가 끝난 봉과 같다(바로잡기 없음), 검산 통과
            self.assertEqual(day_run(root, d(9, 4), {U: 0.95})[0], 0)
            self.assertEqual([x for x in recs(root)["broker"] if x["type"] in ("adjust", "reject")], [])
            self.assertEqual(R.check(root.path, [root.records]), [])

    def test_e2b_without_the_open_phase_the_buy_goes_out_the_next_morning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.95})[0], 0)
            self.assertEqual(day_run(root, d(9, 3), {U: 0.95})[0], 0)
            # 장 시작 뒤 단계를 안 돌림 → 다음 날 판단 때 팔린 돈(결제 전)으로 장 시작 사기
            self.assertEqual(day_run(root, d(9, 4), {U: 0.95})[0], 0)
            r = recs(root)
            buy = [o for o in of(r["order"], side="buy") if o["run"] == "2025-09-04"]
            self.assertEqual([(o["asset"], o["order_kind"], o["session"]) for o in buy], [(U, "open", "2025-09-04")])
            self.assertGreater(buy[0]["reserved"], 0.8 * of(r["account"], run="2025-09-04")[0]["value"])
            self.assertEqual(of(r["broker"], type="phase"), [])
            self.assertEqual(day_run(root, d(9, 5), {U: 0.95})[0], 0)
            self.assertTrue([f for f in recs(root)["fill"] if f["order"] == buy[0]["id"] and f["at"] == "2025-09-04T09:00:00+09:00"])
            # 판단이 돌지 않은 날의 open은 아무것도 하지 않는다
            (code, said), n = quiet(D.open_phase, root, d(9, 6), at(d(9, 6), 9, 2)), len(recs(root)["broker"])
            self.assertEqual((code, len(recs(root)["broker"])), (0, n))
            self.assertIn("판단 기록(run)이 없다", said)

    def test_e3_late_decision_goes_out_as_now_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            code, said = day_run(root, d(9, 3), {E: 0.3, U: 0.3, BTC: 0.3}, record_time=(9, 30))
            self.assertEqual(code, 0)
            self.assertIn("지금 가격으로", said)             # 늦음 경고의 뜻
            r = recs(root)
            mine = of(r["order"])
            self.assertEqual({o["asset"]: o["order_kind"] for o in mine}, {E: "now", U: "now", BTC: "now"})
            fills = {f["asset"]: f for f in of(r["fill"])}
            for a in (E, U):
                o = next(x.open for x in PR.load(a, root.prices) if x.date == d(9, 3))
                self.assertEqual((fills[a]["price"], fills[a]["price_kind"]), (RU.step(a, o, 1), "now"))
                self.assertTrue(fills[a]["at"].startswith("2025-09-03T09:30"))
            line = of(r["account"])[0]
            self.assertEqual(line["pending"], [])
            self.assertTrue(line["priced_at"][E].startswith("2025-09-03T09:30"))   # 장중이면 평가도 지금 가격
            self.assertEqual(R.check(root.path, [root.records]), [])

    def test_e4_coin_fills_at_decision_and_is_valued_at_the_live_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 3), {BTC: 0.4})[0], 0)
            r = recs(root)
            f = of(r["fill"])[0]
            self.assertEqual((f["asset"], f["price_kind"], f["pocket"], f["settle"]), (BTC, "now", "upbit", None))
            self.assertTrue(f["at"].startswith("2025-09-03T08:30"))
            self.assertEqual(f["fill_date"], "2025-09-02")              # 09:00 전이라 어제 09:00에 시작한 봉 안의 값
            line = of(r["account"])[0]
            rows = PR.load(BTC, root.prices)
            running = next(x for x in rows if x.date == d(9, 2))      # 진행 중인 봉(가짜 공급자의 '지금 값' = 그 봉의 시가)
            done = next(x for x in rows if x.date == d(9, 1))         # 08:30에 끝나 있는 마지막 봉
            self.assertEqual(line["prices"][BTC], ["2025-09-03", running.open])
            self.assertNotEqual(line["prices"][BTC][1], done.close)     # 이틀 전 봉의 종가가 아니다(P8-3)
            self.assertTrue(line["priced_at"][BTC].startswith("2025-09-03T08:30"))
            self.assertEqual(line["positions"][BTC], f["qty_units"])    # 그날 낸 지금 주문까지 반영한 상태
            self.assertAlmostEqual(line["value"], line["cash"] + f["qty_units"] * running.open / 1e8, 1)
            self.assertEqual(line["priced_at"][E], "2025-09-02T15:30:00+09:00")   # 한국 거래소는 마지막 공식 종가

    def test_e4b_when_live_prices_cannot_be_fetched(self):
        """실제 실행에서 지금 가격·호가를 못 받으면: 비트코인 주문은 거절 줄로 남고(값을 지어내지 않는다) 기록은 계속 쓰이며,
        평가는 prepare가 받은 판단 때 가격 → 마지막 끝난 봉 순서로 물러선다."""
        with tempfile.TemporaryDirectory() as tmp, use(NoLive):
            root = make_root(tmp)
            code, said = day_run(root, d(9, 3), {E: 0.3, BTC: 0.3})
            self.assertEqual(code, 0)
            self.assertIn("거절됨", said)
            r = recs(root)
            rej = of(r["broker"], type="reject")
            self.assertEqual([(x["data"]["asset"], "호가" in x["note"]) for x in rej], [(BTC, True)])
            self.assertEqual(of(r["fill"]), [])
            self.assertEqual([o["asset"] for o in of(r["order"])], [E])       # 장 시작 주문은 지금 가격이 없어도 나간다
            line = of(r["account"])[0]
            done = next(x for x in PR.load(BTC, root.prices) if x.date == d(9, 1))
            self.assertEqual(line["prices"][BTC], ["2025-09-02", done.close])   # TICK(판단 때 가격)은 그 봉보다 묵어 안 쓴다
            self.assertEqual(R.check(root.path, [root.records]), [])
        hists = {BTC: PR.view(BTC, [Row(d(9, 1), 1.0, 1.0, 1.0, 2.0, 1.0)], at(d(9, 3), 8, 30))}
        tick = {BTC: {"price": 3.0, "time": "2025-09-03T07:37:00+09:00"}}
        got = D.value_prices(hists, NoLive({}), at(d(9, 3), 8, 30), tick, False)
        self.assertEqual(got[BTC], (3.0, at(d(9, 3), 7, 37)))                    # 지금 가격이 없으면 판단 때 가격
        self.assertEqual(D.value_prices(hists, NoLive({}), at(d(9, 3), 8, 30), {}, False)[BTC][0], 2.0)

    def test_e5_moving_weight_from_exchange_to_coin_waits_for_settlement(self):
        """11-05 사례가 매일 실행에서 풀리는 모양: ETF를 판 돈은 결제일(체결 + 2영업일) 09:00 뒤의 실행에서야 업비트로 옮겨 산다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.9})[0], 0)                 # 화
            for day in (3, 4, 5, 6):                                               # 수(팔기) … 토
                self.assertEqual(day_run(root, d(9, day), {E: 0.1, BTC: 0.8})[0], 0, day)
            r = recs(root)
            total = of(r["account"], run="2025-09-03")[0]["value"]
            sale = next(f for f in of(r["fill"], side="sell") if f["asset"] == E)
            self.assertEqual((sale["at"], sale["settle"]), ("2025-09-03T09:00:00+09:00", "2025-09-05"))
            moved = {x["run"]: x["data"]["amount"] for x in of(r["broker"], type="transfer") if x["data"]["src"] == "sec"}
            bought = {f["run"]: f["value"] for f in of(r["fill"], side="buy") if f["asset"] == BTC}
            # 수: 판 돈은 못 옮긴다 — 계좌에 남아 있던 푼돈만 옮겨 산다. 목·금(결제일 09:00 전): 아무것도 못 한다
            self.assertLess(bought.get("2025-09-03", 0), 0.15 * total)
            self.assertNotIn("2025-09-04", bought)
            self.assertNotIn("2025-09-05", bought)
            self.assertNotIn("2025-09-04", moved)
            self.assertNotIn("2025-09-05", moved)
            # 토(결제일 다음 실행): 증권 → 은행 → 업비트로 옮기고 비트코인을 산다
            self.assertGreater(moved["2025-09-06"], 0.6 * total)
            self.assertGreater(bought["2025-09-06"], 0.6 * total)
            self.assertEqual([x for x in r["broker"] if x["type"] == "reject"], [])
            self.assertEqual(R.check(root.path, [root.records]), [])

    def test_problems_stop_everything(self):
        """증권사 기록이 어긋나 있으면 prepare·record·open은 아무것도 쓰지 않고 멈춘다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2))[0], 0)
            p = root.records / "fill.jsonl"
            p.write_text(p.read_text(encoding="utf-8").replace('"fee": ', '"fee": 9', 1), encoding="utf-8")
            size = {x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}
            code, said = quiet(D.prepare, root, d(9, 3), at(d(9, 3), 7, 37), fetch=False, ticker=TICK, echo=False)
            self.assertEqual(code, 1)
            self.assertIn("어긋나", said)
            self.assertEqual(quiet(D.open_phase, root, d(9, 2), at(d(9, 2), 9, 5))[0], 1)
            (root.day_dir(d(9, 3)) / "prepare.json").write_text(json.dumps({"fetch": {}, "prepared_at": "x"}), encoding="utf-8")
            inp = root.day_dir(d(9, 2)) / "filled.json"
            self.assertEqual(quiet(D.record, root, d(9, 3), at(d(9, 3), 8, 30), inp)[0], 1)
            self.assertEqual({x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}, size)
            self.assertNotEqual(B.status(root.path)[1], 0)


class Plan(unittest.TestCase):
    """주문 계획(rebalance)만: 증권사에 낸 요청과 내지 않은 것."""

    def setUp(self):
        from tests.test_broker import bars
        self.rows = bars()
        self.q = Q.BarQuotes(self.rows)
        self.day = dt.date(2026, 11, 5)
        self.px = {a: [x for x in self.rows[a] if x.date < self.day][-1].close for a in (E, U, "005930")}
        self.px[BTC] = next(x for x in self.rows[BTC] if x.date == dt.date(2026, 11, 4)).open

    def at(self, h, m):
        return dt.datetime.combine(self.day, dt.time(h, m), C.KST)

    def broker(self, **start):
        b = B.Broker("t", self.q)
        self.assertTrue(b.start(self.at(7, 0), **start).ok)
        return b

    def test_no_cash_means_only_the_sell_goes_out_and_no_reject(self):
        b = self.broker(units={E: 100}, cost={E: 8_000_000})
        plan = RB.rebalance(b, {E: 0.0, U: 0.9}, self.px, RB.Clock(self.at(8, 30)))
        self.assertEqual([(o["asset"], o["side"], o["order_kind"]) for o in plan.of("order")], [(E, "sell", "open")])
        self.assertEqual(plan.of("broker", "reject"), [])
        self.assertEqual(plan.short, [U])
        self.assertTrue(any("0주만" in n for n in plan.notes))
        self.assertEqual(plan.of("broker", "plan")[0]["data"]["targets"], {E: 0.0, U: 0.9})
        # 장 시작 뒤(09:02): 팔린 것이 장부에 들어오고 그 돈으로 지금 산다. plan 줄은 다시 쓰지 않는다
        prices = dict(self.px, **{a: next(x.open for x in self.rows[a] if x.date == self.day) for a in (E, U)})
        more = RB.rebalance(b, {E: 0.0, U: 0.9}, prices, RB.Clock(self.at(9, 2)), phase="open")
        self.assertEqual([(o["asset"], o["side"], o["order_kind"]) for o in more.of("order")], [(U, "buy", "now")])
        self.assertEqual(len(more.of("fill")), 2)
        self.assertEqual(more.of("broker", "plan"), [])
        self.assertGreater(b.holdings()[U] * prices[U], 0.85 * RB.total_value(b, prices))
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])

    def test_quantity_rules_match_the_old_ledger(self):
        """문턱·내림·가까운 주 수·걸린 주문을 든 것으로: 옛 장부(ledger.Account.plan)와 같은 수량."""
        b = self.broker(bank=10_000_000)
        old = L.Account("x")
        targets = {E: 0.25, U: 0.25, "005930": 0.1}
        want = {o.asset: o.qty_units for o in old.plan(self.day, targets, self.px, 0.02)}
        plan = RB.rebalance(b, targets, self.px, RB.Clock(self.at(8, 30)))
        self.assertEqual({o["asset"]: o["qty_units"] for o in plan.of("order")}, want)
        self.assertEqual({o["limit"] for o in plan.of("order")}, {float(RU.default_open_limit(a, self.px[a])) for a in want})
        again = RB.rebalance(b, targets, self.px, RB.Clock(self.at(8, 40)))      # 걸린 주문을 든 것으로 치니 다시 내지 않는다
        self.assertEqual(again.of("order"), [])
        small = RB.rebalance(b, dict(targets, **{E: 0.26}), self.px, RB.Clock(self.at(8, 50)))   # 1%p 차이 < 문턱 2%
        self.assertEqual(small.of("order"), [])

    def test_money_moves_bank_first_then_the_other_pocket_with_a_floor(self):
        b = self.broker(bank=1_000_000, upbit=2_050_000, sec=30_000)
        # 증권 계좌에서 살 돈: 은행 100만 원 먼저, 모자란 몫은 업비트의 남는 돈(자기 살 몫을 뺀)을 은행을 거쳐
        plan = RB.rebalance(b, {E: 0.6}, self.px, RB.Clock(self.at(8, 30)))
        moves = [(x["data"]["src"], x["data"]["dst"], x["data"]["fee"]) for x in plan.of("broker", "transfer")]
        self.assertEqual(moves, [("bank", "sec", 0), ("upbit", "bank", 1_000), ("bank", "sec", 0)])
        self.assertEqual(plan.of("broker", "reject"), [])
        self.assertEqual(b.pockets()["bank"], 0)
        # 푼돈은 수수료를 내고 옮기지 않는다
        b = self.broker(upbit=50_000, sec=760_000)
        plan = RB.rebalance(b, {E: 0.9}, self.px, RB.Clock(self.at(8, 30)))
        self.assertEqual(plan.of("broker", "transfer"), [])
        self.assertTrue(any("수수료" in n for n in plan.notes))
        self.assertEqual(b.pockets()["upbit"], 50_000)

    def test_weekend_and_dead_minutes(self):
        b = self.broker(bank=5_000_000)
        sat = dt.datetime(2026, 11, 7, 8, 30, tzinfo=C.KST)
        px = {E: [x for x in self.rows[E] if x.date < sat.date()][-1].close}
        plan = RB.rebalance(b, {E: 0.5}, px, RB.Clock(sat))
        self.assertEqual([(o["order_kind"], o["session"]) for o in plan.of("order")], [("open", "2026-11-09")])   # 다음 장 시작
        b = self.broker(bank=5_000_000)
        dead = dt.datetime(2026, 11, 5, 15, 25, tzinfo=C.KST)                         # 장 마감 동시호가: 아무 주문도 못 낸다
        plan = RB.rebalance(b, {E: 0.5}, self.px, RB.Clock(dead))
        self.assertEqual((plan.of("order"), plan.of("broker", "reject")), ([], []))
        self.assertTrue(plan.notes)
        self.assertEqual(b.market(dead), "")
        self.assertEqual([b.market(self.at(*t)) for t in ((8, 59), (9, 1), (15, 19), (15, 31))], ["open", "now", "now", "open"])

    def test_clock_gives_each_request_its_own_second(self):
        c = RB.Clock(self.at(8, 30))
        self.assertEqual([c.now(), c.now()], [self.at(8, 30) + dt.timedelta(seconds=s) for s in (1, 2)])
        live = RB.Clock(C.now_kst() - dt.timedelta(hours=1), live=True)
        a, b_ = live.now(), live.now()
        self.assertGreater(b_, a)
        self.assertLess(abs((C.now_kst() - a).total_seconds()), 5)                     # 진짜 폴더에서는 실제 시각


class Replan(unittest.TestCase):
    """주문 계획 2회차(2026-11-10, docs/worklog/2026-11-10-broker-whatif.md 덧붙임): ① 걸어 둔 주문은 목표가 문턱 밖으로 바뀌면
    취소하고 다시 낸다(문턱 안이면 그대로, 취소 못 하는 장중 걸린 사기는 든 것으로) ② 돈이 모자란 날은 목표와 차이가 큰 것부터
    산다(입력 dict의 차례와 상관없이), 돈 옮기기도 같은 차례. 고치기 전 코드에서 r1·r4·r5·r6이 실패하는 것을 보고 고쳤다."""

    def setUp(self):
        from tests.test_broker import bars, replace_bar
        self.bars, self.replace_bar = bars, replace_bar
        self.rows = bars()
        self.q = Q.BarQuotes(self.rows)

    def at(self, day, h, m):
        return dt.datetime.combine(day, dt.time(h, m), C.KST)

    def px(self, before, rows=None):
        rows = rows or self.rows
        out = {a: [x for x in rows[a] if x.date < before][-1].close for a in (E, U, "005930")}
        out[BTC] = next(x for x in rows[BTC] if x.date == before - dt.timedelta(days=1)).open
        return out

    def broker(self, when, q=None, **start):
        b = B.Broker("t", q or self.q)
        self.assertTrue(b.start(when, **start).ok)
        return b

    def test_r1_pending_buy_is_cancelled_and_reissued_when_the_target_drops(self):
        wed, thu = dt.date(2026, 11, 4), dt.date(2026, 11, 5)
        b = self.broker(self.at(wed, 7, 0), bank=10_000_000)
        first = RB.rebalance(b, {E: 0.5}, self.px(thu), RB.Clock(self.at(wed, 16, 0)))   # 장 마감 뒤 → 다음 장 시작 사기
        (o,) = first.of("order")
        self.assertEqual((o["order_kind"], o["session"]), ("open", "2026-11-05"))
        plan = RB.rebalance(b, {E: 0.25}, self.px(thu), RB.Clock(self.at(thu, 8, 30)))   # 09:00 전: 아직 취소할 수 있다
        self.assertEqual([x["data"]["order"] for x in plan.of("broker", "cancel")], [o["id"]])
        (n,) = plan.of("order")
        self.assertEqual((n["side"], n["order_kind"], n["session"]), ("buy", "open", "2026-11-05"))
        self.assertAlmostEqual(n["qty_units"], o["qty_units"] / 2, delta=1)
        self.assertEqual([p.id for p in b.pending()], [n["id"]])
        self.assertEqual(plan.of("broker", "reject"), [])
        zero = RB.rebalance(b, {E: 0.0}, self.px(thu), RB.Clock(self.at(thu, 8, 40)))     # 목표 0: 취소만
        self.assertEqual([x["data"]["order"] for x in zero.of("broker", "cancel")], [n["id"]])
        self.assertEqual((zero.of("order"), b.pending()), ([], []))
        self.assertTrue(any("취소" in s for s in RB.describe(zero)))
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])

    def test_r2_target_inside_the_band_leaves_the_pending_order_alone(self):
        wed, thu = dt.date(2026, 11, 4), dt.date(2026, 11, 5)
        b = self.broker(self.at(wed, 7, 0), bank=10_000_000)
        (o,) = RB.rebalance(b, {E: 0.5}, self.px(thu), RB.Clock(self.at(wed, 16, 0))).of("order")
        for k, w in enumerate((0.5, 0.51, 0.49)):                                       # 날마다 다시 내지 않는다
            plan = RB.rebalance(b, {E: w}, self.px(thu), RB.Clock(self.at(thu, 8, 30 + k)))
            self.assertEqual((plan.of("broker", "cancel"), plan.of("order")), ([], []), w)
        self.assertEqual([p.id for p in b.pending()], [o["id"]])

    def test_r3_an_order_left_on_the_book_during_the_session_counts_as_held(self):
        """장 시작에 시가 ≥ 한도라 그날 장중에 걸린 사기는 취소할 수 없다(체결 시각 09:00이 지남) — 그대로 두고 든 것으로 친다."""
        wed, thu = dt.date(2026, 11, 4), dt.date(2026, 11, 5)
        prev = [x for x in self.rows[E] if x.date == wed][0].close
        rows = self.replace_bar(self.rows, E, thu, open=prev * 1.2, high=prev * 1.25, low=prev * 1.15, close=prev * 1.2)
        b = self.broker(self.at(wed, 7, 0), q=Q.BarQuotes(rows), bank=10_000_000)
        (o,) = RB.rebalance(b, {E: 0.5}, self.px(thu, rows), RB.Clock(self.at(wed, 16, 0))).of("order")
        now = dict(self.px(thu, rows), **{E: prev * 1.2})
        for w in (0.5, 0.2, 0.0):
            plan = RB.rebalance(b, {E: w}, now, RB.Clock(self.at(thu, 9, 5 + int(w * 10))), phase="open")
            self.assertEqual(plan.of("broker", "cancel"), [], w)
            self.assertEqual(plan.of("broker", "reject"), [], w)
            self.assertEqual([x for x in plan.of("order") if x["asset"] == E], [], w)   # 든 것으로 쳐서 더 사지도, 못 팔지도
            self.assertEqual([p.id for p in b.pending()], [o["id"]], w)

    def plan_short(self, targets, start, when=None):
        thu = dt.date(2026, 11, 5)
        b = self.broker(self.at(thu, 7, 0), **start)
        plan = RB.rebalance(b, targets, self.px(thu), RB.Clock(when or self.at(thu, 8, 30)))
        got = [(x["asset"], x["side"], x["order_kind"], x["qty_units"], x.get("amount")) for x in plan.of("order")]
        return b, plan, got

    def test_r4_order_of_the_target_dict_does_not_matter_when_money_is_short(self):
        """돈이 모자란 날(장 시작 사기는 한도 1.05배만큼 돈을 잡는다): 목표와 차이가 큰 것부터 산다 — 입력의 차례와 상관없다."""
        start = {"bank": 3_000_000}
        _, p1, a = self.plan_short({E: 0.7, U: 0.3}, start)
        _, p2, b_ = self.plan_short({U: 0.3, E: 0.7}, start)
        self.assertEqual(sorted(a), sorted(b_))
        self.assertEqual(p1.short, p2.short)
        self.assertEqual(p1.short, [U])                                                 # 차이가 작은 쪽이 덜 산다
        qe = next(x[3] for x in a if x[0] == E)
        self.assertEqual(qe, int(0.7 * 3_000_000 // (self.px(dt.date(2026, 11, 5))[E] * (1 + 0.00015))))

    def test_r5_the_bigger_gap_gets_the_money_first_across_pockets(self):
        """증권 계좌에만 돈이 있는데 비트코인(업비트) 사기와 증권 계좌 사기가 다툴 때, 목표와 차이가 큰 비트코인이 먼저 돈을 받는다
        (고치기 전: 증권 계좌가 자기 살 몫을 먼저 남겨 비트코인이 덜 샀다 — 2026-11-05~10 대입의 (가)가 그랬다)."""
        b, plan, got = self.plan_short({BTC: 0.7, E: 0.5}, {"sec": 2_000_000})
        coin = next(f for f in plan.of("fill") if f["asset"] == BTC)
        self.assertGreater(coin["value"], 0.69 * 2_000_000)
        self.assertIn(E, plan.short)
        self.assertNotIn(BTC, plan.short)
        moves = [(x["data"]["src"], x["data"]["dst"]) for x in plan.of("broker", "transfer")]
        self.assertEqual(moves[:2], [("sec", "bank"), ("bank", "upbit")])
        # 차례가 뒤집히면(증권 계좌 쪽 차이가 더 크면) 증권 계좌가 먼저
        b, plan, got = self.plan_short({BTC: 0.5, E: 0.7}, {"sec": 2_000_000})
        self.assertIn(BTC, plan.short)
        self.assertNotIn(E, plan.short)
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])

    def test_r6_weekend_order_shrinks_before_monday(self):
        """대입 보고서의 11-01·11-02 경우를 작게: 토요일에 낸 월요일 장 시작 사기 18주를 일요일에 9주로 줄이려 하면 — 고치기 전에는
        걸린 사기를 팔 수 없어 18주가 그대로 사졌다. 이제 취소하고 9주로 다시 내고, 월요일 09:00에 9주만 사진다."""
        sat, sun, mon = dt.date(2026, 11, 7), dt.date(2026, 11, 8), dt.date(2026, 11, 9)
        b = self.broker(self.at(sat, 7, 0), bank=10_000_000)
        p = self.px(mon)
        w18 = 18 * p[E] * (1 + 0.00015) / 10_000_000 + 0.0001
        (o,) = RB.rebalance(b, {E: w18}, p, RB.Clock(self.at(sat, 8, 30))).of("order")
        self.assertEqual((o["qty_units"], o["session"]), (18, "2026-11-09"))
        plan = RB.rebalance(b, {E: 9 * p[E] / 10_000_000 + 0.0001}, p, RB.Clock(self.at(sun, 8, 30)))
        self.assertEqual([x["data"]["order"] for x in plan.of("broker", "cancel")], [o["id"]])
        self.assertEqual([(x["qty_units"], x["session"]) for x in plan.of("order")], [(9, "2026-11-09")])
        b.sync(self.at(mon, 9, 5))
        self.assertEqual(b.holdings().get(E), 9)
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])


    def test_r7_daily_run_cancels_and_the_report_says_so(self):
        """매일 실행에서: 토요일에 낸 월요일 장 시작 사기를 일요일 목표가 줄면 취소하고 다시 낸다 — 거절 없음, 검산 통과, 일지에 한 줄."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 6), {E: 0.9})[0], 0)                  # 토
            self.assertEqual(day_run(root, d(9, 7), {E: 0.4})[0], 0)                  # 일
            r = recs(root)
            sat = of(r["order"], run="2025-09-06", asset=E)
            self.assertEqual([x["data"]["order"] for x in of(r["broker"], type="cancel")], [o["id"] for o in sat])
            (new,) = of(r["order"], run="2025-09-07", asset=E)
            self.assertLess(new["qty_units"], sat[0]["qty_units"])
            self.assertEqual(of(r["account"], run="2025-09-07")[0]["pending"], [new["id"]])
            self.assertEqual(of(r["broker"], type="reject"), [])
            every = [x for x in r["broker"] if x["type"] == "cancel"]                  # 계좌 넷(계산기 계좌도 목표가 바뀐다)
            self.assertEqual(R.read(root.records, "run")[-1]["steps"]["broker"]["cancels"], len(every))
            self.assertIn("걸어 둔 주문을 취소했다", RP.daily(root.records, d(9, 7)))
            self.assertEqual(R.check(root.path, [root.records]), [])


class Switch(unittest.TestCase):
    """갈아타는 날(17절, 2026-11-10 3회 사용자 결정: 옛 장부의 계좌는 비교용으로 남기고, 옛 주문을 그대로 넣어 다시 계산한 잔고에서
    이어 간다). 3회 전의 뜻(옛 대기 주문을 옛 규칙으로 끝내고 옛 장부의 보유·현금으로 start)에서 바꾼 것은 보고서
    docs/worklog/2026-11-10-broker-carryover.md '시험을 바꾼 것' 표."""

    def old_day(self, root, day, weights=None):
        """갈아타지 못하게 막아 옛 장부로 하루를 돌린다(옛 모양 기록을 만든다)."""
        with mock.patch.object(D, "carry_over", lambda *a, **k: (None, "시험: 막아 둠")):
            return day_run(root, day, weights)

    @staticmethod
    def expected(root, day, when):
        """갈아타기 전 기록·시세로 따로 계산한 그대로 대입(갈아타는 실행이 쓴 것과 같은지 견준다)."""
        from market import whatif as W
        before = {k: R.read(root.records, k) for k in ("run", "order")}
        rows = {p.stem: PR.load(p.stem, root.prices) for p in sorted(root.prices.glob("*.csv"))}
        return W.carry(before, rows, when, D.quotes_for(root), day)

    @staticmethod
    def state(lines, aid):
        b = B.Broker(aid, Q.BarQuotes({}), lines)
        return b.pockets(), b.holdings(), sorted(p.id for p in b.pending())

    def test_e6_synthetic_switch_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            code, said = self.old_day(root, d(9, 2), {E: 0.5, BTC: 0.4})
            self.assertEqual(code, 0)
            self.assertIn("옛 장부로 돌았다", said)
            r = recs(root)
            self.assertEqual((r["broker"], [o for o in r["order"] if "pocket" in o]), ([], []))
            self.assertTrue(all(o["fill_rule"] == "open" for o in r["order"]))
            # 다음 날 어제 09:00 비트코인 봉(진행 중)을 못 받으면 그대로 넣기를 끝낼 수 없다: 계좌 넷 모두 그날도 옛 장부로 돈다.
            # 이날은 한국 묶음을 팔아 그 돈으로 미국 묶음을 같은 09:00에 사는 옛 주문을 낸다(옛 장부는 치러 주고 증권사는 거절 — F137)
            with use(NoRunningCoin):
                code, said = day_run(root, d(9, 3), {E: 0.05, U: 0.5, BTC: 0.4})
            self.assertEqual(code, 0)
            self.assertIn("받지 못함", said)
            r = recs(root)
            self.assertEqual(r["broker"], [])
            self.assertNotIn("pockets", r["account"][-1])
            self.assertEqual(R.read(root.records, "run")[-1]["steps"]["broker"]["mode"], "ledger")
            brief = (root.day_dir(d(9, 3)) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("옛 장부로 돈다", brief)
            # 그다음 날: 옛 주문을 그대로 넣어 다시 계산한 잔고에서 이어 간다
            before = recs(root)
            old = [o for o in before["order"] if "pocket" not in o]
            want = self.expected(root, d(9, 4), at(d(9, 4), 8, 30))
            code, said = day_run(root, d(9, 4))
            self.assertEqual(code, 0, said)
            self.assertIn("다시 계산한 잔고에서 이어 간다", said)
            r = recs(root)
            starts = [x for x in r["broker"] if x["type"] == "start"]
            self.assertEqual([(x["account"], x["at"], x["run"], x["data"]) for x in starts],
                             [(a, at(d(9, 2), 8, 30).isoformat(), "2025-09-04",
                               {"pockets": {"bank": C.START_CASH, "sec": 0, "upbit": 0}, "units": {}, "cost": {}})
                              for a in C.LIVE_ACCOUNTS])
            # 옛 주문마다 다시 넣은 줄(주문 또는 거절) 하나, run은 갈아타는 날·시각은 그때, id는 옛 주문과 겹치지 않는다
            again = [o for o in r["order"] if o.get("replay_of")] + [x for x in r["broker"] if x["type"] == "reject"
                                                                     and x["data"].get("replay_of")]
            refs = sorted(x.get("replay_of") or x["data"]["replay_of"] for x in again)
            self.assertEqual(refs, sorted(o["id"] for o in old))
            self.assertTrue(all(x["run"] == "2025-09-04" for x in again))
            self.assertFalse({o["id"] for o in old} & {o["id"] for o in r["order"] if "pocket" in o})
            rejects = [x for x in again if x.get("type") == "reject"]
            self.assertTrue(rejects, "같은 09:00에 판 돈으로 사는 옛 주문이 거절되어야 한다(F137)")
            # 증권사 기록 = 따로 계산한 그대로 대입(그날 판단 전까지)과 같다
            lines = {k: r[k] for k in B.KINDS}
            for aid in C.LIVE_ACCOUNTS:
                cut = next(x["seq"] for x in r["broker"] if x["account"] == aid and x["type"] == "phase")
                upto = {k: [x for x in v if x.get("seq", 10**9) <= cut and x.get("account") == aid] for k, v in lines.items()}
                b = want.brokers[aid]
                self.assertEqual(self.state(upto, aid), (b.pockets(), b.holdings(), sorted(p.id for p in b.pending())), aid)
            # 옛 장부 값과 다시 계산한 값이 다르다(받아지지 않은 옛 주문 때문에)
            old_v = [x for x in r["account"] if x["account"] == "live:hand" and "seq" not in x][-1]["value"]
            new_v = of(r["account"], run="2025-09-04")[0]["value"]
            self.assertNotAlmostEqual(old_v, new_v, places=0)
            # 옛 계좌·주문·체결 줄은 그대로 남고(비교용), 갈아타는 날의 계좌 줄은 증권사의 것 하나, 옛 모양 체결 줄을 더 쓰지 않는다
            self.assertEqual([x for x in r["account"] if "seq" not in x], [x for x in before["account"]])
            self.assertEqual([x for x in r["fill"] if "seq" not in x], before["fill"])
            self.assertEqual([x["account"] for x in r["account"] if x["run"] == "2025-09-04"], list(C.LIVE_ACCOUNTS))
            self.assertFalse([o for o in r["order"] if "pocket" not in o and o["run"] == "2025-09-04"])   # 새 주문은 증권사로만
            self.assertEqual(R.check(root.path, [root.records]), [])
            # 이어서 장 시작 뒤 단계와 다음 날
            self.assertEqual(quiet(D.open_phase, root, d(9, 4), at(d(9, 4), 9, 3))[0], 0)
            self.assertEqual(day_run(root, d(9, 5))[0], 0)
            self.assertEqual(R.check(root.path, [root.records]), [])
            # 갈아탄 뒤 옛 장부는 증권사 주문을 읽지 않는다
            r = recs(root)
            self.assertTrue(all("seq" not in x for k in ("order", "account") for x in D.old_lines(r, k)))
            # 일지: 옛 날짜는 옛 문장, 갈아탄 날은 '다시 계산' 문장과 표 칸(옛 값과 새 값의 차이를 하루 손익으로 보이지 않는다)
            self.assertIn("값은 주문 뒤 처음 정해지는 시가로 치른다", RP.daily(root.records, d(9, 2)))
            text = RP.daily(root.records, d(9, 4))
            self.assertIn("오늘부터 계좌를 실제 규칙으로 다시 계산한 잔고에서 이어 간다", text)
            self.assertIn("받아지지 않았다", text)
            self.assertIn("| **계좌 합계** | | **", text)
            self.assertIn("다시 계산(옛 장부", text)
            self.assertNotRegex(re.sub(r"\[\[채울 것.*?\]\]|<!--.*?-->", "", text, flags=re.S), r"None|nan|\[\[")
            self.assertNotIn("지난 주문이 치러진 값", text)   # 다시 넣은 옛 주문의 체결은 '오늘 치러진 것'이 아니다
            nxt = RP.daily(root.records, d(9, 5))
            self.assertNotIn("다시 계산", nxt.split("| 대상 |")[1])
            self.assertIn("계좌는", nxt)

    def test_e6b_a_crash_inside_the_recompute_means_the_old_ledger_that_day(self):
        """2회차(메인 결정 B): 다시 계산(whatif.carry) 안에서 예외가 나면 멈추지 않고 그날은 계좌 넷 모두 옛 장부로 돈다 — 예측·채점
        기록(실험의 본체)이 비지 않게. briefing과 run notes에 '갈아타지 못함'과 까닭. 다음 날 정상이면 갈아탄다."""
        from market import whatif as W
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(self.old_day(root, d(9, 2), {E: 0.5, BTC: 0.4})[0], 0)

            def boom(*a, **k):
                raise ZeroDivisionError("시험: 다시 계산 안의 버그")
            with mock.patch.object(W, "carry", boom):
                code, said = day_run(root, d(9, 3), {E: 0.3, BTC: 0.3})
            self.assertEqual(code, 0, said)
            r = recs(root)
            self.assertEqual(r["broker"], [])
            self.assertTrue(of(r["order"], run="2025-09-03"))                 # 옛 장부 주문(그날 판단은 남았다)
            run = R.read(root.records, "run")[-1]
            self.assertEqual(run["steps"]["broker"]["mode"], "ledger")
            self.assertTrue(any("갈아타지 못함" in n and "ZeroDivisionError" in n for n in run["notes"]), run["notes"])
            self.assertTrue(R.read(root.records, "prediction"))
            brief = (root.day_dir(d(9, 3)) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("갈아타지 못함", brief.split("## 시세")[0])
            self.assertIn("ZeroDivisionError", brief.split("## 시세")[0])
            code, said = day_run(root, d(9, 4))
            self.assertEqual(code, 0, said)
            self.assertIn("다시 계산한 잔고에서 이어 간다", said)
            self.assertEqual(R.check(root.path, [root.records]), [])

    def test_e6_real_records_copy_switches_cleanly(self):
        """지금 저장소의 기록·시세를 임시 폴더에 복사해(진짜 폴더는 읽기만) 다음 판단을 흉내 낸다. 그다음 거래일들의 가짜 봉은
        임시 폴더 시세 파일에만 덧붙인다. 이미 갈아탄 뒤의 저장소라면 건너뛴다."""
        if not (C.RECORDS / "run.jsonl").exists() or any(x["type"] == "start" for x in R.read(C.RECORDS, "broker")):
            self.skipTest("진짜 기록이 없거나 이미 갈아탄 뒤")
        with tempfile.TemporaryDirectory() as tmp:
            root = D.copy_for_simulation(Path(tmp) / "repo", C.PRICES)
            root.records.mkdir(parents=True)
            for p in C.RECORDS.glob("*.jsonl"):
                shutil.copyfile(p, root.records / p.name)
            last_run = dt.date.fromisoformat(max(x["run"] for x in R.read(root.records, "run")))
            end = last_run + dt.timedelta(days=9)
            for p in sorted(root.prices.glob("*.csv")):       # 가짜 봉: 마지막 종가에서 호가 몇 칸씩 오르는 값
                a = p.stem
                rows = PR.load(a, root.prices)
                day, c, new = rows[-1].date + dt.timedelta(days=1), rows[-1].close, []
                while day <= end:
                    if not (C.on_exchange(a) and day.weekday() >= 5):
                        t = RU.tick(a, c)
                        o = RU.floor_tick(a, c) + t
                        new.append(Row(day, o, o + 3 * t, o - 3 * t, o + t, 1000.0, "fake", "2026-01-01T00:00:00+09:00"))
                        c = o + t
                    day += dt.timedelta(days=1)
                PR.append(a, new, root.prices)
            # 옛 주문을 모두 낼 수 있는 첫 아침을 고른다(그 09:00 비트코인 값을 받을 수 있어야 한다)
            day = next(x for x in (last_run + dt.timedelta(days=i) for i in range(1, 6))
                       if D.compute(root, x, at(x, 7, 37), {}, {}).switched)
            before = recs(root)
            old = [o for o in before["order"] if "pocket" not in o]
            want = self.expected(root, day, at(day, 8, 30))

            def real(inp):   # 진짜 날짜의 검사(조사 as_of, 돌아보기 '다음엔', 든 종목)에 맞춘 가짜 입력
                for r in inp["research"]:
                    r["as_of"] = f"{day}T07:20+09:00"
                for rs in inp.get("stock_readings", {}).values():
                    for r in rs.values():
                        r.update(value="아니오", why="가짜: 최근 실적 없음", places=["P_dart"])
                inp["reading_checks"] = []
                for rv in inp["reviews"]:
                    rv["reason"] += ". 다음엔: 같은 조각이 켜지면 조건을 같이 적는다"
                for a in inp["weights"]:
                    inp["weights"][a] = {"target": 0.0 if C.is_stock(a) else 0.3, "reason": "가짜 비중"}

            code, said = day_run(root, day, edit=real)
            self.assertEqual(code, 0, said)
            self.assertIn("다시 계산한 잔고에서 이어 간다", said)
            r = recs(root)
            first = min((x for x in R.read(root.records, "run") if x.get("mode") == "live"),
                        key=lambda x: x["run"])["steps"]["decided_at"]
            for x in (x for x in r["broker"] if x["type"] == "start"):     # 계좌 넷: 첫 옛 실행 시각의 은행 1,000만 원
                self.assertEqual((x["at"], x["data"]["pockets"], x["data"]["units"]),
                                 (first, {"bank": C.START_CASH, "sec": 0, "upbit": 0}, {}))
            self.assertEqual({x["account"] for x in r["broker"] if x["type"] == "start"}, set(C.LIVE_ACCOUNTS))
            refs = sorted([o["replay_of"] for o in r["order"] if o.get("replay_of")]
                          + [x["data"]["replay_of"] for x in r["broker"] if x["type"] == "reject" and x["data"].get("replay_of")])
            self.assertEqual(refs, sorted(o["id"] for o in old))            # 옛 주문 하나에 다시 넣은 줄 하나
            for aid in C.LIVE_ACCOUNTS:   # 증권사 기록 = 따로 계산한 그대로 대입
                cut = next(x["seq"] for x in r["broker"] if x["account"] == aid and x["type"] == "phase")
                upto = {k: [x for x in r[k] if x.get("seq", 10**9) <= cut and x.get("account") == aid] for k in B.KINDS}
                b = want.brokers[aid]
                self.assertEqual(self.state(upto, aid), (b.pockets(), b.holdings(), sorted(p.id for p in b.pending())), aid)
            # 옛 주문 줄의 체결 값: 장 시작 = 그 봉의 시가, 장 마감 = 그 봉의 종가(옛 체결 줄이 있는 것은 그 체결 날짜의 봉)
            fills = {f["order"]: f for f in r["fill"] if "seq" in f}
            olds = {f["order"]: f for f in before["fill"]}
            for o in (o for o in r["order"] if o.get("replay_of") and o["order_kind"] in ("open", "close")):
                f = fills.get(o["id"])
                if f is None or f["price_kind"] == "limit":
                    continue
                bar = next(x for x in PR.load(o["asset"], root.prices) if x.date.isoformat() == f["fill_date"])
                self.assertEqual(f["price"], bar.open if o["order_kind"] == "open" else bar.close, o["id"])
                was = olds.get(o["replay_of"])
                if was and was["qty_units"]:
                    self.assertEqual((f["fill_date"], f["price"]), (was["fill_date"], was["price"]), o["id"])
            self.assertEqual(R.check_folder(root.records), [])
            self.assertEqual(B.check_folder(root.records, root.prices)[0], [])       # 증권사 검산(옛 주문과 하나씩)
            # 이어서 장 시작 뒤 단계(영업일이면)와 다음 날
            if day.weekday() < 5:
                (code, said), _ = quiet(D.open_phase, root, day, at(day, 9, 2)), None
                self.assertEqual(code, 0, said)
            nxt = day + dt.timedelta(days=1)
            code, said = day_run(root, nxt, edit=lambda inp: (real(inp), [r.update(as_of=f"{nxt}T07:20+09:00")
                                                                      for r in inp["research"]]))
            self.assertEqual(code, 0, said)
            self.assertEqual(B.check_folder(root.records, root.prices)[0], [])
            self.assertEqual(R.check_folder(root.records), [])
            # 일지: 갈아탄 날은 '다시 계산' 문장으로 숫자가 다 차고, 옛 날짜 일지의 표는 저장소에 있는 것과 같다
            text = RP.daily(root.records, day)
            self.assertIn("현금은 은행", text)
            self.assertIn("오늘부터 계좌를 실제 규칙으로 다시 계산한 잔고에서 이어 간다", text)
            self.assertNotRegex(re.sub(r"\[\[채울 것.*?\]\]", "", text), r"None|nan|\[\[")
            for old_r in sorted(C.REPORTS.glob("2026-??-??.md"))[-3:]:
                when = dt.date.fromisoformat(old_r.stem)
                if when > last_run:
                    continue
                table = lambda s: [x for x in s.splitlines() if x.startswith("|")]
                self.assertEqual(table(RP.daily(root.records, when)), table(old_r.read_text(encoding="utf-8")), old_r.name)


class Report(unittest.TestCase):
    def test_e7_report_from_broker_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.5, BTC: 0.3})[0], 0)
            self.assertEqual(day_run(root, d(9, 3), {E: 0.1, U: 0.5, BTC: 0.3})[0], 0)
            quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 2))
            self.assertEqual(day_run(root, d(9, 4), {E: 0.1, U: 0.5, BTC: 0.3})[0], 0)
            first = RP.daily(root.records, d(9, 2))
            self.assertIn("비트코인(업비트) 약 300만 원어치를 바로 샀다(그 순간의 실제 가격)", first)
            self.assertIn("KODEX 200 약 500만 원어치를 사는 주문을 넣었다(오늘 장이 열리는 09:00 가격에 치른다)", first)
            self.assertIn("아직 치러지지 않은 주문", first)
            self.assertIn("현금은 은행", first)
            # 옮긴 돈은 사는 차례(목표와 차이가 큰 것부터 — 2026-11-10 2회차)대로: KODEX 200(차이 0.5)이 비트코인(0.3)보다 먼저
            self.assertIn("오늘 옮긴 돈: 은행 → 증권 계좌 약 525만 원, 은행 → 업비트 약 300만 원", first)
            self.assertIn("팔 때 떼는 세금(미국 묶음을 팔아 남긴 이익의 15.4%)", first)
            self.assertNotIn("계산하지 않았다", first)
            self.assertIn("비트코인(업비트) 9월 2일 08:30", first)
            second = RP.daily(root.records, d(9, 3))
            # 장 시작 뒤 단계(계좌 줄 뒤)의 체결은 그날 표에 섞이지 않는다: 표의 보유 = 계좌 줄의 보유
            line = of(recs(root)["account"], run="2025-09-03")[0]
            self.assertIn(f"| KODEX 200 | {line['positions'][E]}주 |", second)
            self.assertIn("지난 주문이 치러진 값: KODEX 200 사기", second)
            third = RP.daily(root.records, d(9, 4))
            self.assertIn("TIGER 미국S&P500 사기", third.split("지난 주문이 치러진 값")[1].splitlines()[0])
            self.assertIn("증권 계좌에서 판 돈은 이틀 뒤", third)
            for text in (first, second, third):
                body = re.sub(r"\[\[채울 것.*?\]\]|<!--.*?-->", "", text, flags=re.S)
                self.assertNotRegex(body, r"None|nan|\[\[|체결|결제|호가")          # 숫자가 다 찼고, 전문용어를 늘리지 않는다
            # 표의 합이 맞는다: 대상 평가액 + 현금 = 계좌 합계
            for day in (2, 3, 4):
                line = of(recs(root)["account"], run=f"2025-09-0{day}")[0]
                text = RP.daily(root.records, d(9, day))
                self.assertIn(f"| **계좌 합계** | | **{line['value']:,.0f}원** |", text)
                self.assertIn(f"| 현금 | | {line['cash']:,.0f}원 |", text)
            self.assertEqual(RP.write(root, d(9, 4)), 0)
            self.assertEqual(RP.check(root, d(9, 4)), 1)                                  # 채울 자리가 남아 있다
            weekly = RP.weekly(root.records, d(9, 7), root.places) if of(recs(root)["account"], run="2025-09-07") else ""
            self.assertNotIn("약 0원", weekly)

    def test_reject_reasons_are_put_in_plain_words(self):
        self.assertEqual(RP.reject_reason("돈이 모자람: 필요 1,000원 > 주문 가능 금액 0원 (upbit)"), "그때 계좌에 쓸 수 있는 돈이 모자라서")
        self.assertEqual(RP.reject_reason("지금 호가를 받지 못함"), "그 순간의 가격을 받지 못해서")
        self.assertEqual(RP.reject_reason("처음 보는 이유"), "증권사 규칙에 맞지 않아서")
        with tempfile.TemporaryDirectory() as tmp, use(NoLive):
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 3), {E: 0.3, BTC: 0.3})[0], 0)
            text = RP.daily(root.records, d(9, 3))
            self.assertIn("받아들여지지 않은 주문이 비트코인(업비트) 사기(그 순간의 가격을 받지 못해서) 있었다", text)


class Structure(unittest.TestCase):
    def test_e8_only_the_broker_makes_order_fill_and_broker_lines(self):
        """갈아탄 뒤 길에서 주문·체결·증권사 사건 줄을 만드는 코드는 broker.py뿐이다. 예외: 옛 장부(ledger.py — 갈아타기 전 기록과
        갈아타는 날 옛 주문 끝내기)와 1단계 재생(replay.py — 옛 장부를 쓴다), 칸을 정의하는 records.py."""
        allowed = {"broker.py", "ledger.py", "replay.py", "records.py"}
        made_only_by_broker = r"""["'](order_kind|reserved|ref_price|cash_after|price_kind|qty_units|settle)["']\s*:"""
        for p in sorted((ROOT / "market").glob("*.py")):
            if p.name in allowed:
                continue
            text = p.read_text(encoding="utf-8")
            self.assertNotRegex(text, made_only_by_broker, p.name)
            self.assertNotRegex(text, r"""write\([^)]*["'](broker|order|fill)["']""", p.name)
            self.assertNotIn('"type": "reject"', text, p.name)
        # 진짜 기록 폴더는 실제 주소, 흉내 내기 폴더는 그 폴더 시세 파일
        self.assertIsInstance(D.quotes_for(D.Root(C.ROOT.resolve())), Q.NetQuotes)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(D.quotes_for(D.Root(Path(tmp))), Q.BarQuotes)
        # 주문 계획과 매일 실행은 증권사의 공개 요청만 쓴다(상태를 직접 고치지 않는다)
        for name in ("rebalance.py", "daily.py"):
            text = (ROOT / "market" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\.state\.|\._emit\(|\._broker_line\(|State\(", name)
        # 옛 장부를 부르는 곳은 갈아타기 전 길뿐이다: daily의 ledger 호출은 rebuild·settle·plan·_fill(끝내기)·비중 식
        text = (ROOT / "market" / "daily.py").read_text(encoding="utf-8")
        self.assertLessEqual(set(re.findall(r"\bL\.(\w+)", text)),
                             {"rebuild", "Account", "Order", "fill_row", "last_prices", "target_weight"})

    def test_commands_exist_and_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2))[0], 0)
            size = {x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}
            code, said = quiet(B.main, ["status", "--root", str(root.path)])
            self.assertEqual(code, 0)
            self.assertIn("주문 가능", said)
            self.assertEqual({x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}, size)
            base = ["--root", str(root.path), "--day", "2025-09-02"]
            code, said = quiet(D.main, ["open", *base, "--at", "09:04"])
            self.assertEqual(code, 0)
            self.assertIn("장 시작 뒤 단계(09:04)", said)
        # 실제 주소 시험 도구: 응답의 겹 구조와 시각 칸을 그대로 보여 준다(주소 대신 가짜 응답)
        now = {"pollingInterval": 7000, "time": "20261110100710",
               "datas": [{"itemCode": E, "closePrice": "82,495", "localTradedAt": "2026-11-10T10:07:05+09:00",
                          "marketStatus": "OPEN", "tradeStopType": {"name": "TRADING"}}]}
        f = Q.naver_now_fields(now)
        self.assertEqual(f["outer"], ["datas", "pollingInterval", "time"])
        self.assertEqual(f["times"], {"(겉) time": "20261110100710", "localTradedAt": "2026-11-10T10:07:05+09:00",
                                      "marketStatus": "OPEN"})
        when = dt.datetime(2026, 11, 10, 10, 7, 10, tzinfo=C.KST)
        with mock.patch("market.prices._get", side_effect=lambda url, headers=None: json.dumps(now).encode()
                        if "polling" in url else (_ for _ in ()).throw(OSError("blocked"))):
            text = "\n".join(Q.show([E], when, Q.NetQuotes(clock=lambda: when)))
        self.assertIn("지금 가격: 82,495원", text)
        self.assertIn("요청보다 +5초", text)
        self.assertIn("localTradedAt", text)
        self.assertIn("오늘 봉: 없음", text)


if __name__ == "__main__":
    unittest.main()
