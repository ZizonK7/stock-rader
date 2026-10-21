"""매일 실행 흉내 내기: 임시 폴더에서 가짜 시세·가짜 조사·가짜 손 예측으로 prepare → record → report를 며칠 돌린다.
진짜 기록(records/*.jsonl)은 만들지 않는다. 네트워크를 쓰지 않는다(--no-fetch, 판단 때 가격은 넣어 준다)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from market import config as C
from market import daily as D
from market import prices as PR
from market import records as R
from market import report as RP
from tests import fixtures

TICK = {"KRW-BTC": {"price": 50_000_000.0, "time": "2025-09-02T07:37:00+09:00", "source": "test"}}


def at(day: dt.date, h: int, m: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m), C.KST)


def make_root(tmp: str, coin_until: dt.date | None = None) -> D.Root:
    root = D.copy_for_simulation(Path(tmp) / "repo")
    for a, rows in fixtures.market(dt.date(2024, 3, 1), dt.date(2025, 10, 31)).items():
        if a == "KRW-BTC" and coin_until:
            rows = [r for r in rows if r.date <= coin_until]
        PR.append(a, list(rows), root.prices)
    return root


def fill(tpl: dict, value: str = "예", review_why=None) -> dict:
    """틀을 가짜 조사·손 예측으로 채운다."""
    inp = json.loads(json.dumps(tpl))
    for i, r in enumerate(inp["research"]):
        place = r["_suggest"][0] if r["_suggest"] else f"P_try_{i}"
        r["answer"] = "가짜 요약"
        r["agent"] = f"각도-{r['angle']}"
        r["looked"] = [{"place": place, "result": "찾음", "new": False, "url": "https://example.org/a", "date": inp["day"],
                        "note": "가짜"},
                       {"place": f"P_new_{i}", "result": "없음", "new": True, "url": "", "date": "", "note": ""}]
    for st, rd in inp["readings"].items():
        rd.update(value=value, why="가짜 이유", places=["P_fed_calendar"])
    for h in inp["hand"]:
        h.update(p_up=0.55, size_lo=1.0, size_hi=2.0, used=[f"Q_rate_up={value}"], decisive=[f"Q_rate_up={value}"],
                 reason="가짜 손 예측", confidence="보통")
    for a in inp["weights"]:
        inp["weights"][a] = {"target": 0.3, "reason": "가짜 비중"}
    for rv in inp["reviews"]:
        why = review_why or ("맞음" if rv["_info"]["verdict"] == "맞음" else "조건 흐림")
        rv.update(why=why, wrong=[] if why == "맞음" else ["Q_rate_up"], reason="가짜 돌아보기")
    inp["agents"] = 4
    return inp


def run_day(root: D.Root, day: dt.date, value: str = "예", review_why=None, record_time=(8, 30)) -> tuple[int, dict]:
    assert D.prepare(root, day, at(day, 7, 37), fetch=False, ticker=TICK, echo=False) == 0
    tpl = json.loads((root.day_dir(day) / "input.json").read_text(encoding="utf-8"))
    inp = fill(tpl, value, review_why)
    p = root.day_dir(day) / "filled.json"
    p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
    return D.record(root, day, at(day, *record_time), p), inp


class DailyLoop(unittest.TestCase):
    def test_four_days_settle_score_review_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            days = [dt.date(2025, 9, 2) + dt.timedelta(days=i) for i in range(4)]
            for i, d in enumerate(days):
                code, _ = run_day(root, d, value="예" if i % 2 == 0 else "아니오")
                self.assertEqual(code, 0, d)
                self.assertEqual(RP.write(root, d), 0)
            self.assertEqual(R.check_folder(root.records), [])
            recs = {k: R.read(root.records, k) for k in R.FIELDS}
            # 첫날 계좌 넷이 1,000만 원으로 시작
            first = [a for a in recs["account"] if a["run"] == "2025-09-02"]
            self.assertEqual({a["account"] for a in first}, set(C.LIVE_ACCOUNTS))
            self.assertTrue(all(a["value"] == C.START_CASH for a in first))
            # 예측기: 손·계산기·이전 버전·기준선
            preds = {p["predictor"] for p in recs["prediction"]}
            self.assertLessEqual({"hand", "calc", "version:v1", "baseline:half", "baseline:base_rate"}, preds)
            # 정산: 코인은 D+2, ETF는 D+3 실행에서 (판단 시각 뒤 첫 공식 가격)
            fills = recs["fill"]
            self.assertTrue(any(f["asset"] == "KRW-BTC" and f["run"] == "2025-09-04" for f in fills), fills)
            self.assertTrue(any(f["asset"] == "069500" and f["run"] == "2025-09-05" for f in fills), fills)
            for f in fills:
                o = next(o for o in recs["order"] if o["id"] == f["order"])
                when = C.fill_dt(C.ASSETS[f["asset"]], dt.date.fromisoformat(f["fill_date"]))
                self.assertGreater(when, dt.datetime.fromisoformat(o["decided_at"]))
            # 채점·돌아보기가 이어진다
            self.assertTrue(recs["score"])
            self.assertTrue(all("counted" in s for s in recs["score"]))
            self.assertTrue(recs["review"])
            hand_scored = {s["prediction"] for s in recs["score"] if s["predictor"] == "hand" and s["counted"]
                           and s["run"] < "2025-09-05"}
            self.assertLessEqual(hand_scored, {r["prediction"] for r in recs["review"]})
            # 판단 때 가격, 조사, 손 대 보기
            run = [r for r in recs["run"] if r["mode"] == "live"][0]
            self.assertEqual(run["steps"]["decision_price"]["KRW-BTC"]["price"], 50_000_000.0)
            self.assertEqual(len(recs["hand_reading"]), 4)
            self.assertTrue(all(r["looked"] for r in recs["research"]))
            # 일지: 숫자가 채워지고 말로 쓸 자리가 남는다
            text = (root.reports / "2025-09-05.md").read_text(encoding="utf-8")
            self.assertIn("| **계좌 합계** |", text)
            self.assertIn("셋을 나눠 사 두기만 했을 때", text)
            self.assertIn(RP.MARK, text)
            self.assertEqual(RP.check(root, dt.date(2025, 9, 5)), 1)
            self.assertEqual(RP.check(root, dt.date(2025, 9, 5)), 1)
            # 붙은 물결표는 Markdown에서 취소선이 된다 → 점검이 막는다(주석 안은 괜찮다)
            (root.reports / "2025-09-05.md").write_text(text.replace(RP.MARK, "확률 47~49%"), encoding="utf-8")
            self.assertEqual(RP.check(root, dt.date(2025, 9, 5)), 1)
            filled = text.replace(RP.MARK, "채움 47–49% <!-- 0.6~1.2% -->")
            (root.reports / "2025-09-05.md").write_text(filled, encoding="utf-8")
            self.assertEqual(RP.check(root, dt.date(2025, 9, 5)), 0)
            # 첫날: 견줄 기준선 문장 없음, 아직 정산 안 된 주문이 표 아래에 보인다
            first = (root.reports / "2025-09-02.md").read_text(encoding="utf-8")
            self.assertNotIn("셋을 나눠 사 두기만 했을 때", first.split("|")[0])
            self.assertIn("체결을 기다리는 주문", first)

    def test_sunday_writes_weekly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            for d in (dt.date(2025, 9, 6), dt.date(2025, 9, 7)):  # 토, 일
                self.assertEqual(run_day(root, d)[0], 0)
            RP.write(root, dt.date(2025, 9, 7))
            week = (root.reports / "2025-09-07-week.md").read_text(encoding="utf-8")
            for part in ("## 1. 요약", "## 5. 성적표", "기준선: 셋을 나눠 사 두기", "볼 곳 성적"):
                self.assertIn(part, week)

    def test_missed_day_is_blank_and_no_backdating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(run_day(root, dt.date(2025, 9, 2))[0], 0)
            self.assertEqual(run_day(root, dt.date(2025, 9, 4))[0], 0)
            runs = R.read(root.records, "run")
            self.assertEqual([(r["run"], r["mode"]) for r in runs],
                             [("2025-09-02", "live"), ("2025-09-03", "missed"), ("2025-09-04", "live")])
            self.assertFalse([p for p in R.read(root.records, "prediction") if p["run"] == "2025-09-03"])
            # 같은 날 두 번 쓰지 않는다, 지난 날짜로 쓰지 않는다
            self.assertEqual(D.prepare(root, dt.date(2025, 9, 4), at(dt.date(2025, 9, 4), 9, 0), fetch=False, ticker=TICK, echo=False), 1)
            self.assertEqual(D.prepare(root, dt.date(2025, 9, 3), at(dt.date(2025, 9, 3), 9, 0), fetch=False, ticker=TICK, echo=False), 1)

    def test_correction_is_appended_not_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(run_day(root, dt.date(2025, 9, 2))[0], 0)
            before = (root.records / "research.jsonl").read_text(encoding="utf-8")
            d = dt.date(2025, 9, 3)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            inp = fill(tpl)
            inp["corrections"] = [{"target": "research:2025-09-02:1", "note": "링크가 다른 기사였다 — 맞는 링크는 …"}]
            p = root.day_dir(d) / "c.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 0)
            self.assertTrue((root.records / "research.jsonl").read_text(encoding="utf-8").startswith(before))
            self.assertEqual(R.read(root.records, "correction")[0]["target"], "research:2025-09-02:1")
            inp["corrections"] = [{"target": "없는-id", "note": "x"}]

    def test_bad_input_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 2)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            inp = fill(tpl, "예")
            inp["hand"][0]["used"] = ["Q_rate_up=아니오"]          # 그날 대 본 값과 다름
            inp["hand"][1]["size_hi"] = inp["hand"][1]["size_lo"] * 3  # 폭 상한
            inp["hand"][2]["reason"] = ""                           # 이유 없는 숫자
            inp["weights"] = {a: {"target": 0.5, "reason": "r"} for a in inp["weights"]}  # 합 > 1
            p = root.day_dir(d) / "bad.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            self.assertFalse(list(root.records.glob("*.jsonl")))

    def test_noise_excuse_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            self.assertEqual(run_day(root, dt.date(2025, 9, 2))[0], 0)
            d = dt.date(2025, 9, 3)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertTrue(tpl["reviews"])
            not_noise = [r for r in tpl["reviews"] if not r["_info"]["noise"]]
            self.assertTrue(not_noise)
            inp = fill(tpl, review_why="잡음")
            p = root.day_dir(d) / "noise.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)

    def test_late_run_skips_asset_with_stale_prices(self):
        """판단 시각까지 끝났어야 할 일봉이 파일에 없으면(시세 받기 실패) 그 대상은 예측·주문하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp, coin_until=dt.date(2025, 8, 31))  # 07:40에 받아서 9-01 코인 일봉이 없는 파일
            d = dt.date(2025, 9, 2)
            self.assertEqual(D.compute(root, d, at(d, 7, 50), {a: {"status": "ok", "at": at(d, 7, 40).isoformat()}
                                                               for a in C.ASSET_IDS}, {}).skipped, {})
            # 코인 9-01 일봉은 9-02 09:00에 끝난다. 10:00 판단인데 시세를 받지 않았으면 낡은 것
            stale = {a: {"status": "fail"} for a in C.ASSET_IDS}
            day = D.compute(root, d, at(d, 10, 0), stale, {})
            self.assertIn("KRW-BTC", day.skipped)
            self.assertNotIn("069500", day.skipped)
            # 코인은 휴장이 없다: 9:00 뒤에 받았는데도 없으면 여전히 건너뛴다
            ok = {a: {"status": "ok", "at": at(d, 9, 30).isoformat()} for a in C.ASSET_IDS}
            self.assertIn("KRW-BTC", D.compute(root, d, at(d, 9, 45), ok, {}).skipped)
            # ETF는 일봉이 끝난 뒤에 받았는데 없으면 휴장으로 본다(9-02 16:00 뒤 받은 것처럼 흉내: 9-02 줄을 지운 파일)
            etf = [r for r in PR.load("069500", root.prices) if r.date != d]
            (root.prices / "069500.csv").unlink()
            PR.append("069500", etf, root.prices)
            late = at(d, 17, 0)
            self.assertIn("069500", D.compute(root, d, late, stale, {}).skipped)
            ok = {a: {"status": "ok", "at": at(d, 16, 30).isoformat()} for a in C.ASSET_IDS}
            self.assertNotIn("069500", D.compute(root, d, late, ok, {}).skipped)

    def test_live_root_refuses_backdating(self):
        with self.assertRaises(SystemExit):
            D.when({"--day": "2026-10-01"}, C.DECISION_TIME)


if __name__ == "__main__":
    unittest.main()
