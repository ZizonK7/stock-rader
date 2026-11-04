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
from market import scoring as S
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
        h.update(p_up=0.55, size_lo=1.0, size_hi=2.0, used=[f"Q_rate_up_shift={value}"], decisive=[f"Q_rate_up_shift={value}"],
                 reason="가짜 손 예측", confidence="보통")
    for a in inp["weights"]:
        inp["weights"][a] = {"target": 0.3, "reason": "가짜 비중"}
    for rv in inp["reviews"]:
        why = review_why or ("맞음" if rv["_info"]["verdict"] == "맞음" else "조건 흐림")
        rv.update(why=why, wrong=[] if why == "맞음" else ["Q_rate_up_shift"], reason="가짜 돌아보기")
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
            # 예측기: 손·계산기·이전 버전·기준선 셋 (v1은 2026-11-04 쉼)
            preds = {p["predictor"] for p in recs["prediction"]}
            self.assertLessEqual({"hand", "calc", "version:v4", "baseline:half", "baseline:base_rate", "baseline:all"}, preds)
            self.assertNotIn("version:v1", preds)
            # 답한 층(layer)과 참고 칸(analog) — 2026-11-04
            calc = [p for p in recs["prediction"] if p["predictor"] == "calc"]
            self.assertTrue(all(p["layer"] in ("조각", "동전") for p in calc))  # 출발점 조각은 은퇴(2026-11-04 결정 E3)
            self.assertTrue(all("analog" in p for p in calc))
            got = [p["analog"] for p in calc if p["analog"]]
            self.assertTrue(got)
            self.assertTrue(all(set(a) == {"n", "runs", "eff", "up_rate"} and 1 <= a["runs"] <= a["n"] for a in got))
            # 판단 때 가격(TICK)이 있어도 계산기·이전 버전(v5에는 출발점 조각이 살아 있다)에 넘기지 않는다
            self.assertFalse([p for p in recs["prediction"] if any(u.startswith("C_started_move") for u in p.get("used", []))])
            self.assertTrue(all(p["layer"] in ("조각", "기준값", "동전") for p in recs["prediction"] if p["predictor"].startswith("version:")))
            self.assertLessEqual({"version:v3", "version:v4", "version:v5"}, preds)
            # 예측 기준 = 체결가(결정 E2): 새 줄은 모두 base_kind fill·base_close null, 기준 날짜 = 판단(08:30) 뒤 첫 체결 봉(평일이라 그날)
            self.assertTrue(all(p.get("base_kind") == "fill" and p["base_close"] is None for p in recs["prediction"]))
            self.assertEqual({p["base_date"] for p in recs["prediction"]}, {d.isoformat() for d in days})
            self.assertTrue(all(p["base_date"] == p["run"] for p in recs["prediction"]))
            # 채점: 체결 봉의 시가 → 체결 봉부터 h봉째 종가 (1봉이면 체결 봉 자체의 종가)
            rows = {a: PR.load(a, root.prices) for a in C.ASSET_IDS}
            by_id = {p["id"]: p for p in recs["prediction"]}
            for s in recs["score"]:
                p = by_id[s["prediction"]]
                rs = rows[s["asset"]]
                i = next(k for k, r in enumerate(rs) if r.date.isoformat() == s["fill_date"])
                self.assertEqual(s["fill_date"], p["base_date"])
                self.assertEqual(s["fill_price"], rs[i].open)
                self.assertEqual(s["target_date"], rs[i + p["horizon"] - 1].date.isoformat())
                self.assertAlmostEqual(s["ret"], round(rs[i + p["horizon"] - 1].close / rs[i].open - 1, 6))
            self.assertTrue(any(s["horizon"] == 1 and s["target_date"] == s["fill_date"] for s in recs["score"]))
            # 기준선도 같은 기준(다음 봉 시가 → h봉째 종가)으로 센 비율이다
            br = next(p for p in recs["prediction"] if p["predictor"] == "baseline:base_rate" and p["asset"] == "069500"
                      and p["horizon"] == 5 and p["run"] == "2025-09-02")
            view = PR.view("069500", rows["069500"], at(days[0], 8, 30))
            self.assertEqual(br["p_up"], round(S.base_rate(view, 5, True)[0], 4))
            self.assertIn("다음 봉 시가", br["reason"])
            self.assertTrue(all(p["layer"] == "조각" for p in recs["prediction"] if p["predictor"] == "hand"))
            # 계산기 계좌는 R_weights_v2: 동전(0.5)이면 정확히 1/3
            reasons = [o["reason"] for o in recs["order"] if o["account"] == "live:calc"]
            self.assertTrue(reasons and all(r.startswith("R_weights_v2") for r in reasons), reasons)
            # 정산: 판단 뒤 첫 시가(결정 E1) — 코인·ETF 모두 D+2 실행에서. 08:30 판단이라 그날 09:00 시가에 체결
            fills = recs["fill"]
            self.assertTrue(any(f["asset"] == "KRW-BTC" and f["run"] == "2025-09-04" for f in fills), fills)
            self.assertTrue(any(f["asset"] == "069500" and f["run"] == "2025-09-04" for f in fills), fills)
            self.assertTrue(all(o.get("fill_rule") == "open" for o in recs["order"]))
            for f in fills:
                o = next(o for o in recs["order"] if o["id"] == f["order"])
                when = C.fill_dt(C.ASSETS[f["asset"]], dt.date.fromisoformat(f["fill_date"]))
                self.assertGreater(when, dt.datetime.fromisoformat(o["decided_at"]))
                self.assertEqual((f["fill_date"], f["price_kind"]), (o["run"], "open"))
                self.assertEqual(f["price"], next(r.open for r in rows[f["asset"]] if r.date.isoformat() == f["fill_date"]))
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
            inp["hand"][0]["used"] = ["Q_rate_up_shift=아니오"]          # 그날 대 본 값과 다름
            inp["hand"][1]["size_hi"] = inp["hand"][1]["size_lo"] * 3  # 폭 상한
            inp["hand"][2]["reason"] = ""                           # 이유 없는 숫자
            inp["weights"] = {a: {"target": 0.5, "reason": "r"} for a in inp["weights"]}  # 합 > 1
            p = root.day_dir(d) / "bad.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            self.assertFalse(list(root.records.glob("*.jsonl")))

    def test_value_based_state_refuses_partial(self):
        """값 기준으로 대 보는 상태(answers)는 '일부'를 쓰지 못한다 — 검사가 '일부'를 셀 수 없다 (2026-11-03)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 2)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertIn("S_q_fear_above_year", tpl["readings"])
            self.assertNotIn("S_q_risk_off", tpl["readings"])  # 은퇴한 조각의 상태는 묻지 않는다
            inp = fill(tpl, "예")
            inp["readings"]["S_q_fear_above_year"]["value"] = "일부"
            p = root.day_dir(d) / "partial.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            self.assertFalse(list(root.records.glob("*.jsonl")))

    def test_info_required_without_decisive(self):
        """결론 조각(decisive)이 없으면 손 예측에 info(결론을 정한 것)를 적어야 한다 — 기록의 layer가 된다 (2026-11-04)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 2)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertIn("info", tpl["hand"][0])
            self.assertIn("_info_form", tpl)
            inp = fill(tpl)
            coin = next(h for h in inp["hand"] if h["asset"] == "KRW-BTC" and h["horizon"] == 1)
            self.assertNotEqual(coin["_calc_layer"], "출발점")  # 출발점 조각은 은퇴(2026-11-04 결정 E3)
            coin.update(decisive=[], info="")
            p = root.day_dir(d) / "noinfo.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            self.assertFalse(list(root.records.glob("*.jsonl")))
            coin["info"] = "아무거나"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            coin["info"] = "조각"  # 결론 조각 없이 '조각'이라고 쓰면 layer가 거짓이 된다
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 1)
            self.assertFalse(list(root.records.glob("*.jsonl")))
            coin["info"] = "출발점"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 0)
            hand = {(x["asset"], x["horizon"]): x for x in R.read(root.records, "prediction") if x["predictor"] == "hand"}
            self.assertEqual(hand[("KRW-BTC", 1)]["layer"], "출발점")
            self.assertEqual(hand[("069500", 1)]["layer"], "조각")  # decisive가 있으면 '조각'

    def test_numeric_seed_state_is_set_by_code(self):
        """숫자 씨앗 상태(2026-11-04, E5): 틀의 readings에 없고, 숫자가 없으면 '안 물음', 조사 numbers에 숫자가 있으면 코드가 값을
        정해 기록한다(숫자도 남긴다). 메인이 적은 값이 숫자와 다르면 거절, 쓴 조각의 값은 코드가 정한 값과 같아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 2)
            D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
            tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
            self.assertNotIn("S_q_overnight_gap", tpl["readings"])
            self.assertIn("S_q_overnight_gap", tpl["_seed_readings"])
            gap_q = [r for r in tpl["research"] if r["state"] == "S_q_overnight_gap"]
            self.assertEqual(len(gap_q), 1)
            self.assertTrue(gap_q[0]["asked"].startswith("[숫자"))
            self.assertGreater(gap_q[0]["numbers"]["kr_overnight_sd60_pct"], 0)  # 잣대는 코드가 시세로 미리 채운다
            inp = fill(tpl)
            p = root.day_dir(d) / "seed.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 8, 30), p), 0)
            self.assertNotIn("S_q_overnight_gap", R.read(root.records, "hand_reading")[0]["values"])  # 숫자 없음 = 안 물음
            # 다음 날: 간밤 등락 −2.5%, 잣대 1.0% → |−2.5| > 1 → '예', 부호 −
            d2 = dt.date(2025, 9, 3)
            D.prepare(root, d2, at(d2, 7, 37), fetch=False, ticker=TICK, echo=False)
            inp = fill(json.loads((root.day_dir(d2) / "input.json").read_text(encoding="utf-8")))
            q = next(r for r in inp["research"] if r["state"] == "S_q_overnight_gap")
            q["numbers"] = {"kr_overnight_pct": -2.5, "kr_overnight_sd60_pct": 1.0}
            inp["readings"]["S_q_overnight_gap"] = {"value": "아니오", "why": "메인이 잘못 적음", "places": ["P_yahoo_ewy"]}
            p = root.day_dir(d2) / "seed2.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d2, at(d2, 8, 30), p), 1)   # 숫자로는 '예' — 메인 값과 다르면 거절
            del inp["readings"]["S_q_overnight_gap"]                     # 값은 코드가 정한다
            kr = next(h for h in inp["hand"] if h["asset"] == "069500" and h["horizon"] == 1)
            kr["used"].append("Q_overnight_lead_v2=아니오")
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d2, at(d2, 8, 30), p), 1)   # 쓴 조각의 값도 코드가 정한 값과 같아야
            kr["used"][-1] = "Q_overnight_lead_v2=예"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d2, at(d2, 8, 30), p), 0)
            hr = R.read(root.records, "hand_reading")[-1]
            self.assertEqual(hr["values"]["S_q_overnight_gap"], "예")
            self.assertEqual(hr["numbers"]["S_q_overnight_gap"], {"kr_overnight_pct": -2.5, "kr_overnight_sd60_pct": 1.0})
            self.assertIn("규칙 'abs > 1'", hr["why"]["S_q_overnight_gap"])
            self.assertTrue(hr["places"]["S_q_overnight_gap"])  # 숫자가 나온 조사 답의 '찾음' 볼 곳

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

    def test_late_runs_warn_and_miss_the_open(self):
        """체결은 판단 뒤 첫 시가(결정 E1): prepare가 08:50 뒤면 경고, record가 09:00 뒤면 그날 시가를 놓쳐 다음 시가에 체결되고
        예측 기준(체결 봉)도 다음 봉이다 — run notes에 경고가 남는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 5)  # 금: ETF 다음 시가는 월요일
            self.assertEqual(D.prepare(root, d, at(d, 8, 55), fetch=False, ticker=TICK, echo=False), 0)
            self.assertIn("경고", (root.day_dir(d) / "briefing.md").read_text(encoding="utf-8"))
            prep = json.loads((root.day_dir(d) / "prepare.json").read_text(encoding="utf-8"))
            self.assertTrue(prep["warnings"] and "08:50" in prep["warnings"][0])
            inp = fill(json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8")))
            p = root.day_dir(d) / "late.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, at(d, 9, 10), p), 0)
            run = R.read(root.records, "run")[-1]
            self.assertTrue(any("시가를 놓쳐" in n for n in run["notes"]), run["notes"])
            base = {p["asset"]: p["base_date"] for p in R.read(root.records, "prediction")}
            self.assertEqual(base, {"069500": "2025-09-08", "360750": "2025-09-08", "KRW-BTC": "2025-09-06"})
            # 그 뒤 실행에서 정산: 다음 시가에 체결된다(코인 토 9-06 → 9-08 실행, ETF 월 9-08 → 9-10 실행)
            later = D.compute(root, dt.date(2025, 9, 10), at(dt.date(2025, 9, 10), 7, 37), {}, {})
            got = {f["asset"]: f["fill_date"] for f in later.fills if f["account"] == "live:hand"}
            self.assertEqual(got, {"069500": "2025-09-08", "360750": "2025-09-08", "KRW-BTC": "2025-09-06"})
            # 제때 한 판단(07:37 준비, 08:30 기록)은 경고가 없다
            root2 = make_root(tmp + "/b")
            self.assertEqual(run_day(root2, dt.date(2025, 9, 9))[0], 0)
            self.assertFalse([n for n in R.read(root2.records, "run")[-1]["notes"] if "시가" in n])

    def test_cli_day_with_root(self):
        """명령줄 그대로 하루(prepare → record → report, --root 임시 폴더, --no-fetch): 새 줄은 체결가 기준, 주문은 시가 규칙."""
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            base = ["--root", str(root.path), "--day", "2025-09-02"]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(D.main(["prepare", *base, "--at", "07:37", "--no-fetch"]), 0)
            self.assertIn("record를 09:00 전에", out.getvalue())
            d = dt.date(2025, 9, 2)
            inp = fill(json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8")))
            p = root.day_dir(d) / "cli.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(D.main(["record", str(p), *base, "--at", "08:40"]), 0)
                self.assertEqual(D.main(["report", *base]), 0)
            preds = R.read(root.records, "prediction")
            self.assertTrue(preds and all(x["base_kind"] == "fill" and x["base_date"] == "2025-09-02" for x in preds))
            self.assertTrue(all(o["fill_rule"] == "open" for o in R.read(root.records, "order")))
            self.assertTrue((root.reports / "2025-09-02.md").exists())
            self.assertEqual(R.check_folder(root.records), [])

    def test_live_root_refuses_backdating(self):
        with self.assertRaises(SystemExit):
            D.when({"--day": "2026-10-01"}, C.DECISION_TIME)


if __name__ == "__main__":
    unittest.main()
