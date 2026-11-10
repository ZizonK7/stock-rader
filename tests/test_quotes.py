"""실제 가격 받기(market/quotes.py): 예시 응답 → 값 바꾸기, 실패하면 None, 일봉 가짜 공급자가 시각에 정직한지 (1회차 시험 목록 21).

예시 응답은 2026-11-10 장중 시험에서 본 칸 이름(지시서 '실제 응답에서 본 것')과 업비트 공개 문서의 칸으로 만든 것이다 — 값은
지어낸 것이고 네트워크를 쓰지 않는다. 네이버 지금 가격의 겹 구조·시각 칸 이름과 네이버 분봉 칸 이름은 확인 못 해서 여러 꼴을 시험한다."""
import datetime as dt
import json
import unittest
from unittest import mock

from market import config as C
from market import quotes as Q
from market.prices import Row

D = dt.date


def K(*a):
    return dt.datetime(*a, tzinfo=C.KST)


AT = K(2026, 11, 10, 10, 7, 10)
TICKER = [{"market": "KRW-BTC", "trade_price": 152_000_000.0, "opening_price": 151_000_000.0,
           "trade_timestamp": 1794272827000, "timestamp": 1794272829000}]
BOOK = [{"market": "KRW-BTC", "timestamp": 1794272829000, "total_ask_size": 1.2, "total_bid_size": 0.9,
         "orderbook_units": [{"ask_price": 152_001_000.0, "bid_price": 152_000_000.0, "ask_size": 0.5, "bid_size": 0.3},
                             {"ask_price": 152_002_000.0, "bid_price": 151_999_000.0, "ask_size": 0.7, "bid_size": 0.6}]}]
DAYS = [{"market": "KRW-BTC", "candle_date_time_utc": "2026-11-10T00:00:00", "candle_date_time_kst": "2026-11-10T09:00:00",
         "opening_price": 151_000_000.0, "high_price": 152_500_000.0, "low_price": 150_800_000.0,
         "trade_price": 152_000_000.0, "candle_acc_trade_volume": 1234.5},
        {"market": "KRW-BTC", "candle_date_time_utc": "2026-11-09T00:00:00", "candle_date_time_kst": "2026-11-09T09:00:00",
         "opening_price": 150_000_000.0, "high_price": 151_500_000.0, "low_price": 149_000_000.0,
         "trade_price": 151_000_000.0, "candle_acc_trade_volume": 2345.6}]
MINUTES = [{"market": "KRW-BTC", "candle_date_time_kst": "2026-11-10T09:00:00", "opening_price": 151_000_000.0,
            "high_price": 151_100_000.0, "low_price": 150_900_000.0, "trade_price": 151_050_000.0}]
FCHART = ('<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata symbol="069500" name="KODEX 200" count="3" '
          'timeframe="day" precision="0" origintime="20021014">'
          '<item data="20261106|80000|80500|79500|80050|1000" />'
          '<item data="20261109|80100|80600|79900|80400|1100" />'
          '<item data="20261110|82400|82600|82300|82495|500" /></chartdata></protocol>')
NOW = {"pollingInterval": 7000, "datas": [{"itemCode": "069500", "stockName": "KODEX 200", "closePrice": "82,495",
                                            "openPrice": "82,400", "highPrice": "82,600", "lowPrice": "82,300",
                                            "localTradedAt": "2026-11-10T10:07:05+09:00", "marketStatus": "OPEN"}],
       "time": "20261110100710"}
NAVER_MIN = [{"localDateTime": "20261110100700", "currentPrice": 82495, "openPrice": 82490, "highPrice": 82500,
              "lowPrice": 82485, "accumulatedTradingVolume": 1000}]


