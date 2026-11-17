"""아침 흐름(2026-11-17 사용자 결정, 잼민이 지시서 p564): 대상마다 아침 조사 파일, 피드백 정리(매일 lessons), 08:40 다시 보기,
09:00 뒤 판단은 주문·예측을 하나도 넣지 않음('못 넣음' 줄), 장전 값 원문. 네트워크를 쓰지 않는다."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market import config as C
from market import daily as D
from market import flow as FL
from market import premarket as PM
from market import records as R
from market import report as RP
from market import scan as SC
from tests import test_daily as TD

DAY = dt.date(2026, 11, 18)
AT = dt.datetime(2026, 11, 18, 8, 50, tzinfo=C.KST)


def good(asset: str, day: dt.date = DAY) -> dict:
    kinds = SC.REQUIRED[SC.kind_of(asset)]
    return {"asset": asset, "by": "haiku-5.5 high", "as_of": f"{day.isoformat()}T07:55+09:00",
            "opened": ["https://example.org/a", "https://example.org/b"], "blocked": [],
            "found": [{"kind": kinds[0], "text": "가짜 요약", "url": "https://example.org/a", "numbers": {"foreign_net_krw100m": 12.5}}],
            "missing": list(kinds[1:])}


class Scan(unittest.TestCase):
    def test_one_file_checks(self):
        self.assertEqual(SC.check_one(good("005930"), "005930", DAY, AT), [])
        self.assertEqual(SC.check_one(good("KRW-BTC"), "KRW-BTC", DAY, AT), [])
        d = good("005930")
        d["missing"] = d["missing"][1:]                            # 실적·수급 중 하나를 빠뜨림
        self.assertTrue(any("놓치지 않게" in e for e in SC.check_one(d, "005930", DAY, AT)))
        d = good("005930")
        d["opened"] = ["https://example.org/a"]                    # 연 곳 하나
        self.assertTrue(any("2곳 이상" in e for e in SC.check_one(d, "005930", DAY, AT)))
        d["blocked"] = ["data.krx.co.kr 403"]                       # 못 연 곳을 적으면 받는다(우회하지 않게)
        self.assertEqual(SC.check_one(d, "005930", DAY, AT), [])
        d = good("005930")
        d["as_of"] = "2026-11-18T08:55+09:00"                       # 판단 뒤 정보
        self.assertTrue(SC.check_one(d, "005930", DAY, AT))
        d = good("005930")
        d["found"][0]["url"] = ""
        self.assertTrue(SC.check_one(d, "005930", DAY, AT))
        d = good("005930")
        d["found"][0]["numbers"] = {"foreign": 1}                  # 단위 없는 이름
        self.assertTrue(SC.check_one(d, "005930", DAY, AT))
        self.assertEqual(SC.check_one({"asset": "005930", "by": "main", "failed": "두 번 시간 초과"}, "005930", DAY, AT), [])
        self.assertTrue(SC.check_one({"asset": "005930", "by": "", "failed": "x"}, "005930", DAY, AT))

    def test_folder_briefs_table_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = SC.write_briefs(DAY, Path(tmp), [("005930", "삼성전자", True, False), ("069500", "KODEX 200", False, False)])
            brief = (folder / "_brief" / "005930.md").read_text(encoding="utf-8")
            for word in ("공시", "실적", "수급", "2곳", str((folder / "005930.json").resolve())):
                self.assertIn(word, brief)
            tasks = json.loads((folder / "_tasks.json").read_text(encoding="utf-8"))
            self.assertEqual([t["asset"] for t in tasks], ["005930", "069500"])
            errs = SC.input_errors(folder, ["005930", "069500"], DAY, AT)
            self.assertTrue(errs and "2개" in errs[0])                # 둘 다 없음
            (folder / "005930.json").write_text(json.dumps(good("005930"), ensure_ascii=False), encoding="utf-8")
            (folder / "069500.json").write_text("{깨짐", encoding="utf-8")
            errs = SC.input_errors(folder, ["005930", "069500"], DAY, AT)
            self.assertEqual(errs, ["scan 069500: JSON이 깨졌다"])
            self.assertEqual(SC.input_errors(folder, ["005930", "069500"], dt.date(2026, 11, 17), AT), [])  # 그 전 날은 안 봄
            (folder / "069500.json").write_text(json.dumps(good("069500"), ensure_ascii=False), encoding="utf-8")
            lines = SC.record_lines(folder, ["005930", "069500"], DAY)
            self.assertEqual([x["id"] for x in lines], ["scan:2026-11-18:005930", "scan:2026-11-18:069500"])
            for i, x in enumerate(lines):
                self.assertEqual(R.line_errors("scan", {"kind": "scan", **x}, f"scan+{i}"), [])
            table = "\n".join(SC.table(folder, [("005930", "삼성전자"), ("069500", "KODEX 200")], DAY, AT))
            self.assertIn("가짜 요약", table)
            self.assertIn("못 찾음", table)


class Recheck(unittest.TestCase):
    def inp(self):
        return {"weights": {"069500": {"target": 0.2}}, "hand": [{"asset": "069500", "horizon": 1, "p_up": 0.52}],
                "recheck": {"sent_at": "2026-11-18T08:40:30+09:00",
                            "agents": [{"focus": "간밤·아침 속보", "model": "haiku", "effort": "medium", "returned": True},
                                       {"focus": "사는·든 종목 이슈", "model": "haiku", "effort": "medium", "returned": False}],
                            "found": [{"what": "가짜 이슈", "url": "https://example.org/x", "asset": "069500"}],
                            "premarket": "장외 값 비어 있음",
                            "changed": [{"asset": "069500", "what": "weight", "from": 0.33, "to": 0.2, "why": "속보"},
                                        {"asset": "069500", "what": "p_up", "horizon": 1, "from": 0.55, "to": 0.52, "why": "속보"}]}}

    def test_recheck(self):
        self.assertEqual(FL.recheck_errors(self.inp(), DAY, AT), [])
        self.assertTrue(FL.recheck_errors({}, DAY, AT))
        self.assertEqual(FL.recheck_errors({}, dt.date(2026, 11, 17), AT), [])
        early = self.inp()
        early["recheck"]["sent_at"] = "2026-11-18T08:39:59+09:00"
        self.assertTrue(any("08:40" in e for e in FL.recheck_errors(early, DAY, AT)))
        one = self.inp()
        one["recheck"]["agents"] = one["recheck"]["agents"][:1]
        self.assertTrue(FL.recheck_errors(one, DAY, AT))
        off = self.inp()
        off["weights"]["069500"]["target"] = 0.25               # 바꾼 값과 최종 값이 다름
        self.assertTrue(any("최종 값" in e for e in FL.recheck_errors(off, DAY, AT)))
        same = self.inp()
        same["recheck"]["changed"][0]["from"] = 0.2
        self.assertTrue(FL.recheck_errors(same, DAY, AT))
        nopm = self.inp()
        nopm["recheck"]["premarket"] = ""
        self.assertTrue(FL.recheck_errors(nopm, DAY, AT))

    def test_late_boundary_kst(self):
        self.assertFalse(FL.is_late(DAY, dt.datetime(2026, 11, 18, 8, 59, 59, 999999, tzinfo=C.KST)))
        self.assertTrue(FL.is_late(DAY, dt.datetime(2026, 11, 18, 9, 0, 0, tzinfo=C.KST)))
        self.assertTrue(FL.is_late(DAY, dt.datetime(2026, 11, 18, 0, 0, 1, tzinfo=dt.timezone.utc)))   # 09:00:01 KST
        self.assertFalse(FL.is_late(DAY, dt.datetime(2026, 11, 17, 23, 59, tzinfo=dt.timezone.utc)))   # 08:59 KST
        self.assertFalse(FL.is_late(dt.date(2026, 11, 17), dt.datetime(2026, 11, 17, 9, 30, tzinfo=C.KST)))  # 그 전 날은 경고만


class Premarket(unittest.TestCase):
    NAVER = {"datas": [{"itemCode": "003230", "stockName": "가짜", "closePrice": "1,266,000", "marketStatus": "CLOSE",
                        "localTradedAt": "2026-11-17T15:30:00+09:00", "isinCode": "X",
                        "overMarketPriceInfo": {"tradingSessionType": "PRE_MARKET", "overPrice": "1,270,000",
                                                "fluctuationsRatio": "0.32", "localTradedAt": "2026-11-18T08:21:00+09:00"}}]}

    def test_pick_count_lines(self):
        raw = PM.pick(self.NAVER)
        self.assertNotIn("isinCode", raw)
        self.assertEqual(raw["overMarketPriceInfo"]["overPrice"], "1,270,000")
        self.assertIsNone(PM.pick({"x": 1}))
        es = PM.es_pick({"chart": {"result": [{"meta": {"symbol": "ES=F", "regularMarketPrice": 7435.0, "chartPreviousClose": 7400.0}}]}})
        snap = {"fetched_at": "2026-11-18T08:41:00+09:00",
                "items": [{"asset": "003230", "status": 200, "fetched_at": "2026-11-18T08:41:00+09:00", "raw": raw},
                          {"asset": "005930", "status": 403, "fetched_at": "2026-11-18T08:41:01+09:00", "raw": None}],
                "es": {"status": 200, "raw": es}}
        self.assertEqual(PM.filled(snap, DAY), (1, 1))
        text = "\n".join(PM.summary(snap))
        self.assertIn("PRE_MARKET", text)
        self.assertIn("+0.47%", text)
        self.assertIn("받지 못함", text)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "premarket.json"
            p.write_text(json.dumps(snap), encoding="utf-8")
            lines = PM.record_lines(p, DAY)
            self.assertEqual(len(lines), 3)
            for i, x in enumerate(lines):
                self.assertEqual(R.line_errors("premarket", {"kind": "premarket", **x}, f"p+{i}"), [])
            self.assertEqual(PM.record_lines(p, DAY + dt.timedelta(days=1)), [])   # 다른 날 받은 파일은 안 씀


def flow_input(root: D.Root, day: dt.date, recheck_at: str = "08:41") -> dict:
    """틀을 채우고, 대상마다 조사 파일·precheck·recheck·lessons를 더한다."""
    tpl = json.loads((root.day_dir(day) / "input.json").read_text(encoding="utf-8"))
    inp = TD.fill(tpl)
    for t in json.loads((root.day_dir(day) / "scan" / "_tasks.json").read_text(encoding="utf-8")):
        Path(t["out"]).write_text(json.dumps(good(t["asset"], day), ensure_ascii=False), encoding="utf-8")
    inp["precheck"] = {"roots": [], "note": "밟은 뿌리 없음", "lessons_by": "opus-5.5 high"}
    a = next(iter(inp["weights"]))
    old = inp["weights"][a]["target"]
    inp["weights"][a]["target"] = round(old - 0.05, 4)
    inp["recheck"] = {"sent_at": f"{day.isoformat()}T{recheck_at}:00+09:00",
                      "agents": [{"focus": "속보", "model": "haiku", "effort": "medium", "returned": True},
                                 {"focus": "종목", "model": "haiku", "effort": "medium", "returned": True}],
                      "found": [], "premarket": "비어 있음",
                      "changed": [{"asset": a, "what": "weight", "from": old, "to": inp["weights"][a]["target"], "why": "가짜"}]}
    inp["lessons"] = [{"root": "L04", "kind": "같음", "reviews": [rid], "tried": {}} for rid in inp.get("_lessons_due", {})]
    return inp


class FlowDay(unittest.TestCase):
    """흉내 낸 날들(FLOW_SINCE를 당김): 꼭 할 단계가 빠지면 거절, 다 있으면 기록, 09:00 뒤면 '못 넣음' 줄만."""

    def test_days(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(C, "FLOW_SINCE", "2025-09-03"):
            root = TD.make_root(tmp)
            self.assertEqual(TD.run_day(root, dt.date(2025, 9, 2))[0], 0)          # 그 전 날: 예전처럼
            d = dt.date(2025, 9, 3)
            assert D.prepare(root, d, TD.at(d, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            brief = (root.day_dir(d) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("아침 흐름", brief)
            self.assertIn("피드백 정리", brief)
            inp = flow_input(root, d)
            p = root.day_dir(d) / "flow.json"
            n_run = len(R.read(root.records, "run"))
            for drop in ("precheck", "recheck", "lessons"):                         # 하나라도 빠지면 아무것도 안 씀
                bad = {k: v for k, v in inp.items() if k != drop}
                p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(D.record(root, d, TD.at(d, 8, 50), p), 1, drop)
                self.assertEqual(len(R.read(root.records, "run")), n_run)
            gone = Path(json.loads((root.day_dir(d) / "scan" / "_tasks.json").read_text(encoding="utf-8"))[0]["out"])
            keep = gone.read_text(encoding="utf-8")
            gone.unlink()                                                           # 조사 파일 하나가 없으면 거절
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, TD.at(d, 8, 50), p), 1)
            gone.write_text(keep, encoding="utf-8")
            early = flow_input(root, d, recheck_at="08:35")                          # 08:40 전에 보낸 다시 보기
            p.write_text(json.dumps(early, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, TD.at(d, 8, 50), p), 1)
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, d, TD.at(d, 8, 50), p), 0)
            scans = [x for x in R.read(root.records, "scan") if x["run"] == d.isoformat()]
            tasks = json.loads((root.day_dir(d) / "scan" / "_tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(len(scans), len(tasks))
            run = [x for x in R.read(root.records, "run") if x["run"] == d.isoformat()][0]
            self.assertEqual(run["steps"]["recheck"]["changed"][0]["to"], inp["recheck"]["changed"][0]["to"])
            self.assertEqual(run["steps"]["scan"]["assets"], len(tasks))
            self.assertEqual(R.check_folder(root.records), [])

            # 09:00:00에 판단하면 '못 넣음' 줄만 — 주문·예측·체결·채점 없음(모든 대상)
            late = dt.date(2025, 9, 4)
            assert D.prepare(root, late, TD.at(late, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            inp = flow_input(root, late)
            p = root.day_dir(late) / "flow.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            before = {k: len(R.read(root.records, k)) for k in R.FIELDS}
            self.assertEqual(D.record(root, late, TD.at(late, 9, 0), p), 1)
            after = {k: len(R.read(root.records, k)) for k in R.FIELDS}
            self.assertEqual({k: after[k] - before[k] for k in R.FIELDS if after[k] != before[k]}, {"run": 1})
            line = R.read(root.records, "run")[-1]
            self.assertEqual((line["id"], line["mode"]), (f"late:{late.isoformat()}", "late"))
            self.assertIn("못 넣음", line["notes"][0])
            self.assertTrue(line["steps"]["rejected"]["weights"])
            self.assertEqual(D.record(root, late, TD.at(late, 8, 0), p), 1)        # 같은 날 다시 못 함
            self.assertEqual(D.open_phase(root, late, TD.at(late, 9, 5)), 0)      # 장 시작 뒤 단계는 아무것도 안 함
            self.assertEqual(after["order"], len(R.read(root.records, "order")))
            self.assertEqual(RP.write(root, late), 0)
            diary = (root.reports / f"{late.isoformat()}.md").read_text(encoding="utf-8")
            self.assertIn("주문을 하나도 넣지 못했다", diary)
            self.assertEqual(R.check_folder(root.records), [])

            # 다음 날은 빈 날 없이 이어 간다(어제 체결·채점이 오늘 들어옴)
            nxt = dt.date(2025, 9, 5)
            assert D.prepare(root, nxt, TD.at(nxt, 7, 37), fetch=False, ticker=TD.TICK, echo=False) == 0
            self.assertNotIn("빠진 날", (root.day_dir(nxt) / "briefing.md").read_text(encoding="utf-8"))
            inp = flow_input(root, nxt)
            p = root.day_dir(nxt) / "flow.json"
            p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(D.record(root, nxt, TD.at(nxt, 8, 55), p), 0)
            self.assertEqual(R.check_folder(root.records), [])


if __name__ == "__main__":
    unittest.main()
