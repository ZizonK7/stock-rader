"""계산기와 맞추기: 범위 합치기·폭 상한, 기준값 없으면 '모름', 결론을 바꾸는 조각만 설명, 맞추기는 FIT_UNTIL 뒤를 안 본다."""
import datetime as dt
import math
import unittest
from statistics import NormalDist

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


class CoinBaseAndStart(unittest.TestCase):
    """방향 기본값 = 동전(고정값), 출발점 조각, 답한 층, 참고 칸 (2026-11-04)."""

    KST = C.KST

    def coin(self, asset="KRW-BTC", h=1):
        return K.Effect("C_c", "up_odds", "base", 1.0, 1.0, asset, h, (), None, "이유", None, True)

    def decision(self, price=101.0, minutes_left=144.0, base=dt.date(2026, 11, 2)):
        end = C.final_dt(C.ASSETS["KRW-BTC"], C.next_bar("KRW-BTC", base))
        return {"price": price, "time": end - dt.timedelta(minutes=minutes_left), "base_date": base, "base_close": 100.0}

    def pieces(self, start=True):
        out = [K.Piece("C_c", "", "", [self.coin(), self.coin(h=7)])]
        return out + ([K.Piece("C_s", "", "", [], True)] if start else [])

    def test_fixed_base_is_coin_and_layer_coin(self):
        r = K.predict("KRW-BTC", 7, {}, self.pieces(False))
        self.assertAlmostEqual(r.p_up(), 0.5)
        self.assertEqual(r.layer(), "동전")
        self.assertEqual(r.conclusion()[0], "반반")
        mul = eff("C_m", "up_odds", "mul", 1.2, 1.3, [("S_a", "예")], "up", asset="KRW-BTC", h=7)
        r = K.predict("KRW-BTC", 7, {"S_a": "예"}, self.pieces(False) + [K.Piece("C_m", "", "", [mul])])
        self.assertGreater(r.p_up(), 0.5)
        self.assertEqual(r.layer(), "조각")
        fitted = K.predict("069500", 1, {}, [K.Piece("C_b", "", "", [eff("C_b", "up_odds", "base", 1.1, 1.2)])])
        self.assertEqual(fitted.layer(), "기준값")  # 이전 버전 모음(맞춘 절편)
        self.assertEqual(K.predict("005930", 1, {}, self.pieces()).layer(), "모름")  # 종목: 기준값 없음

    def test_start_formula(self):
        r = K.predict("KRW-BTC", 1, {}, self.pieces(), {"vol60": 0.02}, self.decision(101.0, 144.0))
        z = math.log(1.01) / (0.02 * math.sqrt(144 / 1440))
        self.assertAlmostEqual(r.p_up(), min(max(NormalDist().cdf(z), K.START_P_MIN), 1 - K.START_P_MIN), places=12)
        self.assertEqual(r.layer(), "출발점")
        f = K.record_fields(r)
        self.assertIsNone(f["p_range"])
        self.assertEqual(f["used"][0], "C_s:up_odds")
        self.assertNotIn("C_c:up_odds", f["used"])     # 출발점이 걸리면 기준값의 오를 확률은 쓰지 않은 것
        self.assertIn("C_s:up_odds", f["decisive"])     # 빼면 반반으로 돌아간다
        self.assertIn("출발점", f["reason"])
        down = K.predict("KRW-BTC", 1, {}, self.pieces(), {"vol60": 0.02}, self.decision(99.0, 144.0))
        self.assertLess(down.p_up(), 0.5)

    def test_start_boundaries(self):
        nums = {"vol60": 0.02}
        # 남은 시간 아래 끝 10분: 목표 봉이 이미 끝난 뒤의 가격이어도 0으로 나누지 않는다
        late = K.predict("KRW-BTC", 1, {}, self.pieces(), nums, self.decision(101.0, -30.0))
        self.assertAlmostEqual(late.start.minutes, K.START_MIN_MINUTES)
        self.assertGreater(NormalDist().cdf(math.log(1.01) / (0.02 * math.sqrt(10 / 1440))), 0.99)  # 식 그대로면 거의 1
        self.assertAlmostEqual(late.p_up(), 1 - K.START_P_MIN, places=12)  # 양 끝 0.10–0.90으로 자른다(두꺼운 꼬리)
        self.assertAlmostEqual(K.predict("KRW-BTC", 1, {}, self.pieces(), nums, self.decision(99.0, -30.0)).p_up(), K.START_P_MIN, places=12)
        # 걸리지 않는 곳: 가격 없음, 흔들림 숫자 없음, 첫 기간이 아님, 조각이 없음(이전 버전 모음), 기준 봉이 끝나기 전 가격
        for args in ((nums, None), (nums, {**self.decision(), "price": None}), ({}, self.decision())):
            self.assertEqual(K.predict("KRW-BTC", 1, {}, self.pieces(), *args).layer(), "동전", args)
        self.assertIsNone(K.predict("KRW-BTC", 7, {}, self.pieces(), nums, self.decision()).start)
        self.assertIsNone(K.predict("KRW-BTC", 1, {}, self.pieces(False), nums, self.decision()).start)
        stale = self.decision(101.0, 144.0)
        stale["time"] = C.final_dt(C.ASSETS["KRW-BTC"], stale["base_date"]) - dt.timedelta(minutes=5)
        self.assertIsNone(K.predict("KRW-BTC", 1, {}, self.pieces(), nums, stale).start)

    def test_start_piece_is_retired_and_never_answers(self):
        """출발점 조각은 2026-11-04 은퇴(결정 E3: 기준이 체결가라 이미 움직인 몫이 0). 판단 때 가격을 넘겨도 걸리지 않는다.
        떠 둔 모음(v5)에는 살아 있지만 매일 실행이 판단 때 가격을 넘기지 않으므로 거기서도 걸리지 않는다."""
        pieces, _ = K.load_pieces()
        self.assertEqual([p.id for p in pieces if p.start], [])
        base = [e for p in pieces for e in p.effects if p.id == "C_base_coin" and e.on == "up_odds"]
        self.assertEqual(len(base), 9)
        self.assertTrue(all(e.fixed and (e.lo, e.hi) == (1.0, 1.0) for e in base))  # 다시 맞춰도 동전 그대로
        r = K.predict("KRW-BTC", 1, {}, pieces, {"vol60": 0.02, "vol250": 0.02}, self.decision(101.0, 144.0))
        self.assertIsNone(r.start)
        self.assertNotEqual(r.layer(), "출발점")
        old, _ = K.load_pieces(C.VERSIONS / "v5.toml")
        self.assertEqual([p.id for p in old if p.start], ["C_started_move"])
        self.assertIsNone(K.predict("KRW-BTC", 1, {}, old, {"vol60": 0.02, "vol250": 0.02}).start)

    def test_fixed_claim_is_not_fitted(self):
        """fixed = true인 주장은 맞추지 않는다: 시세가 무엇이든 주장의 range 그대로."""
        raw = {"id": "C_x", "claims": [{"on": "up_odds", "op": "base", "fixed": True, "range": [1.0, 1.0], "reason": "동전"},
                                       {"on": "move", "op": "base", "reason": "r"}]}
        claims = F.claims_of([raw])
        self.assertEqual(claims[0].fixed, (1.0, 1.0))
        rows = fixtures.market(dt.date(2023, 6, 1), dt.date(2025, 6, 30))
        up = {a: tuple(Row(r.date, r.open * (1.004 ** i), r.high * (1.004 ** (i + 1)), r.low * (1.004 ** i),
                           r.close * (1.004 ** (i + 1)), 1) for i, r in enumerate(rs))
              for a, rs in rows.items()}  # 자주 오르는 시세(봉 안에서도 오름 — 시가 → 종가로 맞춘다): 맞추면 절편이 1보다 크다
        for market in (rows, up):
            fitted = F.fit_all(market, claims, dt.date(2025, 6, 30))
            effs, text, _ = F.effects_for(raw, fitted, claims)
            odds = [e for e in effs if e["on"] == "up_odds"]
            self.assertEqual(len(odds), 9)
            self.assertTrue(all(e["range"] == [1.0, 1.0] and e["fixed"] is True for e in odds))
            self.assertIn("고정값 9칸", text)
        self.assertGreater(F.fit_all(up, claims, dt.date(2025, 6, 30))[0].base[0], 1.0)  # 맞춘 절편은 따로 있다(쓰지 않음)


