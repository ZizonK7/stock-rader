"""가상 증권사 3회차 — 새 눈 검토(docs/worklog/2026-11-10-broker-review.md, 발견 1–10)와 실제 주소 시험
(docs/worklog/2026-11-10-broker-live-smoke.md, S1·S2)이 찾은 것을 지킨다. 시험 이름의 f<번호>는 검토 보고서의 발견 번호다.

고침마다 먼저 검토가 되살린 방법대로 실패하는 시험을 썼다(고치기 전 코드에서 실패하는 것을 보고 고쳤다). 시세는 가짜다(네트워크 없음).
F137(시험에는 넣고 실제 쓸 때는 없는 입력): 가짜 공급자에서 두 길이 같은 값을 주면 시험이 갈라내지 못한다 — 그래서 실제 주소처럼
장 밖에도 다른 '지금 값'을 주는 공급자, 지금 가격을 못 받는 공급자, 진행 중인 봉을 주지 않는 공급자를 따로 끼운다."""
import contextlib
import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market import broker as B
from market import config as C
from market import daily as D
from market import prices as PR
from market import quotes as Q
from market import records as R
from market import report as RP
from market import rules as RU
from market.prices import Row
from tests.test_broker import START, T, bar, bars, replace_bar
from tests.test_broker_daily import BTC, E, U, NoLive, d, day_run, of, quiet, recs, use
from tests.test_daily import TICK, at, make_root

DAY = dt.date


class LoudOutside(Q.BarQuotes):
    """실제 주소처럼 장 밖(08:30)에도 한국 거래소 '지금 값'을 주는데, 그 값이 시세 파일의 공식 종가와 다른 공급자
    (장 시작 전 동시호가 예상가·장 뒤 거래 값이 섞인 경우를 흉내 낸다). 업비트 지금 값도 봉 시가와 다르게 준다."""

    def now(self, asset, at):
        n = super().now(asset, at)
        if n is None:
            return None
        return Q.Now(n.asset, n.price * 1.1, at, n.open, "loud")


class NoRunningKrx(Q.BarQuotes):
    """진행 중인 한국 거래소 봉(오늘 시가)을 아직 주지 않는 공급자(09:00 직후 네이버가 오늘 봉을 늦게 보여 줄 때)."""

    def day(self, asset, date, at):
        got = super().day(asset, date, at)
        return None if got is not None and not got.final and C.on_exchange(asset) else got


class FinalLower(Q.BarQuotes):
    """진행 중인 한국 묶음 봉에서는 시가를 0.6% 높게 주는 공급자 — 시세 파일(끝난 봉)에는 그보다 0.6% 낮은 값이 들어 있다:
    네이버가 분배금을 지난 값에 소급해 낮춘 값을 다음 날 아침에 받아 저장한 경우(끝난 봉의 시가만 0.6% 낮음)."""

    def day(self, asset, date, at):
        got = super().day(asset, date, at)
        if got is not None and not got.final and asset == E:
            o = RU.floor_tick(E, got.open / 0.994)
            return Q.Day(got.asset, got.date, o, None, None, None, False, "running")
        return got


class TenBars(Q.BarQuotes):
    """그 시각까지의 마지막 10봉만 주는 공급자(고치기 전 NetQuotes.day의 count=10)."""

    def day(self, asset, date, at):
        rows = [r for r in self.rows.get(asset, ()) if C.fill_dt(C.ASSETS[asset], r.date, C.OPEN_TIME) <= at][-10:]
        if date not in {r.date for r in rows}:
            return None
        return super().day(asset, date, at)


