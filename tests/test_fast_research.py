"""조사를 '오늘 판단용 빠른 정보'로 (2026-11-04, 결정 E4·E5·E6): 각도 질문, numbers·as_of 칸과 검사, 숫자 상태의 rule 문법과
코드가 정하는 값, dir = "sign"(숫자의 부호 쪽) 방향 시험, 숫자 상태·씨앗의 점검, 흉내 내기 하루 흐름(입력에 numbers·as_of)."""
import contextlib
import datetime as dt
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from market import checks as CH
from market import config as C
from market import daily as D
from market import hand as H
from market import logic
from market import records as R
from market import research as RS
from tests.test_daily import TICK, at, fill, make_root

try:
    SCHEMA, SCORE, AUDIT = logic.core()
except SystemExit:
    SCHEMA = None

TEXT = C.KNOWLEDGE.read_text(encoding="utf-8")


class RuleAndNumbers(unittest.TestCase):
    def test_rule_grammar_is_small_and_safe(self):
        self.assertEqual(RS.parse_rule("abs > 1"), (True, ">", 1.0))
        self.assertEqual(RS.parse_rule(" <= -0.5 "), (False, "<=", -0.5))
        self.assertEqual(RS.parse_rule(">=2"), (False, ">=", 2.0))
        for bad in ("", "> x", "== 1", "abs abs > 1", "__import__('os').system('true')", "> 1 or True", "1 < 2"):
            with self.assertRaises(ValueError, msg=bad):
                RS.parse_rule(bad)
        self.assertTrue(RS.rule_holds(RS.parse_rule("abs > 1"), -1.5))
        self.assertFalse(RS.rule_holds(RS.parse_rule("abs > 1"), 1.0))
        self.assertTrue(RS.rule_holds(RS.parse_rule("< -1"), -1.5))
        self.assertFalse(RS.rule_holds(RS.parse_rule("< -1"), 1.5))

    def test_number_value_with_per(self):
        spec = {"from": "a_pct", "per": "b_pct", "rule": "abs > 1", "parsed": RS.parse_rule("abs > 1")}
        self.assertEqual(RS.number_value(spec, {"a_pct": -2.5, "b_pct": 1.0}), ("예", -2.5))
        self.assertEqual(RS.number_value(spec, {"a_pct": 0.5, "b_pct": 1.0}), ("아니오", 0.5))
        self.assertEqual(RS.number_value(spec, {"a_pct": 2.0}), ("모름", None))          # 잣대 없음
        self.assertEqual(RS.number_value(spec, {"a_pct": 2.0, "b_pct": 0}), ("모름", None))  # 0으로 나누지 않는다
        self.assertEqual(RS.number_value(spec, {"a_pct": True, "b_pct": 1}), ("모름", None))  # 참/거짓은 수가 아님

    def test_knowledge_numeric_states(self):
        specs = RS.numeric_states(tomllib.loads(TEXT))
        first = {"S_q_overnight_gap", "S_q_surprise", "S_q_foreign_net"}
        self.assertLessEqual(first, set(specs))
        for st, spec in specs.items():
            for k in RS.number_keys(spec):
                self.assertTrue(R.NUMBER_KEY.match(k), k)
                # 첫 셋의 숫자 이름은 각도 질문의 안내 목록에도 있다(에이전트가 같은 이름으로 받아 오게). 매일 실행이 더하는
                # 숫자 상태는 코드를 고치지 않으므로 이름 모양만 본다(audit도 같다)
                if st in first:
                    self.assertTrue(any(k in keys for keys in RS.NUMBER_KEYS.values()), f"{st}: {k}가 NUMBER_KEYS에 없음")


class RecordFields(unittest.TestCase):
    def base(self, **extra):
        return {"kind": "research", "id": "research:2026-11-05:1", "run": "2026-11-05", "question": "q", "state": "",
                "angle": "간밤 반응", "asked": "a", "answer": "b", "looked": [], "agent": "x", **extra}

    def test_research_numbers_and_as_of(self):
        ok = self.base(numbers={"kr_overnight_pct": -1.2, "usdkrw_krw": 1341}, as_of="2026-11-05T07:20+09:00")
        self.assertEqual(R.line_errors("research", ok, "w"), [])
        self.assertEqual(R.line_errors("research", self.base(), "w"), [])  # 2026-11-05 전 기록에는 없다
        bad = self.base(numbers={"Kr Overnight": 1, "kr_gap": 1, "kr_overnight_pct": True, "oil_pct": float("nan")},
                        as_of="2026-11-05 07:20")
        errs = " ".join(R.line_errors("research", bad, "w"))
        self.assertIn("'Kr Overnight'", errs)
        self.assertIn("'kr_gap'", errs)         # 끝이 단위가 아님
        self.assertIn("수가 아님", errs)          # True, nan
        self.assertIn("시간대", errs)              # as_of에 시간대가 없음
        self.assertTrue(R.line_errors("research", self.base(numbers=[1, 2]), "w"))

    def test_hand_reading_numbers(self):
        line = {"kind": "hand_reading", "id": "h", "run": "2026-11-05", "values": {}, "why": {}, "places": {},
                "research": [], "numbers": {"S_q_overnight_gap": {"kr_overnight_pct": -2.5}}}
        self.assertEqual(R.line_errors("hand_reading", line, "w"), [])
        line["numbers"] = {"S_q_overnight_gap": {"kr_overnight_pct": "많이"}}
        self.assertTrue(R.line_errors("hand_reading", line, "w"))


