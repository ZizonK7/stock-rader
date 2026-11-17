"""2026-11-17 (잼민이 지시서 p562): 종목 정규장 값, 뿌리 묶기(일요일), 지켜보는 종목 차례, 물어보는 명령. 네트워크를 쓰지 않는다."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market import ask as AK
from market import config as C
from market import daily as D
from market import hand as H
from market import lessons as LS
from market import prices as PR
from market import records as R
from market import stocks as ST
from tests import test_daily as TD

CODE = "005930"


def row(d, o, c, src):
    return PR.Row(d, o, max(o, c), min(o, c), c, 1.0, src, "t")


class Regular(unittest.TestCase):
    """종목 일봉은 날짜마다 정규장 값(야후)으로, 없는 날만 네이버 값 + 알림."""

    def test_yahoo_bar_replaces_naver_close_and_missing_day_is_noted(self):
        d1, d2 = dt.date(2026, 11, 13), dt.date(2026, 11, 16)
        naver = [row(d1, 214000, 205500, "naver"), row(d2, 206500, 195000, "naver")]   # 종가가 시간외 포함
        yahoo = [row(d1, 214000, 206000, "yahoo")]                                       # 11-16은 아직 없음
        C.STOCK_NAMES.setdefault(CODE, "삼성전자")
        notes = []
        with mock.patch.object(PR, "fetch_yahoo", return_value=yahoo):
            got = ST.regular(CODE, naver, notes)
        self.assertEqual([(r.close, r.source) for r in got], [(206000, "yahoo"), (195000, "naver")])
        self.assertTrue(any("2026-11-16" in n for n in notes))
        with mock.patch.object(PR, "fetch_yahoo", side_effect=OSError("막힘")):
            notes = []
            self.assertEqual(ST.regular(CODE, naver, notes), naver)
            self.assertTrue(notes)


def review(rid, run, reason):
    return {"id": rid, "run": run, "reason": reason}


class Lessons(unittest.TestCase):
    """매일 피드백 정리(2026-11-17 p564 — 처음엔 일요일만): 아직 묶지 않은 '다음엔'(BIND_FROM 뒤)을 빠짐없이."""
    sunday = dt.date(2026, 11, 22)

    def due(self):
        reviews = [review("review:a", "2026-11-17", "x. 다음엔: 종목 1봉 크기를 1.0배로"),
                   review("review:b", "2026-11-19", "지난 다음엔 review:z → 해 봄 — 됐다. 다음엔: 계속"),
                   review("review:c", "2026-11-20", "y. 다음엔: 그대로 — 맞음"),          # 그대로는 묶지 않는다
                   review("review:old", "2026-11-10", "옛. 다음엔: 무엇"),                  # BIND_FROM 전(씨앗에 들어 있다)
                   review("review:done", "2026-11-18", "w. 다음엔: 묶임")]                  # 이미 묶었다
        today = [{"prediction": "p1", "reason": "z. 다음엔: 금리 조각을 같이 안 움직이는 종목엔 안 쓴다"}]
        bound = [{"reviews": ["review:done"]}]
        return LS.due(reviews, bound, self.sunday, today)

    def test_week_due_and_all_must_be_grouped(self):
        due = self.due()
        self.assertEqual(set(due), {"review:a", "review:b", "review:p1"})
        known = {"L01", "L04"}
        ok = {"lessons": [
            {"root": "L04", "kind": "같음", "reviews": ["review:p1"], "tried": {}},
            {"root": "L12", "kind": "새 뿌리", "sentence": "종목 하루 크기는 평소의 1.0배에서", "reviews": ["review:a", "review:b"],
             "tried": {"나아짐": 1}}]}
        self.assertEqual(LS.input_errors(ok, self.sunday, due, known), [])
        miss = {"lessons": ok["lessons"][:1]}
        self.assertTrue(any("어느 뿌리에도 없다" in e for e in LS.input_errors(miss, self.sunday, due, known)))
        bad_tried = json.loads(json.dumps(ok))
        bad_tried["lessons"][1]["tried"] = {}
        self.assertTrue(any("tried 합" in e for e in LS.input_errors(bad_tried, self.sunday, due, known)))
        conflict = json.loads(json.dumps(ok))
        conflict["lessons"][0] = {"root": "L13", "kind": "충돌", "sentence": "s", "reviews": ["review:p1"], "tried": {},
                                  "against": "L01"}
        self.assertTrue(any("split" in e for e in LS.input_errors(conflict, self.sunday, due, known)))
        self.assertTrue(LS.input_errors({}, self.sunday, due, known))                 # 비면 거절(매일)
        self.assertTrue(LS.input_errors({}, dt.date(2026, 11, 19), due, known))      # 일요일이 아니어도
        self.assertEqual(LS.input_errors({}, dt.date(2026, 11, 17), {}, known), [])  # 그 전 날은 묻지 않는다
        self.assertEqual(LS.input_errors({"lessons": []}, dt.date(2026, 11, 19), {}, known), [])  # 묶을 것이 없으면 []

    def test_generalize_merge_retire(self):
        """고침(일반화)·합침·내림 — 지난 기록은 그대로, 목록에서만 내리고 까닭을 남긴다."""
        due = self.due()
        known = {"L01", "L04", "L05"}
        base = [{"root": "L04", "kind": "같음", "reviews": ["review:p1"], "tried": {}},
                {"root": "L12", "kind": "새 뿌리", "sentence": "s", "reviews": ["review:a", "review:b"], "tried": {"모름": 1}}]
        ok = {"lessons": base + [
            {"root": "L01", "kind": "고침", "sentence": "더 넓은 문장", "why": "세 곳에서 같은 말"},
            {"root": "L05", "kind": "합침", "into": "L04", "why": "같은 말"}]}
        self.assertEqual(LS.input_errors(ok, self.sunday, due, known), [])
        for bad in ({"root": "L01", "kind": "고침", "why": "x"},                       # 고친 문장 없음
                    {"root": "L05", "kind": "합침", "into": "L05", "why": "x"},        # 자기에게
                    {"root": "L05", "kind": "내림"},                                   # 까닭 없음
                    {"root": "L09", "kind": "내림", "why": "x"}):                      # 없는 뿌리
            self.assertTrue(LS.input_errors({"lessons": base + [bad]}, self.sunday, due, known), bad)
        reused = {"lessons": base[:1] + [{**base[1], "root": "L07"}]}
        self.assertTrue(any("이미 있다" in e for e in LS.input_errors(reused, self.sunday, due, known, {"L07"})))
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "lessons.toml"
            seed.write_text('[[root]]\nid = "L01"\nsentence = "a"\n[[root]]\nid = "L04"\nsentence = "b"\n'
                            '[[root]]\nid = "L05"\nsentence = "c"\n', encoding="utf-8")
            recs = Path(tmp) / "records"
            R.write(recs, "lesson", [dict(x, id=f"lesson:2026-11-22:{i}", run="2026-11-22", reviews=x.get("reviews", []),
                                          tried={k: 0 for k in LS.TRIED})
                                     for i, x in enumerate(LS.record_lines(self.sunday, {"lessons": [
                                         {"root": "L05", "kind": "같음", "reviews": ["r1", "r2"], "tried": {}},
                                         {"root": "L01", "kind": "고침", "sentence": "더 넓은 문장", "why": "w"},
                                         {"root": "L05", "kind": "합침", "into": "L04", "why": "같은 말"}]}))])
            now = {r["id"]: r for r in LS.current(seed, recs)}
            self.assertEqual(set(now), {"L01", "L04"})
            self.assertEqual(now["L01"]["sentence"], "더 넓은 문장")
            self.assertEqual(now["L04"]["reviews"], 2)          # 합친 뿌리의 건수가 옮겨 감
            self.assertIn("L04에 합침", LS.gone(seed, recs)[0]["gone"])

    def test_seed_file_and_current_view(self):
        self.assertEqual(LS.check(LS.load()), [])
        roots = LS.current()
        self.assertGreaterEqual(len(roots), 11)
        self.assertTrue(any("C17" in sp for r in roots for sp in r["splits"]))


class Rotation(unittest.TestCase):
    def test_asked_first_then_oldest_hand_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = D.Root(Path(tmp))
            (root.path / "asked.toml").write_text('[[q]]\ncode = "000003"\nname = "셋"\nat = "2026-11-17T12:00:00+09:00"\n',
                                                  encoding="utf-8")
            day = type("Day", (), {})()
            day.held = ["000009"]
            day.stock_ok = ["000001", "000002", "000003", "000009"]
            day.recs = {"prediction": [{"predictor": "hand", "asset": "000001", "run": "2026-11-10"},
                                       {"predictor": "hand", "asset": "000003", "run": "2026-11-12"}]}
            # 물어본 000003 먼저(물어본 뒤 손 예측 없음), 그다음 손 예측이 없던 000002, 든 000009는 빠진다
            self.assertEqual(D.watch_rotation(day, root), ["000003", "000002", "000001"])
            day.recs["prediction"].append({"predictor": "hand", "asset": "000003", "run": "2026-11-18"})
            self.assertEqual(D.watch_rotation(day, root), ["000002", "000001", "000003"])

    def test_watch_today_must_be_predicted(self):
        ctx_base = {"day": "2026-11-18", "raw": {"state": [], "hypothesis": []}, "research_states": [], "places": [],
                    "stale": set(), "predictable": [], "stocks": ["000002"], "held": [], "calc_values": {},
                    "due_reviews": {}, "due_checks": {}, "node_ids": set()}
        errs, _ = H.validate({"day": "2026-11-18", "hand": [], "weights": {}}, {**ctx_base, "watch_today": ["000002"]})
        self.assertTrue(any("오늘 차례 종목" in e for e in errs))
        errs, _ = H.validate({"day": "2026-11-18", "hand": [], "weights": {}}, ctx_base)
        self.assertFalse(any("오늘 차례 종목" in e for e in errs))

    def test_stock_total_cap(self):
        """종목 수 상한 8·합 상한 0.45(2026-11-17): 넷을 0.12씩(합 0.48)이면 합 상한에 걸리고, 넷을 0.11씩(0.44)이면 안 걸린다."""
        codes = ["000001", "000002", "000003", "000004"]
        ctx = {"day": "2026-11-18", "raw": {"state": [], "hypothesis": []}, "research_states": [], "places": [],
               "stale": set(), "predictable": [], "stocks": codes, "held": [], "calc_values": {}, "due_reviews": {},
               "due_checks": {}, "node_ids": set(), "buyable": codes}
        for w, bad in ((0.12, True), (0.11, False)):
            inp = {"day": "2026-11-18", "hand": [], "weights": {a: {"target": w, "reason": "가짜"} for a in codes}}
            errs, _ = H.validate(inp, ctx)
            self.assertEqual(any("종목 비중 합" in e for e in errs), bad, w)
            self.assertFalse(any("종목 4개 > 상한" in e for e in errs))


class ChartName(unittest.TestCase):
    def test_html_escaped_name_matches(self):
        self.assertEqual(ST.chart_name('<chartdata symbol="033780" name="KT&amp;G" count="1">'), "KT&G")


class Ask(unittest.TestCase):
    def test_lookup_uses_the_list_first(self):
        with mock.patch.object(ST, "load", return_value=[{"code": "003230", "name": "삼양식품", "market": "KOSPI"}]), \
                mock.patch.object(PR, "_get", side_effect=AssertionError("목록에 있으면 네트워크를 쓰지 않는다")):
            self.assertEqual(AK.lookup("삼양식품"), ("003230", "삼양식품", "KOSPI"))
            self.assertEqual(AK.lookup("003230"), ("003230", "삼양식품", "KOSPI"))

    def test_asked_stock_is_added_as_buyable(self):
        """사는 목록과 지켜보는 목록을 나누지 않는다(2026-11-17 사용자): 더한 종목은 buy 칸 없이 — 살 수 있다."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "stocks.toml"
            ST.add(p, "003230", "삼양식품", "사용자 질문", "KOSPI", dt.date(2026, 11, 17))
            s = ST.load(p)[0]
            self.assertNotIn("buy", s)
            self.assertEqual(ST.check(ST.load(p)), [])


