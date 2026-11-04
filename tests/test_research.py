"""2단계 장치: 조사 기록 칸, 볼 곳 성적, 새 곳 시도 몫, 한 번만 세기, 조사 조각·버전·볼 곳 점검, 더한 조건 맞추기."""
import datetime as dt
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from market import checks as CH
from market import config as C
from market import fit as F
from market import logic
from market import records as R
from market import research as RS
from market import scoring as S
from market import versions as V

try:
    SCHEMA, SCORE, AUDIT = logic.core()
except SystemExit:
    SCHEMA = None

TEXT = C.KNOWLEDGE.read_text(encoding="utf-8")


def look(place, result="찾음", new=False, url="https://example.org/x"):
    return {"place": place, "result": result, "new": new, "url": url, "date": "2026-10-22", "note": ""}


def research(run, i, state, angle, looked):
    return {"id": f"research:{run}:{i}", "run": run, "question": f"q:{run}:{state}", "state": state, "angle": angle,
            "asked": "질문", "answer": "요약", "looked": looked, "agent": "a"}


class ResearchRecords(unittest.TestCase):
    def test_research_line_rules(self):
        ok = research("2026-10-22", 1, "S_q_rate_up", "금리·환율·정책", [look("P_fed_calendar")])
        self.assertEqual(R.line_errors("research", {"kind": "research", **ok}, "w"), [])
        bad = research("2026-10-22", 2, "S_q_rate_up", "금리·환율·정책",
                       [look("P_x", url=""), look("P_y", result="몰라"), {"place": "P_z", "result": "없음"}])
        bad["answer"] = "기사 본문" * 100
        errs = " ".join(R.line_errors("research", {"kind": "research", **bad}, "w"))
        for word in ("링크", "result는", "new", "본문"):
            self.assertIn(word, errs)

    def test_review_why_words(self):
        line = {"kind": "review", "id": "r", "run": "2026-10-22", "prediction": "p", "wrong": [], "why": "운이 나빴다",
                "reason": "x"}
        self.assertTrue(any("why는" in e for e in R.line_errors("review", line, "w")))

    def test_place_cards_and_new_share(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            R.write(folder, "research", [
                research("2026-10-22", 1, "S_q_rate_up", "금리·환율·정책",
                         [look("P_fed_calendar"), look("P_fedwatch", "없음", url=""), look("P_new_site", "찾음", new=True)]),
                research("2026-10-22", 2, "S_q_risk_off", "미국 시장", [look("P_cboe_vix", "늦음")])])
            R.write(folder, "hand_reading", [{"id": "hand_reading:2026-10-22", "run": "2026-10-22",
                                              "values": {"S_q_rate_up": "예"}, "why": {"S_q_rate_up": "w"},
                                              "places": {"S_q_rate_up": ["P_fed_calendar"]}, "research": []}])
            R.write(folder, "reading_check", [{"id": "rc", "run": "2026-10-30", "target": "hand_reading:2026-10-22",
                                               "state": "S_q_rate_up", "then": "예", "now": "예", "hit": True,
                                               "late": ["P_fedwatch"], "reason": "x"}])
            places = RS.load_places()
            cards = RS.place_cards(folder, places)
            self.assertEqual((cards["P_fed_calendar"]["찾음"], cards["P_fed_calendar"]["hits"]), (1, 1))
            self.assertEqual((cards["P_fedwatch"]["없음"], cards["P_fedwatch"]["late_after"]), (1, 1))
            self.assertEqual(cards["P_cboe_vix"]["늦음"], 1)
            self.assertFalse(cards["P_new_site"]["listed"])
            self.assertIn("P_new_site", RS.table(cards))
            warns = RS.new_share_warnings(R.read(folder, "research"), places, set())
            self.assertFalse(any("금리" in w for w in warns))     # 새 곳을 시도함
            self.assertTrue(any("미국 시장" in w for w in warns))  # 시도 안 함
            # 다시 볼 차례: 7일 지난 손 대 보기 중 아직 안 본 것
            self.assertEqual(RS.due_checks(folder, dt.date(2026, 11, 1)), [])

    def test_questions_come_from_live_research_pieces(self):
        raw = tomllib.loads(TEXT)
        qs = RS.questions(dt.date(2026, 10, 22), raw, RS.load_places(), {})
        states = {q["state"] for q in qs if q["state"]}
        self.assertEqual(states, {s["id"] for s in RS.research_states(raw)})
        self.assertEqual(sum(1 for q in qs if not q["state"]), len(RS.ANGLES))  # 각도마다 열린 질문
        self.assertTrue(all(q["suggest"] for q in qs if q["state"]))


    def test_every_research_condition_is_asked(self):
        """쪼개며 더한 조건(두 번째 조건)도 묻는다 — 기록돼야 검사(also)가 그 조건을 본다 (2026-11-03)."""
        raw = tomllib.loads(TEXT)
        states = {s["id"] for s in RS.research_states(raw)}
        for h in raw["hypothesis"]:
            if h.get("read_by") == "조사" and h.get("status") != "retired":
                self.assertLessEqual(set(h["conditions"]), states, h["id"])
        self.assertIn("S_q_rate_up", states)
        q = next(q for q in RS.questions(dt.date(2026, 11, 4), raw, RS.load_places(), {}) if q["state"] == "S_q_rate_up")
        self.assertIn("Q_rate_up_shift", q["pieces"])
        shift = next(h for h in raw["hypothesis"] if h["id"] == "Q_rate_up_shift")
        self.assertEqual(shift["claims"][0]["also"], {"S_q_rate_up": "예"})

    def test_uncounted_values_are_shown(self):
        """예/아니오가 아니라 시험에서 빠진 값의 수가 성적표 세부에 보인다(2026-10-31~11-03은 S_q_rate_up을 묻지 않았다)."""
        _, detail = CH.research_direction("Q_rate_up_shift")
        self.assertIn("세지 않은 채점", detail)
        self.assertIn("S_q_rate_up=안 물음", detail)


class Verdict(unittest.TestCase):
    """방향 시험 판정(2026-11-04): 날이 아니라 구간(같은 값이 이어진 날들)을 세고, 동점은 세지 않는다(모름)."""

    @staticmethod
    def days(start: dt.date, pattern: str, ups: str, gap_after: int | None = None):
        """pattern: 날마다 'y'(조건 켜짐)/'n'(꺼짐)/'?'(모름), ups: 날마다 '+'(오름)/'-'."""
        out, d = [], start
        for i, (s, u) in enumerate(zip(pattern, ups)):
            side = {"y": True, "n": False, "?": None}[s]
            out.append((d.isoformat(), "A", side, 0.01 if u == "+" else -0.01))
            d += dt.timedelta(days=1 + (10 if gap_after is not None and i == gap_after else 0))
        return out

    def test_one_long_regime_is_one_run(self):
        cells = {("up_odds", "up", "A", 1): self.days(dt.date(2026, 1, 1), "y" * 19 + "n" * 19, "+" * 19 + "-" * 19)}
        verdict, detail = CH._verdict(cells, "x", "c")
        self.assertTrue(verdict.startswith("시험 전"), verdict)
        self.assertIn("예 19일 1구간", detail)
        self.assertIn("아니오 19일 1구간", detail)

    def test_runs_break_on_change_unknown_and_gap(self):
        e = self.days(dt.date(2026, 1, 1), "yy?yynyy", "+" * 8, gap_after=6)
        self.assertEqual(CH._runs(e, True), 4)   # yy | (모름) | yy | (아니오) | y | (11일 벌어짐) | y
        self.assertEqual(CH._runs(e, False), 1)
        pooled = [(d, "A", True, r) for d, _, _, r in e[:3]] + [(d, "B", True, r) for d, _, _, r in e[:3]]
        self.assertEqual(CH._runs(pooled, True), 2)  # 묶은 칸은 종목마다 센다

    def test_tie_is_not_wrong_and_clear_side_counts(self):
        pattern = ("y" * 3 + "n" * 3) * 5
        tie = {("up_odds", "up", "A", 1): self.days(dt.date(2026, 1, 1), pattern, ("++-" + "+-+") * 5)}
        verdict, detail = CH._verdict(tie, "x", "c")
        self.assertTrue(verdict.startswith("시험 전") and "동점 1칸" in verdict, verdict)
        self.assertIn("동점 — 세지 않음(모름)", detail)
        clear = {("up_odds", "up", "A", 1): self.days(dt.date(2026, 1, 1), pattern, ("+++" + "--+") * 5)}
        self.assertTrue(CH._verdict(clear, "x", "c")[0].startswith("지지됨"))
        wrong = {("up_odds", "down", "A", 1): clear[("up_odds", "up", "A", 1)]}
        self.assertTrue(CH._verdict(wrong, "x", "c")[0].startswith("반박됨"))


class LayerVsBaselines(unittest.TestCase):
    """layer_vs_baselines (2026-11-04): 표본이 모이기 전엔 시험 전, 모이면 '계산기 − 동전'의 덩어리 부트스트랩 90% 구간."""

    def write(self, folder: Path, n: int, calc_brier: float, layer: str = "동전", asset: str = "069500", h: int = 1):
        preds, scores = [], []
        for i in range(n):
            base = (dt.date(2026, 11, 5) + dt.timedelta(days=i)).isoformat()
            for who, b in (("calc", calc_brier), ("baseline:all", 0.26), ("baseline:base_rate", 0.27)):
                pid = f"{base}:{who}:{asset}:{h}"
                preds.append({"kind": "prediction", "id": pid, "run": base, "predictor": who, "asset": asset, "horizon": h,
                              "base_date": base, "layer": layer})
                scores.append({"kind": "score", "id": f"score:{pid}", "run": base, "prediction": pid, "brier": b})
        folder.mkdir(parents=True, exist_ok=True)
        for kind, rows in (("prediction", preds), ("score", scores)):
            (folder / f"{kind}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def check(self, folder: Path, node: dict):
        with mock.patch.object(C, "RECORDS", folder), mock.patch.object(CH, "_node", lambda nid: node):
            return CH.layer_vs_baselines("X")

    def test_waits_then_judges(self):
        node = {"seen_until": "2026-11-04", "claims": []}
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), CH.LAYER_MIN_FIRST - 1, 0.10)
            verdict, detail = self.check(Path(d), node)
            self.assertTrue(verdict.startswith("시험 전"), verdict)
            self.assertIn(f"기준 날짜 {CH.LAYER_MIN_FIRST - 1}개", detail)
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), CH.LAYER_MIN_FIRST, 0.10)
            verdict, detail = self.check(Path(d), node)
            self.assertTrue(verdict.startswith("지지됨"), verdict)
            self.assertIn("전체 0.2600", detail)
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), CH.LAYER_MIN_FIRST, 0.40)
            self.assertTrue(self.check(Path(d), node)[0].startswith("반박됨"))
        with tempfile.TemporaryDirectory() as d:  # 1봉이 아닌 기간: 겹치지 않는 표본(기간마다 하나) 12개
            self.write(Path(d), 5 * (CH.LAYER_MIN_SPACED - 1), 0.10, h=5)      # 날 55개 = 표본 11개
            self.assertTrue(self.check(Path(d), node)[0].startswith("시험 전"))
            self.write(Path(d), 5 * (CH.LAYER_MIN_SPACED - 1) + 1, 0.10, h=5)  # 날 56개 = 표본 12개
            self.assertTrue(self.check(Path(d), node)[0].startswith("지지됨"))

    def test_start_piece_sees_only_its_layer(self):
        start = {"seen_until": "2026-11-04", "claims": [{"on": "up_odds", "op": "start"}]}
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), CH.LAYER_MIN_FIRST, 0.10, layer="동전")
            self.assertIn("없음", self.check(Path(d), start)[1])
            self.write(Path(d), CH.LAYER_MIN_FIRST, 0.10, layer="출발점")
            self.assertTrue(self.check(Path(d), start)[0].startswith("지지됨"))

    def test_attached_to_layer_nodes(self):
        raw = tomllib.loads(TEXT)
        nodes = {n["id"]: n for n in raw["hypothesis"] + raw["rule"]}
        for nid in ("C_started_move", "R_layer_v0"):
            self.assertIn("layer_vs_baselines", nodes[nid]["check"])
            self.assertEqual(nodes[nid]["seen_until"], "2026-11-04")


