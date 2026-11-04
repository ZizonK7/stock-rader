"""정산: 체결 가격·날짜, 수수료, 단위, 현금 음수 금지, 시간 순서."""
import datetime as dt
import unittest

from market import config as C
from market import ledger as L
from market import prices as PR
from market.prices import Row


def row(d, o, c):
    return Row(d, float(o), float(max(o, c)), float(min(o, c)), float(c), 1.0)


D = dt.date


def hists(at_day: dt.date, etf_rows, btc_rows, etf2_rows=None):
    at = C.decision_dt(at_day)
    return {"069500": PR.view("069500", etf_rows, at), "360750": PR.view("360750", etf2_rows or etf_rows, at),
            "KRW-BTC": PR.view("KRW-BTC", btc_rows, at)}


# 2026-07-03(금), 07-04·05 주말, 07-06(월)
ETF = (row(D(2026, 7, 1), 100, 101), row(D(2026, 7, 2), 101, 102), row(D(2026, 7, 3), 102, 104),
       row(D(2026, 7, 6), 104, 110), row(D(2026, 7, 7), 110, 111), row(D(2026, 7, 8), 111, 112))
BTC = tuple(row(D(2026, 7, d), 1000 + d, 1000 + d + 0.5) for d in range(1, 12))


class Fill(unittest.TestCase):
    def test_weekend_etf_order_fills_on_monday_close(self):
        acc = L.Account("a")
        acc.plan(D(2026, 7, 4), {"069500": 0.5}, {"069500": 104.0}, 0.0)
        self.assertEqual(acc.settle(hists(D(2026, 7, 7), ETF, BTC), D(2026, 7, 7)), [])  # 아직 코인 7/6 시가를 모름
        fills = acc.settle(hists(D(2026, 7, 9), ETF, BTC), D(2026, 7, 9))
        self.assertEqual(len(fills), 1)
        self.assertEqual((fills[0]["fill_date"], fills[0]["price"]), ("2026-07-06", 110.0))

    def test_coin_order_fills_at_that_days_open(self):
        acc = L.Account("a")
        acc.plan(D(2026, 7, 5), {"KRW-BTC": 0.5}, {"KRW-BTC": 1003.5}, 0.0)
        fills = acc.settle(hists(D(2026, 7, 8), ETF, BTC), D(2026, 7, 8))
        self.assertEqual((fills[0]["fill_date"], fills[0]["price"]), ("2026-07-05", 1005.0))
        self.assertEqual(fills[0]["qty_units"] % 1, 0)  # 1e-8 단위 정수

    def test_fees_and_rounding(self):
        acc = L.Account("a", cash=1_000_000)
        acc.plan(D(2026, 7, 1), {"069500": 0.5}, {"069500": 100.0}, 0.0)
        f = acc.settle(hists(D(2026, 7, 4), ETF, BTC), D(2026, 7, 4))[0]
        self.assertEqual(f["price"], 101.0)
        self.assertEqual(f["value"], f["qty_units"] * 101)
        self.assertEqual(f["fee"], -(-f["value"] * 15 // 100000))  # 0.015% 올림
        self.assertEqual(acc.cash, 1_000_000 - f["value"] - f["fee"])

    def test_cash_never_negative_buy_is_reduced(self):
        acc = L.Account("a", cash=10_000)
        acc.pending.append(L.Order("o", D(2026, 7, 1), "a", "069500", "buy", 1000, 1.0))
        f = acc.settle(hists(D(2026, 7, 4), ETF, BTC), D(2026, 7, 4))[0]
        self.assertGreaterEqual(acc.cash, 0)
        self.assertEqual(f["qty_units"], 98)  # 10,000원 ÷ (101 × 1.00015)
        self.assertIn("줄임", f["note"])

    def test_sell_only_what_is_held(self):
        acc = L.Account("a", cash=0)
        acc.units["069500"] = 5
        acc.pending.append(L.Order("o", D(2026, 7, 1), "a", "069500", "sell", 9, 1.0))
        f = acc.settle(hists(D(2026, 7, 4), ETF, BTC), D(2026, 7, 4))[0]
        self.assertEqual((f["qty_units"], acc.units["069500"]), (5, 0))

    def test_settles_in_time_order_coin_morning_before_etf_afternoon(self):
        acc = L.Account("a", cash=0)
        acc.units["069500"] = 100
        acc.pending += [L.Order("buy", D(2026, 7, 2), "a", "KRW-BTC", "buy", 100_000_000, 1000.0),
                        L.Order("sell", D(2026, 7, 2), "a", "069500", "sell", 100, 100.0)]
        fills = acc.settle(hists(D(2026, 7, 5), ETF, BTC), D(2026, 7, 5))
        self.assertEqual([f["order"] for f in fills], ["buy", "sell"])
        self.assertEqual(fills[0]["qty_units"], 0)  # 09:00 사기 때는 15:30 판 돈이 아직 없다


class Plan(unittest.TestCase):
    def test_band_and_pending_are_counted(self):
        acc = L.Account("a")
        first = acc.plan(D(2026, 7, 1), {"069500": 1 / 3}, {"069500": 100.0}, 0.02)
        self.assertEqual(len(first), 1)
        again = acc.plan(D(2026, 7, 2), {"069500": 1 / 3}, {"069500": 100.0}, 0.02)
        self.assertEqual(again, [])  # 체결 안 된 주문을 이미 셌다

    def test_target_weight_rule(self):
        """R_weights_v2 (2026-11-04): 1/3 + (p − 0.5) × 4/3, 0~0.5. 모름·동전(0.5)이면 정확히 기준선 1/3."""
        self.assertAlmostEqual(L.target_weight(0.50), 1 / 3)
        self.assertAlmostEqual(L.target_weight(None), 1 / 3)       # 모름도 기준선(현금으로 물러서지 않는다)
        self.assertAlmostEqual(L.target_weight(0.40), 1 / 3 - 0.1 * 4 / 3)
        self.assertAlmostEqual(L.target_weight(0.55), 1 / 3 + 0.05 * 4 / 3)
        self.assertEqual(L.target_weight(0.25), 0.0)               # 1/3 − 1/3 = 0
        self.assertEqual(L.target_weight(0.10), 0.0)               # 아래로 자름
        self.assertEqual(L.target_weight(0.80), 0.5)               # 위로 자름(반 넘게는 들지 않는다)

    def test_target_weight_v1_kept_for_replay(self):
        """이전 규칙(R_weights v1)은 1단계 재생이 쓴 그대로 남는다: p = 0.5에서 1/6만 산다(모름 = 현금 보유)."""
        self.assertEqual(L.target_weight_v1(0.40), 0)
        self.assertAlmostEqual(L.target_weight_v1(0.50), 1 / 6)
        self.assertAlmostEqual(L.target_weight_v1(0.70), 1 / 3)
        self.assertIsNone(L.target_weight_v1(None))


if __name__ == "__main__":
    unittest.main()
