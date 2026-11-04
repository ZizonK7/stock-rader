"""계산기: 대 보기 값(조각 값)만 보고 오를 확률과 움직임 크기 범위를 계산한다.

사용법 (저장소 루트):
  python -m market.calc                지금 저장된 시세로 오늘 07:37 판단을 계산해 보여 준다 (기록하지 않는다)
  python -m market.calc 2026-06-15     그날 07:37에 알려진 시세로 (그 뒤 시세는 읽지 않는다)

계산 (agent_usage predict.py와 같은 모양):
- 조각 = 효과(effect)가 있는 살아 있는 가설. 효과 하나 = 무엇에(on) · 어떻게(op: 기준/곱하기) · 범위 · 언제(when) · 대상·기간.
- 오를 확률의 승산 = 기준 승산 × 켜진 곱하기들. 움직임 크기도 같다. 기준값이 없으면 지어내지 않고 '모름'.
- 범위 합치기: 로그 반폭을 제곱합 제곱근으로 합치고, 상한(2배)을 넘으면 가운데 둘레로 자른다.
- 움직임 기준값에 scale = "vol250"이 있으면(2026-11-03, C_base_vol) 범위는 '최근 250봉 하루 변동성 × √기간'의 배수다.
  합친 범위에 판단 때의 그 값(대 보기 숫자 vol250, %로)을 곱한다. 숫자가 없으면 움직임은 '모름'(지어내지 않는다).
- 결론 = (방향 쪽, 확신도, 크기 등급). 설명에는 빼면 결론이 바뀌는 조각만 남긴다(기준값은 늘 남긴다).
- 방향 기본값은 동전이다(2026-11-04, C_base_coin): 오를 확률 기준값은 맞추지 않는 고정값 승산 1.0(효과 칸 fixed = true).
  곱하기 조각이 켜지면 그 값에서 움직인다. 이전 버전 모음(v1–v4)은 맞춘 절편을 그대로 쓴다.
- 출발점 조각(2026-11-04, C_started_move, 주장 op = "start"): 판단 때 가격이 있는 대상의 첫 기간(1봉)은, 판단 때 가격이
  기준 종가에서 이미 움직인 몫으로 오를 확률을 낸다 — p = Φ(ln(판단 때 가격 ÷ 기준 종가) ÷ (vol60 × √(남은 시간 ÷ 1일))), 0.10–0.90으로 자름.
  미리 정한 식이고 맞추지 않는다. 걸리면 그 칸의 오를 확률은 기준값·곱하기 대신 이 값이다(움직임 범위는 그대로).
  **2026-11-04 은퇴(결정 E3)**: 예측 기준이 체결가(판단 뒤 첫 시가)가 되어 판단 때 이미 움직인 몫이 정의상 0이다. 매일 실행은
  판단 때 가격을 넘기지 않는다(daily) — 식(start_p)은 떠 둔 모음(v5)을 읽을 수 있게 남겨 두었고, 넘기지 않으니 걸리지 않는다.
- layer(답한 층): 출발점 / 조각(곱하기가 하나라도 걸림) / 동전(고정 기준값만) / 기준값(맞춘 기준값만, 이전 버전) / 모름.
- analog(참고 칸, 확률에 쓰지 않는다): 같은 대상 최근 750봉 안에서 시세 상태 7개가 오늘과 모두 같고 목표 봉이 이미 끝난 날들의
  수·이어진 구간 수·유효 수·오른 비율. 시가를 넘기면(매일 실행, 2026-11-05~) 오른 비율은 체결가 기준(다음 봉 시가 → h봉째 종가).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist

from . import config as C
from . import pieces as P
from . import prices as PR

MAX_RATIO = 2.0
SIDE = {1: "오른다 쪽", -1: "내린다 쪽", 0: "반반"}
# 출발점 조각: 남은 시간의 아래 끝(분). 목표 봉이 거의 끝났을 때 0으로 나누거나 확률이 0·1로 굳는 것을 막는다.
# 10분은 판단 때 가격(업비트 현재가)을 받고 기록하기까지 걸리는 시간보다 짧은, 미리 정한 값이다(기록에서 고르지 않았다)
START_MIN_MINUTES = 10.0
# 출발점 확률의 양 끝(메인 결정 2026-11-04, 시험 전에 정함): 식은 정규분포를 가정하는데 이 분야는 큰 움직임이 정규보다 5–8배 잦다
# (분석 보고서 E2). 0.998 같은 극단 확률이 한 번 빗나가면 확률 점수가 1에 가깝게 깎이므로 0.10–0.90으로 자른다. 기록에서 고른 값이 아니다
START_P_MIN = 0.10
DAY_MINUTES = 1440.0
ANALOG_WINDOW = 750   # 참고 칸 창: 최근 3년(250봉 × 3). 분석 보고서 E의 '같은 대상 · 3년' 층과 같은 창


@dataclass(frozen=True)
class Effect:
    piece: str
    on: str
    op: str
    lo: float
    hi: float
    asset: str
    horizon: int
    when: tuple[tuple[str, str], ...] = ()
    dir: str | None = None
    reason: str = ""
    scale: str | None = None  # 기준값의 단위: None = %, "vol250" = 최근 250봉 하루 변동성 × √기간의 배수
    fixed: bool = False       # 맞추지 않는 고정값(2026-11-04, C_base_coin의 오를 확률 기준값 = 승산 1.0)

    @property
    def center(self) -> float:
        return math.sqrt(self.lo * self.hi)

    def matches(self, values: dict[str, str]) -> bool:
        return all(values.get(s) == v for s, v in self.when)


@dataclass
class Piece:
    id: str
    text: str
    reason: str
    effects: list[Effect]
    start: bool = False  # 출발점 조각(주장 op = "start"): 효과 숫자 없이 미리 정한 식으로 첫 기간의 오를 확률을 낸다


def is_start(h: dict) -> bool:
    """출발점 조각인가: 주장에 op = "start"가 있다 (숫자는 맞추지 않고 calc.start_p의 식으로 계산)."""
    return any(c.get("op") == "start" for c in h.get("claims", []))


def pieces_from(raw: dict) -> list[Piece]:
    out = []
    for h in raw.get("hypothesis", []) + raw.get("rule", []):
        if h.get("status", "hypothesis") == "retired" or not (h.get("effect") or is_start(h)):
            continue
        effs = []
        for e in h.get("effect", []):
            when = tuple((k, v) for k, v in (e.get("when") or {}).items())
            effs.append(Effect(h["id"], e["on"], e["op"], float(e["range"][0]), float(e["range"][1]),
                               str(e["asset"]), int(e["horizon"]), when, e.get("dir"), e.get("reason", ""),
                               e.get("scale"), bool(e.get("fixed", False))))
        out.append(Piece(h["id"], h.get("text", h.get("method", "")), h.get("reason", ""), effs, is_start(h)))
    return out


def digest(data: bytes) -> str:
    """지식 파일 지문. 줄바꿈을 LF로 맞춘 뒤 잰다(윈도 체크아웃의 CRLF와 클라우드의 LF가 같은 지문을 내게)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()[:12]


