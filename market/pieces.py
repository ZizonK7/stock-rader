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


def vol250_series(closes: list[float]) -> list[float | None]:
    """맞추기용: 각 시점 t까지의 최근 250봉 하루 변동성(read의 numbers['vol250']과 같은 값). t < NEED-1이면 None.
    움직임 기준값을 '최근 흔들림의 배수'로 맞출 때 표본마다 그때의 흔들림으로 나누는 데 쓴다(2026-11-03, C_base_vol)."""
    out: list[float | None] = []
    for t in range(len(closes)):
        out.append(pstdev(_logret(closes[t + 1 - NEED: t + 1], 250)) if t + 1 >= NEED else None)
    return out


def read_series(closes: list[float]) -> list[dict[str, str] | None]:
    """맞추기용: 각 시점 t까지의 종가로 읽은 값 (t < NEED-1이면 None). read(closes[:t+1])와 같다."""
    out: list[dict[str, str] | None] = []
    for t in range(len(closes)):
        out.append(read(closes[t + 1 - NEED: t + 1])[0] if t + 1 >= NEED else None)
    return out


# ---------- 두 시세로 계산하는 상태 (2026-11-17) ----------
# 위 일곱은 대상 하나의 종가만 본다. 아래는 대상과 그 '자기 시장 묶음'(config.market_bundle)의 일봉을 같이 본다.
# 계산 조각의 조건(STATES)에는 넣지 않는다: 참고 칸(calc.analog)이 '시세 상태 일곱이 같은 날'을 찾으므로 일곱 그대로 둔다.
# 매일 실행(daily.compute)이 이 값을 그날 시세 대 보기 값(calc_values·reading·계산기 예측 줄 readings)에 더해 기록하고,
# 조사 조각 주장의 더한 조건(also)으로 쓰인다(checks.research_direction이 계산기 예측 줄 readings에서 읽는다).
COMOVE_STATE = "S_comove_market"
# 60봉: 평소 움직임(60봉 하루 변동성)과 같은 창 — 약 석 달, 한 회사의 분기 하나. 상관계수 표본 60개면 0 근처 값의 표준오차가
# 약 1/√59 ≈ 0.13이다.
COMOVE_BARS = 60
# 문턱 0.3: 표본 60개에서 상관이 0인데 우연히 나올 값의 약 두 표준오차(2 × 0.13 ≈ 0.26)를 올린 값 — '같이 움직인다'고 0과 가를 수
# 있는 가장 낮은 선으로 미리 정했다. 2026-11-17 하나뿐인 나눠 본 표(종목 다섯의 11-10까지 60봉 상관 0.40·0.41·0.45 vs −0.05·0.01)를
# 보고 정했으니, 그 다섯을 가르는 선이라는 것은 우연이 아니다 — 맞춘 숫자가 아니라 손으로 정한 값이고, 그 뒤의 기록으로만 시험한다.
COMOVE_MIN = 0.3
DERIVED = {COMOVE_STATE: f"최근 {COMOVE_BARS}봉 하루 수익률이 자기 시장 묶음의 하루 수익률과 같이 움직인 정도(상관계수)가 "
                         f"{COMOVE_MIN} 이상이다(묶음 자신은 늘 예)"}


def comove(rows, bundle_rows, n: int = COMOVE_BARS) -> tuple[str, float | None]:
    """(값, 상관계수). rows·bundle_rows = 판단 때 끝난 일봉만(prices.view). 두 시세에 다 있는 날짜(종가 > 0)의 마지막 n + 1개로
    하루 로그 수익률 n개씩을 만들어 상관계수를 잰다. 날짜가 모자라거나 한쪽이 전혀 안 움직였으면 '모름'.
    대상이 곧 묶음이면(같은 줄) 상관 1 — 예."""
    a = {r.date: r.close for r in rows if r.close > 0}
    b = {r.date: r.close for r in bundle_rows if r.close > 0}
    days = sorted(set(a) & set(b))[-(n + 1):]
    if len(days) < n + 1:
        return "모름", None
    ra = [math.log(a[y] / a[x]) for x, y in zip(days, days[1:])]
    rb = [math.log(b[y] / b[x]) for x, y in zip(days, days[1:])]
    sa, sb = pstdev(ra), pstdev(rb)
    if sa == 0 or sb == 0:
        return "모름", None
    ma, mb = fmean(ra), fmean(rb)
    corr = fmean([(x - ma) * (y - mb) for x, y in zip(ra, rb)]) / (sa * sb)
    corr = max(-1.0, min(1.0, corr))
    return ("예" if corr >= COMOVE_MIN else "아니오"), round(corr, 4)