class SignDirection(unittest.TestCase):
    """dir = "sign": 수익률에 그날 숫자의 부호를 곱해 '예'인 날과 '아니오'인 날의 '부호 쪽으로 간 비율'을 견준다."""

    def test_compare_sign(self):
        ok, shown = CH._compare("up_odds", "sign", [0.01, 0.02, -0.01], [0.01, -0.01, -0.02])
        self.assertTrue(ok)
        self.assertIn("부호 쪽으로 간 비율", shown)
        self.assertFalse(CH._compare("up_odds", "sign", [-0.01, -0.02], [0.01, 0.02])[0])

    def write(self, folder: Path, aligned_yes: bool):
        hrs, preds, scores = [], [], []
        for i in range(20):
            day = (dt.date(2026, 11, 5) + dt.timedelta(days=i)).isoformat()
            yes = i % 2 == 0
            x = (2.0 if i % 4 == 0 else -2.0) if yes else (0.5 if i % 4 == 1 else -0.5)
            if i == 19:
                x = 0.0  # 부호 없는 날은 세지 않는다
            sign = (x > 0) - (x < 0)
            ret = sign * 0.01 if (yes and aligned_yes) else (-sign * 0.01 if yes else (0.01 if i % 3 else -0.01))
            hrs.append({"kind": "hand_reading", "id": f"hand_reading:{day}", "run": day, "values": {"S_n": "예" if yes else "아니오"},
                        "why": {}, "places": {}, "research": [], "numbers": {"S_n": {"a_pct": x}}})
            pid = f"{day}:calc:069500:1"
            preds.append({"kind": "prediction", "id": pid, "run": day, "predictor": "calc", "asset": "069500", "horizon": 1,
                          "base_date": day})
            scores.append({"kind": "score", "id": f"score:{pid}", "run": day, "prediction": pid, "ret": ret})
        for kind, rows in (("hand_reading", hrs), ("prediction", preds), ("score", scores)):
            (folder / f"{kind}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                                  encoding="utf-8")

    def judge(self, folder: Path):
        node = {"seen_until": "2026-11-04", "conditions": ["S_n"],
                "claims": [{"on": "up_odds", "dir": "sign", "when": "예", "scope": "home", "span": ["다음"]}]}
        raw = {"state": [{"id": "S_n", "answers": ["예", "아니오", "모름"],
                          "number": {"from": "a_pct", "rule": "abs > 1", "unit": "u"}}]}
        with mock.patch.object(C, "RECORDS", folder), mock.patch.object(CH, "_node", lambda nid: node), \
                mock.patch.object(CH, "_raw", lambda: raw):
            return CH.research_direction("Q_n")

    def test_research_direction_sign(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), aligned_yes=True)
            verdict, detail = self.judge(Path(d))
            self.assertTrue(verdict.startswith("지지됨"), verdict + detail)
            self.assertIn("부호 쪽으로 간 비율 예 1.00", detail)
            self.assertIn("S_n=부호 없음 1", detail)
        with tempfile.TemporaryDirectory() as d:
            self.write(Path(d), aligned_yes=False)
            self.assertTrue(self.judge(Path(d))[0].startswith("반박됨"))