class DailyWatch(unittest.TestCase):
    """흉내 낸 하루: 목록의 다른 종목이 손 예측 없이도 기록되고, 오늘 차례 종목은 손 예측이 요구된다."""

    def test_listed_stock_gets_lean_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = TD.make_root(tmp)
            (root.path / "stocks.toml").write_text(
                '[[stock]]\ncode = "777770"\nname = "가짜지켜봄"\nmarket = "KOSPI"\nadded = "2025-01-01"\nwhy = "시험"\n'
                '', encoding="utf-8")
            from tests import fixtures
            PR.append("777770", list(fixtures.series("777770", dt.date(2024, 3, 1), dt.date(2025, 10, 31))), root.prices)
            d = dt.date(2025, 9, 2)
            assert D.prepare(root, d, TD.at(d, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertEqual(list(tpl["_watch_today"]), ["777770"])
            inp = TD.fill(tpl)
            for rs in inp["stock_readings"].values():
                for rd in rs.values():
                    rd.update(value="모름", why="가짜: 실적 발표 없음")
            for h in inp["hand"]:
                if h["asset"] == "777770":
                    h.update(used=[], decisive=[], info="기타", p_up=0.5)
            p = root.day_dir(d) / "filled.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, TD.at(d, 8, 30), p), 0)
            preds = [p for p in R.read(root.records, "prediction") if p["asset"] == "777770"]
            # 오늘 차례(물어본 것 없음, 손 예측 없던 종목)라 손 예측과 온전한 줄
            self.assertIn("hand", {p["predictor"] for p in preds})
            self.assertTrue(R.read(root.records, "reading"))


if __name__ == "__main__":
    unittest.main()


class Precheck(unittest.TestCase):
    """피드백 정리를 읽었나(2026-11-18 판단부터, market/flow.py): 밟은 뿌리·고친 것·정리한 에이전트가 없으면 거절."""

    def test_precheck_required(self):
        from market import flow as FL
        day = dt.date(2026, 11, 18)
        ok = {"precheck": {"roots": ["L04"], "note": "종목 1봉을 0.5로 되돌림", "read": "뿌리 목록 11개, 최근 돌아보기 6건"}}
        self.assertEqual(FL.precheck_errors(ok, day, {"L04"}), [])
        self.assertTrue(FL.precheck_errors({}, day, {"L04"}))
        for k in ("note", "read"):
            self.assertTrue(FL.precheck_errors({"precheck": {**ok["precheck"], k: ""}}, day, {"L04"}))
        unknown = json.loads(json.dumps(ok))
        unknown["precheck"]["roots"] = ["L99"]
        self.assertTrue(FL.precheck_errors(unknown, day, {"L04"}))
        self.assertEqual(FL.precheck_errors({}, dt.date(2026, 11, 17), set()), [])   # 그 전 날은 묻지 않는다