# ---------- 발견 1: 계좌 줄이 넘겨받은 평가 가격을 쓴다 ----------
class F1AccountLinePrices(unittest.TestCase):
    def test_f1_account_line_uses_the_prices_it_is_given(self):
        rows = bars()
        b = B.Broker("t", LoudOutside(rows))
        b.start(START, bank=1_000_000, units={E: 10, BTC: 1_000_000}, cost={E: 800_000, BTC: 1_000_000})
        when = T(11, 6, 8, 30)
        given = {E: (12_345.0, T(11, 5, 15, 30)), U: (25_000.0, T(11, 5, 15, 30)), BTC: (1.0e8, T(11, 6, 7, 37))}
        line, _ = b.account_line(when, None, given)
        self.assertEqual(line["prices"], {a: [t.date().isoformat(), p] for a, (p, t) in given.items()})
        self.assertEqual(line["priced_at"], {a: t.isoformat() for a, (p, t) in given.items()})
        self.assertAlmostEqual(line["value"], 1_000_000 + 10 * 12_345 + 1_000_000 * 1.0e8 / 1e8, 2)

    def test_f1_without_given_prices_a_closed_exchange_is_not_valued_at_a_loud_now(self):
        """넘겨받은 값이 없는 대상만 공급자에게 묻는다 — 한국 거래소는 장이 닫혀 있으면(받은 장 상태) 지금 값을 쓰지 않는다."""
        rows = bars()
        b = B.Broker("t", LoudOutside(rows))
        b.start(START, units={E: 10}, cost={E: 800_000})
        line, _ = b.account_line(T(11, 6, 8, 30))
        self.assertEqual(line["prices"][E], ["2026-11-05", bar(rows, E, DAY(2026, 11, 5)).close])
        self.assertEqual(line["priced_at"][E], "2026-11-05T15:30:00+09:00")

    def test_f1_daily_account_lines_follow_value_prices_when_upbit_now_fails(self):
        """업비트 지금 가격을 못 받고 prepare의 판단 때 가격(07:37)은 있는 날: 계좌 줄의 비트코인은 판단 때 가격, 한국 거래소는
        시세 파일의 공식 종가 — daily.value_prices가 정한 값·시각 그대로(줄의 priced_at까지)."""
        with tempfile.TemporaryDirectory() as tmp, use(NoLive):
            root = make_root(tmp)
            code, said = day_run(root, d(9, 2), {E: 0.3, BTC: 0.3})
            self.assertEqual(code, 0, said)
            for line in recs(root)["account"]:
                self.assertEqual(line["prices"][BTC], ["2025-09-02", TICK[BTC]["price"]], line["account"])
                self.assertEqual(line["priced_at"][BTC], TICK[BTC]["time"])
                for a in (E, U):   # 장 밖: 시세 파일의 마지막 공식 종가(9월 1일 봉)
                    close = next(x.close for x in PR.load(a, root.prices) if x.date == d(9, 1))
                    self.assertEqual((line["prices"][a], line["priced_at"][a]), (["2025-09-01", close], "2025-09-01T15:30:00+09:00"))
            self.assertEqual(R.check(root.path, [root.records]), [])