@unittest.skipIf(SCHEMA is None, "PROVISO-Nesy를 찾지 못함 (PROVISO_PATH)")
class NumericAudit(unittest.TestCase):
    def kn(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.toml"
            p.write_text(text, encoding="utf-8")
            return SCHEMA.load(p)

    def want(self):
        raw = tomllib.loads(TEXT)
        return {h["id"]: h.get("effect", []) for h in raw.get("hypothesis", []) if h.get("claims")}

    def test_current_knowledge_passes(self):
        kn = SCHEMA.load(C.KNOWLEDGE)
        self.assertEqual(SCHEMA.validate(kn), [])
        errors, _ = CH.audit(kn, self.want())
        self.assertEqual(errors, [])
        self.assertEqual(AUDIT.unsplit_revisions(kn), [])  # Q_overnight_lead → v2는 조건(숫자 상태)을 바꿨다
        for nid in ("Q_overnight_lead_v2", "Q_surprise_follow", "Q_foreign_flow"):
            n = kn.nodes[nid]
            self.assertTrue(n.data["seed"] and n.data["strength"] == "낮음", nid)
            for ref in n.refs("evidence") + n.refs("against"):
                self.assertEqual(kn.nodes[ref].kind, "fact")

    def test_bad_numeric_state_and_sign_claim(self):
        bad = ('\n[[state]]\nid = "S_q_bad_num"\ntext = "t"\nknown_before = true\nread_by = "조사"\nangle = "수급"\nask = "a"\n'
               'answers = ["예", "아니오", "오름"]\nnumber = { from = "BadKey", rule = "> one" }\n'
               '\n[[hypothesis]]\nid = "Q_bad_sign"\ntext = "t"\nsource = "s"\nreason = "r"\nread_by = "조사"\n'
               'conditions = ["S_q_fear_above_year"]\ncheck = ["research_direction"]\nseen_until = "2026-11-04"\n'
               'seed = true\nstrength = "낮음"\n'
               'claims = [{ on = "up_odds", dir = "sign", when = "예", scope = "home", reason = "r" }]\n')
        errors = " ".join(CH.audit(self.kn(TEXT + bad), self.want())[0])
        for word in ("number.from 'BadKey'", "rule '> one'", "number.unit", "예/아니오/모름", 'dir = "sign"'):
            self.assertIn(word, errors)

    def test_old_angle_is_refused(self):
        bad = TEXT.replace('angle = "수급"', 'angle = "한국 시장"', 1)
        errors = " ".join(CH.audit(self.kn(bad), self.want())[0])
        self.assertIn("angle은", errors)

    def test_places_cover_number_names(self):
        places = RS.load_places()
        self.assertEqual(RS.check_places(places, {s["id"] for s in tomllib.loads(TEXT)["state"]}), [])
        self.assertTrue({p["angle"] for p in places} <= set(RS.ANGLES))
        got = {k for p in places for k in p.get("covers", []) if R.NUMBER_KEY.match(k)}
        for k in ("kr_overnight_pct", "kr_foreign_net_krw100m", "surprise_z", "coin_funding_pct"):
            self.assertIn(k, got)
        self.assertTrue(RS.check_places([{"id": "P_x", "name": "n", "url": "u", "angle": "수급", "added": "d",
                                          "covers": ["not a key"]}], set()))


class Questions(unittest.TestCase):
    def test_angle_questions_are_for_today(self):
        raw = tomllib.loads(TEXT)
        qs = RS.questions(dt.date(2026, 11, 5), raw, RS.load_places(), {})
        now = [q for q in qs if not q["state"]]
        self.assertEqual([q["angle"] for q in now], list(RS.ANGLES))
        self.assertTrue(all(q["question"].startswith("q:2026-11-05:now:") for q in now))
        for q in qs:
            self.assertLessEqual(len(q["asked"]), R.SUMMARY_MAX, q["question"])
            self.assertNotIn("왜 움직였", q["asked"])        # 사후 설명 질문은 묻지 않는다
            self.assertNotIn("며칠~4주", q["asked"])
        self.assertIn("kr_foreign_net_krw100m", next(q for q in now if q["angle"] == "수급")["numbers"])
        deep = RS.deep_question(dt.date(2026, 11, 5), "069500", "한국 묶음")
        self.assertEqual((deep["question"], deep["angle"]), ("q:2026-11-05:deep:069500", "069500-2차"))
        self.assertLessEqual(len(deep["asked"]), R.SUMMARY_MAX)
        self.assertNotIn("왜 일어났", deep["asked"])

    def test_questions_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(RS.main(["questions", "2026-11-05"]), 0)
        text = out.getvalue()
        for part in ("q:2026-11-05:now:간밤 반응", "q:2026-11-05:S_q_overnight_gap", "숫자(numbers): kr_overnight_pct",
                     "as_of"):
            self.assertIn(part, text)


class FastInput(unittest.TestCase):
    """흉내 내기(임시 폴더): FAST_SINCE를 흉내 낸 날로 당겨 numbers·as_of 요구, 판단 뒤 정보 거절, 숫자 상태가 조사 답의 숫자로
    정해지는 하루 흐름(prepare → 입력 채우기 → record)."""

    def run_day(self, change):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_root(tmp.name)
        d = dt.date(2025, 9, 2)
        with mock.patch.object(RS, "FAST_SINCE", "2025-09-01"):
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertIn("_numbers_form", tpl)
            self.assertIn("_deep_form", tpl)
            self.assertTrue(all("numbers" in r and r["as_of"] == "" for r in tpl["research"]))
            inp = fill(tpl)
            for r in inp["research"]:
                r["as_of"] = "2025-09-02T07:30+09:00"
                r["numbers"] = {**r["numbers"], "events_n": 1}
            change(inp)
            p = root.day_dir(d) / "fast.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = D.record(root, d, at(d, 8, 30), p)
        return code, root, out.getvalue()

    def test_missing_numbers_or_as_of_is_refused(self):
        def drop(inp):
            inp["research"][0].pop("numbers")
            inp["research"][1]["as_of"] = ""
        code, root, out = self.run_day(drop)
        self.assertEqual(code, 1)
        self.assertIn("numbers(숫자 이름_단위 → 값)가 없음", out)
        self.assertIn("as_of(답의 숫자가 가리키는 시각", out)
        self.assertFalse(list(root.records.glob("*.jsonl")))

    def test_information_after_decision_is_refused(self):
        def late(inp):
            inp["research"][0]["as_of"] = "2025-09-02T09:10+09:00"  # 판단(08:30) 뒤
        code, root, out = self.run_day(late)
        self.assertEqual(code, 1)
        self.assertIn("판단 뒤 정보", out)

    def test_day_with_numbers_records_them(self):
        def numbers(inp):
            now = {r["angle"]: r for r in inp["research"] if not r["state"]}
            now["간밤 반응"]["numbers"].update(kr_overnight_pct=0.4)            # 잣대는 틀이 채웠다 → 작은 격차 '아니오'
            now["수급"]["numbers"].update(kr_foreign_net_krw100m=-15298, kr_foreign_sd20_krw100m=6000)  # −2.5배 → '예'
            now["일정·예상치"]["numbers"] = {}                                  # 숫자 없는 답: 경고만, surprise는 안 물음
            inp["research"] = [r for r in inp["research"] if r["state"] not in RS.seed_states(tomllib.loads(TEXT))]
        code, root, out = self.run_day(numbers)
        self.assertEqual(code, 0, out)
        self.assertIn("숫자 없는 답", out)
        res = R.read(root.records, "research")
        self.assertTrue(all(r["as_of"] == "2025-09-02T07:30+09:00" and isinstance(r["numbers"], dict) for r in res))
        hr = R.read(root.records, "hand_reading")[0]
        self.assertEqual(hr["values"]["S_q_overnight_gap"], "아니오")
        self.assertEqual(hr["values"]["S_q_foreign_net"], "예")
        self.assertNotIn("S_q_surprise", hr["values"])  # 숫자가 없으면 씨앗은 '안 물음'
        self.assertEqual(hr["numbers"]["S_q_foreign_net"], {"kr_foreign_net_krw100m": -15298, "kr_foreign_sd20_krw100m": 6000})
        self.assertEqual(R.check_folder(root.records), [])

    def test_resolve_numbers_prefers_reading_then_own_question(self):
        raw = tomllib.loads(TEXT)
        inp = {"readings": {"S_q_surprise": {"numbers": {"surprise_z": -1.4}, "places": ["P_econoday"], "why": ""}},
               "research": [{"state": "", "numbers": {"surprise_z": 0.2, "kr_foreign_net_krw100m": 100,
                                                      "kr_foreign_sd20_krw100m": 50}, "looked": []},
                            {"state": "S_q_foreign_net", "numbers": {"kr_foreign_net_krw100m": 10,
                                                                     "kr_foreign_sd20_krw100m": 50},
                             "looked": [{"place": "P_krx", "result": "찾음"}]}]}
        states = ["S_q_surprise", "S_q_foreign_net", "S_q_overnight_gap"]
        readings, _, numbers, errs, warns = H.resolve_numbers(inp, raw, states, set(states))
        self.assertEqual(errs, [])
        self.assertEqual(readings["S_q_surprise"]["value"], "예")           # 대 보기의 숫자가 먼저
        self.assertEqual(readings["S_q_foreign_net"]["value"], "아니오")    # 그 상태를 물은 답(10 ÷ 50)이 먼저
        self.assertEqual(readings["S_q_foreign_net"]["places"], ["P_krx"])
        self.assertTrue(any("다르게" in w for w in warns))                  # 답들이 같은 숫자를 다르게 줌
        self.assertNotIn("S_q_overnight_gap", readings)                    # 숫자 없는 씨앗 = 안 물음
        self.assertEqual(numbers["S_q_surprise"], {"surprise_z": -1.4})
        lines = RS.preview_numbers(inp, raw)  # python -m market.research numeric 가 보여 주는 것(record와 같은 함수)
        self.assertTrue(any(line.startswith("- S_q_surprise = 예") for line in lines), lines)
        self.assertTrue(any("S_q_overnight_gap: 숫자 없음" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
