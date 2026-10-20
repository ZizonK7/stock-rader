"""채점: 목표 종가, 방향(0.5는 말하지 않은 것), 확률 점수, 크기 적중, 아직 모르는 목표는 기다림."""
import datetime as dt
import unittest

from market import config as C
from market import prices as PR
from market import scoring as S
from market.prices import Row

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

    def test_summarize(self):
        sc = [{"predictor": "calc", "brier": 0.1, "direction_hit": True, "size_hit": False, "up": True},
              {"predictor": "calc", "brier": 0.3, "direction_hit": None, "size_hit": None, "up": False}]
        v = S.summarize(sc, lambda s: s["predictor"])["calc"]
        self.assertEqual((v["n"], v["brier"], v["calls"], v["hits"], v["size_n"]), (2, 0.2, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