# ---------- 발견 2: 검산이 덧붙인 줄을 잡는다 ----------
class F2Check(unittest.TestCase):
    def setUp(self):
        self.rows = bars()

    def test_f2b_open_fill_on_another_day_or_at_another_time_is_an_error(self):
        day, other = DAY(2026, 11, 3), DAY(2026, 11, 2)
        rows = replace_bar(self.rows, E, other, open=bar(self.rows, E, day).open)   # 두 날의 시가를 같게(값으로는 못 가름)
        b = B.Broker("t", Q.BarQuotes(rows))
        b.start(START, sec=1_000_000)
        prev = bar(rows, E, other).close
        self.assertTrue(b.order(T(11, 2, 16, 0), E, "buy", "open", qty=5, limit=RU.default_open_limit(E, prev)).ok)
        b.sync(T(11, 4, 8, 0))
        lines = b.lines()
        self.assertEqual(B.check(lines, rows)[0], [])
        f = lines["fill"][0]
        self.assertEqual((f["fill_date"], f["at"]), ("2026-11-03", "2026-11-03T09:00:00+09:00"))
        forged = copy.deepcopy(lines)
        forged["fill"][0]["fill_date"] = "2026-11-02"                        # 다른 날 시가로 치른 장 시작 주문
        self.assertTrue(any("체결 날짜" in e for e in B.check(forged, rows)[0]), B.check(forged, rows)[0])
        forged = copy.deepcopy(lines)
        forged["fill"][0]["at"] = "2026-11-03T10:00:00+09:00"                # 장 시작 체결인데 시각이 10:00
        self.assertTrue(any("체결 시각" in e for e in B.check(forged, rows)[0]))

    def test_f2b_now_fill_time_must_be_the_request_time(self):
        b = B.Broker("t", Q.BarQuotes(self.rows))
        b.start(START, sec=1_000_000, upbit=1_000_000)
        self.assertTrue(b.order(T(11, 5, 10, 0), U, "buy", "now", qty=2).ok)
        self.assertTrue(b.order(T(11, 5, 10, 1), BTC, "buy", "now", amount=100_000).ok)
        lines = b.lines()
        self.assertEqual(B.check(lines, self.rows)[0], [])
        for i in (0, 1):
            forged = copy.deepcopy(lines)
            forged["fill"][i]["at"] = "2026-11-05T11:00:00+09:00"
            self.assertTrue(any("체결 시각" in e for e in B.check(forged, self.rows)[0]), i)

    def test_f2c_lines_without_seq_after_start_are_errors_and_the_report_ignores_them(self):
        b = B.Broker("t", Q.BarQuotes(self.rows))
        b.start(START, sec=2_000_000)
        b.order(T(11, 5, 10, 0), U, "buy", "now", qty=4)
        b.snapshot(T(11, 6, 8, 0))
        lines = b.lines()
        real = lines["account"][0]
        fake = {k: v for k, v in real.items() if k not in ("seq", "pockets", "priced_at", "at")}
        fake.update(id="t:2026-11-06-x", cash=99_000_000, value=99_000_000.0)
        forged = copy.deepcopy(lines)
        forged["account"].append(fake)
        errs = B.check(forged, self.rows)[0]
        self.assertTrue(any("seq" in e and "t:2026-11-06-x" in e for e in errs), errs)
        for kind, extra in (("order", {"id": "t:x", "run": "2026-11-06", "account": "t", "asset": E, "side": "buy",
                                       "qty_units": 1, "ref_price": 80_000.0, "reason": "", "decided_at":
                                       "2026-11-06T08:00:00+09:00"}),):
            forged = copy.deepcopy(lines)
            forged[kind].append(extra)
            self.assertTrue(any("seq" in e for e in B.check(forged, self.rows)[0]), kind)
        # 일지는 start 뒤의 seq 없는 계좌 줄을 읽지 않는다
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "records"
            b.write(folder)
            R.write(folder, "account", [fake])
            book = RP.Book(folder)
            self.assertEqual(book.acc["t"]["2026-11-06"]["cash"], real["cash"])

    def test_f2c_old_shape_lines_before_start_and_old_fills_on_the_switch_day_are_fine(self):
        old_acc = {"id": "live:hand:2026-11-04", "run": "2026-11-04", "account": "live:hand", "cash": 1_000_000,
                   "positions": {E: 0, U: 0, BTC: 0}, "prices": {}, "value": 1_000_000.0, "pending": ["live:hand:2026-11-04:069500"]}
        old_order = {"id": "live:hand:2026-11-04:069500", "run": "2026-11-04", "account": "live:hand", "asset": E,
                     "side": "buy", "qty_units": 10, "ref_price": 80_000.0, "reason": "", "decided_at":
                     "2026-11-04T08:30:00+09:00", "fill_rule": "open"}
        o = bar(self.rows, E, DAY(2026, 11, 4)).open
        value = 10 * o
        fee = RU.fee(E, value)
        old_fill = {"id": "fill:live:hand:2026-11-04:069500", "run": "2026-11-06", "account": "live:hand",
                    "order": old_order["id"], "asset": E, "side": "buy", "fill_date": "2026-11-04", "price": float(o),
                    "price_kind": "open", "qty_units": 10, "value": value, "fee": fee, "cash_after": 1_000_000 - value - fee,
                    "note": ""}
        b = B.Broker("live:hand", Q.BarQuotes(self.rows))
        b.start(T(11, 6, 8, 0), bank=1_000_000 - value - fee, units={E: 10}, cost={E: value})
        lines = b.lines()
        lines["account"].insert(0, old_acc)
        lines["order"].insert(0, old_order)
        lines["fill"].insert(0, old_fill)
        self.assertEqual(B.check(lines, self.rows)[0], [])
        later = copy.deepcopy(lines)                       # 옛 모양 체결 줄이 갈아탄 날 뒤에 붙으면 오류
        later["fill"].append(dict(old_fill, id="fill:x", run="2026-11-07"))
        self.assertTrue(any("seq" in e for e in B.check(later, self.rows)[0]))

    def test_f2d_start_must_match_the_old_ledger(self):
        old_acc = {"id": "live:calc:2026-11-04", "run": "2026-11-04", "account": "live:calc", "cash": 1_200_000,
                   "positions": {E: 3, U: 0, BTC: 0}, "prices": {}, "value": 1_440_000.0, "pending": []}

        def lines_with(bank, units):
            b = B.Broker("live:calc", Q.BarQuotes(self.rows))
            b.start(T(11, 6, 8, 0), bank=bank, units=units, cost={a: 80_000.0 * n for a, n in units.items()})
            ls = b.lines()
            ls["account"].insert(0, old_acc)
            return ls
        self.assertEqual(B.check(lines_with(1_200_000, {E: 3}), self.rows)[0], [])
        for bank, units in ((2_200_000, {E: 3}), (1_200_000, {E: 4}), (1_200_000, {})):
            errs = B.check(lines_with(bank, units), self.rows)[0]
            self.assertTrue(any("옛 장부" in e for e in errs), (bank, units, errs))
        # 옛 기록이 없는 진짜 계좌는 은행 = 시작 금액
        b = B.Broker("live:hand", Q.BarQuotes(self.rows))
        b.start(START, bank=C.START_CASH + 1_000_000)
        self.assertTrue(any("옛 장부" in e for e in B.check(b.lines(), self.rows)[0]))
        b = B.Broker("live:hand", Q.BarQuotes(self.rows))
        b.start(START, bank=C.START_CASH)
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])

    def test_f2a_now_fill_inside_the_days_range_is_not_caught_yet(self):
        """(a) 지금 체결을 그날 저가–고가 안의 값으로 꾸미면 일봉만으로는 못 가른다 — 지금은 막지 않고 문서에 적었다
        (docs/broker-sandbox.md 15절 '검산이 못 잡는 것'). 분봉으로 견주게 되면 이 시험을 뒤집는다."""
        b = B.Broker("t", Q.BarQuotes(self.rows))
        b.start(START, upbit=1_000_000)
        self.assertTrue(b.order(T(11, 2, 10, 0), BTC, "sell", "now", qty=0).ok is False)   # 가진 것 없음(거절)
        self.assertTrue(b.order(T(11, 2, 10, 0, 1), BTC, "buy", "now", amount=500_000).ok)
        lines = b.lines()
        f = lines["fill"][0]
        low = bar(self.rows, BTC, DAY(2026, 11, 2)).low
        forged = copy.deepcopy(lines)
        q = int(round(f["value"] / low * 1e8))                               # 같은 돈으로 그날 저가에 산 것처럼
        g = forged["fill"][0]
        g.update(price=f["value"] / (q / 1e8), qty_units=q)
        self.assertEqual(B.check(forged, self.rows)[0], [])                  # 지금은 통과한다(못 잡는 것)


