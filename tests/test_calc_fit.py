"""계산기와 맞추기: 범위 합치기·폭 상한, 기준값 없으면 '모름', 결론을 바꾸는 조각만 설명, 맞추기는 FIT_UNTIL 뒤를 안 본다."""
import datetime as dt
import math
import unittest

from market import calc as K
from market import config as C
from market import fit as F
from market import pieces as P
from market.prices import Row
from tests import fixtures


def eff(piece, on, op, lo, hi, when=(), d=None, asset="069500", h=1):
    return K.Effect(piece, on, op, lo, hi, asset, h, tuple(when), d, "이유")


class Combine(unittest.TestCase):
    def test_log_widths_add_in_quadrature_and_are_clamped(self):
        lo, hi = K.combine([(1.0, 1.21), (1.0, 1.21)])
        self.assertAlmostEqual(math.log(hi / lo), math.sqrt(2) * math.log(1.21), places=9)
        wide = K.clamp((1.0, 9.0))
        self.assertAlmostEqual(wide[1] / wide[0], K.MAX_RATIO)
        self.assertAlmostEqual(math.sqrt(wide[0] * wide[1]), 3.0)  # 가운데는 그대로

    def test_no_base_means_unknown_not_invented(self):
        pieces = [K.Piece("C_x", "t", "r", [eff("C_x", "up_odds", "mul", 1.1, 1.3, [("S_a", "예")], "up")])]
        r = K.predict("069500", 1, {"S_a": "예"}, pieces)
        self.assertIsNone(r.p_up())
        self.assertIsNone(K.record_fields(r)["p_up"])
        self.assertIn("모름", r.reason())

    def test_decisive_lists_only_pieces_that_change_the_conclusion(self):
        base = eff("C_base", "up_odds", "base", 0.95, 1.0)
        big = eff("C_big", "up_odds", "mul", 1.3, 1.5, [("S_a", "예")], "up")
        tiny = eff("C_tiny", "up_odds", "mul", 1.0, 1.01, [("S_b", "예")], "up")
        pieces = [K.Piece("C_base", "", "", [base]), K.Piece("C_big", "", "", [big]), K.Piece("C_tiny", "", "", [tiny])]
        r = K.predict("069500", 1, {"S_a": "예", "S_b": "예"}, pieces)
        self.assertEqual([e.piece for e in r.decisive()], ["C_big"])
        f = K.record_fields(r)
        self.assertIn("C_tiny:up_odds", f["used"])
        self.assertNotIn("C_tiny", f["reason"])  # 빼도 결론이 그대로인 조각은 설명에 없다
        self.assertIn("C_big", f["reason"])

    def test_effect_for_other_asset_or_horizon_is_not_used(self):
        base = eff("C_base", "up_odds", "base", 1.0, 1.1)
        other = eff("C_big", "up_odds", "mul", 1.3, 1.5, [("S_a", "예")], "up", asset="360750")
        r = K.predict("069500", 1, {"S_a": "예"}, [K.Piece("C_base", "", "", [base]), K.Piece("C_big", "", "", [other])])
        self.assertEqual(r.odds_muls, [])

    def test_real_knowledge_ranges_respect_width_cap(self):
        pieces, _ = K.load_pieces()
        for p in pieces:
            for e in p.effects:
                self.assertLessEqual(e.hi / e.lo, K.MAX_RATIO + 1e-9, (p.id, e))
                self.assertTrue(e.reason.strip(), p.id)
        values = {s: "예" for s in P.STATES}
        for a in C.ASSET_IDS:
            for h in C.ASSETS[a].horizons:
                r = K.predict(a, h, values, pieces)
                for rng in (r.odds(), r.move()):
                    if rng:
                        self.assertLessEqual(rng[1] / rng[0], K.MAX_RATIO + 1e-9)


class Pieces(unittest.TestCase):
    def test_short_history_is_unknown(self):
        values, _ = P.read([100.0] * 100)
        self.assertEqual(set(values.values()), {"모름"})

    def test_reads_only_given_closes(self):
        rows = fixtures.series("069500", dt.date(2024, 1, 1), dt.date(2025, 6, 30))
        closes = [r.close for r in rows]
        self.assertEqual(P.read(closes[:300])[0], P.read_series(closes)[299])

    def test_names_have_no_asset_words(self):
        for sid, text in P.STATES.items():
            for w in C.ASSET_WORDS:
                self.assertNotIn(w.lower(), (sid + text).lower())


class FitUntil(unittest.TestCase):
    def test_rows_after_fit_until_do_not_change_the_fit(self):
        until = dt.date(2025, 6, 30)
        clean = fixtures.market(dt.date(2023, 6, 1), dt.date(2025, 9, 30))
        poisoned = {a: tuple(r if r.date <= until else Row(r.date, r.open * 3, r.high * 3, r.low * 3, r.close * 3, 1, "poison")
                             for r in rows) for a, rows in clean.items()}
        claims = F.claims_of(F.block_raws())
        a = F.fit_all(clean, claims, until)
        b = F.fit_all(poisoned, claims, until)
        self.assertEqual([(f.base, f.muls, f.dropped) for f in a], [(f.base, f.muls, f.dropped) for f in b])
        for f in a:
            s = F.samples(f.asset, clean[f.asset], f.horizon, until)
            self.assertLessEqual(s.last, until)

    def test_fitted_ranges_are_capped_and_follow_claimed_direction(self):
        claims = F.claims_of(F.block_raws())
        for f in F.fit_all(fixtures.market(dt.date(2023, 6, 1), dt.date(2025, 6, 30)), claims, dt.date(2025, 6, 30)):
            for pid, (lo, hi) in f.muls.items():
                self.assertLessEqual(hi / lo, F.MAX_RATIO + 1e-9)
                d = next(c.dir for c in claims if c.piece == pid and c.on == f.on)
                self.assertTrue(lo >= 1 if d == "up" else hi <= 1, (pid, f.on, lo, hi, d))
            self.assertLessEqual(len(f.muls), max(f.n_eff / F.EFF_PER_MUL, 0))

    def test_rounding_keeps_cap(self):
        lo, hi = F.rounded(1.0001, 2.0009)
        self.assertLessEqual(hi / lo, 2.0)


if __name__ == "__main__":
    unittest.main()
