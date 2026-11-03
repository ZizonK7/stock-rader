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


class ScaledBase(unittest.TestCase):
    """움직임 기준값을 '최근 250봉 하루 변동성 × √기간'의 배수로 (2026-11-03, C_base_vol)."""

    def scaled(self, lo=0.5, hi=1.0):
        return K.Effect("C_b", "move", "base", lo, hi, "069500", 5, (), None, "이유", "vol250")

    def test_range_is_multiplied_by_vol_at_decision(self):
        mul = eff("C_m", "move", "mul", 1.2, 1.2, [("S_a", "예")], "up", h=5)
        pieces = [K.Piece("C_b", "", "", [self.scaled()]), K.Piece("C_m", "", "", [mul])]
        r = K.predict("069500", 5, {"S_a": "예"}, pieces, {"vol250": 0.02})
        unit = 0.02 * math.sqrt(5) * 100
        lo, hi = r.move()
        self.assertAlmostEqual(lo, 0.5 * 1.2 * unit, places=9)
        self.assertAlmostEqual(hi, 1.0 * 1.2 * unit, places=9)
        self.assertEqual(r.conclusion()[2], "큼")  # 크기 등급은 배수끼리 비교(흔들림 수준과 무관)
        self.assertIn("최근 250봉 흔들림", r.reason())

    def test_no_vol_number_means_unknown_move(self):
        r = K.predict("069500", 5, {}, [K.Piece("C_b", "", "", [self.scaled()])])
        self.assertIsNone(r.move())
        self.assertIsNone(K.record_fields(r)["size_lo"])

    def test_fit_divides_by_vol_of_the_time(self):
        # 움직임이 늘 그때 흔들림의 0.8배(부호만 바뀜)면, 흔들림 수준이 해마다 달라도 기준값 창은 0.8을 담는다
        units = [0.01 * (1 + (i // 100) % 3) for i in range(600)]
        rets = [u * 0.8 * (1 if i % 2 else -1) for i, u in enumerate(units)]
        s = F.Samples("069500", 1, [{} for _ in rets], rets, units)
        base = F.Claim("C_b", "move", "base", "r", scale="vol250")
        f = F.fit_one(s, "move", [base])
        self.assertLessEqual(f.base[0], 0.8)
        self.assertGreaterEqual(f.base[1], 0.8)
        plain = F.fit_one(s, "move", [F.Claim("C_b", "move", "base", "r")])
        self.assertGreater(plain.base[0], 0.79)  # 고정 %로는 한 수준(0.8%·1.6%·2.4% 중 하나)만 담는다
        self.assertEqual(F.effects_for({"id": "C_b"}, [F.Fitted("069500", 1, "move", (0.5, 1.0), {}, {}, 600, 600)],
                                       [base])[0][0]["scale"], "vol250")

    def test_vol_series_matches_reading(self):
        c = [r.close for r in fixtures.market(dt.date(2023, 6, 1), dt.date(2024, 12, 31))["069500"]]
        vs = P.vol250_series(c)
        self.assertIsNone(vs[P.NEED - 2])
        self.assertAlmostEqual(vs[-1], P.read(c)[1]["vol250"], places=6)
        self.assertAlmostEqual(vs[400], P.read(c[:401])[1]["vol250"], places=6)


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
