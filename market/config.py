"""대상, 수수료, 기간, 시각, 경로 — 한곳에 모은 고정값.

시각은 모두 한국 시간(KST, UTC+9)이다. 판단 시각은 날짜 D의 07:37.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICES = ROOT / "data" / "prices"
RECORDS = ROOT / "records"
REPLAY = RECORDS / "replay"
KNOWLEDGE = ROOT / "knowledge.toml"

KST = dt.timezone(dt.timedelta(hours=9))
DECISION_TIME = dt.time(7, 37)
START_CASH = 10_000_000  # 원
NET_TIMEOUT = 30  # 초. 모든 네트워크 요청에 건다 (응답 없이 멈추는 일을 막는다)


@dataclass(frozen=True)
class Asset:
    id: str            # 파일 이름과 기록에 쓰는 코드
    name: str          # 사람용 이름 (조각 이름·문장에는 쓰지 않는다)
    kind: str          # "etf" | "coin"
    fee: float         # 매매 수수료 비율
    unit: int          # 수량 최소 단위의 역수: ETF 1주(1), 비트코인 1e-8 (100_000_000)
    horizons: tuple[int, ...]  # 예측 기간 (봉 수: ETF 거래일, 코인 날)
    # 일봉이 '끝난' 시각 = 날짜 + final_days일, final_time. ETF는 15:30 종가 뒤 여유를 둔 16:00, 코인은 다음 날 09:00
    final_days: int
    final_time: dt.time
    # 체결: ETF는 판단일과 같거나 뒤인 첫 거래일 종가(15:30), 코인은 판단일 일봉(09:00 시작)의 시가
    fill_field: str     # "close" | "open"
    fill_time: dt.time


ASSETS: dict[str, Asset] = {
    "069500": Asset("069500", "KODEX 200", "etf", 0.00015, 1, (1, 5, 20), 0, dt.time(16, 0), "close", dt.time(15, 30)),
    "360750": Asset("360750", "TIGER 미국S&P500", "etf", 0.00015, 1, (1, 5, 20), 0, dt.time(16, 0), "close", dt.time(15, 30)),
    "KRW-BTC": Asset("KRW-BTC", "비트코인(업비트)", "coin", 0.0005, 100_000_000, (1, 7, 28), 1, dt.time(9, 0, 30), "open", dt.time(9, 0)),
}
ASSET_IDS = tuple(ASSETS)

# 대상 이름에 쓰이는 낱말. 조각 id·문장에 나오면 audit 오류 (조각은 다른 대상·분야에서 다시 쓴다)
ASSET_WORDS = ("KODEX", "TIGER", "069500", "360750", "KRW-BTC", "BTC", "비트코인", "코인", "S&P", "코스피", "KOSPI",
               "업비트", "나스닥", "ETF")

# 기간 이름: 다음 거래일 / 1주 / 4주 (자리 순서)
HORIZON_LABEL = ("다음", "1주", "4주")

# 숫자를 맞출 때 쓰는 시세의 끝 (재생 기간 2026-07-01~ 은 홀드아웃). 목표 종가 날짜까지 이 날 이하여야 한다
FIT_UNTIL = dt.date(2026, 6, 30)
REPLAY_START = dt.date(2026, 7, 1)
REPLAY_END = dt.date(2026, 10, 19)


def decision_dt(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, DECISION_TIME, KST)


def final_dt(asset: Asset, day: dt.date) -> dt.datetime:
    """날짜 day의 일봉이 끝났다고 보는 시각."""
    return dt.datetime.combine(day + dt.timedelta(days=asset.final_days), asset.final_time, KST)


def fill_dt(asset: Asset, day: dt.date) -> dt.datetime:
    """날짜 day의 체결 가격이 정해지는 시각 (ETF 종가 15:30, 코인 시가 09:00)."""
    return dt.datetime.combine(day, asset.fill_time, KST)


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)