def load_pieces(path: Path = C.KNOWLEDGE) -> tuple[list[Piece], str]:
    """(조각들, 지식 파일 지문). 지문은 예측 기록에 남겨 어느 논리집으로 계산했는지 보인다."""
    data = path.read_bytes()
    return pieces_from(tomllib.loads(data.decode("utf-8"))), digest(data)


def combine(ranges: list[tuple[float, float]]) -> tuple[float, float]:
    c = sum(math.log(lo * hi) / 2 for lo, hi in ranges)
    w = math.sqrt(sum((math.log(hi / lo) / 2) ** 2 for lo, hi in ranges))
    return math.exp(c - w), math.exp(c + w)


def clamp(rng: tuple[float, float], max_ratio: float = MAX_RATIO) -> tuple[float, float]:
    lo, hi = rng
    if hi / lo <= max_ratio * (1 + 1e-9):
        return rng
    c, half = math.sqrt(lo * hi), math.sqrt(max_ratio)
    return c / half, c * half


@dataclass(frozen=True)
class Start:
    """출발점 조각이 낸 값 (판단 때 이미 일어난 움직임)."""
    piece: str
    p: float          # 오를 확률 = Φ(z)
    z: float
    gap: float        # ln(판단 때 가격 ÷ 기준 종가)
    minutes: float    # 남은 시간(분, 아래 끝 START_MIN_MINUTES)
    vol60: float      # 60봉 하루 변동성
    on: str = "up_odds"