# ---------- 발견 3: 바로잡기는 돈을 옮기지 않고, 증권사 안의 예외는 멈춤으로 ----------
class Drift(Q.BarQuotes):
    """진행 중인 한국 묶음 봉의 시가가 끝난 봉과 delta원 다르게 온다."""

    def __init__(self, rows, delta):
        super().__init__(rows)
        self.delta = delta

    def day(self, asset, date, at):
        got = super().day(asset, date, at)
        if got is not None and not got.final and asset == E:
            return Q.Day(got.asset, got.date, got.open + self.delta, None, None, None, False, "drift")
        return got


class F3Adjust(unittest.TestCase):
    def setUp(self):
        self.rows = bars()

    def run_drift(self, delta):
        b = B.Broker("t", Drift(self.rows, delta))
        b.start(START, sec=1_000_000, units={E: 5}, cost={E: 400_000})
        b.order(T(11, 5, 8, 0), E, "sell", "open", qty=5)
        b.sync(T(11, 5, 9, 1))
        return b

    def test_f3_adjust_records_but_moves_no_money(self):
        b = self.run_drift(5)
        pockets, cost = b.pockets(), dict(b.state.cost)
        r = b.sync(T(11, 6, 8, 0))
        adj = [x for k, x in r.lines if k == "broker" and x["type"] == "adjust"]
        self.assertEqual(len(adj), 1)
        o = bar(self.rows, E, DAY(2026, 11, 5)).open
        data = adj[0]["data"]
        self.assertEqual((data["price_was"], data["price_final"], data["cash"], data["cost"]), (o + 5, o, 0, 0))
        self.assertEqual((data["source_was"]["from"], data["source_final"]["from"]), ("drift", "bars"))
        self.assertEqual((b.pockets(), b.state.cost), (pockets, cost))         # 돈·평균 산 값이 그대로
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(b.sync(T(11, 6, 8, 1)).lines, [])                    # 한 체결에 한 번
        lines = b.lines()
        errs, warns = B.check(lines, self.rows)
        self.assertEqual(errs, [])
        self.assertTrue(any("진행 중인 봉" in w for w in warns), warns)       # 0.2% 안의 차이는 경고
        forged = copy.deepcopy(lines)                                         # 돈을 옮기는 바로잡기 줄을 손으로 적으면 오류
        next(x for x in forged["broker"] if x["type"] == "adjust")["data"]["cash"] = 1_000
        self.assertTrue(any("바로잡기" in e for e in B.check(forged, self.rows)[0]))

    def test_f3_in_progress_open_more_than_2pct_off_is_an_error(self):
        o = bar(self.rows, E, DAY(2026, 11, 5)).open
        b = self.run_drift(RU.floor_tick(E, o * 0.03))
        errs = B.check(b.lines(), self.rows)[0]
        self.assertTrue(any("2%" in e for e in errs), errs)
        b = self.run_drift(RU.floor_tick(E, o * 0.015))
        self.assertEqual(B.check(b.lines(), self.rows)[0], [])

    def test_f3_review_case_final_bar_lower_after_spending_the_sale_does_not_crash(self):
        """검토가 되살린 경우: 한국 묶음 95% → 미국 묶음으로 바꾸고 09:02 장 시작 뒤 단계에서 판 돈을 다 쓴 뒤, 끝난 봉 시가가 0.6% 낮다.
        고치기 전에는 다음 날 prepare가 RuntimeError로 죽었다. 이제 바로잡기 줄 하나와 경고 한 줄로 지나간다."""
        with tempfile.TemporaryDirectory() as tmp, use(FinalLower):
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.95})[0], 0)
            self.assertEqual(day_run(root, d(9, 3), {U: 1.0})[0], 0)
            self.assertEqual(quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 2))[0], 0)
            code, said = quiet(D.prepare, root, d(9, 4), at(d(9, 4), 7, 37), fetch=False, ticker=TICK, echo=False)
            self.assertEqual(code, 0, said)
            code, said = day_run(root, d(9, 4), {U: 1.0})
            self.assertEqual(code, 0, said)
            adj = of(recs(root)["broker"], type="adjust")
            self.assertTrue(adj)
            self.assertTrue(all(x["data"]["cash"] == 0 for x in adj))
            self.assertEqual(said.count("바로잡지 않고 적어 둠"), len(adj))
            self.assertEqual(R.check(root.path, [root.records]), [])

    def test_f3_exceptions_inside_the_broker_stop_prepare_record_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.5})[0], 0)
            size = {x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}

            def boom(self, at):
                raise RuntimeError("증권사 내부 오류: 시험")
            with mock.patch.object(B.Broker, "sync", boom):
                code, said = quiet(D.prepare, root, d(9, 3), at(d(9, 3), 7, 37), fetch=False, ticker=TICK, echo=False)
                self.assertEqual(code, 1)
                self.assertIn("멈춘다", said)
                self.assertIn("RuntimeError", said)
                self.assertEqual(quiet(D.open_phase, root, d(9, 2), at(d(9, 2), 9, 5))[0], 1)
            self.assertEqual(quiet(D.prepare, root, d(9, 3), at(d(9, 3), 7, 37), fetch=False, ticker=TICK, echo=False)[0], 0)

            def snap(self, *a, **k):
                raise RuntimeError("증권사 내부 오류: 계좌 줄")
            with mock.patch.object(B.Broker, "snapshot", snap):
                code, said = day_run(root, d(9, 3), {E: 0.5})
            self.assertEqual(code, 1)
            self.assertIn("멈춘다", said)
            self.assertEqual({x.name: x.stat().st_size for x in root.records.glob("*.jsonl")}, size)