class Analog(unittest.TestCase):
    """참고 칸: 같은 대상 최근 750봉에서 시세 상태가 같은 지난 날들 (확률에 쓰지 않는다, 2026-11-04)."""

    def test_open_basis_matches_brute_force(self):
        """시가를 넘기면 오른 비율은 체결가 기준(시가(t+1) → 종가(t+h), 결정 E2). 시가가 0인 봉의 후보는 뺀다."""
        rows = fixtures.series("069500", dt.date(2022, 1, 1), dt.date(2025, 6, 30))
        c, o = [r.close for r in rows], [r.open for r in rows]
        values = P.read(c)[0]
        reads = P.read_series(c)
        last = len(c) - 1
        o2 = list(o)
        cand1 = [t for t in range(max(0, last + 1 - K.ANALOG_WINDOW), last) if reads[t] == values]
        if cand1:
            o2[cand1[0] + 1] = 0.0  # 거래 정지 봉
        got = K.analog(c, (1, 5), values, opens=o2)
        for h in (1, 5):
            cand = [t for t in range(max(0, last + 1 - K.ANALOG_WINDOW), last - h + 1) if reads[t] == values and o2[t + 1] > 0]
            if not cand:
                self.assertIsNone(got[h])
                continue
            self.assertEqual(got[h]["n"], len(cand))
            self.assertAlmostEqual(got[h]["up_rate"], round(sum(c[t + h] > o2[t + 1] for t in cand) / len(cand), 4))

    def test_matches_brute_force_and_never_sees_the_future(self):
        c = [r.close for r in fixtures.series("069500", dt.date(2022, 1, 1), dt.date(2025, 6, 30))]
        values = P.read(c)[0]
        got = K.analog(c, (1, 5, 20), values)
        reads = P.read_series(c)
        last = len(c) - 1
        for h in (1, 5, 20):
            cand = [t for t in range(max(0, last + 1 - K.ANALOG_WINDOW), last - h + 1) if reads[t] == values]
            if not cand:
                self.assertIsNone(got[h])
                continue
            self.assertLessEqual(max(cand) + h, last)  # 목표 봉이 판단 때 이미 끝난 날만
            self.assertEqual(got[h]["n"], len(cand))
            self.assertAlmostEqual(got[h]["up_rate"], round(sum(c[t + h] > c[t] for t in cand) / len(cand), 4))
            self.assertAlmostEqual(got[h]["eff"], round(len(cand) / h, 2))
            self.assertEqual(got[h]["runs"], 1 + sum(1 for a, b in zip(cand, cand[1:]) if b - a > h))
        # 뒤에 시세를 더해도(독) 그날까지 자른 답은 같다
        poisoned = c + [x * 5 for x in c[-30:]]
        self.assertEqual(K.analog(poisoned[:len(c)], (1, 5, 20), values), got)

    def test_unknown_values_give_none(self):
        self.assertEqual(K.analog([100.0] * 100, (1,), P.read([100.0] * 100)[0]), {1: None})


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


