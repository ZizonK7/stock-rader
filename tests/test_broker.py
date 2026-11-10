"""가상 증권사(market/broker.py)·규칙 숫자(market/rules.py)·새 기록 칸 — docs/broker-sandbox.md 13–19절.

규칙마다 '되는 예'와 '거절되는 예'. 시험 이름의 번호(t01…t20)는 1회차 지시서 '시험 목록'의 번호다(t21은 test_quotes.py).
시세는 아래 bars()가 만든 가짜 일봉이다(실제 시세가 아니다, 네트워크 없음)."""
import copy
import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path

from market import broker as B
from market import config as C
from market import quotes as Q
from market import records as R
from market import rules as RU
from market.prices import Row

D = dt.date
ROOT = Path(__file__).resolve().parent.parent


def T(m, d, h, mi=0, s=0):
    return dt.datetime(2026, m, d, h, mi, s, tzinfo=C.KST)


def bars(start=D(2026, 10, 26), end=D(2026, 11, 20)):
    """영업일마다 오르는 가짜 일봉. 한국 묶음 ETF 8만 원대(호가 5원), 해외 지수 ETF 2만 5천 원대, 종목 10만 원대(호가 100원),
    코인 1억 원대(호가 1,000원, 주말에도)."""
    out = {"069500": [], "360750": [], "005930": [], "KRW-BTC": []}
    i = k = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            o = 80_000 + 100 * i
            out["069500"].append(Row(d, o, o + 500, o - 500, o + 50, 1000.0))
            o = 25_000 + 10 * i
            out["360750"].append(Row(d, o, o + 100, o - 100, o + 5, 1000.0))
            o = 100_000 + 100 * i
            out["005930"].append(Row(d, o, o + 1000, o - 1000, o + 100, 1000.0))
            i += 1
        o = 100_000_000 + 100_000 * k
        out["KRW-BTC"].append(Row(d, o, o + 2_000_000, o - 2_000_000, o + 500_000, 10.0))
        k += 1
        d += dt.timedelta(days=1)
    return out


def bar(rows, asset, day):
    return next(r for r in rows[asset] if r.date == day)


def replace_bar(rows, asset, day, **kw):
    rows = {a: list(rs) for a, rs in rows.items()}
    rows[asset] = [Row(r.date, kw.get("open", r.open), kw.get("high", r.high), kw.get("low", r.low),
                       kw.get("close", r.close), r.volume) if r.date == day else r for r in rows[asset]]
    return rows