# ---------- 발견 4: 오래 멈췄다 돌아도 치러진 주문은 치러진다 ----------
class F4LongGap(unittest.TestCase):
    def setUp(self):
        self.rows = bars(end=DAY(2026, 12, 10))

    def test_f4_net_quotes_reads_finished_bars_from_the_price_file_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for a in (E, BTC):
                PR.append(a, [r for r in self.rows[a] if r.date <= DAY(2026, 11, 19)], folder)
            now = T(11, 20, 7, 37)
            q = Q.NetQuotes(clock=lambda: now, prices_folder=folder)
            with mock.patch("market.prices._get", side_effect=OSError("blocked")) as got:
                d0 = q.day(E, DAY(2026, 11, 2), now)
                self.assertEqual((d0.open, d0.final), (bar(self.rows, E, DAY(2026, 11, 2)).open, True))
                self.assertIn("file", d0.source)
                self.assertTrue(q.day(BTC, DAY(2026, 11, 2), now).final)
                self.assertEqual(got.call_count, 0)                           # 시세 파일에 있으면 주소를 부르지 않는다
            # 시세 파일에 없으면 그 날짜까지 닿을 만큼 받는다(10개가 아니라)
            with mock.patch("market.prices._get", side_effect=OSError("blocked")) as got:
                Q.NetQuotes(clock=lambda: now, prices_folder=Path(tmp) / "none").day(E, DAY(2026, 10, 20), now)
            url = got.call_args.args[0]
            n = int(url.split("count=")[1].split("&")[0])
            self.assertGreaterEqual(n, (now.date() - DAY(2026, 10, 20)).days)

    def test_f4_a_long_gap_still_fills_the_open_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            PR.append(E, [r for r in self.rows[E] if r.date <= DAY(2026, 11, 19)], folder)
            sell_at, later = T(11, 2, 8, 0), T(11, 20, 7, 37)
            q = Q.NetQuotes(clock=lambda: later, prices_folder=folder)
            b = B.Broker("t", q)
            b.start(START, units={E: 100}, cost={E: 8_000_000})
            with mock.patch("market.prices._get", side_effect=OSError("blocked")):
                self.assertTrue(b.order(sell_at, E, "sell", "open", qty=100).ok)
                r = b.sync(later)
            f = [x for k, x in r.lines if k == "fill"]
            self.assertEqual([(x["fill_date"], x["price"]) for x in f], [("2026-11-02", bar(self.rows, E, DAY(2026, 11, 2)).open)])
            self.assertEqual(b.holdings(), {})

    def test_f4_check_catches_an_expire_that_should_have_filled(self):
        b = B.Broker("t", TenBars(self.rows))
        b.start(START, units={E: 100}, cost={E: 8_000_000}, sec=10_000_000)
        self.assertTrue(b.order(T(11, 2, 8, 0), E, "sell", "open", qty=100).ok)
        lim = RU.default_open_limit(E, bar(self.rows, E, DAY(2026, 10, 30)).close)
        self.assertTrue(b.order(T(11, 2, 8, 1), U, "buy", "open", qty=10,
                                limit=RU.default_open_limit(U, bar(self.rows, U, DAY(2026, 10, 30)).close)).ok)
        self.assertGreater(lim, 0)
        r = b.sync(T(11, 20, 7, 37))
        self.assertEqual(sorted(x["type"] for k, x in r.lines if k == "broker"), ["expire", "expire"])   # 고치기 전 공급자로는 끝남
        errs = B.check(b.lines(), self.rows)[0]
        self.assertEqual(sum("치러졌어야" in e for e in errs), 2, errs)
        # 정말로 봉이 없는 날(거래 정지·휴장)의 expire는 오류가 아니다
        gone = {a: [r for r in rs if r.date != DAY(2026, 11, 2)] for a, rs in self.rows.items()}
        self.assertFalse(any("치러졌어야" in e for e in B.check(b.lines(), gone)[0]))


