"""2단계 장치: 조사 기록 칸, 볼 곳 성적, 새 곳 시도 몫, 한 번만 세기, 조사 조각·버전·볼 곳 점검, 더한 조건 맞추기."""
import datetime as dt
import tempfile
import tomllib
import unittest
from pathlib import Path

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
        self.assertTrue(got[0][1].startswith("반박"))  # 재생: 9칸 중 4
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
