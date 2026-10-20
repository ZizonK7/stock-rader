"""재생: 그날 뒤 시세에 독을 넣어도 그날까지의 기록이 글자 그대로 같다(미래 누설 없음). 계좌·기록 칸도 확인."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from market import calc as K
from market import config as C
from market import records as R
from market import replay as RP
from market.prices import Row
from tests import fixtures

START, END, CUT = dt.date(2025, 7, 1), dt.date(2025, 7, 31), dt.date(2025, 7, 16)


def poison(market, cut):
    """cut 날짜 이상 일봉을 터무니없는 값으로 바꾼다."""
    out = {}
    for a, rows in market.items():
        out[a] = tuple(r if r.date < cut else Row(r.date, r.open * 7, r.high * 9, r.low * 0.1, r.close * 0.2, 0, "poison")
                       for r in rows)
    return out


def by_run(rec, until):
    return {k: [json.dumps(x, sort_keys=True) for x in v if x["run"] <= until.isoformat()] for k, v in rec.items()}


class NoLookahead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pieces, cls.digest = K.load_pieces()
        cls.clean = fixtures.market(dt.date(2024, 3, 1), dt.date(2025, 8, 31))

    def test_poisoned_future_gives_same_records_up_to_that_day(self):
        a = RP.run(self.clean, START, END, self.pieces, self.digest, None)
        b = RP.run(poison(self.clean, CUT), START, END, self.pieces, self.digest, None)
        self.assertEqual(by_run(a, CUT), by_run(b, CUT))
        # 독이 실제로 들어갔는지: 뒤 날들은 달라야 한다
        self.assertNotEqual(by_run(a, END), by_run(b, END))
        self.assertTrue(a["prediction"] and a["score"] and a["fill"])

    def test_records_pass_field_checks_and_accounts_behave(self):
        with tempfile.TemporaryDirectory() as d:
            rec = RP.run(self.clean, START, END, self.pieces, self.digest, Path(d))
            self.assertEqual(R.check_folder(Path(d)), [])
        for acc in rec["account"]:
            self.assertGreaterEqual(acc["cash"], 0)
        cash = [a for a in rec["account"] if a["account"] == "baseline:cash"]
        self.assertTrue(all(a["value"] == C.START_CASH for a in cash))
        eq_orders = [o for o in rec["order"] if o["account"] == "baseline:equal_hold"]
        self.assertEqual({o["run"] for o in eq_orders}, {START.isoformat()})  # 첫날만 산다
        for p in rec["prediction"]:
            self.assertLess(p["base_date"], p["run"])  # 기준 종가는 판단 날짜 앞

    def test_last_day_is_score_only(self):
        rec = RP.run(self.clean, START, END, self.pieces, self.digest, None)
        last = (END + dt.timedelta(days=1)).isoformat()
        self.assertFalse([p for p in rec["prediction"] if p["run"] == last])
        self.assertEqual([r["mode"] for r in rec["run"] if r["run"] == last], ["replay-score-only"])


if __name__ == "__main__":
    unittest.main()
