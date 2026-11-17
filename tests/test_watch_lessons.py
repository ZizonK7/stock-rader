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
    sunday = dt.date(2026, 11, 22)

    def due(self):
        reviews = [review("review:a", "2026-11-17", "x. 다음엔: 종목 1봉 크기를 1.0배로"),
                   review("review:b", "2026-11-19", "지난 다음엔 review:z → 해 봄 — 됐다. 다음엔: 계속"),
                   review("review:c", "2026-11-20", "y. 다음엔: 그대로 — 맞음"),          # 그대로는 묶지 않는다
                   review("review:old", "2026-11-10", "옛. 다음엔: 무엇")]                  # 이 주가 아니다
        today = [{"prediction": "p1", "reason": "z. 다음엔: 금리 조각을 같이 안 움직이는 종목엔 안 쓴다"}]
        return LS.week_due(reviews, self.sunday, today)

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
        self.assertTrue(LS.input_errors({}, self.sunday, due, known))                 # 일요일엔 비면 거절
        self.assertEqual(LS.input_errors({}, dt.date(2026, 11, 18), due, known), [])  # 다른 날은 없다

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
            self.assertEqual(D.watch_rotation(day, root), ["000003", "000002"])
            day.recs["prediction"].append({"predictor": "hand", "asset": "000003", "run": "2026-11-18"})
            self.assertEqual(D.watch_rotation(day, root), ["000002", "000001"])

    def test_watch_today_must_be_predicted(self):
        ctx_base = {"day": "2026-11-18", "raw": {"state": [], "hypothesis": []}, "research_states": [], "places": [],
                    "stale": set(), "predictable": [], "stocks": ["000002"], "held": [], "calc_values": {},
                    "due_reviews": {}, "due_checks": {}, "node_ids": set()}
        errs, _ = H.validate({"day": "2026-11-18", "hand": [], "weights": {}}, {**ctx_base, "watch_today": ["000002"]})
        self.assertTrue(any("지켜보는 종목" in e for e in errs))
        errs, _ = H.validate({"day": "2026-11-18", "hand": [], "weights": {}}, ctx_base)
        self.assertFalse(any("지켜보는 종목" in e for e in errs))


class ChartName(unittest.TestCase):
    def test_html_escaped_name_matches(self):
        self.assertEqual(ST.chart_name('<chartdata symbol="033780" name="KT&amp;G" count="1">'), "KT&G")


class Ask(unittest.TestCase):
    def test_lookup_uses_the_list_first(self):
        with mock.patch.object(ST, "load", return_value=[{"code": "003230", "name": "삼양식품", "market": "KOSPI"}]), \
                mock.patch.object(PR, "_get", side_effect=AssertionError("목록에 있으면 네트워크를 쓰지 않는다")):
            self.assertEqual(AK.lookup("삼양식품"), ("003230", "삼양식품", "KOSPI"))
            self.assertEqual(AK.lookup("003230"), ("003230", "삼양식품", "KOSPI"))

    def test_added_watch_stock_is_not_buyable_and_list_stays_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "stocks.toml"
            ST.add(p, "003230", "삼양식품", "사용자 질문", "KOSPI", dt.date(2026, 11, 17), watch_only=True)
            s = ST.load(p)[0]
            self.assertIs(s["buy"], False)
            self.assertIs(s["watch"], True)
            self.assertEqual(ST.check(ST.load(p)), [])


class DailyWatch(unittest.TestCase):
    """흉내 낸 하루: 목록의 다른 종목이 손 예측 없이도 기록되고, 오늘 차례 종목은 손 예측이 요구된다."""

    def test_listed_stock_gets_lean_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = TD.make_root(tmp)
            (root.path / "stocks.toml").write_text(
                '[[stock]]\ncode = "777770"\nname = "가짜지켜봄"\nmarket = "KOSPI"\nadded = "2025-01-01"\nwhy = "시험"\n'
                'buy = false\nwatch = true\n', encoding="utf-8")
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


class SundayLessons(unittest.TestCase):
    """흉내 낸 일주일: 일요일 record는 lessons 칸이 없으면 거절하고(아무것도 안 씀), 있으면 쓴다."""

    def test_sunday_requires_lessons(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(LS, "LESSONS_SINCE", "2025-09-01"):
            root = TD.make_root(tmp)
            for i in range(5):
                self.assertEqual(TD.run_day(root, dt.date(2025, 9, 2) + dt.timedelta(days=i))[0], 0)
            sun = dt.date(2025, 9, 7)
            assert D.prepare(root, sun, TD.at(sun, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            brief = (root.day_dir(sun) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("뿌리 목록", brief)
            self.assertIn("오늘은 일요일", brief)
            inp = TD.fill(json.loads((root.day_dir(sun) / "input.json").read_text(encoding="utf-8")))
            self.assertIn("lessons", inp)
            bad = {k: v for k, v in inp.items() if k != "lessons"}
            p = root.day_dir(sun) / "bad.json"
            p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            n = len(R.read(root.records, "run"))
            self.assertEqual(D.record(root, sun, TD.at(sun, 8, 30), p), 1)
            self.assertEqual(len(R.read(root.records, "run")), n)
            p = root.day_dir(sun) / "ok.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, sun, TD.at(sun, 8, 30), p), 0)