@dataclass
class Result:
    asset: str
    horizon: int
    values: dict[str, str]
    base_odds: Effect | None
    odds_muls: list[Effect] = field(default_factory=list)
    base_move: Effect | None = None
    move_muls: list[Effect] = field(default_factory=list)
    unit: float = 1.0  # 움직임 범위에 곱할 값(%). 기준값이 흔들림의 배수면 판단 때의 '250봉 하루 변동성 × √기간 × 100'
    start: Start | None = None  # 출발점 조각이 걸렸으면 그 값 — 오를 확률은 이 값이 기준값·곱하기 대신이다

    def odds(self, skip=None) -> tuple[float, float] | None:
        if not self.base_odds:
            return None
        return clamp(combine([(e.lo, e.hi) for e in [self.base_odds, *self.odds_muls] if e is not skip]))

    def p_up(self, skip=None) -> float | None:
        if self.start is not None and skip is not self.start:
            return self.start.p
        o = self.odds(skip)
        if not o:
            return None
        c = math.sqrt(o[0] * o[1])
        return c / (1 + c)

    def p_range(self) -> list[float] | None:
        if self.start is not None:
            return None  # 출발점 값은 식 하나로 낸 점 하나다(맞춘 범위가 없다)
        o = self.odds()
        return [o[0] / (1 + o[0]), o[1] / (1 + o[1])] if o else None

    def move(self, skip=None) -> tuple[float, float] | None:
        if not self.base_move:
            return None
        lo, hi = clamp(combine([(e.lo, e.hi) for e in [self.base_move, *self.move_muls] if e is not skip]))
        return lo * self.unit, hi * self.unit

    def used(self) -> list:
        """쓴 조각. 출발점이 걸리면 오를 확률은 그 값이라 기준값·곱하기의 오를 확률 쪽은 쓰지 않은 것이다."""
        odds = [self.start] if self.start is not None else [self.base_odds, *self.odds_muls]
        return [e for e in [*odds, self.base_move, *self.move_muls] if e]

    def conclusion(self, skip=None) -> tuple[str, str, str]:
        p = self.p_up(skip)
        if p is None:
            side, conf = "모름", "모름"
        else:
            d = p - 0.5
            side = SIDE[(d > 1e-9) - (d < -1e-9)]
            conf = "낮음" if abs(d) < 0.05 else "보통" if abs(d) < 0.10 else "높음"
        m, size = self.move(skip), "모름"
        if m and self.base_move:
            ratio = math.sqrt(m[0] * m[1]) / (self.base_move.center * self.unit)
            size = "작음" if ratio < 0.85 else "큼" if ratio > 1.18 else "보통"
        return side, conf, size

    def decisive(self) -> list:
        """빼면 결론(방향 쪽·확신도·크기 등급)이 바뀌는 조각: 곱하기와 출발점."""
        now = self.conclusion()
        return [e for e in [self.start, *self.odds_muls, *self.move_muls] if e and self.conclusion(e) != now]

    def layer(self) -> str:
        """답한 층(2026-11-04, 예측 줄 layer 칸): 출발점 / 조각(곱하기가 하나라도 걸림) / 동전(고정 기준값만) /
        기준값(맞춘 기준값만 — 이전 버전 모음) / 모름(오를 확률 기준값이 없음)."""
        if self.start is not None:
            return "출발점"
        if self.p_up() is None:
            return "모름"
        if self.odds_muls or self.move_muls:
            return "조각"
        return "동전" if self.base_odds.fixed and abs(self.base_odds.center - 1.0) < 1e-12 else "기준값"

    def reason(self) -> str:
        p, m = self.p_up(), self.move()
        if p is None:
            head = "오를 확률: 모름(이 대상·기간의 기준값이 없다 — 지어내지 않는다)"
        elif self.start is not None:
            s = self.start
            head = (f"오를 확률 {p:.1%} (출발점 '{s.piece}': 판단 때 가격이 기준 종가보다 {math.expm1(s.gap) * 100:+.2f}%, "
                    f"목표 봉 끝까지 {s.minutes:.0f}분, 60봉 하루 흔들림 {s.vol60 * 100:.2f}% → z {s.z:+.2f}; 기준값·곱하기 대신)")
        else:
            base_p = self.base_odds.center / (1 + self.base_odds.center)
            head = f"오를 확률 {p:.0%} (기준 {base_p:.0%}" + (" 고정" if self.base_odds.fixed else "")
            keep = set(map(id, self.decisive()))
            parts = [f"'{e.piece}' ×{e.center:.2f}" for e in self.odds_muls if id(e) in keep]
            head += (" → " + ", ".join(parts) if parts else "") + ")"
        if m:
            keep = set(map(id, self.decisive()))
            parts = [f"'{e.piece}' ×{e.center:.2f}" for e in self.move_muls if id(e) in keep]
            head += f"; 움직임 {m[0]:.2f}–{m[1]:.2f}%" + (" (" + ", ".join(parts) + ")" if parts else "")
            if self.base_move and self.base_move.scale:
                head += f" [기준: 최근 250봉 흔들림 × √{self.horizon} = {self.unit:.2f}%의 {self.base_move.lo:g}–{self.base_move.hi:g}배]"
        side, conf, size = self.conclusion()
        return f"{head}. 결론: {side}, 확신 {conf}, 크기 {size}"