class OpenBasis(unittest.TestCase):
    """맞추기 표본의 수익률 = 시가(t+1) → 종가(t+h) (2026-11-04, 결정 E3). 상태 값·흔들림 단위는 종가(t)까지 그대로."""

    def test_sample_returns_are_next_open_to_close(self):
        rows = fixtures.series("069500", dt.date(2023, 1, 1), dt.date(2025, 6, 30))
        reads = P.read_series([r.close for r in rows])
        vols = P.vol250_series([r.close for r in rows])
        for h in (1, 5):
            s = F.samples("069500", rows, h, dt.date(2025, 6, 30))
            ts = [t for t in range(len(rows) - h) if reads[t] is not None]
            self.assertEqual(len(s.rets), len(ts))
            for k in (0, len(ts) // 2, -1):
                t = ts[k]
                self.assertAlmostEqual(s.rets[k], rows[t + h].close / rows[t + 1].open - 1, places=12)
                self.assertEqual(s.readings[k], reads[t])
                self.assertAlmostEqual(s.units[k], vols[t] * math.sqrt(h), places=12)
            self.assertNotAlmostEqual(s.rets[0], rows[ts[0] + h].close / rows[ts[0]].close - 1, places=9)  # 옛 종가 → 종가가 아니다
        # 시가가 0인 봉(거래 정지)의 표본은 뺀다
        t0 = ts[0]
        bad = list(rows)
        bad[t0 + 1] = Row(rows[t0 + 1].date, 0.0, 0.0, 0.0, rows[t0 + 1].close, 0)
        self.assertEqual(len(F.samples("069500", tuple(bad), 1, dt.date(2025, 6, 30)).rets),
                         len(F.samples("069500", rows, 1, dt.date(2025, 6, 30)).rets) - 1)

    def test_knowledge_fit_text_names_the_return(self):
        raw = F.block_raws()
        live = [r for r in raw if r.get("status", "hypothesis") != "retired"]
        self.assertTrue(live and all("다음 봉 시가 → h봉째 종가" in r["fit"] for r in live))


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
