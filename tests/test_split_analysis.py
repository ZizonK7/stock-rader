"""2026-11-17 쪼개기 전 나눠 보기: 묶음 대비 채점 칸, '자기 시장 묶음과 같이 움직임' 상태, 나눠 보기 명령, 대화 세션 몫.
네트워크를 쓰지 않는다. 진짜 기록은 읽기만 한다(확인 5)."""
import datetime as dt
import json
import random
import tempfile
import unittest
from pathlib import Path

from market import asks as A
from market import breakdown as BD
from market import config as C
from market import daily as D
from market import hand as H
from market import pieces as P
from market import prices as PR
from market import records as R
from market import scoring as S
from market import stocks as ST
from market.prices import Row
from tests import test_daily as TD

STOCK = "005930"


def row(d: dt.date, o: float, c: float) -> Row:
    return Row(d, o, max(o, c), min(o, c), c, 1000.0, "fixture", "2026-01-01T00:00:00+09:00")


class Relative(unittest.TestCase):
    """확인 3: 종목 +1 %, 묶음 +3 %면 절대로는 오름, 묶음 대비로는 내림."""

    def hists(self):
        d0, d1 = dt.date(2026, 11, 18), dt.date(2026, 11, 19)
        stock = PR.History(STOCK, (row(dt.date(2026, 11, 17), 99, 99), row(d0, 100, 100.5), row(d1, 100.2, 101)))
        bundle = PR.History("069500", (row(dt.date(2026, 11, 17), 99, 99), row(d0, 100, 102), row(d1, 102, 103)))
        return {STOCK: stock, "069500": bundle}

    def test_fill_base_stock_up_one_bundle_up_three_counts_down(self):
        p = {"id": "x:hand:005930:2", "run": "2026-11-18", "predictor": "hand", "asset": STOCK, "horizon": 2,
             "base_date": "2026-11-18", "base_close": None, "base_kind": "fill", "p_up": 0.6, "size_lo": None}
        [s] = S.score_due([p], self.hists(), dt.date(2026, 11, 20), set())
        self.assertAlmostEqual(s["ret"], 0.01)          # 100 → 101
        self.assertTrue(s["up"])                         # 절대 방향 칸은 그대로
        self.assertEqual(s["bench"], "069500")
        self.assertAlmostEqual(s["bench_ret"], 0.03)    # 같은 창: 체결 봉 시가 100 → 목표 봉 종가 103
        self.assertAlmostEqual(s["rel_ret"], -0.02)
        self.assertFalse(s["rel_up"])
        self.assertEqual(R.line_errors("score", {"kind": "score", **s}, "s"), [])

    def test_old_base_uses_base_close_and_bundles_have_no_relative(self):
        h = self.hists()
        p = {"id": "y", "run": "2026-11-17", "predictor": "hand", "asset": STOCK, "horizon": 2,
             "base_date": "2026-11-17", "base_close": 99.0, "p_up": 0.6}
        [s] = S.score_due([p], h, dt.date(2026, 11, 20), set())
        self.assertAlmostEqual(s["bench_ret"], 103 / 99 - 1)
        self.assertAlmostEqual(s["rel_ret"], (101 / 99 - 1) - (103 / 99 - 1))
        q = {**p, "id": "z", "asset": "069500"}
        [b] = S.score_due([q], h, dt.date(2026, 11, 20), set())
        self.assertNotIn("rel_ret", b)                   # 묶음 자신은 대비 칸이 없다

    def test_missing_bundle_bar_gives_nothing_and_old_lines_are_recomputed(self):
        h = self.hists()
        h["069500"] = PR.History("069500", h["069500"].rows[:-1])   # 목표 날짜 봉이 없다
        p = {"id": "w", "run": "2026-11-18", "predictor": "hand", "asset": STOCK, "horizon": 2,
             "base_date": "2026-11-18", "base_close": None, "base_kind": "fill", "p_up": 0.6}
        [s] = S.score_due([p], h, dt.date(2026, 11, 20), set())
        self.assertNotIn("rel_ret", s)
        # 지난 채점 줄(칸 없음)은 읽을 때 같은 계산으로 — 줄을 고치지 않는다
        old = {k: v for k, v in S.score_due([p], self.hists(), dt.date(2026, 11, 20), set())[0].items()
               if k not in ("bench", "bench_ret", "rel_ret", "rel_up")}
        self.assertAlmostEqual(S.relative_of(p, old, self.hists())["rel_ret"], -0.02)


def walk(n: int, seed: str, follow: list[float] | None = None, share: float = 0.0) -> list[float]:
    """하루 수익률 n개: follow가 있으면 share만큼 그것을 따라간다."""
    rng = random.Random(seed)
    return [share * (follow[i] if follow else 0) + (1 - share) * rng.gauss(0, 0.01) for i in range(n)]


def rows_of(rets: list[float], start: dt.date) -> list[Row]:
    out, c, d = [], 100.0, start
    for r in [0.0] + rets:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        c *= 1 + r
        out.append(row(d, c, c))
        d += dt.timedelta(days=1)
    return out