def start_p(asset: str, horizon: int, numbers: dict | None, decision: dict | None, piece: str) -> Start | None:
    """출발점 조각의 값 (걸리지 않으면 None). decision = {price, time(판단 때 가격의 시각), base_date, base_close}.
    걸리는 곳: 판단 때 가격이 있는 대상의 첫 기간(1봉). 판단 때 가격이 기준 봉이 끝나기 전 것이면(늦은 실행에서 기준 봉이
    바뀐 경우) 이미 일어난 움직임이 아니므로 쓰지 않는다. 남은 시간 = 목표 봉이 끝나는 시각 − 판단 때 가격의 시각(아래 끝 10분)."""
    if not decision or horizon != C.ASSETS[asset].horizons[0]:
        return None
    price, base_close = decision.get("price"), decision.get("base_close")
    vol = (numbers or {}).get("vol60")
    when, base_date = decision.get("time"), decision.get("base_date")
    if not (isinstance(price, (int, float)) and price > 0 and isinstance(base_close, (int, float)) and base_close > 0
            and isinstance(vol, (int, float)) and vol > 0 and when and base_date):
        return None
    when = when if isinstance(when, dt.datetime) else dt.datetime.fromisoformat(str(when))
    base_date = base_date if isinstance(base_date, dt.date) else dt.date.fromisoformat(str(base_date))
    spec = C.ASSETS[asset]
    if when < C.final_dt(spec, base_date):
        return None
    end = C.final_dt(spec, C.next_bar(asset, base_date))
    minutes = max((end - when).total_seconds() / 60, START_MIN_MINUTES)
    gap = math.log(price / base_close)
    z = gap / (vol * math.sqrt(minutes / DAY_MINUTES))
    p = min(max(NormalDist().cdf(z), START_P_MIN), 1 - START_P_MIN)
    return Start(piece, p, z, gap, minutes, vol)


def predict(asset: str, horizon: int, values: dict[str, str], pieces: list[Piece],
            numbers: dict[str, float] | None = None, decision: dict | None = None) -> Result:
    """numbers: 대 보기 숫자(pieces.read). 움직임 기준값이 흔들림의 배수(scale = "vol250")일 때 vol250을,
    출발점 조각이 vol60을 쓴다. decision: 판단 때 가격(있는 대상만, start_p)."""
    effs = [e for p in pieces for e in p.effects if e.asset == asset and e.horizon == horizon]

    def base(on: str) -> Effect | None:
        cands = [e for e in effs if e.on == on and e.op == "base" and e.matches(values)]
        return max(cands, key=lambda e: len(e.when)) if cands else None

    def muls(on: str) -> list[Effect]:
        return [e for e in effs if e.on == on and e.op == "mul" and e.matches(values)]

    bm, unit = base("move"), 1.0
    if bm is not None and bm.scale == "vol250":
        v = (numbers or {}).get("vol250")
        if isinstance(v, (int, float)) and v > 0:
            unit = v * math.sqrt(horizon) * 100
        else:
            bm = None  # 흔들림 숫자가 없으면 움직임은 '모름'
    elif bm is not None and bm.scale:
        bm = None  # 모르는 단위
    starter = next((p.id for p in pieces if p.start), None)
    start = start_p(asset, horizon, numbers, decision, starter) if starter else None
    return Result(asset, horizon, values, base("up_odds"), muls("up_odds"), bm, muls("move"), unit, start)


def record_fields(r: Result) -> dict:
    """예측 기록에 넣을 칸."""
    p, m = r.p_up(), r.move()
    return {
        "p_up": round(p, 4) if p is not None else None,
        "p_range": [round(x, 4) for x in r.p_range()] if r.p_range() else None,
        "size_lo": round(m[0], 4) if m else None,
        "size_hi": round(m[1], 4) if m else None,
        "used": [f"{e.piece}:{e.on}" for e in r.used()],
        "decisive": [f"{e.piece}:{e.on}" for e in r.decisive()],
        "reason": r.reason(),
        "layer": r.layer(),
    }