# ---------- 발견 5: 장 시작 뒤 단계가 너무 일찍 돌면 ----------
class F5EarlyOpenPhase(unittest.TestCase):
    def test_f5_open_phase_before_todays_bar_is_seen_leaves_no_phase_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(day_run(root, d(9, 2), {E: 0.95})[0], 0)
            self.assertEqual(day_run(root, d(9, 3), {U: 0.95})[0], 0)
            with use(NoRunningKrx):
                code, said = quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 1))
            self.assertEqual(code, 0)
            self.assertIn("아직 장부에 안 들어왔다", said)
            self.assertEqual(of(recs(root)["broker"], type="phase"), [])
            code, said = quiet(D.open_phase, root, d(9, 3), at(d(9, 3), 9, 30))   # 다시 돌리면 이번에는 산다
            self.assertEqual(code, 0, said)
            self.assertIn("바로 체결: TIGER 미국S&P500 사기", said)
            self.assertEqual(len(of(recs(root)["broker"], type="phase")), 1)
            # 걸린 장 시작 주문이 없던 계좌는 지금처럼 phase 줄을 남긴다
            self.assertEqual(len(of(recs(root)["broker"], "baseline:cash", type="phase")), 1)
            self.assertEqual(R.check(root.path, [root.records]), [])