class Parse(unittest.TestCase):
    def test_upbit_ticker(self):
        n = Q.parse_upbit_ticker(TICKER)
        self.assertEqual((n.asset, n.price, n.open), ("KRW-BTC", 152_000_000.0, True))
        self.assertEqual(n.time, K(2026, 11, 10, 10, 7, 7))
        self.assertIsNone(Q.parse_upbit_ticker([{"trade_price": 1.0}]))      # 시각이 없으면 쓰지 않는다
        self.assertIsNone(Q.parse_upbit_ticker([]))

    def test_upbit_orderbook(self):
        b = Q.parse_upbit_book(BOOK)
        self.assertEqual(b.asks, ((152_001_000.0, 0.5), (152_002_000.0, 0.7)))
        self.assertEqual(b.bids, ((152_000_000.0, 0.3), (151_999_000.0, 0.6)))   # 높은 값부터
        self.assertEqual(b.time, K(2026, 11, 10, 10, 7, 9))
        self.assertIsNone(Q.parse_upbit_book([{"timestamp": 1}]))

    def test_upbit_days_marks_the_running_bar(self):
        days = Q.parse_upbit_days(DAYS, AT)
        self.assertEqual([(d.date, d.final) for d in days], [(D(2026, 11, 9), True), (D(2026, 11, 10), False)])
        self.assertEqual(days[1].open, 151_000_000.0)
        early = Q.parse_upbit_days(DAYS, K(2026, 11, 10, 8, 0))             # 09:00 전에는 오늘 봉을 안 준다
        self.assertEqual([d.date for d in early], [D(2026, 11, 9)])
        self.assertFalse(early[0].final)                                     # 11-09 봉은 11-10 09:00에 끝난다

    def test_upbit_minutes(self):
        m = Q.parse_upbit_minutes(MINUTES)
        self.assertEqual((m[0].time, m[0].high, m[0].low), (K(2026, 11, 10, 9, 0), 151_100_000.0, 150_900_000.0))

    def test_naver_days_last_row_is_running(self):
        days = Q.parse_naver_days(FCHART, "069500", AT)
        self.assertEqual([(d.date, d.final) for d in days],
                         [(D(2026, 11, 6), True), (D(2026, 11, 9), True), (D(2026, 11, 10), False)])
        self.assertEqual(days[-1].open, 82_400.0)
        self.assertEqual([d.date for d in Q.parse_naver_days(FCHART, "069500", K(2026, 11, 10, 8, 0))],
                         [D(2026, 11, 6), D(2026, 11, 9)])

    def test_naver_now_finds_the_price_in_unknown_nesting(self):
        n = Q.parse_naver_now(NOW, "069500")
        self.assertEqual((n.price, n.time, n.open), (82_495.0, K(2026, 11, 10, 10, 7, 5), True))
        flat = dict(NOW["datas"][0])
        del flat["localTradedAt"]
        n = Q.parse_naver_now({"result": {"items": [flat]}, "time": "20261110100710"}, "069500")
        self.assertEqual(n.time, K(2026, 11, 10, 10, 7, 10))                # 겉의 time
        self.assertIsNone(Q.parse_naver_now({"datas": [flat]}, "069500"))  # 시각이 없으면 None
        closed = json.loads(json.dumps(NOW).replace('"OPEN"', '"CLOSE"'))
        self.assertFalse(Q.parse_naver_now(closed, "069500").open)
        self.assertIsNone(Q.parse_naver_now({"error": "x"}, "069500"))

    def test_naver_minutes_dict_or_list_rows(self):
        m = Q.parse_naver_minutes(NAVER_MIN, "069500")
        self.assertEqual((m[0].time, m[0].high, m[0].low), (K(2026, 11, 10, 10, 7), 82_500.0, 82_485.0))
        m = Q.parse_naver_minutes([["202611101007", 82490, 82500, 82485, 82495, 1000]], "069500")
        self.assertEqual((m[0].time, m[0].high), (K(2026, 11, 10, 10, 7), 82_500.0))
        self.assertEqual(Q.parse_naver_minutes("x", "069500"), [])


