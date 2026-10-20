"""계산기: 대 보기 값(조각 값)만 보고 오를 확률과 움직임 크기 범위를 계산한다.

사용법 (저장소 루트):
  python -m market.calc                지금 저장된 시세로 오늘 07:37 판단을 계산해 보여 준다 (기록하지 않는다)
  python -m market.calc 2026-06-15     그날 07:37에 알려진 시세로 (그 뒤 시세는 읽지 않는다)

계산 (agent_usage predict.py와 같은 모양):
- 조각 = 효과(effect)가 있는 살아 있는 가설. 효과 하나 = 무엇에(on) · 어떻게(op: 기준/곱하기) · 범위 · 언제(when) · 대상·기간.
- 오를 확률의 승산 = 기준 승산 × 켜진 곱하기들. 움직임 크기도 같다. 기준값이 없으면 지어내지 않고 '모름'.
- 범위 합치기: 로그 반폭을 제곱합 제곱근으로 합치고, 상한(2배)을 넘으면 가운데 둘레로 자른다.
- 결론 = (방향 쪽, 확신도, 크기 등급). 설명에는 빼면 결론이 바뀌는 조각만 남긴다(기준값은 늘 남긴다).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from . import pieces as P
from . import prices as PR

MAX_RATIO = 2.0
SIDE = {1: "오른다 쪽", -1: "내린다 쪽", 0: "반반"}


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


def pieces_from(raw: dict) -> list[Piece]:
    out = []
    for h in raw.get("hypothesis", []) + raw.get("rule", []):
        if h.get("status", "hypothesis") == "retired" or not h.get("effect"):
            continue
        effs = []
        for e in h["effect"]:
            when = tuple((k, v) for k, v in (e.get("when") or {}).items())
            effs.append(Effect(h["id"], e["on"], e["op"], float(e["range"][0]), float(e["range"][1]),
                               str(e["asset"]), int(e["horizon"]), when, e.get("dir"), e.get("reason", "")))
        out.append(Piece(h["id"], h.get("text", h.get("method", "")), h.get("reason", ""), effs))
    return out


def load_pieces(path: Path = C.KNOWLEDGE) -> tuple[list[Piece], str]:
    """(조각들, 지식 파일 지문). 지문은 예측 기록에 남겨 어느 논리집으로 계산했는지 보인다."""
    data = path.read_bytes()
    return pieces_from(tomllib.loads(data.decode("utf-8"))), hashlib.sha256(data).hexdigest()[:12]


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


@dataclass
class Result:
    asset: str
    horizon: int
    values: dict[str, str]
    base_odds: Effect | None
    odds_muls: list[Effect] = field(default_factory=list)
    base_move: Effect | None = None
    move_muls: list[Effect] = field(default_factory=list)

    def odds(self, skip: Effect | None = None) -> tuple[float, float] | None:
        if not self.base_odds:
            return None
        return clamp(combine([(e.lo, e.hi) for e in [self.base_odds, *self.odds_muls] if e is not skip]))

    def p_up(self, skip: Effect | None = None) -> float | None:
        o = self.odds(skip)
        if not o:
            return None
        c = math.sqrt(o[0] * o[1])
        return c / (1 + c)

    def p_range(self) -> list[float] | None:
        o = self.odds()
        return [o[0] / (1 + o[0]), o[1] / (1 + o[1])] if o else None

    def move(self, skip: Effect | None = None) -> tuple[float, float] | None:
        if not self.base_move:
            return None
        return clamp(combine([(e.lo, e.hi) for e in [self.base_move, *self.move_muls] if e is not skip]))

    def used(self) -> list[Effect]:
        return [e for e in [self.base_odds, *self.odds_muls, self.base_move, *self.move_muls] if e]

    def conclusion(self, skip: Effect | None = None) -> tuple[str, str, str]:
        p = self.p_up(skip)
        if p is None:
            side, conf = "모름", "모름"
        else:
            d = p - 0.5
            side = SIDE[(d > 1e-9) - (d < -1e-9)]
            conf = "낮음" if abs(d) < 0.05 else "보통" if abs(d) < 0.10 else "높음"
        m, size = self.move(skip), "모름"
        if m and self.base_move:
            ratio = math.sqrt(m[0] * m[1]) / self.base_move.center
            size = "작음" if ratio < 0.85 else "큼" if ratio > 1.18 else "보통"
        return side, conf, size

    def decisive(self) -> list[Effect]:
        """빼면 결론(방향 쪽·확신도·크기 등급)이 바뀌는 곱하기 조각."""
        now = self.conclusion()
        return [e for e in [*self.odds_muls, *self.move_muls] if self.conclusion(e) != now]

    def reason(self) -> str:
        p, m = self.p_up(), self.move()
        if p is None:
            head = "오를 확률: 모름(이 대상·기간의 기준값이 없다 — 지어내지 않는다)"
        else:
            base_p = self.base_odds.center / (1 + self.base_odds.center)
            head = f"오를 확률 {p:.0%} (기준 {base_p:.0%}"
            keep = set(map(id, self.decisive()))
            parts = [f"'{e.piece}' ×{e.center:.2f}" for e in self.odds_muls if id(e) in keep]
            head += (" → " + ", ".join(parts) if parts else "") + ")"
        if m:
            keep = set(map(id, self.decisive()))
            parts = [f"'{e.piece}' ×{e.center:.2f}" for e in self.move_muls if id(e) in keep]
            head += f"; 움직임 {m[0]:.2f}–{m[1]:.2f}%" + (" (" + ", ".join(parts) + ")" if parts else "")
        side, conf, size = self.conclusion()
        return f"{head}. 결론: {side}, 확신 {conf}, 크기 {size}"


def predict(asset: str, horizon: int, values: dict[str, str], pieces: list[Piece]) -> Result:
    effs = [e for p in pieces for e in p.effects if e.asset == asset and e.horizon == horizon]

    def base(on: str) -> Effect | None:
        cands = [e for e in effs if e.on == on and e.op == "base" and e.matches(values)]
        return max(cands, key=lambda e: len(e.when)) if cands else None

    def muls(on: str) -> list[Effect]:
        return [e for e in effs if e.on == on and e.op == "mul" and e.matches(values)]

    return Result(asset, horizon, values, base("up_odds"), muls("up_odds"), base("move"), muls("move"))


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
    }


def main(argv: list[str]) -> int:
    day = dt.date.fromisoformat(argv[0]) if argv else C.now_kst().date()
    at = C.decision_dt(day)
    pieces, digest = load_pieces()
    print(f"{day} 07:37 판단 (논리집 {digest})")
    for asset in C.ASSET_IDS:
        hist = PR.view(asset, PR.load(asset), at)
        values, _ = P.read(hist.closes)
        print(f"\n{asset} — 마지막 일봉 {hist.last.date if hist.last else '없음'}")
        print("  대 보기: " + ", ".join(f"{k}={v}" for k, v in values.items()))
        for h in C.ASSETS[asset].horizons:
            print(f"  {h}봉: {predict(asset, h, values, pieces).reason()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
