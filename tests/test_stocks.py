"""개별 종목(2026-10-29): 종목 목록, 계좌의 종목 보유·정산·평가, 손 예측 장치(상한·결론 조각·든 종목 매일 예측),
종목마다 대 보는 조사 상태, 매일 실행 흉내(종목을 사서 정산·평가·채점·일지까지). 네트워크를 쓰지 않는다."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from market import checks as CK
from market import config as C
from market import daily as D
from market import hand as H
from market import ledger as L
from market import prices as PR
from market import records as R
from market import report as RP
from market import stocks as ST
from market.prices import Row
from tests import fixtures
from tests.test_daily import TICK, at, fill

POOL = '''
[[stock]]
code = "111110"
name = "가짜반도체"
market = "KOSPI"
added = "2025-09-01"
why = "시험용"

[[stock]]
code = "222220"
name = "가짜은행"
market = "KOSPI"
added = "2025-09-01"
why = "시험용"

[[stock]]
code = "333330"
name = "가짜조선"
market = "KOSPI"
added = "2025-09-01"
why = "시험용"

[[stock]]
code = "444440"
name = "가짜자동차"
market = "KOSPI"
added = "2025-09-01"
why = "시험용"
'''
S1, S2, S3, S4 = "111110", "222220", "333330", "444440"


def make_root(tmp: str, stock_until: dt.date | None = None) -> D.Root:
    root = D.copy_for_simulation(Path(tmp) / "repo")
    (root.path / "stocks.toml").write_text(POOL, encoding="utf-8")
    start, end = dt.date(2024, 3, 1), dt.date(2025, 10, 31)
    for a, rows in fixtures.market(start, end).items():
        PR.append(a, list(rows), root.prices)
    for i, code in enumerate((S1, S2, S3, S4)):
        rows = fixtures.series(code, start, stock_until or end, price=50000 + 10000 * i, vol=0.02, drift=0.001)
        PR.append(code, list(rows), root.prices)
    return root


def buy_stock(inp: dict, code: str, target: float = 0.1, p_week: float = 0.6, decisive: bool = True,
              beat: str = "예") -> dict:
    """틀에 종목 사기를 더한다: 종목마다 대 보는 상태, 손 예측 셋, 비중(한국 묶음에서 덜어 냄)."""
    inp.setdefault("stock_readings", {})[code] = {
        "S_q_earnings_beat": {"value": beat, "why": "가짜: 잠정 실적과 전망 평균 비교", "places": ["P_dart"]}}
    for hz in C.ASSETS[code].horizons:
        inp["hand"] = [h for h in inp["hand"] if not (h["asset"] == code and h["horizon"] == hz)]
        used = [f"Q_earnings_beat={beat}"] if hz != 1 else ["Q_rate_up=" + inp["readings"]["S_q_rate_up"]["value"]]
        inp["hand"].append({"asset": code, "horizon": hz, "p_up": p_week if hz == 5 else 0.55, "size_lo": 2.0,
                            "size_hi": 4.0, "used": used, "decisive": used[:1] if decisive else [],
                            "reason": "가짜 종목 예측", "confidence": "보통"})
    inp["weights"][code] = {"target": target, "reason": "가짜: 한국 묶음 몫에서 덜어 냄 (Q_earnings_beat)"}
    inp["weights"]["069500"] = {"target": 0.3 - target, "reason": "가짜: 종목 몫만큼 덜어 냄"}
    return inp


def run(root: D.Root, day: dt.date, edit=None, record_time=(8, 30)) -> tuple[int, dict]:
    assert D.prepare(root, day, at(day, 7, 37), fetch=False, ticker=TICK, echo=False) == 0
    tpl = json.loads((root.day_dir(day) / "input.json").read_text(encoding="utf-8"))
    inp = fill(tpl)
    for code, rs in inp.get("stock_readings", {}).items():
        for r in rs.values():
            r.update(value="아니오", why="가짜: 최근 실적 없음", places=["P_dart"])
    inp["reading_checks"] = []  # 다시 보기는 이 시험의 대상이 아니다(빠지면 경고만)
    for a in inp["weights"]:
        if C.is_stock(a):  # 든 종목: 기본은 판다(사는 날은 edit가 정한다)
            inp["weights"][a] = {"target": 0.0, "reason": "가짜: 까닭이 없어 판다"}
    if edit:
        inp = edit(inp)
    p = root.day_dir(day) / "in.json"
    p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
    return D.record(root, day, at(day, *record_time), p), inp


class Registry(unittest.TestCase):
    def test_stock_spec_and_traits(self):
        self.assertTrue(C.is_stock("005930"))
        self.assertFalse(C.is_stock("069500"))      # 세 대상은 종목이 아니다
        self.assertFalse(C.is_stock("KRW-BTC"))
        spec = C.ASSETS["005930"]
        self.assertEqual((spec.kind, spec.unit, spec.horizons, spec.fill_field), ("stock", 1, (1, 5, 20), "close"))
        self.assertNotIn("005930", C.ASSETS)        # 반복·in에는 세 대상만 (계산 조각 효과 대상 검사)
        self.assertEqual(C.ASSET_IDS, ("069500", "360750", "KRW-BTC"))
        self.assertTrue(C.on_exchange("005930"))
        self.assertTrue(C.in_scope("005930", "equity") and C.in_scope("005930", "single"))
        self.assertFalse(C.in_scope("069500", "single"))
        self.assertFalse(C.in_scope("005930", "priced_abroad"))
        with self.assertRaises(KeyError):
            C.ASSETS["없는것"]

    def test_pool_file_checks_and_add(self):
        self.assertEqual(ST.check(ST.load()), [])   # 저장소의 종목 목록
        self.assertTrue(ST.load())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "stocks.toml"
            p.write_text(POOL, encoding="utf-8")
            self.assertEqual(ST.add(p, "555550", "가짜바이오", "실적 소식 — 시험", day=dt.date(2025, 9, 3)), "555550")
            self.assertIn("555550", ST.codes(p))
            self.assertTrue(p.read_text(encoding="utf-8").startswith(POOL.rstrip("\n")))  # 앞부분은 그대로, 끝에 덧붙임
            with self.assertRaises(SystemExit):
                ST.add(p, "555550", "또", "중복")
            with self.assertRaises(SystemExit):
                ST.add(p, "069500", "묶음", "세 대상은 종목이 아님")
            with self.assertRaises(SystemExit):
                ST.add(p, "666660", "이유없음", " ")
        bad = ST.check([{"code": "12", "name": "x"}, {"code": "777770", "name": "y", "market": "NYSE", "added": "d", "why": "w"}])
        self.assertTrue(any("코드" in e for e in bad) and any("market" in e for e in bad))

    def test_fetch_refuses_wrong_name(self):
        text = ('<chartdata symbol="000660" name="SK하이닉스" count="2">\n'
                '<item data="20261027|1346000|1381000|1336000|1374000|3136495" />\n'
                '<item data="20261028|1390000|1510000|1358000|1508000|4244800" />').encode("euc-kr")
        self.assertEqual(ST.chart_name(text.decode("euc-kr")), "SK하이닉스")
        with mock.patch.object(PR, "_get", return_value=text):
            self.assertEqual(len(ST.fetch("000660", 3, "SK하이닉스")), 2)
            with self.assertRaises(ValueError):
                ST.fetch("000660", 3, "삼성전자")       # 코드를 잘못 적은 것

    def test_stock_names_are_banned_in_pieces(self):
        self.assertIn("삼성전자", CK._names_in("삼성전자 실적이 좋으면 오른다"))
        self.assertIn("005930", CK._names_in("005930"))
        self.assertEqual(CK._names_in("회사의 실적이 예상보다 좋았으면"), [])


def srow(d, o, c):
    return Row(d, float(o), float(max(o, c)), float(min(o, c)), float(c), 1.0)


class LedgerStocks(unittest.TestCase):
    def test_buy_stock_from_index_share_settle_value_rebuild(self):
        days = [dt.date(2026, 7, 1) + dt.timedelta(days=i) for i in range(8)]
        etf = tuple(srow(d, 100, 100) for d in days if d.weekday() < 5)
        stock = tuple(srow(d, 1000, 1000 + 10 * i) for i, d in enumerate(days) if d.weekday() < 5)
        btc = tuple(srow(d, 50, 50) for d in days)
        acc = L.Account("live:hand", cash=0, units={"069500": 1000, "360750": 0, "KRW-BTC": 0})
        decided = dt.datetime(2026, 7, 1, 8, 30, tzinfo=C.KST)
        orders = acc.plan(dt.date(2026, 7, 1), {"069500": 0.85, "005930": 0.15}, {"069500": 100.0, "005930": 1000.0},
                          0.02, {}, decided)
        self.assertEqual([(o.asset, o.side) for o in orders], [("069500", "sell"), ("005930", "buy")])
        self.assertEqual(acc.held(), {"005930"})
        at = C.decision_dt(dt.date(2026, 7, 4))
        hists = {a: PR.view(a, rows, at) for a, rows in (("069500", etf), ("360750", etf), ("KRW-BTC", btc), ("005930", stock))}
        fills = acc.settle(hists, dt.date(2026, 7, 4))
        self.assertEqual([f["side"] for f in fills], ["sell", "buy"])  # 같은 15:30: 팔고 산다
        buy = fills[1]
        self.assertEqual((buy["fill_date"], buy["price"]), ("2026-07-01", 1000.0))  # 판단 뒤 첫 종가
        self.assertGreater(acc.units["005930"], 0)
        last = L.last_prices(hists)
        line = acc.record(dt.date(2026, 7, 4), last)
        self.assertIn("005930", line["positions"])
        self.assertIn("005930", line["prices"])
        self.assertAlmostEqual(line["value"], acc.cash + 1000 * 0 + acc.units["069500"] * 100 + acc.units["005930"] * 1020 / 1, 2)
        back = L.rebuild("live:hand", [line], [])
        self.assertEqual(back.units["005930"], acc.units["005930"])
        # 다 팔면 보유 칸에서 빠지고(세 대상은 0이어도 남는다) 평가 가격도 빠진다
        acc.units["005930"] = 0
        line2 = acc.record(dt.date(2026, 7, 5), last)
        self.assertNotIn("005930", line2["positions"])
        self.assertIn("360750", line2["positions"])
        self.assertNotIn("005930", line2["prices"])

    def test_expensive_stock_rounds_to_nearest_share_within_slack(self):
        """1주가 계좌의 15%쯤인 종목: 목표 10%면 1주(가까운 주 수), 4%면 0주, 1주가 상한 + 5%p를 넘으면 안 산다."""
        def qty(price, target):
            acc = L.Account("live:hand")
            orders = acc.plan(dt.date(2026, 7, 1), {"005930": target}, {"005930": price}, 0.02)
            return orders[0].qty_units if orders else 0
        self.assertEqual(qty(1_500_000.0, 0.10), 1)
        self.assertEqual(qty(1_500_000.0, 0.04), 0)
        self.assertEqual(qty(2_200_000.0, 0.15), 0)
        self.assertEqual(qty(100_000.0, 0.10), 10)  # 싼 종목도 가까운 주 수
        # 1주를 든 채 목표 10%면 반 주보다 작은 차이라 거래하지 않는다, 목표 0이면 다 판다
        acc = L.Account("live:hand", cash=8_500_000, units={"069500": 0, "360750": 0, "KRW-BTC": 0, "005930": 1})
        self.assertEqual(acc.plan(dt.date(2026, 7, 2), {"005930": 0.10}, {"005930": 1_500_000.0}, 0.02), [])
        sell = acc.plan(dt.date(2026, 7, 2), {"005930": 0.0}, {"005930": 1_500_000.0}, 0.02)
        self.assertEqual((sell[0].side, sell[0].qty_units), ("sell", 1))

    def test_lagging_unrelated_stock_does_not_block_settlement(self):
        days = [dt.date(2026, 7, 1) + dt.timedelta(days=i) for i in range(6)]
        etf = tuple(srow(d, 100, 100) for d in days if d.weekday() < 5)
        btc = tuple(srow(d, 50, 50) for d in days)
        acc = L.Account("live:hand")
        acc.plan(dt.date(2026, 7, 1), {"069500": 0.3}, {"069500": 100.0}, 0.0, {},
                 dt.datetime(2026, 7, 1, 8, 30, tzinfo=C.KST))
        at = C.decision_dt(dt.date(2026, 7, 4))
        hists = {"069500": PR.view("069500", etf, at), "360750": PR.view("360750", etf, at),
                 "KRW-BTC": PR.view("KRW-BTC", btc, at), "005930": PR.History("005930", ())}  # 시세 없는 종목
        self.assertEqual(len(acc.settle(hists, dt.date(2026, 7, 4))), 1)


class DailyStocks(unittest.TestCase):
    def test_buy_stock_then_settle_value_score_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d0 = dt.date(2025, 9, 2)  # 화
            # 첫날: 종목 하나를 산다(한국 묶음 몫에서 덜어 냄)
            D.prepare(root, d0, at(d0, 7, 37), fetch=False, ticker=TICK, echo=False)
            brief = (root.day_dir(d0) / "briefing.md").read_text(encoding="utf-8")
            self.assertIn("## 종목", brief)
            self.assertIn("가짜반도체", brief)
            tpl = json.loads((root.day_dir(d0) / "input.json").read_text(encoding="utf-8"))
            self.assertIn(S1, tpl["_stocks"])
            self.assertNotIn("S_q_earnings_beat", tpl["readings"])    # 종목마다 대 보는 상태는 따로
            self.assertTrue(any("[종목마다]" in r["asked"] for r in tpl["research"]))
            code, inp = run(root, d0, lambda i: buy_stock(i, S1))
            self.assertEqual(code, 0)
            orders = [o for o in R.read(root.records, "order") if o["account"] == "live:hand"]
            self.assertIn((S1, "buy"), {(o["asset"], o["side"]) for o in orders})
            preds = R.read(root.records, "prediction")
            mine = {p["predictor"] for p in preds if p["asset"] == S1}
            self.assertEqual(mine, {"hand", "calc", "baseline:half", "baseline:base_rate"})  # 이전 버전은 종목에 없음
            self.assertTrue(all(p["p_up"] is None for p in preds if p["asset"] == S1 and p["predictor"] == "calc"))
            hr = R.read(root.records, "hand_reading")[0]
            self.assertEqual(hr["by_asset"][S1]["S_q_earnings_beat"]["value"], "예")
            hand_wk = next(p for p in preds if p["id"] == f"{d0}:hand:{S1}:5")
            self.assertEqual(hand_wk["readings"]["S_q_earnings_beat"], "예")
            self.assertEqual([p for p in preds if p["asset"] == S2], [])  # 손대지 않은 종목은 기록하지 않는다
            self.assertEqual(R.check_folder(root.records), [])
            RP.write(root, d0)
            first = (root.reports / f"{d0}.md").read_text(encoding="utf-8")
            self.assertIn("가짜반도체(종목)", first)
            self.assertIn("ETF·종목은 장 마감 가격", first)

            # 다음 날들: 든 종목은 틀에 나오고 매일 예측한다. ETF처럼 D+3 실행에서 정산된다
            for d in (dt.date(2025, 9, 3), dt.date(2025, 9, 4), dt.date(2025, 9, 5)):
                D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
                tpl = json.loads((root.day_dir(d) / "input.json").read_text(encoding="utf-8"))
                self.assertIn(S1, tpl["weights"])
                self.assertIn((S1, 5), {(h["asset"], h["horizon"]) for h in tpl["hand"]})
                self.assertIn(S1, tpl["stock_readings"])
                code, _ = run(root, d, lambda i: buy_stock(i, S1))
                self.assertEqual(code, 0, d)
                RP.write(root, d)
            fills = [f for f in R.read(root.records, "fill") if f["asset"] == S1]
            self.assertEqual(fills[0]["run"], "2025-09-05")
            self.assertEqual(fills[0]["fill_date"], "2025-09-02")
            acc = [a for a in R.read(root.records, "account") if a["account"] == "live:hand" and a["run"] == "2025-09-05"][0]
            self.assertGreater(acc["positions"][S1], 0)
            self.assertIn(S1, acc["prices"])
            total = acc["cash"] + sum(n * acc["prices"][a][1] / C.ASSETS[a].unit for a, n in acc["positions"].items())
            self.assertAlmostEqual(total, acc["value"], 2)
            # 종목 손 예측이 채점된다(1일 예측: 9-02 기준 종가 → 9-03 종가)
            scored = {s["prediction"] for s in R.read(root.records, "score")}
            self.assertIn(f"{d0}:hand:{S1}:1", scored)
            self.assertIn(f"{d0}:baseline:half:{S1}:1", scored)
            text = (root.reports / "2025-09-05.md").read_text(encoding="utf-8")
            row = next(line for line in text.splitlines() if line.startswith("| 가짜반도체(종목)"))
            self.assertIn(f"{acc['positions'][S1]}주", row)
            self.assertIn("ETF·종목 0.015%", text)
            self.assertEqual(R.check_folder(root.records), [])

            # 팔기: target 0 → 팔고, 정산 뒤에는 보유 칸에서 빠지고 표에는 판 날 손익이 남는다
            def sell(i):
                i = buy_stock(i, S1, target=0.0, p_week=0.45)
                return i
            for d in (dt.date(2025, 9, 6), dt.date(2025, 9, 7), dt.date(2025, 9, 8), dt.date(2025, 9, 9),
                      dt.date(2025, 9, 10), dt.date(2025, 9, 11)):
                code, _ = run(root, d, sell if d == dt.date(2025, 9, 6) else None)
                self.assertEqual(code, 0, d)
                RP.write(root, d)
            sold = [f for f in R.read(root.records, "fill") if f["asset"] == S1 and f["side"] == "sell"]
            self.assertTrue(sold)
            last = [a for a in R.read(root.records, "account") if a["account"] == "live:hand"][-1]
            self.assertNotIn(S1, last["positions"])
            sold_day = (root.reports / f"{sold[0]['run']}.md").read_text(encoding="utf-8")
            self.assertIn("| 가짜반도체(종목) | 0주 |", sold_day)
            self.assertIn("가짜반도체(종목)", (root.reports / "2025-09-07-week.md").read_text(encoding="utf-8"))
            self.assertEqual(R.check_folder(root.records), [])

    def test_research_direction_uses_per_stock_readings(self):
        """종목마다 대 본 값(by_asset)이 그 종목의 결과와 짝지어져 조각 시험 칸에 들어간다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            for i in range(11):
                d = dt.date(2025, 9, 2) + dt.timedelta(days=i)
                self.assertEqual(run(root, d, lambda x, i=i: buy_stock(x, S1, beat="예" if i % 2 else "아니오"))[0], 0, d)
            with mock.patch.object(C, "RECORDS", root.records), mock.patch.object(C, "RESEARCH_SINCE", "2025-09-01"), \
                    mock.patch.object(CK, "_node", lambda nid: {**CK._raw_node(nid), "seen_until": "2025-09-01"}):
                verdict, detail = CK.research_direction("Q_earnings_beat")
            self.assertIn(f"up_odds {S1}:5", detail)
            self.assertNotIn("069500", detail)  # scope single: 묶음에는 걸리지 않는다

    def test_stock_rules_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d = dt.date(2025, 9, 2)
            cases = {
                "상한": lambda i: buy_stock(i, S1, target=0.2),
                "모름이면 안 삼": lambda i: buy_stock(i, S1, p_week=0.5),
                "결론 조각 없음": lambda i: buy_stock(i, S1, decisive=False),
                "종목 수": lambda i: buy_stock(buy_stock(buy_stock(buy_stock(i, S1, 0.05), S2, 0.05), S3, 0.05), S4, 0.05),
                "목록에 없음": lambda i: buy_stock(i, "999990"),
                "종목 조각을 묶음에": lambda i: (i["hand"][0]["used"].append("Q_earnings_beat=예"), i)[1],
                "종목 대 보기 빠짐": lambda i: (buy_stock(i, S1), i.pop("stock_readings"), i)[2],
            }
            for name, edit in cases.items():
                with self.subTest(name):
                    D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False)
                    code, _ = run(root, d, edit)
                    self.assertEqual(code, 1, name)
                    self.assertFalse(list(root.records.glob("*.jsonl")), name)

    def test_held_stock_must_be_redecided(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            d0 = dt.date(2025, 9, 2)
            self.assertEqual(run(root, d0, lambda i: buy_stock(i, S1))[0], 0)
            d1 = dt.date(2025, 9, 3)

            def drop(i):
                i["weights"].pop(S1)
                i["hand"] = [h for h in i["hand"] if h["asset"] != S1]
                return i
            self.assertEqual(run(root, d1, drop)[0], 1)
            errs, _ = H.validate({"day": "x"}, {"day": "x", "raw": {}, "research_states": [], "places": [], "stale": set(),
                                                "predictable": [], "stocks": [S1], "held": [S1], "calc_values": {},
                                                "due_reviews": {}, "due_checks": {}, "node_ids": set()})
            self.assertTrue(any("든 종목" in e for e in errs))

    def test_stale_stock_is_skipped_but_core_goes_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp, stock_until=dt.date(2025, 8, 29))  # 종목 시세만 낡음
            d = dt.date(2025, 9, 3)
            day = D.compute(root, d, at(d, 7, 50), {}, {})
            self.assertIn(S1, day.stock_skipped)
            self.assertNotIn(S1, day.stock_ok)
            self.assertEqual(day.skipped, {})
            self.assertEqual(run(root, d, lambda i: buy_stock(i, S1))[0], 1)  # 낡은 종목은 살 수 없다
            self.assertEqual(run(root, d)[0], 0)                                # 세 대상은 그대로 돈다


if __name__ == "__main__":
    unittest.main()