START = T(10, 30, 8, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.rows = bars()
        self.q = Q.BarQuotes(self.rows)

    def broker(self, q=None, **start):
        b = B.Broker("t", q or self.q)
        r = b.start(START, **start)
        self.assertTrue(r.ok, r.reason)
        return b

    def check_clean(self, b, rows=None):
        errs, _ = B.check(b.lines(), rows or self.rows)
        self.assertEqual(errs, [])

    def limit(self, asset, session):
        prev = [r for r in self.rows[asset] if r.date < session][-1].close
        return RU.default_open_limit(asset, prev)


class Accounts(Base):
    def test_t01_start_state_and_bank_to_sec_is_instant(self):
        b = self.broker(bank=1_000_000, upbit=50_000, units={"069500": 3}, cost={"069500": 240_000})
        again = B.Broker("t", self.q, b.lines())          # 기록 줄에서 다시 계산
        for x in (b, again):
            self.assertEqual(x.pockets(), {"bank": 1_000_000, "sec": 0, "upbit": 50_000})
            self.assertEqual(x.holdings(), {"069500": 3})
            self.assertEqual(x.avg_cost("069500"), 80_000)
        self.assertEqual(again.problems, [])
        r = b.transfer(T(10, 30, 8, 5), "bank", "sec", 400_000)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(b.orderable("sec", T(10, 30, 8, 5)), 400_000)     # 그 자리에서
        self.assertEqual(b.withdrawable("sec", T(10, 30, 8, 5)), 400_000)
        self.assertFalse(b.start(T(10, 30, 8, 6), bank=1).ok)            # 시작은 한 번
        self.check_clean(b)

    def test_t14_transfers_fee_routes_and_limits(self):
        b = self.broker(bank=100_000, upbit=100_000)
        at = T(11, 2, 10, 0)
        self.assertTrue(b.transfer(at, "upbit", "bank", 50_000).ok)
        self.assertEqual(b.pockets(), {"bank": 150_000, "sec": 0, "upbit": 49_000})    # 출금 수수료 1,000원
        r = b.transfer(at, "upbit", "sec", 1_000)
        self.assertFalse(r.ok)
        self.assertIn("은행을 거친다", r.reason)
        self.assertFalse(b.transfer(at, "sec", "upbit", 1).ok)
        r = b.transfer(at, "upbit", "bank", 48_500)                      # 48,500 + 1,000 > 49,000
        self.assertFalse(r.ok)
        self.assertFalse(b.transfer(at, "bank", "sec", 150_001).ok)
        self.assertFalse(b.transfer(at, "bank", "sec", 0).ok)
        self.assertEqual(b.pockets(), {"bank": 150_000, "sec": 0, "upbit": 49_000})
        self.assertTrue(b.transfer(at, "bank", "sec", 150_000).ok)        # 같은 시각 두 번 이체(은행을 거침)
        self.assertEqual(b.pockets()["bank"], 0)
        rejects = [d for k, d in B.mine(b.lines()) if k == "broker" and d["type"] == "reject"]
        self.assertEqual(len(rejects), 5)
        self.assertTrue(all(d["note"] for d in rejects))
        self.check_clean(b)


class OpenOrders(Base):
    def test_t02_open_buy_reserves_fills_below_limit_refunds_and_settles_d_plus_2(self):
        b = self.broker(sec=1_000_000)
        at, day = T(11, 6, 8, 0), D(2026, 11, 6)               # 금요일
        lim = self.limit("069500", day)
        r = b.order(at, "069500", "buy", "open", qty=10, limit=lim)
        self.assertTrue(r.ok, r.reason)
        reserved = 10 * lim + RU.fee("069500", 10 * lim)
        self.assertEqual(b.orderable("sec", at), 1_000_000 - reserved)
        self.assertEqual(B.mine(b.lines())[-1][1]["reserved"], reserved)
        self.assertEqual(b.sync(T(11, 6, 8, 59)).lines, [])            # 09:00 전에는 체결 없음
        r = b.sync(T(11, 6, 9, 1))
        fill = next(d for k, d in r.lines if k == "fill")
        o = bar(self.rows, "069500", day).open
        self.assertLess(o, lim)
        self.assertEqual((fill["price"], fill["price_kind"], fill["at"]), (o, "open", "2026-11-06T09:00:00+09:00"))
        self.assertEqual(fill["settle"], "2026-11-10")                  # 금 + 2영업일 = 화(주말을 건넘)
        self.assertIs(fill["source"]["final"], False)                   # 진행 중인 봉의 시가
        cost = 10 * o + RU.fee("069500", 10 * o)
        self.assertEqual(b.orderable("sec", T(11, 6, 9, 2)), 1_000_000 - cost)   # 차액은 돌려받음
        self.assertEqual(b.holdings(), {"069500": 10})
        self.check_clean(b)

    def test_t03_short_money_rejects_the_whole_order(self):
        b = self.broker(sec=100_000)
        at = T(11, 6, 8, 0)
        before = (b.pockets(), b.orderable("sec", at), len(b.pending()))
        r = b.order(at, "069500", "buy", "open", qty=10, limit=self.limit("069500", D(2026, 11, 6)))
        self.assertFalse(r.ok)
        self.assertIn("돈이 모자람", r.reason)
        self.assertEqual([k for k, _ in r.lines], ["broker"])
        self.assertEqual(r.lines[0][1]["type"], "reject")
        self.assertEqual(r.lines[0][1]["data"]["qty"], 10)               # 수량을 줄여 받지 않는다
        self.assertEqual((b.pockets(), b.orderable("sec", at), len(b.pending())), before)
        self.check_clean(b)

    def test_t04_selling_and_buying_with_the_same_nine_oclock_money_is_rejected(self):
        b = self.broker(units={"069500": 10}, cost={"069500": 800_000})
        day = D(2026, 11, 6)
        self.assertTrue(b.order(T(11, 6, 8, 50), "069500", "sell", "open", qty=10).ok)
        r = b.order(T(11, 6, 8, 50), "360750", "buy", "open", qty=5, limit=self.limit("360750", day))
        self.assertFalse(r.ok)                                           # 같은 09:00에 팔리는 돈은 아직 없다
        self.assertIn("돈이 모자람", r.reason)
        r = b.order(T(11, 6, 9, 1), "360750", "buy", "now", qty=5)      # 팔린 뒤(09:01)에는 그 돈으로 산다
        self.assertTrue(r.ok, r.reason)
        kinds = [(k, d.get("price_kind") or d.get("order_kind")) for k, d in r.lines]
        self.assertEqual(kinds, [("fill", "open"), ("order", "now"), ("fill", "now")])
        sale = r.lines[0][1]
        self.assertEqual(sale["settle"], "2026-11-10")                  # 결제 전이어도 같은 증권 계좌에서 산다
        self.check_clean(b)

    def test_t04b_same_time_fill_money_cannot_be_used_at_that_time(self):
        """T1: 같은 시각에 들어온 체결 돈(업비트 지금 팔기)은 같은 시각의 사기에 못 쓰고, 1초 뒤에는 쓴다."""
        b = self.broker(units={"KRW-BTC": 1_000_000})                    # 0.01개
        at = T(11, 2, 10, 0)
        self.assertTrue(b.order(at, "KRW-BTC", "sell", "now", qty=1_000_000).ok)
        r = b.order(at, "KRW-BTC", "buy", "now", amount=100_000)
        self.assertFalse(r.ok)
        self.assertIn("돈이 모자람", r.reason)
        self.assertFalse(b.transfer(at, "upbit", "bank", 10_000).ok)
        self.assertTrue(b.order(at + dt.timedelta(seconds=1), "KRW-BTC", "buy", "now", amount=100_000).ok)
        self.check_clean(b)

    def test_t05_case_2026_11_05_etf_money_cannot_buy_coin_until_settled(self):
        b = self.broker(units={"069500": 10}, cost={"069500": 800_000})
        self.assertTrue(b.order(T(11, 5, 8, 30), "069500", "sell", "open", qty=10).ok)    # 목
        r = b.order(T(11, 5, 9, 0, 30), "KRW-BTC", "buy", "now", amount=300_000)
        self.assertFalse(r.ok)
        self.assertIn("upbit", r.reason)                                  # 업비트 원화가 없다
        r = b.transfer(T(11, 5, 9, 0, 40), "sec", "bank", 300_000)
        self.assertFalse(r.ok)
        self.assertIn("결제일", r.reason)
        sale = next(d for k, d in B.mine(b.lines()) if k == "fill")
        self.assertEqual(sale["settle"], "2026-11-09")                   # 목 + 2영업일 = 월
        self.assertFalse(b.transfer(T(11, 6, 15, 0), "sec", "bank", 300_000).ok)
        self.assertFalse(b.transfer(T(11, 9, 9, 0, 0), "sec", "bank", 300_000).ok)     # 09:00 '뒤'부터
        at = T(11, 9, 9, 0, 10)
        self.assertTrue(b.transfer(at, "sec", "bank", 300_000).ok)
        self.assertTrue(b.transfer(at, "bank", "upbit", 300_000).ok)
        r = b.order(at, "KRW-BTC", "buy", "now", amount=290_000)
        self.assertTrue(r.ok, r.reason)
        self.check_clean(b)

    def test_t06_cannot_sell_more_than_held_minus_pending_sells(self):
        b = self.broker(units={"069500": 10, "KRW-BTC": 100_000}, cost={"069500": 800_000, "KRW-BTC": 100_000})
        self.assertTrue(b.order(T(11, 6, 8, 0), "069500", "sell", "open", qty=6).ok)
        r = b.order(T(11, 6, 8, 1), "069500", "sell", "open", qty=5)
        self.assertFalse(r.ok)
        self.assertIn("가진 것보다", r.reason)
        self.assertTrue(b.order(T(11, 6, 8, 2), "069500", "sell", "close", qty=4).ok)
        self.assertFalse(b.order(T(11, 6, 8, 3), "005930", "sell", "open", qty=1).ok)      # 없는 것 팔기(공매도)
        self.assertFalse(b.order(T(11, 6, 8, 4), "KRW-BTC", "sell", "now", qty=100_001).ok)
        self.check_clean(b)

    def test_t10_open_buy_at_limit_hangs_then_fills_at_limit_or_expires(self):
        day = D(2026, 11, 6)
        prev = bar(self.rows, "069500", D(2026, 11, 5)).close
        lim = RU.floor_tick("069500", prev)                               # 시가와 같게 맞춘다
        for low, expect in ((lim - 5, "limit"), (lim, "expire")):
            rows = replace_bar(self.rows, "069500", day, open=lim, low=low, high=lim + 500, close=lim + 10)
            b = self.broker(q=Q.BarQuotes(rows), sec=1_000_000)
            self.assertTrue(b.order(T(11, 6, 8, 0), "069500", "buy", "open", qty=10, limit=lim).ok)
            held = b.orderable("sec", T(11, 6, 8, 0))
            r = b.sync(T(11, 6, 9, 1))                                    # 시가 = 한도 → 체결 안 함, 장중에 걸려 있음
            self.assertEqual(r.lines, [])
            self.assertEqual(len(b.pending()), 1)
            self.assertFalse(b.cancel(T(11, 6, 10, 0), b.pending()[0].id).ok)   # 장중에 걸린 주문은 취소 못 함
            r = b.sync(T(11, 7, 8, 0))
            line = [d for k, d in r.lines if k in ("fill", "broker")][0]
            if expect == "limit":
                self.assertEqual((line["price"], line["price_kind"], line["at"]),
                                 (float(lim), "limit", "2026-11-06T15:30:00+09:00"))
            else:
                self.assertEqual((line["type"], line["at"]), ("expire", "2026-11-06T15:30:00+09:00"))
                self.assertEqual(b.orderable("sec", T(11, 7, 8, 0)), 1_000_000)   # 잡은 돈이 풀림
                self.assertLess(held, 1_000_000)
            self.assertEqual(line["run"], "2026-11-07")                  # 줄은 알게 된 날의 실행
            self.assertEqual(b.pending(), [])
            self.check_clean(b, rows)

    def test_t11_close_order_only_before_1520_fills_at_close(self):
        day = D(2026, 11, 6)
        b = self.broker(sec=2_000_000)
        lim = self.limit("069500", day)
        r = b.order(T(11, 6, 15, 20), "069500", "buy", "close", qty=1, limit=lim)
        self.assertFalse(r.ok)
        self.assertIn("15:20", r.reason)
        self.assertTrue(b.order(T(11, 6, 15, 20, 0) - dt.timedelta(minutes=1), "069500", "buy", "close", qty=1,
                                limit=lim).ok)
        self.assertEqual(b.sync(T(11, 6, 15, 59)).lines, [])           # 봉이 끝나기 전
        fill = next(d for k, d in b.sync(T(11, 6, 16, 0)).lines if k == "fill")
        self.assertFalse(b.order(T(11, 7, 10, 0), "069500", "buy", "close", qty=1, limit=lim).ok)   # 토요일
        self.assertEqual((fill["price"], fill["price_kind"]), (bar(self.rows, "069500", day).close, "close"))
        self.check_clean(b)
        # 종가 ≥ 한도면 체결하지 않고 끝난다
        b = self.broker(sec=2_000_000)
        c = bar(self.rows, "069500", day).close
        self.assertTrue(b.order(T(11, 6, 10, 0), "069500", "buy", "close", qty=1, limit=c).ok)
        r = b.sync(T(11, 6, 16, 0))
        self.assertEqual([d["type"] for k, d in r.lines], ["expire"])
        self.check_clean(b)


class NowOrders(Base):
    def test_t07_krx_now_order_needs_open_market_and_a_fresh_price(self):
        at = T(11, 6, 10, 0)
        o = bar(self.rows, "069500", D(2026, 11, 6)).open
        b = self.broker(sec=1_000_000, units={"069500": 5}, cost={"069500": 400_000})
        r = b.order(at, "069500", "buy", "now", qty=2)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.lines[-1][1]["price"], o + 5)                 # 마지막 체결가 + 한 칸
        r = b.order(at, "069500", "sell", "now", qty=1)
        self.assertEqual(r.lines[-1][1]["price"], o - 5)                 # − 한 칸
        self.check_clean(b)
        for when in (T(11, 6, 8, 0), T(11, 6, 15, 20), T(11, 6, 16, 0), T(11, 7, 10, 0)):   # 장이 닫힘·주말
            b = self.broker(sec=1_000_000)
            self.assertFalse(b.order(when, "069500", "buy", "now", qty=1).ok, when)
        b = self.broker(q=Q.BarQuotes(self.rows, stale=dt.timedelta(seconds=61)), sec=1_000_000)
        r = b.order(at, "069500", "buy", "now", qty=1)
        self.assertFalse(r.ok)
        self.assertIn("묵었음", r.reason)
        b = self.broker(q=Q.BarQuotes(self.rows, stale=dt.timedelta(seconds=59)), sec=1_000_000)
        self.assertTrue(b.order(at, "069500", "buy", "now", qty=1).ok)

        class NoPrice(Q.BarQuotes):
            def now(self, asset, at):
                return None

        class Closed(Q.BarQuotes):
            def now(self, asset, at):
                n = super().now(asset, at)
                return Q.Now(n.asset, n.price, n.time, False, n.source)

        r = self.broker(q=NoPrice(self.rows), sec=1_000_000).order(at, "069500", "buy", "now", qty=1)
        self.assertIn("받지 못함", r.reason)
        r = self.broker(q=Closed(self.rows), sec=1_000_000).order(at, "069500", "buy", "now", qty=1)
        self.assertIn("장이 닫혀", r.reason)

    def test_t09_upbit_now_walks_the_book_with_fee_and_8_digit_units(self):
        q = Q.BarQuotes(self.rows, book_sizes=(0.002, 0.003, 1.0))
        b = self.broker(q=q, upbit=1_000_000)
        at = T(11, 2, 10, 0)
        p0 = bar(self.rows, "KRW-BTC", D(2026, 11, 2)).open
        r = b.order(at, "KRW-BTC", "buy", "now", amount=300_000)
        self.assertTrue(r.ok, r.reason)
        f = r.lines[-1][1]
        a1 = RU.ceil_tick("KRW-BTC", p0)
        units = 0.002 + (300_000 - 0.002 * a1) / (a1 + 1000)           # 1단계 0.002개, 남은 돈은 2단계
        self.assertEqual(f["qty_units"], int(units * 1e8))
        self.assertEqual((f["value"], f["fee"], f["tax"]), (300_000, 150, 0))
        self.assertGreater(f["price"], a1)                               # 여러 단계 평균
        self.assertEqual(b.pockets()["upbit"], 1_000_000 - 300_150)
        self.check_clean(b)
        # 호가가 모자라면 모자란 만큼 체결 안 함
        b = self.broker(q=Q.BarQuotes(self.rows, book_sizes=(0.001,)), upbit=1_000_000)
        f = b.order(at, "KRW-BTC", "buy", "now", amount=300_000).lines[-1][1]
        self.assertEqual(f["qty_units"], 100_000)
        self.assertLess(f["value"], 300_000)
        self.assertIn("모자라", f["note"])
        self.assertEqual(b.pockets()["upbit"], 1_000_000 - f["value"] - f["fee"])
        # 5,000원 미만·업비트 원화 모자람
        self.assertIn("최소 주문 금액", b.order(at, "KRW-BTC", "buy", "now", amount=4_999).reason)
        self.assertIn("돈이 모자람", b.order(at, "KRW-BTC", "buy", "now", amount=900_000).reason)
        self.assertFalse(b.order(at, "KRW-BTC", "buy", "open", amount=10_000).ok)       # 업비트는 지금 주문만
        # 지금 팔기는 사려는 값들을 먹는다
        b = self.broker(q=Q.BarQuotes(self.rows, book_sizes=(0.001, 0.002)), units={"KRW-BTC": 200_000},
                        cost={"KRW-BTC": 200_000})
        f = b.order(at, "KRW-BTC", "sell", "now", qty=200_000).lines[-1][1]
        b1 = RU.floor_tick("KRW-BTC", p0) - (1000 if RU.floor_tick("KRW-BTC", p0) == a1 else 0)
        self.assertEqual(f["value"], int(0.001 * b1 + 0.001 * (b1 - 1000)))
        self.assertEqual(f["fee"], RU.fee("KRW-BTC", f["value"]))
        self.assertEqual(b.holdings(), {})
        self.check_clean(b)