class Net(unittest.TestCase):
    """실제 주소 대신 prices._get을 바꿔 끼워 받는 길 전체를 본다(네트워크 없음)."""

    @staticmethod
    def fake(url, headers=None):
        if "ticker" in url:
            return json.dumps(TICKER).encode()
        if "orderbook" in url:
            return json.dumps(BOOK).encode()
        if "candles/days" in url:
            return json.dumps(DAYS).encode()
        if "candles/minutes" in url:
            return json.dumps(MINUTES).encode()
        if "fchart" in url:
            return FCHART.encode("euc-kr")
        if "polling" in url:
            return json.dumps(NOW).encode()
        if "minute" in url:
            return json.dumps(NAVER_MIN).encode()
        raise AssertionError(url)

    def test_net_quotes_with_fake_responses(self):
        q = Q.NetQuotes(clock=lambda: AT)
        with mock.patch("market.prices._get", side_effect=self.fake) as got:
            self.assertEqual(q.now("KRW-BTC", AT).price, 152_000_000.0)
            self.assertEqual(q.now("069500", AT).price, 82_495.0)
            self.assertEqual(q.book("KRW-BTC", AT).asks[0], (152_001_000.0, 0.5))
            self.assertIsNone(q.book("069500", AT))                     # 네이버 호가는 쓰지 않는다
            self.assertEqual(q.day("069500", D(2026, 11, 10), AT).open, 82_400.0)
            self.assertTrue(q.day("KRW-BTC", D(2026, 11, 9), AT).final)
            self.assertEqual(q.minute("KRW-BTC", K(2026, 11, 10, 9, 0, 30)).high, 151_100_000.0)
            self.assertEqual(q.minute("069500", K(2026, 11, 10, 10, 7, 30)).low, 82_485.0)
            n = got.call_count
            q.day("069500", D(2026, 11, 9), AT)                          # 같은 at의 같은 주소는 한 번만
            self.assertEqual(got.call_count, n)
        for u in [c.args[0] for c in got.call_args_list]:
            self.assertTrue(u.startswith("https://"), u)
        self.assertTrue(any("to=2026-11-10+00%3A01%3A00" in c.args[0] for c in got.call_args_list))   # to 미만 → 09:00 봉

    def test_failures_give_none_and_now_is_live_only(self):
        q = Q.NetQuotes(clock=lambda: AT)
        with mock.patch("market.prices._get", side_effect=OSError("blocked")):
            self.assertIsNone(q.now("069500", AT))
            self.assertIsNone(q.book("KRW-BTC", AT))
            self.assertIsNone(q.day("069500", D(2026, 11, 10), AT))
        with mock.patch("market.prices._get", side_effect=self.fake) as got:
            past = AT - dt.timedelta(days=1)                             # 지난 시각의 '지금 값'은 주지 않는다
            self.assertIsNone(q.now("KRW-BTC", past))
            self.assertIsNone(q.book("KRW-BTC", past))
            self.assertEqual(got.call_count, 0)


class Bars(unittest.TestCase):
    ROWS = {"069500": [Row(D(2026, 11, 9), 80_000, 80_500, 79_500, 80_050, 1.0),
                       Row(D(2026, 11, 10), 80_100, 80_900, 79_900, 80_700, 1.0)],
            "KRW-BTC": [Row(D(2026, 11, 9), 1.0e8, 1.02e8, 0.99e8, 1.01e8, 1.0),
                        Row(D(2026, 11, 10), 1.01e8, 1.03e8, 1.00e8, 1.02e8, 1.0)]}

    def test_t21_bar_quotes_are_honest_about_time(self):
        q = Q.BarQuotes(self.ROWS, book_sizes=(0.5, 1.0))
        self.assertIsNone(q.day("069500", D(2026, 11, 10), K(2026, 11, 10, 8, 59, 59)))   # 09:00 전 오늘 봉 없음
        d = q.day("069500", D(2026, 11, 10), K(2026, 11, 10, 9, 0))
        self.assertEqual((d.open, d.high, d.low, d.close, d.final), (80_100, None, None, None, False))   # 시가만
        d = q.day("069500", D(2026, 11, 10), K(2026, 11, 10, 16, 0))
        self.assertEqual((d.high, d.close, d.final), (80_900, 80_700, True))
        n = q.now("069500", K(2026, 11, 10, 8, 0))                        # 장 전: 어제 종가, 닫힘
        self.assertEqual((n.price, n.open, n.time), (80_050, False, K(2026, 11, 9, 15, 30)))
        n = q.now("069500", K(2026, 11, 10, 11, 0))                       # 장중: 시가(종가는 아직 모름)
        self.assertEqual((n.price, n.open), (80_100, True))
        n = q.now("KRW-BTC", K(2026, 11, 10, 8, 0))                       # 09:00 전: 어제 시작한 봉의 시가
        self.assertEqual(n.price, 1.0e8)
        self.assertIsNone(q.day("KRW-BTC", D(2026, 11, 10), K(2026, 11, 10, 8, 0)))
        self.assertFalse(q.day("KRW-BTC", D(2026, 11, 9), K(2026, 11, 10, 8, 0)).final)
        b = q.book("KRW-BTC", K(2026, 11, 10, 11, 0))
        self.assertEqual(b.asks, ((1.01e8, 0.5), (1.01e8 + 1000, 1.0)))
        self.assertEqual(b.bids, ((1.01e8 - 1000, 0.5), (1.01e8 - 2000, 1.0)))
        self.assertIsNone(q.book("069500", K(2026, 11, 10, 11, 0)))
        self.assertIsNone(q.minute("069500", K(2026, 11, 10, 11, 0)))
        self.assertEqual(q.known_days(K(2026, 11, 10, 11, 0)), frozenset({D(2026, 11, 9)}))
        self.assertEqual(Q.coin_bar_date(K(2026, 11, 10, 8, 59)), D(2026, 11, 9))
        self.assertEqual(Q.coin_bar_date(K(2026, 11, 10, 9, 0)), D(2026, 11, 10))


if __name__ == "__main__":
    unittest.main()
