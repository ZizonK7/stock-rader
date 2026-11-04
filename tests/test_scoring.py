"""채점: 목표 종가, 방향(0.5는 말하지 않은 것), 확률 점수, 크기 적중, 아직 모르는 목표는 기다림."""
import datetime as dt
import unittest

from market import config as C
from market import prices as PR
from market import scoring as S
from market.prices import Row
from tests import fixtures

D = dt.date


def rows(closes, start=D(2026, 7, 1)):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append(Row(d, float(c), float(c), float(c), float(c), 1.0))
        d += dt.timedelta(days=1)
    return tuple(out)


def pred(pid, p_up, h=1, lo=None, hi=None, base=D(2026, 7, 1), close=100.0):
    return {"id": pid, "predictor": "calc", "asset": "069500", "horizon": h, "base_date": base.isoformat(),
            "base_close": close, "p_up": p_up, "size_lo": lo, "size_hi": hi}


class Score(unittest.TestCase):
    def setUp(self):
        self.h = {"069500": PR.History("069500", rows([100, 103, 99]))}

    def test_direction_brier_size(self):
        s = S.score_due([pred("a", 0.7, 1, 2.0, 4.0)], self.h, D(2026, 7, 5), set())[0]
        self.assertEqual((s["target_date"], s["up"], s["direction_hit"]), ("2026-07-02", True, True))
        self.assertAlmostEqual(s["brier"], 0.09)
        self.assertTrue(s["size_hit"])
        s = S.score_due([pred("b", 0.7, 2, 2.0, 4.0)], self.h, D(2026, 7, 5), set())[0]
        self.assertEqual((s["up"], s["direction_hit"], s["size_hit"]), (False, False, False))
        self.assertAlmostEqual(s["brier"], 0.49)

    def test_half_is_no_call_and_unknown_has_no_brier(self):
        s = S.score_due([pred("a", 0.5), pred("b", None)], self.h, D(2026, 7, 5), set())
        self.assertIsNone(s[0]["direction_hit"])
        self.assertEqual(s[0]["brier"], 0.25)
        self.assertIsNone(s[1]["brier"])

    def test_waits_until_target_is_known_and_scores_once(self):
        done = set()
        self.assertEqual(S.score_due([pred("a", 0.6, 5)], self.h, D(2026, 7, 5), done), [])
        self.assertEqual(len(S.score_due([pred("a", 0.6, 1)], self.h, D(2026, 7, 5), done)), 1)
        self.assertEqual(S.score_due([pred("a", 0.6, 1)], self.h, D(2026, 7, 5), done), [])

    def test_base_rate_uses_only_given_history(self):
        h = PR.History("069500", rows([100 + (i % 3) for i in range(300)], D(2025, 1, 1)))
        p, w = S.base_rate(h, 1)
        self.assertTrue(0 < p < 1)
        self.assertLessEqual(w[1] / w[0], 2.0 + 1e-9)
        self.assertEqual(S.base_rate(PR.History("069500", h.rows[:100]), 1), (None, None))

    def test_all_rate_uses_whole_known_history(self):
        """baseline:all (2026-11-04): 알려진 전체 시세에서 h봉 수익률이 오른 비율 — 최근 250봉만 보는 기저율과 다르다."""
        closes = [100 + i for i in range(200)] + [300 - i for i in range(300)]  # 앞 200봉은 오르고 뒤 300봉은 내림
        h = PR.History("069500", rows(closes, D(2024, 1, 1)))
        p, w = S.all_rate(h, 1)
        self.assertAlmostEqual(p, 200 / 499)  # 오른 199번 + 꺾이는 곳(299 → 300) 한 번
        self.assertLessEqual(w[1] / w[0], 2.0 + 1e-9)
        self.assertEqual(S.base_rate(h, 1)[0], 0.0)  # 최근 250봉은 모두 내림
        self.assertEqual(S.all_rate(PR.History("069500", h.rows[:20]), 1), (None, None))

    def test_summarize(self):
        sc = [{"predictor": "calc", "brier": 0.1, "direction_hit": True, "size_hit": False, "up": True},
              {"predictor": "calc", "brier": 0.3, "direction_hit": None, "size_hit": None, "up": False}]
        v = S.summarize(sc, lambda s: s["predictor"])["calc"]
        self.assertEqual((v["n"], v["brier"], v["calls"], v["hits"], v["size_n"]), (2, 0.2, 1, 1, 1))


