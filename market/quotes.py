"""실제 가격 받기 — 가상 증권사(market/broker.py)가 체결·평가에 쓰는 값 (docs/broker-sandbox.md 13·15절).

공급자 인터페이스 `Quotes` 하나와 구현 둘:
- `NetQuotes`: 네이버·업비트 실제 주소(키 없음). 모든 요청에 시간 제한 config.NET_TIMEOUT, User-Agent prices.UA.
  실패하면 예외 대신 None — 가격을 못 받은 주문은 증권사가 거절한다(값을 지어내지 않는다, P8-1).
  응답 → 값 바꾸기는 순수 함수(parse_*)로 떼어 두었다(tests/test_quotes.py가 예시 응답으로 시험한다).
- `BarQuotes`: 일봉(prices.Row 묶음)으로 만든 가짜 공급자(시험·흉내 내기용, 네트워크 없음). 시각 at에 정직하다:
  그날 09:00 전에는 그날 봉이 없고, 09:00 뒤에는 시가만 보이며 고가·저가·종가는 봉이 끝난 뒤(config.final_dt)에만 보인다.

값의 모양
- `Now`: 지금 가격·그 가격의 시각·장 상태(열림/닫힘)·출처.
- `Book`: 업비트 호가 — 팔려는 값들(asks, 낮은 값부터)·사려는 값들(bids, 높은 값부터), (값, 수량 코인 개수).
- `Day`: 그날 봉(시가·고가·저가·종가, final = 끝났는가). **끝나지 않은 봉에서 증권사는 시가만 쓴다**
  (NetQuotes는 진행 중인 고가·저가·지금 값도 채워 오지만 끝난 값이 아니다).
- `Minute`: 그 분의 고가·저가(검산용, 없으면 None).
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable

from . import config as C
from . import prices as PR
from . import rules as RU
from .prices import Row


@dataclass(frozen=True)
class Now:
    asset: str
    price: float
    time: dt.datetime     # 그 가격이 나온 시각(체결 시각)
    open: bool            # 장 상태: 열림
    source: str


@dataclass(frozen=True)
class Book:
    asset: str
    asks: tuple[tuple[float, float], ...]   # 팔려는 값들 (값, 수량) — 낮은 값부터
    bids: tuple[tuple[float, float], ...]   # 사려는 값들 (값, 수량) — 높은 값부터
    time: dt.datetime
    source: str


@dataclass(frozen=True)
class Day:
    asset: str
    date: dt.date
    open: float
    high: float | None
    low: float | None
    close: float | None
    final: bool
    source: str


@dataclass(frozen=True)
class Minute:
    asset: str
    time: dt.datetime     # 분의 시작
    high: float
    low: float
    source: str


class Quotes:
    """공급자 인터페이스. 모든 값은 시각 at에 알 수 있는 것만, 모르면 None."""

    def now(self, asset: str, at: dt.datetime) -> Now | None:
        raise NotImplementedError

    def book(self, asset: str, at: dt.datetime) -> Book | None:
        raise NotImplementedError

    def day(self, asset: str, date: dt.date, at: dt.datetime) -> Day | None:
        raise NotImplementedError

    def minute(self, asset: str, at: dt.datetime) -> Minute | None:
        return None

    def known_days(self, at: dt.datetime) -> frozenset[dt.date] | None:
        """시각 at까지 끝난 한국 거래소 봉이 있던 날짜들(영업일 세기용). 모르면 None(휴장일 표만 쓴다)."""
        return None


def coin_bar_date(at: dt.datetime) -> dt.date:
    """시각 at이 속한 업비트 일봉의 날짜(09:00 KST 시작)."""
    t = at.astimezone(C.KST)
    return t.date() if t.time() >= RU.value("upbit_day_start") else t.date() - dt.timedelta(days=1)


def _is_final(asset: str, date: dt.date, at: dt.datetime) -> bool:
    return C.final_dt(C.ASSETS[asset], date) <= at


# ---------- 응답 → 값 (순수 함수) ----------
def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def parse_upbit_ticker(items: Any) -> Now | None:
    """/v1/ticker?markets=KRW-BTC → Now. 칸: trade_price(지금 값), trade_timestamp(ms, 그 체결의 시각). 업비트는 늘 열려 있다."""
    it = items[0] if isinstance(items, list) and items else items
    if not isinstance(it, dict):
        return None
    p, ts = _num(it.get("trade_price")), _num(it.get("trade_timestamp"))
    if p is None or ts is None or p <= 0:
        return None
    return Now(str(it.get("market", "KRW-BTC")), p, dt.datetime.fromtimestamp(ts / 1000, C.KST), True, "upbit:ticker")


def parse_upbit_book(items: Any) -> Book | None:
    """/v1/orderbook?markets=KRW-BTC → Book. 칸: timestamp(ms), orderbook_units[].ask_price/ask_size/bid_price/bid_size."""
    it = items[0] if isinstance(items, list) and items else items
    if not isinstance(it, dict) or not isinstance(it.get("orderbook_units"), list):
        return None
    ts = _num(it.get("timestamp"))
    if ts is None:
        return None
    asks, bids = [], []
    for u in it["orderbook_units"]:
        ap, az, bp, bz = (_num(u.get(k)) for k in ("ask_price", "ask_size", "bid_price", "bid_size"))
        if ap and az and ap > 0 and az > 0:
            asks.append((ap, az))
        if bp and bz and bp > 0 and bz > 0:
            bids.append((bp, bz))
    return Book(str(it.get("market", "KRW-BTC")), tuple(sorted(asks)), tuple(sorted(bids, reverse=True)),
                dt.datetime.fromtimestamp(ts / 1000, C.KST), "upbit:orderbook")


def parse_upbit_days(items: Any, at: dt.datetime, asset: str = "KRW-BTC") -> list[Day]:
    """/v1/candles/days → Day 목록. 첫 줄이 진행 중인 오늘 봉(09:00 KST 시작). 칸은 prices.parse_upbit와 같다."""
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            d = dt.date.fromisoformat(str(it["candle_date_time_kst"])[:10])
        except (KeyError, ValueError):
            continue
        o = _num(it.get("opening_price"))
        if o is None or C.fill_dt(C.ASSETS[asset], d) > at:
            continue
        fin = _is_final(asset, d, at)
        out.append(Day(asset, d, o, _num(it.get("high_price")), _num(it.get("low_price")), _num(it.get("trade_price")),
                       fin, "upbit:candles/days"))
    return sorted(out, key=lambda x: x.date)


def parse_upbit_minutes(items: Any, asset: str = "KRW-BTC") -> list[Minute]:
    """/v1/candles/minutes/1?…&to=<UTC> → Minute 목록(to **미만**의 봉들). 칸: candle_date_time_kst, high_price, low_price."""
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            t = dt.datetime.fromisoformat(str(it["candle_date_time_kst"])).replace(tzinfo=C.KST)
        except (KeyError, ValueError):
            continue
        hi, lo = _num(it.get("high_price")), _num(it.get("low_price"))
        if hi is not None and lo is not None:
            out.append(Minute(asset, t, hi, lo, "upbit:candles/minutes"))
    return sorted(out, key=lambda m: m.time)


def parse_naver_days(text: str, asset: str, at: dt.datetime) -> list[Day]:
    """fchart sise.nhn(timeframe=day) → Day 목록. 마지막 줄이 진행 중인 오늘 봉일 수 있다(시가·고가·저가·지금 값).
    읽기는 prices.parse_naver와 같다. 시각 at에 아직 시작하지 않은 봉(09:00 전의 그날·뒤 날짜)은 버린다."""
    out = []
    for r in PR.parse_naver(text, ""):
        if C.fill_dt(C.ASSETS[asset], r.date, C.OPEN_TIME) > at:
            continue
        out.append(Day(asset, r.date, r.open, r.high, r.low, r.close, _is_final(asset, r.date, at), "naver:fchart"))
    return out


def _find_dict(data: Any, key: str, depth: int = 4) -> dict | None:
    """겹 구조를 모르는 JSON에서 key를 가진 첫 사전(너그럽게 찾기 — 네이버 지금 가격의 겹 구조는 확인 못 함)."""
    if isinstance(data, dict):
        if key in data:
            return data
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        return None
    if depth <= 0:
        return None
    for v in items:
        got = _find_dict(v, key, depth - 1)
        if got is not None:
            return got
    return None


def _naver_time(text: Any) -> dt.datetime | None:
    """네이버 시각 글: ISO(시간대 있거나 없음 — 없으면 KST) 또는 YYYYMMDDHHMM[SS]."""
    if text is None:
        return None
    s = str(text).strip()
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=C.KST)
    except ValueError:
        pass
    if s.isdigit() and len(s) in (12, 14):
        fmt = "%Y%m%d%H%M%S" if len(s) == 14 else "%Y%m%d%H%M"
        return dt.datetime.strptime(s, fmt).replace(tzinfo=C.KST)
    return None


NAVER_TIME_KEYS = ("localTradedAt", "tradedAt", "localDateTime", "time")


def parse_naver_now(data: Any, asset: str) -> Now | None:
    """polling.finance.naver.com/api/realtime/domestic/stock/<코드> → Now.
    확인한 칸: closePrice(지금 값), 시각 칸, 장 상태 OPEN. 겹 구조와 시각 칸 이름은 확인 못 했다 — closePrice가 든 사전을 찾고,
    시각은 그 사전의 NAVER_TIME_KEYS 중 첫 것, 없으면 겉의 time. 시각을 못 찾으면 None(나이를 모르는 값은 쓰지 않는다)."""
    it = _find_dict(data, "closePrice")
    if it is None:
        return None
    p = _num(it.get("closePrice"))
    t = next((x for x in (_naver_time(it.get(k)) for k in NAVER_TIME_KEYS) if x), None)
    if t is None and isinstance(data, dict):
        t = _naver_time(data.get("time"))
    if p is None or p <= 0 or t is None:
        return None
    status = str(it.get("marketStatus", "")).upper()
    return Now(asset, p, t, status == "OPEN", "naver:realtime")


def parse_naver_minutes(data: Any, asset: str) -> list[Minute]:
    """api.stock.naver.com/chart/domestic/item/<코드>/minute → Minute 목록. 칸 이름은 확인 못 했다: 사전이면 시각
    (localDateTime 등)·highPrice·lowPrice, 목록이면 [시각, 시가, 고가, 저가, 종가, 거래량]으로 읽는다."""
    rows = data if isinstance(data, list) else (data.get("priceInfos") or data.get("datas") or []) if isinstance(data, dict) else []
    out = []
    for it in rows:
        if isinstance(it, dict):
            t = next((x for x in (_naver_time(it.get(k)) for k in ("localDateTime", "localDate", "dateTime", "time")) if x), None)
            hi, lo = _num(it.get("highPrice")), _num(it.get("lowPrice"))
        elif isinstance(it, (list, tuple)) and len(it) >= 4:
            t, hi, lo = _naver_time(it[0]), _num(it[2]), _num(it[3])
        else:
            continue
        if t is not None and hi is not None and lo is not None:
            out.append(Minute(asset, t, hi, lo, "naver:minute"))
    return sorted(out, key=lambda m: m.time)


# ---------- 실제 주소 ----------
UPBIT = "https://api.upbit.com/v1"
NAVER_DAY = "https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={n}&requestType=0"
NAVER_NOW = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
NAVER_MIN = "https://api.stock.naver.com/chart/domestic/item/{code}/minute?startDateTime={s}&endDateTime={e}"
# 지금 값은 지금만 받을 수 있다: 주문 시각이 실제 시계와 이만큼 넘게 다르면(흉내 내기에서 NetQuotes를 쓰는 실수) 지금 값을 주지 않는다
LIVE_ONLY = dt.timedelta(minutes=5)


class NetQuotes(Quotes):
    """네이버·업비트 실제 주소. 실패하면 None. 같은 at의 같은 요청은 한 번만 받는다(한 번의 맞추기 안에서)."""

    def __init__(self, clock=C.now_kst):
        self._clock = clock
        self._memo: dict[tuple, Any] = {}

    def _get(self, url: str, at: dt.datetime, text: bool = False) -> Any:
        key = (url, at)
        if key in self._memo:
            return self._memo[key]
        try:
            raw = PR._get(url, {"Accept": "application/json"} if not text else None)
            out = raw.decode("euc-kr", errors="replace") if text else json.loads(raw)
        except Exception:  # 네트워크·모양 오류: 값 없음 → 주문 거절
            out = None
        self._memo[key] = out
        return out

    def _live(self, at: dt.datetime) -> bool:
        return abs(self._clock() - at) <= LIVE_ONLY

    def now(self, asset: str, at: dt.datetime) -> Now | None:
        if not self._live(at):
            return None
        if C.ASSETS[asset].kind == "coin":
            return parse_upbit_ticker(self._get(f"{UPBIT}/ticker?markets={asset}", at))
        return parse_naver_now(self._get(NAVER_NOW.format(code=asset), at), asset)

    def book(self, asset: str, at: dt.datetime) -> Book | None:
        if not self._live(at) or C.ASSETS[asset].kind != "coin":
            return None
        return parse_upbit_book(self._get(f"{UPBIT}/orderbook?markets={asset}", at))

    def day(self, asset: str, date: dt.date, at: dt.datetime) -> Day | None:
        if C.ASSETS[asset].kind == "coin":
            q = {"market": asset, "count": "10"}
            days = parse_upbit_days(self._get(f"{UPBIT}/candles/days?" + urllib.parse.urlencode(q), at), at, asset)
        else:
            text = self._get(NAVER_DAY.format(code=asset, n=10), at, text=True)
            days = parse_naver_days(text, asset, at) if text else []
        return next((d for d in days if d.date == date), None)

    def minute(self, asset: str, at: dt.datetime) -> Minute | None:
        start = at.astimezone(C.KST).replace(second=0, microsecond=0)
        if C.ASSETS[asset].kind == "coin":
            to = (start + dt.timedelta(minutes=1)).astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            q = {"market": asset, "count": "1", "to": to}
            got = parse_upbit_minutes(self._get(f"{UPBIT}/candles/minutes/1?" + urllib.parse.urlencode(q), at), asset)
        else:
            s = start.strftime("%Y%m%d%H%M")
            got = parse_naver_minutes(self._get(NAVER_MIN.format(code=asset, s=s, e=s), at), asset)
        return next((m for m in got if m.time == start), None)


# ---------- 가짜 공급자 (일봉) ----------
class BarQuotes(Quotes):
    """일봉으로 만든 가짜 공급자 (네트워크 없음). 시각 at 기준으로 정직하다:
    - day: 그날 시가 시각(09:00) 전에는 None, 그 뒤 봉이 끝나기 전(config.final_dt)에는 시가만(final False), 끝난 뒤 전부.
      시가가 0인 봉(거래 정지)도 그대로 준다 — 증권사가 체결하지 않는다.
    - now: 한국 거래소는 장중(09:00–15:30, 그날 봉이 있고 시가 > 0)이면 그날 시가를 '지금 값'으로(시각 = at, 장 열림),
      그 밖에는 마지막으로 끝난 봉의 종가(시각 = 그 봉의 15:30, 장 닫힘) — 오늘 봉이 끝나기 전 15:30 뒤면 오늘 시가(시각 09:00).
      코인은 at이 속한 봉의 시가(시각 = at, 늘 열림).
    - book: 코인만, 지금 값 둘레로 book_sizes 단계(단계마다 호가 단위 한 칸, 수량은 코인 개수).
    - minute: 없음(None).
    stale: 지금 값의 시각을 이만큼 앞당긴다(늦은 값 시험용)."""

    def __init__(self, rows: dict[str, Iterable[Row]], book_sizes: tuple[float, ...] = (1.0, 1.0),
                 stale: dt.timedelta = dt.timedelta(0)):
        self.rows = {a: tuple(sorted(rs, key=lambda r: r.date)) for a, rs in rows.items()}
        self.book_sizes = tuple(book_sizes)
        self.stale = stale

    def _row(self, asset: str, date: dt.date) -> Row | None:
        return next((r for r in self.rows.get(asset, ()) if r.date == date), None)

    def day(self, asset: str, date: dt.date, at: dt.datetime) -> Day | None:
        r = self._row(asset, date)
        if r is None or C.fill_dt(C.ASSETS[asset], date, C.OPEN_TIME) > at:
            return None
        if not _is_final(asset, date, at):
            return Day(asset, date, r.open, None, None, None, False, "bars")
        return Day(asset, date, r.open, r.high, r.low, r.close, True, "bars")

    def now(self, asset: str, at: dt.datetime) -> Now | None:
        t = at.astimezone(C.KST)
        if C.ASSETS[asset].kind == "coin":
            r = self._row(asset, coin_bar_date(t))
            return None if r is None or r.open <= 0 else Now(asset, r.open, t - self.stale, True, "bars:open")
        h = RU.hours()
        r = self._row(asset, t.date())
        if r is not None and r.open > 0 and h["open"] <= t.time() < h["close"]:
            return Now(asset, r.open, t - self.stale, True, "bars:open")
        if r is not None and r.open > 0 and t.time() >= h["close"] and not _is_final(asset, r.date, t):
            return Now(asset, r.open, dt.datetime.combine(r.date, h["open"], C.KST), False, "bars:open")
        done = [x for x in self.rows.get(asset, ()) if _is_final(asset, x.date, t) and x.close > 0]
        if not done:
            return None
        last = done[-1]
        return Now(asset, last.close, dt.datetime.combine(last.date, h["close"], C.KST), False, "bars:close")

    def book(self, asset: str, at: dt.datetime) -> Book | None:
        if C.ASSETS[asset].kind != "coin":
            return None
        n = self.now(asset, at)
        if n is None:
            return None
        t = RU.tick(asset, n.price)
        if t is None:
            return None
        ask0 = RU.ceil_tick(asset, n.price)
        bid0 = RU.floor_tick(asset, n.price)
        if bid0 == ask0:
            bid0 -= t
        asks = tuple((float(ask0 + i * t), s) for i, s in enumerate(self.book_sizes))
        bids = tuple((float(bid0 - i * t), s) for i, s in enumerate(self.book_sizes))
        return Book(asset, asks, bids, n.time, "bars:book")

    def known_days(self, at: dt.datetime) -> frozenset[dt.date] | None:
        ex = [a for a in self.rows if C.on_exchange(a)]
        if not ex:
            return None
        return frozenset(r.date for r in self.rows[ex[0]] if _is_final(ex[0], r.date, at))