class Seeds(unittest.TestCase):
    """씨앗 조각(2026-11-04): 상한 6 밖, 관련 있는 날만 묻는다."""

    def test_seed_states_and_questions(self):
        raw = tomllib.loads(TEXT)
        self.assertEqual(RS.seed_states(raw), {"S_q_overnight_move"})
        qs = {q["state"]: q for q in RS.questions(dt.date(2026, 11, 5), raw, RS.load_places(), {}) if q["state"]}
        self.assertTrue(qs["S_q_overnight_move"]["seed"])
        self.assertTrue(qs["S_q_overnight_move"]["asked"].startswith(RS.SEED_MARK))
        self.assertLessEqual(len(qs["S_q_overnight_move"]["asked"]), R.SUMMARY_MAX)
        self.assertFalse(qs["S_q_rate_up"]["seed"])
        lead = next(h for h in raw["hypothesis"] if h["id"] == "Q_overnight_lead")
        self.assertTrue(lead["seed"])
        self.assertEqual({c["when"] for c in lead["claims"]}, {"오름", "내림"})

    def test_directional_answers_are_counted(self):
        """답이 오름/내림처럼 예/아니오가 아니어도 상태의 answers에 있으면 센다(모름·일부는 세지 않는다)."""
        claim = {"when": "오름"}
        known = {"S": {"오름", "내림", "아니오"}}
        self.assertTrue(CH._holds({"S": "오름"}, claim, "S", known))
        self.assertFalse(CH._holds({"S": "내림"}, claim, "S", known))
        self.assertIsNone(CH._holds({"S": "모름"}, claim, "S", known))
        self.assertIsNone(CH._holds({}, claim, "S", known))        # 안 물음
        self.assertIsNone(CH._holds({"S": "오름"}, claim, "S"))     # answers를 모르면 예/아니오만


