"""시세: 받은 글 읽기, 끝난 일봉만, 덧붙이기만, 판단 시각에 알려진 줄만(view)."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from market import config as C
from market import prices as PR
from tests import fixtures

KST = C.KST


class Parse(unittest.TestCase):
    def test_naver(self):
        text = '<chartdata><item data="20260105|100|110|90|105|1234" />\n<item data="20260106|105|106|101|102|99" /></chartdata>'
        rows = PR.parse_naver(text, "t")
        self.assertEqual([r.date for r in rows], [dt.date(2026, 1, 5), dt.date(2026, 1, 6)])
        self.assertEqual((rows[0].open, rows[0].close, rows[0].volume, rows[0].source), (100, 105, 1234, "naver"))

    def test_upbit_uses_kst_date(self):
        items = [{"candle_date_time_kst": "2026-01-05T09:00:00", "opening_price": 1.0, "high_price": 2.0,
                  "low_price": 0.5, "trade_price": 1.5, "candle_acc_trade_volume": 3.0}]
        r = PR.parse_upbit(items, "t")[0]
        self.assertEqual((r.date, r.open, r.close, r.source), (dt.date(2026, 1, 5), 1.0, 1.5, "upbit"))

    def test_yahoo_skips_empty_rows(self):
        ts = int(dt.datetime(2026, 1, 5, 9, 0, tzinfo=KST).timestamp())
        data = {"chart": {"result": [{"timestamp": [ts, ts + 86400], "indicators": {"quote": [
            {"open": [100.0, None], "high": [110.0, 1], "low": [90.0, 1], "close": [105.0, 1], "volume": [5, 1]}]}}]}}
        rows = PR.parse_yahoo(data, "t")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, dt.date(2026, 1, 5))


class Finished(unittest.TestCase):
    def test_etf_today_is_not_finished_before_16(self):
        rows = list(fixtures.series("069500", dt.date(2026, 1, 5), dt.date(2026, 1, 6)))
        at = dt.datetime(2026, 1, 6, 13, 46, tzinfo=KST)
        self.assertEqual([r.date for r in PR.finished("069500", rows, at)], [dt.date(2026, 1, 5)])
        at = dt.datetime(2026, 1, 6, 16, 0, tzinfo=KST)
        self.assertEqual(len(PR.finished("069500", rows, at)), 2)

    def test_coin_candle_finishes_next_morning(self):
        rows = list(fixtures.series("KRW-BTC", dt.date(2026, 1, 4), dt.date(2026, 1, 6)))
        at = dt.datetime(2026, 1, 6, 7, 37, tzinfo=KST)  # 1/5 일봉은 1/6 09:00에 끝난다
        self.assertEqual([r.date for r in PR.finished("KRW-BTC", rows, at)], [dt.date(2026, 1, 4)])


class View(unittest.TestCase):
    def test_decision_time_view(self):
        m = fixtures.market(dt.date(2026, 1, 1), dt.date(2026, 1, 20))
        day = dt.date(2026, 1, 14)  # 수요일
        etf = PR.view("069500", m["069500"], C.decision_dt(day))
        btc = PR.view("KRW-BTC", m["KRW-BTC"], C.decision_dt(day))
        self.assertEqual(etf.last.date, dt.date(2026, 1, 13))
        self.assertEqual(btc.last.date, dt.date(2026, 1, 12))
        self.assertTrue(all(r.date < day for r in etf.rows))

    def test_history_has_no_way_to_reach_later_rows(self):
        m = fixtures.market(dt.date(2026, 1, 1), dt.date(2026, 1, 20))
        h = PR.view("069500", m["069500"], C.decision_dt(dt.date(2026, 1, 10)))
        self.assertIsNone(h.on_or_after(dt.date(2026, 1, 12)))
        with self.assertRaises(AttributeError):
            h.extra = 1  # __slots__: 다른 칸(전체 시세 등)을 매달 수 없다


class AppendOnly(unittest.TestCase):
    def test_append_and_update_never_rewrite(self):
        rows = fixtures.series("069500", dt.date(2026, 1, 1), dt.date(2026, 1, 31))
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertEqual(PR.append("069500", list(rows[:10]), folder), 10)
            before = (folder / "069500.csv").read_text(encoding="utf-8")
            changed = [PR.Row(r.date, r.open, r.high, r.low, r.close + 1, r.volume, "naver", "t") for r in rows[5:15]]
            n, warns = PR.update("069500", folder, dt.datetime(2026, 3, 1, tzinfo=KST), changed)
            self.assertEqual(n, 5)
            self.assertEqual(len([w for w in warns if "다름" in w]), 5)
            after = (folder / "069500.csv").read_text(encoding="utf-8")
            self.assertTrue(after.startswith(before))
            self.assertEqual(len(PR.load("069500", folder)), 15)

    def test_backfill_refuses_existing_file(self):
        rows = fixtures.series("069500", dt.date(2026, 1, 1), dt.date(2026, 1, 10))
        with tempfile.TemporaryDirectory() as d:
            PR.append("069500", list(rows), Path(d))
            with self.assertRaises(SystemExit):
                PR.backfill("069500", Path(d))

    def test_update_warns_about_gap(self):
        rows = fixtures.series("069500", dt.date(2026, 1, 1), dt.date(2026, 2, 28))
        with tempfile.TemporaryDirectory() as d:
            PR.append("069500", list(rows[:5]), Path(d))
            _, warns = PR.update("069500", Path(d), dt.datetime(2026, 3, 5, tzinfo=KST), list(rows[20:]))
            self.assertTrue(any("빠졌을" in w for w in warns))


class RealFiles(unittest.TestCase):
    def test_saved_price_files_are_sorted_and_finished(self):
        for a in C.ASSET_IDS:
            rows = PR.load(a)
            if not rows:
                self.skipTest("시세 파일 없음")
            dates = [r.date for r in rows]
            self.assertEqual(dates, sorted(set(dates)), a)
            for r in rows:
                self.assertLessEqual(C.final_dt(C.ASSETS[a], r.date), dt.datetime.fromisoformat(r.fetched_at), (a, r.date))


if __name__ == "__main__":
    unittest.main()
