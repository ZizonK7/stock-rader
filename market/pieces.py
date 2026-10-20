"""가격으로 대 보는 조각 (계산 조각의 조건 = 상태 노드).

조각마다 종가 목록만 보고 '예/아니오/모름'을 낸다. 대상 이름을 모른다(다른 대상·분야에서 다시 쓴다).
'봉' = 그 대상의 일봉 하나 (ETF는 거래일, 코인은 날).
기준 숫자(20·60·200·250봉, 1·2 표준편차)는 조각의 '모양'이다: 문헌·통념에서 흔히 쓰는 값을 미리 정했고,
기록에서 고르지 않았다. 효과의 크기(얼마나 늘리나)만 fit.py가 기록에서 맞춘다.

입력은 History(어떤 시각에 알려진 줄만)나 그 종가 목록이다. 뒤의 줄을 받지 않으므로 미래를 볼 수 없다.
"""
from __future__ import annotations

import math
from statistics import fmean, pstdev

NEED = 251  # 가장 긴 창(250봉 수익률 = 251개 종가)

# 상태 id → (질문, 계산 설명). knowledge.toml의 [[state]]와 같은 id
STATES = {
    "S_trend_20": "최근 20봉 수익률이 양수다",
    "S_trend_60": "최근 60봉 수익률이 양수다",
    "S_above_ma200": "종가가 최근 200봉 평균보다 위다",
    "S_drop_5": "최근 5봉에 평소 한 주 움직임(60봉 하루 변동성 × √5)보다 크게 내렸다",
    "S_vol_high": "최근 20봉 하루 변동성이 최근 250봉 하루 변동성보다 크다",
    "S_deep_drawdown": "최근 250봉 고점보다 평소 한 달 움직임의 두 배(250봉 하루 변동성 × √20 × 2)보다 깊이 빠져 있다",
    "S_big_move": "마지막 봉의 움직임이 평소 하루 움직임(60봉 하루 변동성)의 두 배를 넘었다",
}


def _logret(closes: list[float], n: int) -> list[float]:
    seg = closes[-(n + 1):]
    return [math.log(b / a) for a, b in zip(seg, seg[1:]) if a > 0 and b > 0]


def read(closes: list[float] | tuple[float, ...]) -> tuple[dict[str, str], dict[str, float]]:
    """마지막 종가 시점의 조각 값과 계산에 쓴 숫자. 줄이 모자라면 모두 '모름'."""
    c = list(closes)
    if len(c) < NEED:
        return {k: "모름" for k in STATES}, {"bars": float(len(c))}
    last = c[-1]
    r20 = last / c[-21] - 1
    r60 = last / c[-61] - 1
    ma200 = fmean(c[-200:])
    r5 = last / c[-6] - 1
    r1 = last / c[-2] - 1
    vol20 = pstdev(_logret(c, 20))
    vol60 = pstdev(_logret(c, 60))
    vol250 = pstdev(_logret(c, 250))
    high250 = max(c[-250:])
    dd = last / high250 - 1
    yes = {True: "예", False: "아니오"}
    values = {
        "S_trend_20": yes[r20 > 0],
        "S_trend_60": yes[r60 > 0],
        "S_above_ma200": yes[last > ma200],
        "S_drop_5": yes[r5 < -vol60 * math.sqrt(5)],
        "S_vol_high": yes[vol20 > vol250],
        "S_deep_drawdown": yes[dd < -2 * vol250 * math.sqrt(20)],
        "S_big_move": yes[abs(r1) > 2 * vol60],
    }
    numbers = {"r1": r1, "r5": r5, "r20": r20, "r60": r60, "ma200_gap": last / ma200 - 1, "vol20": vol20,
               "vol60": vol60, "vol250": vol250, "drawdown250": dd}
    return values, {k: round(v, 6) for k, v in numbers.items()}


def read_series(closes: list[float]) -> list[dict[str, str] | None]:
    """맞추기용: 각 시점 t까지의 종가로 읽은 값 (t < NEED-1이면 None). read(closes[:t+1])와 같다."""
    out: list[dict[str, str] | None] = []
    for t in range(len(closes)):
        out.append(read(closes[t + 1 - NEED: t + 1])[0] if t + 1 >= NEED else None)
    return out