class Comove(unittest.TestCase):
    """확인 4: 묶음과 같이 움직이는 가짜 시세와 따로 움직이는 가짜 시세가 다른 답을 내고, 판단 뒤 시세를 쓰지 않는다."""
    start = dt.date(2026, 1, 5)

    def test_linked_and_unlinked_give_different_answers(self):
        b = walk(80, "bundle")
        bundle = rows_of(b, self.start)
        linked = rows_of(walk(80, "a", b, share=0.8), self.start)
        alone = rows_of(walk(80, "b"), self.start)
        v1, c1 = P.comove(linked, bundle)
        v2, c2 = P.comove(alone, bundle)
        self.assertEqual(v1, "예")
        self.assertGreater(c1, 0.6)
        self.assertEqual(v2, "아니오")
        self.assertLess(abs(c2), P.COMOVE_MIN)
        self.assertEqual(P.comove(bundle, bundle), ("예", 1.0))         # 묶음 자신
        self.assertEqual(P.comove(linked[:30], bundle)[0], "모름")        # 60봉이 안 된다

    def test_uses_only_bars_finished_at_the_decision(self):
        """판단 뒤 봉을 크게 바꿔도(묶음과 완전히 같이 움직이게) 판단 때 값은 그대로다."""
        b = walk(100, "bundle")
        bundle = rows_of(b, self.start)
        alone = rows_of(walk(100, "b"), self.start)
        cut = bundle[70].date
        at = dt.datetime.combine(cut + dt.timedelta(days=1), dt.time(7, 30), C.KST)
        # 판단 뒤 30봉은 묶음과 같은 수익률(독 넣기)
        poisoned = alone[:71] + [row(r.date, alone[70].close * r.close / bundle[70].close, alone[70].close * r.close
                                     / bundle[70].close) for r in bundle[71:]]
        C.STOCK_NAMES.setdefault(STOCK, STOCK)
        hists = {STOCK: PR.view(STOCK, poisoned, at), "069500": PR.view("069500", bundle, at)}
        vals, nums = D.with_comove(STOCK, {"S_trend_20": "예"}, {}, hists)
        self.assertEqual(vals[P.COMOVE_STATE], P.comove(alone[:71], bundle[:71])[0])
        self.assertEqual(vals[P.COMOVE_STATE], "아니오")
        self.assertEqual(P.comove(poisoned, bundle)[0], "예")   # 전체를 봤다면 답이 바뀌었을 것
        self.assertIn("comove60", nums)
        self.assertEqual(vals["S_trend_20"], "예")             # 일곱 값은 그대로 두고 더한다


class Breakdown(unittest.TestCase):
    def test_table_matches_the_note_on_real_records(self):
        """확인 5: 지시서 표(Q_rate_up_shift=예, 손 1봉, 11-16까지의 창) — 독립인 날 넷, 대상별 내린 날."""
        ST.register()
        res = BD.collect("Q_rate_up_shift")
        used = [i for i in res["used"] if i["horizon"] == 1 and i["start"] <= "2026-11-16"]
        by = {a: BD.row([i for i in used if i["asset"] == a]) for a in {i["asset"] for i in used}}
        want = {"069500": (3, 4), "360750": (4, 4), "051910": (3, 4), "035420": (2, 2), "005490": (1, 3),
                "352820": (0, 4), "011200": (0, 1)}
        for a, (down, days) in want.items():
            self.assertEqual((by[a]["dir"][0], by[a]["days"]), (down, days), a)
        self.assertEqual(BD.row([i for i in used if i["asset"] != "KRW-BTC"])["days"], 4)   # 11-11·12·13·16
        self.assertEqual(BD.row(used)["days"], 6)   # + 비트코인 11-14·15(주말에도 봉이 있다)
        self.assertLess(by["352820"]["comove"][1], P.COMOVE_MIN)
        self.assertGreater(by["051910"]["comove"][0], P.COMOVE_MIN)
        self.assertIn("### 대상별", BD.render(res, 1))


class Asks(unittest.TestCase):
    def test_open_close_and_input(self):
        self.assertEqual(A.input_errors({"asks": [{"what": "칸이 필요", "close": "칸이 생기면"}]}), [])
        self.assertTrue(A.input_errors({"asks": [{"what": "칸이 필요"}]}))
        self.assertEqual(A.run_field("2026-11-18", {"asks": [{"what": "a", "close": "b"}]}),
                         [{"id": "ask:2026-11-18:1", "what": "a", "close": "b"}])
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "asks.toml").write_text('[[ask]]\nid = "ask:2026-11-01:1"\nopened = "2026-11-01"\nstatus = "열림"\n'
                                         'what = "옛 일"\nclose = "끝나면"\n\n[[ask]]\nid = "ask:2026-11-18:1"\nopened = '
                                         '"2026-11-18"\nstatus = "닫힘"\nwhat = "a"\nclose = "b"\nclosed = "2026-11-19"\n'
                                         'how = "고침"\n', encoding="utf-8")
            (t / "run.jsonl").write_text(json.dumps({"kind": "run", "id": "live:2026-11-18", "run": "2026-11-18",
                                                     "asks": [{"id": "ask:2026-11-18:1", "what": "a", "close": "b"},
                                                              {"id": "ask:2026-11-18:2", "what": "c", "close": "d"}]},
                                                    ensure_ascii=False) + "\n", encoding="utf-8")
            got = [a["id"] for a in A.open_items(t / "asks.toml", t)]
            self.assertEqual(got, ["ask:2026-11-01:1", "ask:2026-11-18:2"])   # 닫힌 것은 빠진다
            self.assertEqual(A.check(A.load(t / "asks.toml")), [])
        self.assertEqual(A.check(A.load()), [])   # 저장소의 목록