# ---------- 참고 칸: 같은 스위치의 지난 날들 (확률에 쓰지 않는다) ----------
_READ_MEMO: dict[tuple, dict] = {}


def _reading_at(c: list[float], t: int) -> dict[str, str] | None:
    """시점 t까지의 종가로 읽은 값 = pieces.read_series(c)[t]. 값은 마지막 251개 종가에만 기대므로 그 묶음으로 기억해 둔다
    (매일 실행·시험이 같은 과거를 여러 번 읽지 않게)."""
    if t + 1 < P.NEED:
        return None
    key = tuple(c[t + 1 - P.NEED: t + 1])
    got = _READ_MEMO.get(key)
    if got is None:
        if len(_READ_MEMO) > 30_000:
            _READ_MEMO.clear()
        got = _READ_MEMO[key] = P.read(list(key))[0]
    return got


def analog(closes, horizons, values: dict[str, str], window: int = ANALOG_WINDOW,
           opens=None) -> dict[int, dict | None]:
    """기간마다 참고 칸 {n, runs, eff, up_rate} (후보가 없으면 None). 확률에 쓰지 않는다(분석 보고서 E1: 같은 스위치 층은
    이 분야의 방향에서 동전·지난 날 전체 비율을 못 넘었다). 쌓아 두는 까닭: R_layer_v0 8 '참고 칸이 판정 두 번 연속 동전보다
    나으면 검토'를 나중에 보려고.
    - 후보: 같은 대상, 최근 window봉 안의 기준 봉 t 중 시세 상태 7개가 오늘(values)과 모두 같고 t + 기간 ≤ 오늘 기준 봉(마지막 줄)
      — 목표 봉이 판단 때 이미 끝난 날만(미래 금지).
    - runs = 이어진 구간 수(후보 봉 사이가 기간보다 벌어지면 새 구간), eff = n ÷ 기간(겹치는 기간을 거칠게 덜어 낸 유효 수),
      up_rate = 후보의 기간 수익률이 오른 비율. opens(시가 목록, closes와 같은 줄)를 넘기면 수익률은 체결가 기준
      (시가(t+1) → 종가(t+h), 결정 E2 — 예측 줄과 같은 기준)이고 시가가 0인 봉(거래 정지)의 후보는 뺀다. 없으면 종가(t) → 종가(t+h)."""
    c = list(closes)
    o = list(opens) if opens is not None else None
    last = len(c) - 1
    if not values or any(v not in ("예", "아니오") for v in values.values()):
        return {h: None for h in horizons}
    lo = max(0, last + 1 - window)
    out: dict[int, dict | None] = {}
    for h in horizons:
        cand = [t for t in range(lo, last - h + 1) if _reading_at(c, t) == values and (o is None or o[t + 1] > 0)]
        if not cand:
            out[h] = None
            continue
        runs = 1 + sum(1 for a, b in zip(cand, cand[1:]) if b - a > h)
        ups = sum(c[t + h] > (c[t] if o is None else o[t + 1]) for t in cand)
        out[h] = {"n": len(cand), "runs": runs, "eff": round(len(cand) / h, 2), "up_rate": round(ups / len(cand), 4)}
    return out


def main(argv: list[str]) -> int:
    day = dt.date.fromisoformat(argv[0]) if argv else C.now_kst().date()
    at = C.decision_dt(day)
    pieces, digest = load_pieces()
    print(f"{day} 07:37 판단 (논리집 {digest})")
    for asset in C.ASSET_IDS:
        hist = PR.view(asset, PR.load(asset), at)
        values, numbers = P.read(hist.closes)
        print(f"\n{asset} — 마지막 일봉 {hist.last.date if hist.last else '없음'}")
        print("  대 보기: " + ", ".join(f"{k}={v}" for k, v in values.items()))
        for h in C.ASSETS[asset].horizons:
            r = predict(asset, h, values, pieces, numbers)
            print(f"  {h}봉 ({r.layer()}): {r.reason()}")
    print("\n(기준은 체결가 = 판단 뒤 첫 시가다(2026-11-05~). 출발점 조각은 2026-11-04 은퇴 — 판단 때 이미 움직인 몫이 정의상 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