class Rules(Base):
    def test_t08_tick_boundaries_and_limit_price_rules(self):
        s, e = "005930", "069500"
        self.assertEqual([RU.tick(s, p) for p in (1_999, 2_000, 199_900, 200_000, 499_500, 500_000)],
                         [1, 5, 100, 500, 500, 1_000])
        self.assertEqual([RU.tick(e, p) for p in (1_999, 2_000, 82_495)], [1, 5, 5])
        self.assertTrue(RU.on_tick(e, 82_495) and RU.on_tick(e, 25_165) and RU.on_tick(s, 200_500))
        self.assertFalse(RU.on_tick(e, 82_497) or RU.on_tick(s, 199_950) or RU.on_tick(s, 200_100))
        self.assertEqual((RU.step(s, 2_000, -1), RU.step(s, 1_999, 1), RU.step(s, 200_000, -1)), (1_999, 2_000, 199_900))
        self.assertEqual(RU.tick("KRW-BTC", 100_000_000), 1_000)
        self.assertIsNone(RU.tick("KRW-BTC", 1_500_000))                 # 200만 원 아래 표는 아직 없다
        self.assertEqual(RU.price_limits(e, 10_000), (7_000, 13_000))
        b = self.broker(sec=10_000_000)
        day = D(2026, 11, 6)
        prev = bar(self.rows, e, D(2026, 11, 5)).close
        lo, hi = RU.price_limits(e, prev)
        r = b.order(T(11, 6, 8, 0), e, "buy", "open", qty=1, limit=hi - 2)
        self.assertIn("호가 단위", r.reason)
        r = b.order(T(11, 6, 8, 0), e, "buy", "open", qty=1, limit=hi + 5)
        self.assertIn("가격 제한 폭", r.reason)
        self.assertTrue(b.order(T(11, 6, 8, 0), e, "buy", "open", qty=1, limit=hi).ok)
        r = b.order(T(11, 6, 8, 0), s, "buy", "open", qty=1, limit=self.limit(s, day) + 50)
        self.assertIn("호가 단위", r.reason)
        self.assertFalse(b.order(T(11, 6, 8, 0), e, "sell", "open", qty=1, limit=hi).ok)   # 팔기는 시장가
        self.assertFalse(b.order(T(11, 6, 8, 0), e, "buy", "open", qty=1).ok)              # 사기는 한도가 있어야
        self.assertFalse(b.order(T(11, 6, 10, 0), e, "buy", "open", qty=1, limit=hi).ok)   # 장중엔 장 시작 주문 없음
        self.check_clean(b)

    def test_t12_taxes_on_selling(self):
        self.assertEqual(RU.sell_tax("005930", 1_000_000, 50_000), 2_000)        # 종목: 판 금액의 0.20%
        self.assertEqual(RU.sell_tax("069500", 1_000_000, 50_000), 0)            # 국내 묶음 ETF
        self.assertEqual(RU.sell_tax("360750", 1_000_000, 50_000), 7_700)        # 해외 지수 ETF: 이익 × 15.4%
        self.assertEqual(RU.sell_tax("360750", 1_000_000, -50_000), 0)           # 손실이면 0
        self.assertEqual(RU.sell_tax("KRW-BTC", 1_000_000, 50_000), 0)
        day = D(2026, 11, 6)
        b = self.broker(sec=1_000_000, units={"360750": 10, "005930": 3, "069500": 2},
                        cost={"360750": 240_000, "005930": 600_000, "069500": 100_000})
        r = b.order(T(11, 6, 10, 0), "360750", "buy", "now", qty=10)            # 평균 산 값이 움직인다
        buy = r.lines[-1][1]
        self.assertEqual(b.avg_cost("360750"), (240_000 + buy["value"]) / 20)
        for a in ("360750", "005930", "069500"):
            self.assertTrue(b.order(T(11, 6, 16, 0), a, "sell", "open", qty=5 if a == "360750" else 1).ok)
        fills = {d["asset"]: d for k, d in b.sync(T(11, 9, 9, 1)).lines if k == "fill"}
        o = bar(self.rows, "360750", D(2026, 11, 9)).open
        gain = 5 * o - (240_000 + buy["value"]) / 20 * 5
        self.assertGreater(gain, 0)
        self.assertEqual(fills["360750"]["tax"], -(-gain * 0.154 // 1))
        self.assertEqual(fills["005930"]["tax"], -(-fills["005930"]["value"] * 0.002 // 1))
        self.assertEqual(fills["069500"]["tax"], 0)
        self.check_clean(b)
        b = self.broker(units={"360750": 10}, cost={"360750": 300_000})          # 평균 3만 원 → 손실
        self.assertTrue(b.order(T(11, 6, 8, 0), "360750", "sell", "open", qty=10).ok)
        f = next(d for k, d in b.sync(T(11, 6, 9, 1)).lines if k == "fill")
        self.assertEqual(f["tax"], 0)
        self.assertLess(f["gain"], 0)

    def test_t19_every_rule_has_status_source_and_date_and_the_doc_table_ids_exist(self):
        for r in RU.RULES.values():
            self.assertIn(r.status, RU.STATUSES, r.id)
            self.assertTrue(r.source.strip(), r.id)
            D.fromisoformat(r.checked)
        doc = (ROOT / "docs" / "broker-sandbox.md").read_text(encoding="utf-8")
        table = doc.split("## 19.")[1]
        ids = re.findall(r"^\| `([a-z_]+)` \|", table, re.M)
        self.assertGreaterEqual(len(ids), 25)
        self.assertEqual(sorted(set(ids) - set(RU.RULES)), [])

    def test_business_days_and_calendar_warnings(self):
        self.assertEqual(RU.settle_date(D(2026, 12, 23)), D(2026, 12, 28))      # 12-25 휴장
        self.assertEqual(RU.settle_date(D(2026, 12, 30)), D(2027, 1, 5))       # 12-31·1-1 휴장, 주말 → 월·화
        days = {D(2026, 11, 2), D(2026, 11, 4)}                                 # 지난 날은 봉이 있던 날로
        self.assertFalse(RU.is_business_day(D(2026, 11, 3), days))
        self.assertTrue(RU.is_business_day(D(2026, 11, 5), days))              # 봉 날짜 뒤는 표로
        w = RU.calendar_warnings(days, D(2026, 11, 2), D(2026, 11, 4))
        self.assertEqual(len(w), 1)
        self.assertIn("2026-11-03", w[0])


class Money(Base):
    def test_t13_withdrawable_by_date(self):
        """현금 S, 수요일 장 시작에 팔기, 목요일 장 시작 사기: 판 돈은 결제일(금) 09:00 뒤부터, 산 돈은 결제일(월)에 나간다."""
        S = 1_000_000
        b = self.broker(sec=S, units={"069500": 10}, cost={"069500": 800_000})
        self.assertTrue(b.order(T(11, 4, 8, 0), "069500", "sell", "open", qty=10).ok)
        sale = next(d for k, d in b.sync(T(11, 4, 10, 0)).lines if k == "fill")
        P = sale["value"] - sale["fee"]
        self.assertEqual(sale["settle"], "2026-11-06")
        self.assertEqual(b.withdrawable("sec", T(11, 4, 10, 0)), S)             # 판 돈은 아직 못 뺀다
        self.assertEqual(b.orderable("sec", T(11, 4, 10, 0)), S + P)            # 사는 데는 쓴다
        lim = self.limit("069500", D(2026, 11, 5))
        self.assertTrue(b.order(T(11, 4, 17, 0), "069500", "buy", "open", qty=20, limit=lim).ok)
        reserved = 20 * lim + RU.fee("069500", 20 * lim)
        self.assertGreater(reserved, S)                                          # 판 돈까지 써서 산다
        self.assertEqual(b.withdrawable("sec", T(11, 4, 17, 0)), max(0, S - reserved))  # 잡힌 돈은 지금 돈에서 뺀다(좁게)
        buy = next(d for k, d in b.sync(T(11, 5, 10, 0)).lines if k == "fill")
        Bv = buy["value"] + buy["fee"]
        self.assertEqual(buy["settle"], "2026-11-09")
        expect = {T(11, 5, 10, 0): min(S, S + P - Bv), T(11, 6, 9, 0): min(S, S + P - Bv),
                  T(11, 6, 9, 1): S + P - Bv, T(11, 9, 10, 0): S + P - Bv}
        for at, want in expect.items():
            self.assertEqual(b.withdrawable("sec", at), want, at)
        self.assertEqual(b.pockets()["sec"], S + P - Bv)
        # 현금 0에서 팔기만: 결제일 전 0, 결제일 09:00 뒤 판 돈 전부
        b = self.broker(units={"069500": 10}, cost={"069500": 800_000})
        self.assertTrue(b.order(T(11, 4, 8, 0), "069500", "sell", "open", qty=10).ok)
        b.sync(T(11, 4, 10, 0))
        self.assertEqual([b.withdrawable("sec", t) for t in (T(11, 5, 12), T(11, 6, 8, 59), T(11, 6, 9, 0, 1))],
                         [0, 0, P])
        self.assertFalse(b.transfer(T(11, 6, 8, 59), "sec", "bank", 1).ok)
        self.assertTrue(b.transfer(T(11, 6, 9, 0, 1), "sec", "bank", P).ok)
        self.check_clean(b)

    def test_t17_past_time_rejected_and_cancel_only_before_fill_time(self):
        b = self.broker(sec=2_000_000)
        lim = self.limit("069500", D(2026, 11, 6))
        r = b.order(T(11, 6, 8, 0), "069500", "buy", "open", qty=10, limit=lim)
        self.assertTrue(r.ok)
        oid = r.order
        r = b.order(T(11, 6, 7, 59), "069500", "buy", "open", qty=1, limit=lim)
        self.assertFalse(r.ok)
        self.assertIn("지난 시각", r.reason)
        self.assertFalse(b.transfer(T(11, 6, 7, 0), "bank", "sec", 1).ok)
        self.assertFalse(b.sync(T(11, 6, 7, 0)).ok)
        self.assertFalse(b.cancel(T(11, 6, 8, 1), "없는-주문").ok)
        self.assertTrue(b.cancel(T(11, 6, 8, 30), oid).ok)
        self.assertEqual((b.pending(), b.orderable("sec", T(11, 6, 8, 30))), ([], 2_000_000))
        self.assertEqual(b.sync(T(11, 6, 10, 0)).lines, [])              # 취소된 주문은 체결되지 않는다
        r = b.order(T(11, 6, 8, 40), "069500", "sell", "open", qty=1)   # 지난 시각(10:00 맞추기 뒤)
        self.assertFalse(r.ok)
        b2 = self.broker(units={"069500": 5}, cost={"069500": 400_000})
        oid = b2.order(T(11, 6, 8, 0), "069500", "sell", "close", qty=5).order
        r = b2.cancel(T(11, 6, 15, 45), oid)                             # 종가 체결 시각(15:30) 뒤, 봉이 끝나기 전
        self.assertFalse(r.ok)
        self.assertIn("체결 시각", r.reason)
        r = b2.cancel(T(11, 6, 16, 0), oid)                              # 끝난 뒤에는 이미 체결됨
        self.assertFalse(r.ok)
        self.assertEqual(b2.holdings(), {})
        self.check_clean(b)
        self.check_clean(b2)


class Honesty(Base):
    def test_t15_late_insert_equals_immediate(self):
        """09:00 체결을 그날 09:01에 진행 중인 봉으로 넣은 상태 = 다음 날 끝난 일봉으로 넣은 상태."""
        def run(sync_at):
            b = self.broker(sec=1_000_000, units={"360750": 10}, cost={"360750": 240_000})
            self.assertTrue(b.order(T(11, 5, 8, 0), "360750", "sell", "open", qty=10).ok)
            self.assertTrue(b.order(T(11, 5, 8, 1), "069500", "buy", "open", qty=5,
                                    limit=self.limit("069500", D(2026, 11, 5))).ok)
            r = b.sync(sync_at)
            fills = [d for k, d in r.lines if k == "fill"]
            self.assertEqual(len(fills), 2)
            return b, fills
        now, f1 = run(T(11, 5, 9, 1))
        late, f2 = run(T(11, 6, 7, 30))
        at = T(11, 6, 8, 0)
        for b in (now, late):
            b.sync(at)
        self.assertEqual(now.pockets(), late.pockets())
        self.assertEqual(now.holdings(), late.holdings())
        self.assertEqual(now.state.cost, late.state.cost)
        for p in ("sec", "bank"):
            self.assertEqual(now.withdrawable(p, at), late.withdrawable(p, at))
            self.assertEqual(now.orderable(p, at), late.orderable(p, at))
        strip = lambda f: {k: v for k, v in f.items() if k not in ("run", "source")}
        self.assertEqual([strip(f) for f in f1], [strip(f) for f in f2])
        self.assertEqual([f["run"] for f in f2], ["2026-11-06", "2026-11-06"])
        self.assertEqual([f["source"]["final"] for f in f1 + f2], [False, False, True, True])
        self.check_clean(now)
        self.check_clean(late)

    def test_t18_poison_future_bars_does_not_change_results(self):
        def scenario(rows):
            b = B.Broker("t", Q.BarQuotes(rows))
            b.start(START, sec=2_000_000, upbit=500_000, units={"069500": 4}, cost={"069500": 320_000})
            b.order(T(11, 5, 8, 0), "069500", "sell", "open", qty=4)
            b.order(T(11, 5, 8, 1), "360750", "buy", "open", qty=10, limit=self.limit("360750", D(2026, 11, 5)))
            b.order(T(11, 5, 10, 0), "005930", "buy", "now", qty=2)
            b.order(T(11, 5, 10, 0), "KRW-BTC", "buy", "now", amount=200_000)
            b.snapshot(T(11, 5, 10, 0))
            return b.lines()
        cut = T(11, 5, 10, 0)
        poisoned = {}
        for a, rs in self.rows.items():
            out = []
            for r in rs:
                if r.date > cut.date():
                    out.append(Row(r.date, r.open * 3, r.high * 3, r.low * 3, r.close * 3, r.volume))
                elif C.final_dt(C.ASSETS[a], r.date) > cut:     # 아직 안 끝난 오늘 봉(코인 포함): 시가만 보인다
                    out.append(Row(r.date, r.open, r.high * 2, r.low / 2, r.close * 2, r.volume))
                else:
                    out.append(r)
            poisoned[a] = out
        self.assertEqual(scenario(self.rows), scenario(poisoned))

    def test_bar_quotes_are_honest_about_time(self):
        q, day = self.q, D(2026, 11, 6)
        self.assertIsNone(q.day("069500", day, T(11, 6, 8, 59)))
        d = q.day("069500", day, T(11, 6, 9, 0))
        self.assertEqual((d.open, d.high, d.low, d.close, d.final), (bar(self.rows, "069500", day).open, None, None, None, False))
        self.assertTrue(q.day("069500", day, T(11, 6, 16, 0)).final)
        self.assertIsNone(q.day("069500", D(2026, 11, 9), T(11, 6, 16, 0)))
        n = q.now("069500", T(11, 6, 8, 0))
        self.assertEqual((n.price, n.open), (bar(self.rows, "069500", D(2026, 11, 5)).close, False))
        n = q.now("KRW-BTC", T(11, 6, 8, 0))                                # 09:00 전은 어제 시작한 봉
        self.assertEqual(n.price, bar(self.rows, "KRW-BTC", D(2026, 11, 5)).open)
        self.assertEqual(q.known_days(T(11, 6, 8, 0)), frozenset(r.date for r in self.rows["069500"] if r.date <= D(2026, 11, 5)))


class Check(Base):
    def lines_with_fill(self):
        b = self.broker(sec=2_000_000, units={"069500": 5}, cost={"069500": 400_000})
        b.order(T(11, 5, 8, 0), "069500", "sell", "open", qty=5)
        b.order(T(11, 5, 10, 0), "360750", "buy", "now", qty=4)
        b.sync(T(11, 6, 8, 0))
        b.snapshot(T(11, 6, 8, 0))
        return b, b.lines()

    def test_t16_check_catches_forged_price_negative_pocket_adjust_and_now_range(self):
        b, lines = self.lines_with_fill()
        self.assertEqual(B.check(lines, self.rows)[0], [])
        # 체결 줄의 가격을 손으로 바꿈
        forged = copy.deepcopy(lines)
        forged["fill"][0]["price"] += 5
        errs = B.check(forged, self.rows)[0]
        self.assertTrue(any("금액" in e or "체결가" in e for e in errs), errs)
        # 주머니가 음수가 되는 줄을 끼움
        forged = copy.deepcopy(lines)
        forged["broker"].append({"id": "x", "run": "2026-11-06", "account": "t", "at": "2026-11-06T08:30:00+09:00",
                                 "type": "transfer", "data": {"src": "bank", "dst": "sec", "amount": 5_000_000, "fee": 0},
                                 "note": "", "seq": 999})
        errs = B.check(forged, self.rows)[0]
        self.assertTrue(any("음수" in e for e in errs), errs)
        self.assertTrue(any("출금 가능" in e for e in errs), errs)
        # account 줄이 다시 센 값과 다름
        forged = copy.deepcopy(lines)
        forged["account"][0]["pockets"]["sec"] += 1
        self.assertTrue(any("주머니" in e for e in B.check(forged, self.rows)[0]))
        # 지금 체결가가 그날 저가–고가 ± 한 칸 밖
        now_fill = next(f for f in lines["fill"] if f["price_kind"] == "now")
        d = D.fromisoformat(now_fill["fill_date"])
        rows = replace_bar(self.rows, "360750", d, high=now_fill["price"] - 10)
        self.assertTrue(any("저가–고가" in e for e in B.check(lines, rows)[0]))

    def test_t16b_in_progress_open_that_differs_makes_an_adjust_line_and_a_warning(self):
        class Drift(Q.BarQuotes):
            """진행 중인 봉의 시가가 끝난 봉과 5원 다르게 온다."""
            def day(self, asset, date, at):
                d = super().day(asset, date, at)
                if d is not None and not d.final and asset == "069500":
                    return Q.Day(d.asset, d.date, d.open + 5, None, None, None, False, "drift")
                return d

        b = self.broker(q=Drift(self.rows), sec=1_000_000, units={"069500": 5}, cost={"069500": 400_000})
        b.order(T(11, 5, 8, 0), "069500", "sell", "open", qty=5)
        fill = next(d for k, d in b.sync(T(11, 5, 9, 1)).lines if k == "fill")
        o = bar(self.rows, "069500", D(2026, 11, 5)).open
        self.assertEqual(fill["price"], o + 5)
        sec_before = b.pockets()["sec"]
        r = b.sync(T(11, 6, 8, 0))
        adj = [d for k, d in r.lines if k == "broker" and d["type"] == "adjust"]
        self.assertEqual(len(adj), 1)
        self.assertEqual((adj[0]["data"]["price_was"], adj[0]["data"]["price_final"]), (o + 5, o))
        self.assertEqual(b.pockets()["sec"] - sec_before, adj[0]["data"]["cash"])
        self.assertLess(adj[0]["data"]["cash"], 0)
        self.assertTrue(r.warnings)
        self.assertEqual(b.sync(T(11, 6, 8, 1)).lines, [])                # 한 번만
        lines = b.lines()
        self.assertEqual(B.check(lines, self.rows)[0], [])
        no_adjust = copy.deepcopy(lines)
        no_adjust["broker"] = [d for d in no_adjust["broker"] if d["type"] != "adjust"]
        self.assertTrue(any("끝난 일봉" in e for e in B.check(no_adjust, self.rows)[0]))


class Records(Base):
    def test_t20_new_kinds_pass_records_check_and_old_lines_still_pass(self):
        b, lines = Check.lines_with_fill(self)
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "records"
            self.assertGreater(b.write(folder), 0)
            self.assertEqual(R.check_folder(folder), [])
            self.assertEqual(b.write(folder), 0)                          # 같은 줄을 두 번 쓰지 않는다
            again = B.Broker.load(folder, "t", self.q)
            self.assertEqual((again.problems, again.pockets(), again.holdings()), ([], b.pockets(), b.holdings()))
            # records check가 증권사 검산을 부른다: 시세 파일이 있으면 체결가도
            prices = Path(d) / "data" / "prices"
            prices.mkdir(parents=True)
            from market import prices as PR
            for a in ("069500", "360750"):
                PR.append(a, list(self.rows[a]), prices)
            self.assertEqual(R.check(Path(d), [folder]), [])
            p = folder / "fill.jsonl"
            p.write_text(p.read_text(encoding="utf-8").replace('"price": ', '"price": 1', 1), encoding="utf-8")
            self.assertTrue(R.check(Path(d), [folder]))
        self.assertEqual(R.check_folder(C.RECORDS), [])                  # 지금 기록(옛 줄)도 그대로 통과
        base = {"id": "b", "run": "2026-11-10", "account": "t", "at": "2026-11-10T08:00:00+09:00", "type": "start",
                "data": {}, "note": "", "seq": 1}
        self.assertEqual(R.line_errors("broker", {"kind": "broker", **base}, "x"), [])
        self.assertTrue(R.line_errors("broker", {"kind": "broker", **base, "type": "gift"}, "x"))
        self.assertTrue(R.line_errors("broker", {"kind": "broker", **base, "at": "2026-11-10T08:00:00"}, "x"))
        order = next(d for k, d in B.mine(lines) if k == "order")
        self.assertEqual(R.line_errors("order", {"kind": "order", **order}, "x"), [])
        self.assertTrue(R.line_errors("order", {"kind": "order", **order, "fill_rule": "open", "order_kind": "now"}, "x"))
        acc = next(d for k, d in B.mine(lines) if k == "account")
        self.assertEqual(R.line_errors("account", {"kind": "account", **acc}, "x"), [])
        self.assertTrue(R.line_errors("account", {"kind": "account", **acc, "cash": acc["cash"] + 1}, "x"))

    def test_only_broker_writes_broker_lines(self):
        """증권사 사건 줄(broker 종류)을 만드는 코드는 broker.py뿐(14절). 2회차에서 옛 장부를 떼면 order·fill로 넓힌다."""
        for p in sorted((ROOT / "market").glob("*.py")):
            if p.name in ("broker.py", "records.py"):
                continue
            text = p.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"""write\([^)]*["']broker["']""", p.name)
            self.assertNotIn('"type": "reject"', text, p.name)


if __name__ == "__main__":
    unittest.main()