# ---------- 발견 7: 갈아타는 날 일지의 0주 체결 ----------
class F7ZeroFill(unittest.TestCase):
    def test_f7_zero_unit_old_fill_is_written_as_not_bought(self):
        fills = [{"asset": "005930", "side": "buy", "qty_units": 0, "price": 146_700.0, "fill_date": "2026-11-10",
                  "note": "현금이 모자라 수량을 줄임"},
                 {"asset": E, "side": "sell", "qty_units": 3, "price": 82_000.0, "fill_date": "2026-11-10", "note": ""}]
        text = RP.paid_text(fills)
        self.assertIn("사기는 돈이 모자라 사지 못함", text)
        self.assertNotIn("0주", text)
        self.assertIn("팔기 3주 × 82,000원", text)


# ---------- 발견 8·S1·S2: 네이버 지금 가격을 실제 응답 모양으로 ----------
REAL_NOW = {"pollingInterval": 7000, "time": "20261110112830",
            "datas": [{"itemCode": "069500", "stockName": "KODEX 200", "closePrice": "82,320", "compareToPreviousClosePrice": "120",
                       "marketStatus": "OPEN", "marketSessionType": "regularMarket",
                       "localTradedAt": "2026-11-10T11:28:30+09:00",
                       "tradeStopType": {"code": "1", "text": "운영.Trading", "name": "TRADING"},
                       "tradableStatus": "tradable", "tradableStatusCode": "1",
                       "stockExchangeType": {"code": "KS", "nameKor": "코스피", "delayTime": 0}}]}


