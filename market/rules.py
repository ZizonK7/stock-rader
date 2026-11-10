"""가상 증권사의 규칙 숫자 — docs/broker-sandbox.md 19절 표가 원본이다.

숫자마다 상태·출처·확인한 날을 같이 둔다(설계 P5). 상태 낱말:
  확인 = 공식 원문을 열어 봄 / 두 곳 일치 = 공식 원문은 못 열고 서로 다른 곳 둘 이상이 같은 값 /
  확인 전 = 검색 요약뿐이거나 못 엶(더 좁게·더 비싸게 둔다) / 가정 = 증권사마다 다른 값을 정해 둠 /
  설계 결정 = 이 프로그램이 정한 값 / 확인 중 = 조사가 진행 중(표의 값을 쓰고 상태를 그대로 적는다 — 메인이 조사 뒤 고친다).
출처나 확인 날짜가 빠진 규칙은 시험(tests/test_broker.py)이 막는다. 19절 표에 없는 값을 더하면 표에도 더한다.

함수: 호가 단위(tick·step·floor_tick·ceil_tick), 가격 제한 폭(price_limits), 영업일(is_business_day·next_business_day·
add_business_days·calendar_warnings)과 결제일(settle_date), 출금 가능 시각(settled_by), 수수료·세금(fee·sell_tax),
장 시작 사기의 기본 한도(default_open_limit).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Iterable

from . import config as C

STATUSES = ("확인", "두 곳 일치", "확인 전", "가정", "설계 결정", "확인 중")
CHECKED = "2026-11-10"


@dataclass(frozen=True)
class Rule:
    id: str
    value: Any
    status: str
    source: str     # 근거(글) — 어디서 봤나, 왜 이 값인가
    checked: str    # 확인한 날 YYYY-MM-DD
    note: str = ""


def _r(id: str, value: Any, status: str, source: str, checked: str = CHECKED, note: str = "") -> Rule:
    return Rule(id, value, status, source, checked, note)


T = dt.time
RULES: dict[str, Rule] = {r.id: r for r in (
    _r("krx_hours", {"open": T(9, 0), "close": T(15, 30), "open_auction": T(8, 30), "close_auction": T(15, 20)},
       "확인 중", "초안 T2(한국거래소 정규장 09:00–15:30, 장 시작 동시호가 08:30–09:00, 장 마감 동시호가 15:20–15:30). "
       "조사 p308 진행 중", note="애프터마켓(2026-09-14~)·프리마켓은 주문 종류로 열지 않는다"),
    _r("krx_tick_stock", ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500), (None, 1_000)),
       "확인 중", "19절 표. 2026-11-10 장중 값(200,000·200,500)과 맞음. 조사 p308 진행 중",
       note="(이 값 미만, 단위) 순서. None = 그 이상"),
    _r("krx_tick_etf", ((2_000, 1), (None, 5)), "확인 중",
       "19절 표. 2026-11-10 장중 값(82,495·25,165)과 맞음. 조사 p308 진행 중"),
    _r("krx_price_limit", 0.30, "확인 중", "초안 U2(전 거래일 종가 ±30%). 조사 p308 진행 중",
       note="위 끝은 호가 단위로 내림, 아래 끝은 올림(폭 안쪽으로)"),
    _r("krx_settle_days", 2, "확인 중", "초안 S1(체결 + 2영업일, 여러 증권사 안내가 같음). 조사 p308 진행 중"),
    _r("krx_withdraw_time", T(9, 0), "확인 중", "초안 S2 — 결제일 09:00 '뒤'부터 뺄 수 있다고 둔다(P5: 더 좁게). 조사 p308 진행 중"),
    _r("krx_rebuy", True, "확인 중", "초안 M1 — 판 돈은 결제 전이어도 같은 증권 계좌의 주문 가능 금액에 들어간다(키움 도움말 등). "
       "조사 p308 진행 중"),
    _r("krx_fee", 0.00015, "가정", "지금 장부 그대로(config의 ETF·종목 수수료 0.015%). 증권사마다 다르다"),
    _r("krx_holidays", (dt.date(2026, 12, 25), dt.date(2026, 12, 31), dt.date(2027, 1, 1)), "확인 전",
       "19절 표(나머지는 조사 뒤 채움). 주말과 이 표만 영업일에서 뺀다. 지난 날은 시세 파일의 봉이 있던 날로 센다"),
    _r("stock_sell_tax", 0.0020, "두 곳 일치", "2026-01-01 매도분부터 코스피 0.05% + 농어촌특별세 0.15%, 코스닥 0.20% — "
       "기사 여러 곳(세계일보·세정일보 등). 법령 원문은 못 엶"),
    _r("etf_sell_tax", 0.0, "두 곳 일치", "투자자교육협의회·증권사·세무 자료 셋이 같음(ETF는 증권거래세 없음)"),
    _r("foreign_etf_gain_tax", {"rate": 0.154, "assets": ("360750",)}, "두 곳 일치",
       "해외 지수 국내 상장 ETF 매매 차익은 배당소득(15.4%) — KB증권·삼성자산운용·KB자산운용 안내. 실제는 '이익'과 "
       "'과세표준 기준가격이 오른 몫' 중 작은 쪽인데 그 자료가 없어 이익 전부에 뗀다(실제보다 같거나 많다, P5). 손실이면 0",
       note="떼는 때(팔 때)는 확인 전. 이익 = 판 금액 − 평균 산 값 × 수량(수수료는 빼지 않는다 — 세금이 같거나 많은 쪽)"),
    _r("domestic_gain_tax", 0.0, "두 곳 일치", "금융투자소득세 폐지 — 국내 주식형 ETF·개별 종목의 판 이익은 세금 없음(대주주 아님)"),
    _r("coin_gain_tax", {"from": dt.date(2027, 1, 1), "at_sale": 0.0}, "두 곳 일치",
       "가상자산 이익 세금은 2027-01-01부터(경향신문·블록미디어). 시행돼도 해마다 계산 — 팔 때 떼지 않는다"),
    _r("upbit_min_order", 5_000, "확인", "업비트 개발자 센터 '원화 마켓 정보'(최소 주문 금액 5,000원)"),
    _r("upbit_tick", ((2_000_000, 1_000),), "확인",
       "업비트 개발자 센터 '원화 마켓 정보'(2025-07-31 바뀐 표): 200만 원 이상 1,000원",
       note="(이 값 이상, 단위). 200만 원 아래 구간은 공식 표를 아직 옮기지 않았다 — 그 가격대는 단위를 모른다(None)"),
    _r("upbit_qty_unit", 100_000_000, "두 곳 일치", "업비트 공식 FAQ 예시(소수 8자리) — 0.00000001개 단위"),
    _r("upbit_fee", 0.0005, "두 곳 일치", "공식 문서는 예시값(0.0005)뿐, 다른 곳 셋이 같은 0.05%"),
    _r("upbit_day_start", T(9, 0), "확인", "업비트 개발자 센터 '일 캔들' — 09:00 KST(UTC 00:00) 시작, 시가 = 그 뒤 첫 체결가"),
    _r("upbit_market_order", {"buy": "amount", "sell": "qty"}, "확인", "업비트 개발자 센터 '주문하기' — 시장가 사기는 금액, 팔기는 수량"),
    _r("upbit_krw_withdraw_fee", 1_000, "확인 전", "검색 요약뿐(업비트 고객센터가 막힘). 건당, 옮기는 금액과 따로 업비트 원화에서 뗀다고 둔다(P5: 더 비싼 쪽)"),
    _r("upbit_deposit_hold", "coin_withdraw_only", "확인", "DAXA 보도자료(2025-06-24): 원화를 넣은 뒤 막히는 것은 코인 출금뿐"
       "(첫 입금 72시간, 그 뒤 24시간). 이 계좌는 코인을 밖으로 빼지 않으니 걸리지 않는다"),
    _r("bank_transfer", {"instant": True, "fee": 0}, "확인 전", "은행 ↔ 증권 계좌, 은행 → 업비트: 바로, 수수료 0. 은행 공식 안내를 못 엶"),
    _r("open_limit_slack", 0.05, "설계 결정", "장 시작 사기의 기본 한도 = 어제 종가 × 1.05를 호가 단위로 내림. "
       "시가가 5% 넘게 뛰어 열리면 그날 장중에 걸려 있다(15절)"),
    _r("now_quote_max_age", 60, "설계 결정", "지금 주문에 쓸 가격의 나이(초). 네이버 지금 가격은 7초 주기"),
    _r("quote_clock_skew", 5, "설계 결정", "지금 가격의 시각이 주문 시각보다 이만큼(초)까지 늦어도 받는다 — 주문 시각을 정한 뒤 "
       "가격을 받으므로 그사이 체결된 값일 수 있다. 그보다 늦으면 시계가 어긋난 것으로 보고 거절(19절 표 밖, 1회차 구현에서 더함)"),
)}


def value(rule_id: str) -> Any:
    return RULES[rule_id].value


# ---------- 시간 ----------
def hours() -> dict[str, dt.time]:
    return value("krx_hours")


def is_business_day(day: dt.date, bar_days: Iterable[dt.date] | None = None) -> bool:
    """한국 거래소 영업일. 넘겨받은 봉 날짜들이 덮는 날(그 가장 늦은 날까지)은 봉이 있던 날만, 그 뒤는 주말·휴장일 표로."""
    if bar_days:
        days = bar_days if isinstance(bar_days, (set, frozenset)) else set(bar_days)
        if day <= max(days):
            return day in days
    return day.weekday() < 5 and day not in value("krx_holidays")


def next_business_day(day: dt.date, bar_days: Iterable[dt.date] | None = None) -> dt.date:
    """day보다 뒤의 첫 영업일."""
    d = day + dt.timedelta(days=1)
    while not is_business_day(d, bar_days):
        d += dt.timedelta(days=1)
    return d


def add_business_days(day: dt.date, n: int, bar_days: Iterable[dt.date] | None = None) -> dt.date:
    d = day
    for _ in range(n):
        d = next_business_day(d, bar_days)
    return d


def settle_date(fill_day: dt.date, bar_days: Iterable[dt.date] | None = None) -> dt.date:
    """증권 계좌 체결의 결제일 = 체결 + krx_settle_days 영업일."""
    return add_business_days(fill_day, value("krx_settle_days"), bar_days)


def settled_by(settle: dt.date, at: dt.datetime) -> bool:
    """결제일 settle의 돈을 시각 at에 뺄 수 있나: 결제일 전날까지는 못 빼고, 결제일에는 krx_withdraw_time '뒤'부터."""
    t = at.astimezone(C.KST)
    return settle < t.date() or (settle == t.date() and t.time() > value("krx_withdraw_time"))


def calendar_warnings(bar_days: Iterable[dt.date], start: dt.date, end: dt.date) -> list[str]:
    """시세 파일(봉이 있던 날)과 휴장일 표가 어긋나는 날: 표로는 영업일인데 봉이 없음 / 표로는 쉬는 날인데 봉이 있음."""
    days = set(bar_days)
    out = []
    d = start
    while d <= end:
        table = d.weekday() < 5 and d not in value("krx_holidays")
        if table and d not in days:
            out.append(f"{d}: 휴장일 표로는 영업일인데 봉이 없음 — 휴장일 표(rules.krx_holidays)에 더할지 확인")
        elif not table and d in days:
            out.append(f"{d}: 휴장일 표로는 쉬는 날인데 봉이 있음 — 표를 확인")
        d += dt.timedelta(days=1)
    return out


# ---------- 호가 단위 ----------
def tick(asset: str, price: float) -> int | None:
    """그 가격대의 호가 단위(원). 모르면 None(업비트 200만 원 아래 — 표를 아직 옮기지 않음)."""
    kind = C.ASSETS[asset].kind
    if kind == "coin":
        for lo, t in value("upbit_tick"):
            if price >= lo:
                return t
        return None
    table = value("krx_tick_etf" if kind == "etf" else "krx_tick_stock")
    for below, t in table:
        if below is None or price < below:
            return t
    return None


def on_tick(asset: str, price: float) -> bool:
    t = tick(asset, price)
    return t is not None and price > 0 and float(price).is_integer() and int(price) % t == 0


def floor_tick(asset: str, price: float) -> int | None:
    t = tick(asset, price)
    return None if t is None else int(math.floor(price / t + 1e-9)) * t


def ceil_tick(asset: str, price: float) -> int | None:
    t = tick(asset, price)
    return None if t is None else int(math.ceil(price / t - 1e-9)) * t


def step(asset: str, price: float, n: int) -> int | None:
    """호가 단위로 n칸 위(+)·아래(−). 아래로 갈 때는 바로 아래 구간의 단위를 쓴다(2,000 − 1칸 = 1,999)."""
    p = int(price)
    for _ in range(abs(n)):
        t = tick(asset, p if n > 0 else p - 1)
        if t is None:
            return None
        p = p + t if n > 0 else p - t
    return p


def price_limits(asset: str, prev_close: float) -> tuple[int, int]:
    """(아래 끝, 위 끝): 전 거래일 종가 ± krx_price_limit, 호가 단위로 폭 안쪽에 맞춤."""
    r = value("krx_price_limit")
    return ceil_tick(asset, prev_close * (1 - r)), floor_tick(asset, prev_close * (1 + r))


def default_open_limit(asset: str, prev_close: float) -> int | None:
    """장 시작 사기의 기본 한도 = 어제 종가 × (1 + open_limit_slack)를 호가 단위로 내림."""
    return floor_tick(asset, prev_close * (1 + value("open_limit_slack")))


# ---------- 비용 ----------
def fee_rate(asset: str) -> float:
    return value("upbit_fee") if C.ASSETS[asset].kind == "coin" else value("krx_fee")


def fee(asset: str, amount: int) -> int:
    """거래 수수료: 금액 × 비율, 원 단위 올림(옛 장부와 같다)."""
    return math.ceil(amount * fee_rate(asset))


def sell_tax(asset: str, amount: int, gain: float) -> int:
    """팔 때 떼는 세금(원, 올림). 종목: 판 금액 × stock_sell_tax. 해외 지수 ETF: 이익 × 15.4%(손실이면 0).
    국내 묶음 ETF: etf_sell_tax(0) + 판 이익 세금 없음. 코인: 팔 때 떼지 않는다."""
    kind = C.ASSETS[asset].kind
    if kind == "coin":
        return 0
    if kind == "stock":
        return math.ceil(amount * value("stock_sell_tax"))
    t = math.ceil(amount * value("etf_sell_tax"))
    f = value("foreign_etf_gain_tax")
    if asset in f["assets"] and gain > 0:
        t += math.ceil(gain * f["rate"])
    return t