class DailyAsksAndComove(unittest.TestCase):
    """확인 6 일부: 흉내 낸 prepare → record에서 asks가 run 줄에 남고 briefing 맨 위에 보이며, 시세 대 보기 값에 같이 움직임이 있다."""

    def test_record_keeps_asks_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = TD.make_root(tmp)
            d = dt.date(2025, 9, 2)
            assert D.prepare(root, d, TD.at(d, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            inp = TD.fill(json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8")))
            inp["asks"] = [{"what": "가짜: 칸이 필요(근거 1건)", "close": "칸이 생기면"}]
            p = root.day_dir(d) / "filled.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, TD.at(d, 8, 30), p), 0)
            run = [r for r in R.read(root.records, "run") if r["run"] == "2025-09-02"][-1]
            self.assertEqual(run["asks"], [{"id": "ask:2025-09-02:1", "what": "가짜: 칸이 필요(근거 1건)", "close": "칸이 생기면"}])
            reading = [r for r in R.read(root.records, "reading") if r["asset"] == "069500"][-1]
            self.assertEqual(reading["values"][P.COMOVE_STATE], "예")        # 묶음 자신
            calc = [x for x in R.read(root.records, "prediction") if x["predictor"] == "calc"][0]
            self.assertIn(P.COMOVE_STATE, calc["readings"])
            d2 = d + dt.timedelta(days=1)
            assert D.prepare(root, d2, TD.at(d2, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            brief = (root.day_dir(d2) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("열린 대화 세션 몫", brief)
            self.assertIn("ask:2025-09-02:1", brief)
            self.assertLess(brief.index("열린 대화 세션 몫"), brief.index("## 시세"))   # 맨 위
            # 빈 what/close는 거절, 아무것도 안 쓴다
            inp2 = TD.fill(json.loads((root.day_dir(d2) / "input.json").read_text(encoding="utf-8")))
            inp2["asks"] = [{"what": "", "close": "x"}]
            p2 = root.day_dir(d2) / "filled.json"
            p2.write_text(json.dumps(inp2, ensure_ascii=False), encoding="utf-8")
            n = len(R.read(root.records, "run"))
            self.assertEqual(D.record(root, d2, TD.at(d2, 8, 30), p2), 1)
            self.assertEqual(len(R.read(root.records, "run")), n)


class HandWarns(unittest.TestCase):
    def test_piece_used_where_derived_condition_is_off_warns(self):
        raw = {"state": [], "hypothesis": [{"id": "Q_x", "read_by": "조사", "conditions": ["S_q_x", "S_comove_market"],
                                            "claims": [{"on": "up_odds", "dir": "down", "when": "예", "scope": "all",
                                                        "also": {"S_comove_market": "예"}}]}]}
        info = H._piece_index(raw)["Q_x"]
        self.assertEqual(info["derived"], {"S_comove_market": "예"})
        ctx = {"day": "2026-11-18", "raw": raw, "research_states": ["S_q_x"], "places": ["P_a"], "stale": set(),
               "predictable": ["069500"], "stocks": [STOCK], "held": [], "calc_values": {
                   "069500": {"S_comove_market": "예"}, STOCK: {"S_comove_market": "아니오"}},
               "due_reviews": {}, "due_checks": {}, "node_ids": set()}
        hand = [{"asset": a, "horizon": hz, "p_up": 0.45, "size_lo": 1.0, "size_hi": 2.0, "used": ["Q_x=예"],
                 "decisive": ["Q_x=예"], "reason": "가짜", "confidence": "낮음"}
                for a in ("069500", STOCK) for hz in (1, 5, 20)]
        inp = {"day": "2026-11-18", "readings": {"S_q_x": {"value": "예", "why": "가짜", "places": ["P_a"]}},
               "hand": hand, "weights": {}}
        _, warns = H.validate(inp, ctx)
        off = [w for w in warns if "S_comove_market=예인데" in w]
        self.assertEqual(len(off), 3)                       # 종목 기간 셋만 — 묶음은 켜져 있다
        self.assertTrue(all(STOCK in w for w in off))


if __name__ == "__main__":
    unittest.main()