def now_with(**change):
    data = json.loads(json.dumps(REAL_NOW))
    item = data["datas"][0]
    for k, v in change.items():
        if v is None:
            item.pop(k, None)
        else:
            item[k] = v
    return data


class F8NaverNow(unittest.TestCase):
    def test_s1_s2_real_shape_opens_only_when_trading_in_the_regular_market_without_delay(self):
        n = Q.parse_naver_now(REAL_NOW, "069500")
        self.assertEqual((n.price, n.time, n.open, n.why), (82_320.0, dt.datetime(2026, 11, 10, 11, 28, 30, tzinfo=C.KST), True, ""))
        cases = {"marketStatus": ("CLOSE", "닫혀"), "marketSessionType": ("afterMarket", "정규장"),
                 "tradeStopType": ({"code": "2", "name": "STOP"}, "거래 정지"), "tradableStatus": ("untradable", "거래 정지"),
                 "stockExchangeType": ({"code": "KS", "delayTime": 20}, "지연")}
        for key, (val, word) in cases.items():
            got = Q.parse_naver_now(now_with(**{key: val}), "069500")
            self.assertFalse(got.open, key)
            self.assertIn(word, got.why, key)
        for key in ("tradeStopType", "tradableStatus"):                     # 칸이 없으면 모르는 것 → 받지 않는다
            got = Q.parse_naver_now(now_with(**{key: None}), "069500")
            self.assertFalse(got.open, key)
            self.assertIn("모름", got.why, key)
        got = Q.parse_naver_now(now_with(marketSessionType=None), "069500")   # 장 구분 칸이 없으면 장 상태로만
        self.assertTrue(got.open)
        self.assertIsNone(Q.parse_naver_now({"datas": []}, "069500"))
        self.assertIsNone(Q.parse_naver_now({"result": {"items": [REAL_NOW["datas"][0]]}}, "069500"))   # 모르는 겹 구조는 읽지 않는다

    def test_s1_halted_stock_is_rejected_by_the_broker(self):
        rows = bars()

        class Halted(Q.BarQuotes):
            def now(self, asset, at):
                n = super().now(asset, at)
                return Q.Now(n.asset, n.price, n.time, False, n.source, "거래 정지·거래 불가(받은 값 STOP/untradable)")
        b = B.Broker("t", Halted(rows))
        b.start(START, sec=1_000_000)
        r = b.order(T(11, 6, 10, 0), E, "buy", "now", qty=1)
        self.assertFalse(r.ok)
        self.assertIn("거래 정지", r.reason)
        self.assertEqual(RP.reject_reason(r.reason), "그 종목의 거래가 멈춰 있어서")

    def test_s3_quotes_tool_takes_a_fresh_time_per_asset_and_shows_stock_names(self):
        times = iter([dt.datetime(2026, 11, 10, 11, 0, s, tzinfo=C.KST) for s in range(0, 60, 10)])
        asked = []

        class Q2(Q.NetQuotes):
            def now(self, asset, at):
                asked.append(at)
                return Q.Now(asset, 1000.0, at, False, "naver:realtime", "장이 닫혀 있음(받은 장 상태 CLOSE)")

            def day(self, asset, date, at):
                return None

            def minute(self, asset, at):
                return None
        C.STOCK_NAMES["005930"] = "삼성전자"
        with mock.patch.object(Q, "_now", lambda: next(times)), mock.patch("market.prices._get", side_effect=OSError("x")):
            text = "\n".join(Q.show(["069500", "005930"], quotes=Q2()))
        self.assertEqual(len(set(asked)), 2)
        self.assertIn("005930 삼성전자", text)
        self.assertIn("응답 시각", text)
        self.assertIn("안 받음 — 장이 닫혀", text)


if __name__ == "__main__":
    unittest.main()