class FillBasis(unittest.TestCase):
    """체결가 기준 채점(2026-11-05~, 결정 E2): base_kind fill — 체결 봉의 시가 → 체결 봉부터 h봉째 종가."""

    def setUp(self):
        # 7/1(수) 7/2(목) 7/3(금) 7/6(월) 7/7(화): (시가, 종가)
        oc = [(100, 102), (103, 101), (99, 104), (105, 107), (0, 107), (106, 110)]
        days = [D(2026, 7, 1), D(2026, 7, 2), D(2026, 7, 3), D(2026, 7, 6), D(2026, 7, 7), D(2026, 7, 8)]
        self.rows = tuple(Row(d, float(o), float(max(o, c)), float(min(o, c) if o else 0), float(c), 1.0)
                          for d, (o, c) in zip(days, oc))
        self.h = {"069500": PR.History("069500", self.rows)}

    def fpred(self, pid, p_up, h, base, lo=None, hi=None):
        return {"id": pid, "predictor": "calc", "asset": "069500", "horizon": h, "base_date": base.isoformat(),
                "base_close": None, "base_kind": "fill", "p_up": p_up, "size_lo": lo, "size_hi": hi}

    def test_one_bar_is_the_fill_bars_own_close(self):
        s = S.score_due([self.fpred("a", 0.7, 1, D(2026, 7, 2), 1.0, 3.0)], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual((s["fill_date"], s["fill_price"], s["target_date"]), ("2026-07-02", 103.0, "2026-07-02"))
        self.assertAlmostEqual(s["ret"], round(101 / 103 - 1, 6))
        self.assertEqual((s["up"], s["direction_hit"], s["size_hit"]), (False, False, True))

    def test_h_bars_from_the_fill_bar_and_holiday_goes_to_next_bar(self):
        s = S.score_due([self.fpred("a", 0.6, 2, D(2026, 7, 2))], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual((s["fill_date"], s["target_date"]), ("2026-07-02", "2026-07-03"))  # 2봉 = 체결 봉 + 1
        self.assertAlmostEqual(s["ret"], round(104 / 103 - 1, 6))
        # 기준 날짜가 휴장(토요일에 낸 예측의 월요일이 쉬는 날이면 그다음)·거래 정지(시가 0)면 다음 봉의 시가
        s = S.score_due([self.fpred("b", 0.6, 1, D(2026, 7, 4))], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual((s["fill_date"], s["fill_price"]), ("2026-07-06", 105.0))
        s = S.score_due([self.fpred("c", 0.6, 1, D(2026, 7, 7))], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual((s["fill_date"], s["fill_price"], s["target_date"]), ("2026-07-08", 106.0, "2026-07-08"))
        # 5봉: 체결 봉(7/2)부터 다섯째 봉(7/8)의 종가 — 그사이 거래 정지 봉(7/7)이 있어도 봉 수로 센다
        s = S.score_due([self.fpred("d", 0.6, 5, D(2026, 7, 2))], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual((s["fill_date"], s["target_date"]), ("2026-07-02", "2026-07-08"))
        self.assertAlmostEqual(s["ret"], round(110 / 103 - 1, 6))
        self.assertEqual(S.score_due([self.fpred("e", 0.6, 5, D(2026, 7, 3))], self.h, D(2026, 7, 9), set()), [])

    def test_waits_until_fill_and_target_are_known(self):
        h = {"069500": PR.History("069500", self.rows[:3])}
        self.assertEqual(S.score_due([self.fpred("a", 0.6, 1, D(2026, 7, 6))], h, D(2026, 7, 4), set()), [])  # 체결 봉 모름
        self.assertEqual(S.score_due([self.fpred("b", 0.6, 3, D(2026, 7, 2))], h, D(2026, 7, 4), set()), [])  # 목표 봉 모름
        self.assertEqual(len(S.score_due([self.fpred("c", 0.6, 2, D(2026, 7, 2))], h, D(2026, 7, 4), set())), 1)

    def test_old_lines_keep_old_basis(self):
        """base_kind가 없는 옛 줄(2026-11-04까지)은 기준 종가 → h봉 뒤 종가 그대로, fill 칸이 없다."""
        old = {"id": "o", "predictor": "calc", "asset": "069500", "horizon": 1, "base_date": "2026-07-02",
               "base_close": 101.0, "p_up": 0.6, "size_lo": None, "size_hi": None}
        s = S.score_due([old], self.h, D(2026, 7, 9), set())[0]
        self.assertEqual(s["target_date"], "2026-07-03")
        self.assertAlmostEqual(s["ret"], round(104 / 101 - 1, 6))
        self.assertNotIn("fill_price", s)

    def test_baselines_use_the_same_basis(self):
        rows = fixtures.series("069500", D(2024, 1, 1), D(2025, 6, 30))
        h = PR.History("069500", rows)
        n = len(rows)
        for hz in (1, 5):
            rets = [rows[t + hz].close / rows[t + 1].open - 1 for t in range(n - S.BASE_RATE_WINDOW - hz, n - hz)]
            self.assertAlmostEqual(S.base_rate(h, hz, True)[0], sum(r > 0 for r in rets) / len(rets))
            allr = [rows[t + hz].close / rows[t + 1].open - 1 for t in range(n - hz)]
            self.assertAlmostEqual(S.all_rate(h, hz, True)[0], sum(r > 0 for r in allr) / len(allr))
        old = [rows[t + 1].close / rows[t].close - 1 for t in range(n - S.BASE_RATE_WINDOW - 1, n - 1)]
        self.assertAlmostEqual(S.base_rate(h, 1)[0], sum(r > 0 for r in old) / len(old))  # 기본은 옛 기준(재생)


if __name__ == "__main__":
    unittest.main()