class CountOnce(unittest.TestCase):
    def test_first_only_keeps_earliest_said(self):
        def pair(run, base="2026-10-23"):
            return ({"id": f"{run}:calc:069500:1", "run": run, "predictor": "calc", "asset": "069500", "horizon": 1,
                     "base_date": base}, {"ret": 0.01})
        pairs = [pair("2026-10-25"), pair("2026-10-24"), pair("2026-10-26"), pair("2026-10-27", "2026-10-26")]
        kept = S.first_only(pairs)
        self.assertEqual([p["run"] for p, _ in kept], ["2026-10-24", "2026-10-27"])
        scores = [{"prediction": p["id"]} for p, _ in pairs]
        S.mark_counted(scores, {p["id"]: p for p, _ in pairs}, set())
        self.assertEqual([s["counted"] for s in scores], [False, True, False, True])


@unittest.skipIf(SCHEMA is None, "PROVISO-Nesy를 찾지 못함 (PROVISO_PATH)")
class Devices(unittest.TestCase):
    def kn(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.toml"
            p.write_text(text, encoding="utf-8")
            return SCHEMA.load(p)

    def want(self):
        raw = tomllib.loads(TEXT)
        return {h["id"]: h.get("effect", []) for h in raw.get("hypothesis", []) if h.get("claims")}

    def test_research_piece_cap_is_separate(self):
        extra = "".join(f'\n[[hypothesis]]\nid = "Q_extra{i}"\ntext = "t"\nsource = "s"\nreason = "r"\nread_by = "조사"\n'
                        f'conditions = ["S_q_risk_off"]\ncheck = ["research_direction"]\nseen_until = "2026-10-21"\n'
                        f'claims = [{{ on = "move", dir = "up", when = "예", scope = "all", reason = "r" }}]\n'
                        for i in range(CH.MAX_RESEARCH_PIECES))
        errors, _ = CH.audit(self.kn(TEXT + extra), self.want())
        self.assertTrue(any("[조사 조각]" in e and "상한" in e for e in errors), errors)
        self.assertFalse(any("[계산 조각]" in e and "상한" in e for e in errors), errors)

    def test_research_piece_shape(self):
        bad = ('\n[[hypothesis]]\nid = "Q_bad"\ntext = "코인이 오르면"\nsource = "s"\nreason = "r"\nread_by = "조사"\n'
               'conditions = ["S_trend_20"]\nseen_until = "2026-10-01"\n'
               'claims = [{ on = "move", dir = "up", when = "예", scope = "big", span = ["내일"], reason = "r" }]\n')
        errors = " ".join(CH.audit(self.kn(TEXT + bad), self.want())[0])
        for word in ("대상 이름", "ask", "seen_until", "research_direction", "scope", "span"):
            self.assertIn(word, errors)

    def test_seed_cap_and_strength(self):
        def seed(i, strength="낮음"):
            return (f'\n[[hypothesis]]\nid = "Q_seed{i}"\ntext = "t"\nsource = "s"\nreason = "r"\nread_by = "조사"\n'
                    f'conditions = ["S_q_overnight_move"]\ncheck = ["research_direction"]\nseen_until = "2026-11-04"\n'
                    f'seed = true\nstrength = "{strength}"\n'
                    f'claims = [{{ on = "up_odds", dir = "up", when = "오름", scope = "equity", reason = "r" }}]\n')
        few = "".join(seed(i) for i in range(CH.MAX_SEED_PIECES - 1))  # 지금 씨앗 1 + 11 = 12: 정식 상한 6과 따로
        errors, _ = CH.audit(self.kn(TEXT + few), self.want())
        self.assertFalse(any("상한" in e for e in errors), errors)
        errors, _ = CH.audit(self.kn(TEXT + few + seed(99)), self.want())
        self.assertTrue(any("[씨앗 조각]" in e and "상한" in e for e in errors), errors)
        errors, _ = CH.audit(self.kn(TEXT + seed(0, "보통")), self.want())
        self.assertTrue(any("[씨앗 조각]" in e and "strength" in e for e in errors), errors)

    def test_retire_candidate_warning(self):
        """정식 조사 조각이 세운 뒤 30 판단일이 지나도 시험 전이면 '은퇴 후보' 경고(씨앗은 아님)."""
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            runs = [{"kind": "run", "id": f"live:{x}", "run": x, "mode": "live", "steps": {}, "notes": []}
                    for x in ((dt.date(2026, 11, 5) + dt.timedelta(days=i)).isoformat() for i in range(CH.RETIRE_AFTER_RUNS))]
            (folder / "run.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")
            with mock.patch.object(C, "RECORDS", folder):
                _, warns = CH.audit(self.kn(TEXT), self.want())
        cands = [w for w in warns if "은퇴 후보" in w]
        self.assertTrue(any("Q_risk_off_level" in w for w in cands), warns)
        self.assertFalse(any("Q_overnight_lead" in w for w in cands))  # 씨앗(세운 날 11-04 뒤 30일)은 경고하지 않는다
        _, warns = CH.audit(self.kn(TEXT), self.want())
        self.assertFalse(any("은퇴 후보" in w for w in warns))  # 지금 기록으로는 아직

    def test_start_piece_is_not_counted_in_calc_cap(self):
        start = "".join(f'\n[[hypothesis]]\nid = "C_start{i}"\ntext = "t"\nsource = "s"\nreason = "r"\n'
                        f'seen_until = "2026-11-04"\nclaims = [{{ on = "up_odds", op = "start", fixed = true, reason = "r" }}]\n'
                        for i in range(3))
        errors, warns = CH.audit(self.kn(TEXT + start), self.want())
        self.assertFalse(any("[계산 조각]" in e and "상한" in e for e in errors), errors)
        self.assertFalse(any("C_start" in w and "세운 효과" in w for w in warns), warns)

    def test_versions_and_places_are_checked(self):
        self.assertEqual(V.check(), [])
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "v1.toml").write_text(C.VERSIONS.joinpath("v1.toml").read_text(encoding="utf-8") + "\n# 고침\n",
                                            encoding="utf-8")
            (folder / "versions.toml").write_text(C.VERSIONS.joinpath("versions.toml").read_text(encoding="utf-8"),
                                                  encoding="utf-8")
            self.assertTrue(any("지문" in e for e in V.check(folder)))
        errs = RS.check_places([{"id": "X", "name": "n", "url": "u", "angle": "날씨", "added": "d", "covers": ["S_nope"]}],
                               {"S_q_rate_up"})
        self.assertEqual(len(errs), 3)

    def test_checks_report_replay_and_measured_separately(self):
        got = CH.piece_direction("C_trend_60")
        self.assertIsInstance(got, list)
        self.assertEqual([src for src, _, _ in got], ["replay", "measured"])
        # 재생: 예전에는 날 수로 세어 '9칸 중 4 반박'이었다. 2026-11-04부터 양쪽 5구간 — 재생 넉 달은 칸마다 구간이 1~2개라 시험 전
        self.assertTrue(got[0][1].startswith("시험 전"), got[0][1])
        self.assertIn("구간", got[0][2])
        # 2026-10-21에 세운 쪼갠 조각은 재생 기록으로 시험하지 않는다
        self.assertEqual([src for src, _, _ in CH.piece_direction("C_trend_20_calm")], ["measured"])
        kn = SCHEMA.load(C.KNOWLEDGE)
        lines = SCORE.card(kn, kn.nodes["C_trend_60"], CH.CHECKS, CH.SOURCE)
        self.assertEqual({l.source for l in lines}, {"replay", "measured"})

    def test_split_piece_adds_a_condition(self):
        kn = SCHEMA.load(C.KNOWLEDGE)
        self.assertEqual(AUDIT.unsplit_revisions(kn), [])
        old, new = kn.nodes["C_trend_20"], kn.nodes["C_trend_20_calm"]
        self.assertEqual(old.data["status"], "retired")
        self.assertEqual(set(new.refs("conditions")) - set(old.refs("conditions")), {"S_vol_high"})
        self.assertGreater(new.data["seen_until"], C.REPLAY_END.isoformat())


class ExtraCondition(unittest.TestCase):
    def test_also_condition_is_required_to_switch_on(self):
        cl = F.Claim("X", "up_odds", "mul", "r", "up", "S_trend_20", "예", (("S_vol_high", "아니오"),))
        self.assertEqual(F._x({"S_trend_20": "예", "S_vol_high": "아니오"}, cl), 1)
        self.assertEqual(F._x({"S_trend_20": "예", "S_vol_high": "예"}, cl), 0)
        raw = tomllib.loads(TEXT)
        calm = next(h for h in raw["hypothesis"] if h["id"] == "C_trend_20_calm")
        for e in calm.get("effect", []):
            self.assertEqual(e["when"], {"S_trend_20": "예", "S_vol_high": "아니오"})
        old = next(h for h in raw["hypothesis"] if h["id"] == "C_trend_20")
        self.assertTrue(old.get("fit") and old.get("effect"))  # 이전 버전은 맞춘 기록을 지우지 않는다


if __name__ == "__main__":
    unittest.main()
