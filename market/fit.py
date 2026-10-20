"""계산 조각의 숫자를 시세에서 기계적으로 맞춘다 (사람이 숫자를 고르지 않는다).

사용법 (저장소 루트):
  python -m market.fit            2026-06-30까지의 시세로 맞춘 결과를 보여 준다
  python -m market.fit --write    … 그리고 knowledge.toml의 계산 조각 구역(effect·fit·not_set 칸)을 다시 쓴다

조각의 '모양'(claims: 무엇에·어떻게·언제·방향·이유)은 knowledge.toml에 손으로 적는다. 여기서는 범위 숫자만 정한다.
대상 × 기간마다 따로 맞춘다. 표본 = 시점 t(그때까지 종가로 조각을 대 봄)와 t+h봉 뒤의 수익률. **목표 종가 날짜까지 FIT_UNTIL 이하**.

- 오를 확률(up_odds): 로지스틱 회귀(조각이 '예'면 1). 절편 → 기준 승산, 계수 → 곱하기.
  범위 = exp(값 ± 표준오차). 겹치는 기간 표본이라 표준오차에 √h를 곱한다(유효 표본 = 표본 ÷ h).
- 움직임 크기(move): log|수익률|의 최소제곱 회귀. 계수 → 곱하기(범위는 위와 같다).
  기준값 = 조각 효과를 걷어 낸 움직임(%)들을 2배 폭 창으로 훑어 가장 많이 담는 창(agent_usage fit.window와 같은 방법).
- 세우지 않는 효과 (기계적으로, 한 번에 하나씩 빼고 다시 맞춤):
  범위가 주장한 방향과 어긋나거나 1을 넘나듦(값이 표준오차 하나 안쪽) → '방향이 데이터와 안 맞음'.
  범위 폭이 상한(2배)을 넘음 → '너무 불확실'. 곱하기 수가 유효 표본 ÷ 10을 넘음 → 가장 약한 것부터 뺀다.
  기준값 범위가 상한을 넘으면 그 대상·기간은 '모름'(계산기가 지어내지 않는다).
"""
from __future__ import annotations

import datetime as dt
import math
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from . import pieces as P
from . import prices as PR

BLOCK_START = "# ---------- 계산 조각 (python -m market.fit --write가 effect·fit·not_set 칸을 다시 쓴다; 모양은 손으로) ----------"
BLOCK_END = "# ---------- 계산 조각 끝 ----------"
MAX_RATIO = 2.0
EFF_PER_MUL = 10  # 장치(조각 수 상한): 곱하기 효과 하나에 유효 표본 10개 이상
RIDGE = 1e-6


# ---------- 작은 선형대수 ----------
def inverse(m: list[list[float]]) -> list[list[float]]:
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-15:
            raise ZeroDivisionError("특이 행렬")
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [x / p for x in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def logistic(groups: dict[tuple[int, ...], list[float]], k: int) -> tuple[list[float], list[float]]:
    """묶음(무늬 → [표본 수, 오른 수])으로 로지스틱 회귀. (계수[절편, …], 표준오차)."""
    beta = [0.0] * (k + 1)
    rows = [((1.0, *pat), n, ups) for pat, (n, ups) in groups.items() if n > 0]
    info = None
    for _ in range(50):
        grad = [0.0] * (k + 1)
        info = [[RIDGE if i == j else 0.0 for j in range(k + 1)] for i in range(k + 1)]
        for x, n, ups in rows:
            z = sum(b * xi for b, xi in zip(beta, x))
            p = 1 / (1 + math.exp(-z))
            w = n * p * (1 - p)
            for i in range(k + 1):
                grad[i] += (ups - n * p) * x[i]
                for j in range(k + 1):
                    info[i][j] += w * x[i] * x[j]
        inv = inverse(info)
        step = [sum(inv[i][j] * grad[j] for j in range(k + 1)) for i in range(k + 1)]
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-10:
            break
    inv = inverse(info)
    return beta, [math.sqrt(max(inv[i][i], 0.0)) for i in range(k + 1)]


def ols(groups: dict[tuple[int, ...], list[float]], k: int) -> tuple[list[float], list[float]]:
    """묶음(무늬 → [수, y 합, y² 합])으로 최소제곱. (계수[절편, …], 표준오차)."""
    xtx = [[RIDGE if i == j else 0.0 for j in range(k + 1)] for i in range(k + 1)]
    xty = [0.0] * (k + 1)
    n_all, yy = 0.0, 0.0
    for pat, (n, sy, syy) in groups.items():
        x = (1.0, *pat)
        for i in range(k + 1):
            xty[i] += x[i] * sy
            for j in range(k + 1):
                xtx[i][j] += n * x[i] * x[j]
        n_all += n
        yy += syy
    inv = inverse(xtx)
    beta = [sum(inv[i][j] * xty[j] for j in range(k + 1)) for i in range(k + 1)]
    rss = yy - 2 * sum(b * v for b, v in zip(beta, xty)) + sum(
        beta[i] * xtx[i][j] * beta[j] for i in range(k + 1) for j in range(k + 1))
    s2 = max(rss, 0.0) / max(n_all - k - 1, 1)
    return beta, [math.sqrt(max(s2 * inv[i][i], 0.0)) for i in range(k + 1)]


def window(values: list[float], max_ratio: float = MAX_RATIO) -> tuple[float, float] | None:
    """상한 배 폭 창 가운데 값을 가장 많이 담는 창의 [최소, 최대] (같으면 가운데 값에 가까운 창)."""
    v = sorted(x for x in values if x > 0)
    if len(v) < 2:
        return None
    mid = math.log(v[len(v) // 2])
    best, j = None, 0
    for i in range(len(v)):
        j = max(j, i)
        while j + 1 < len(v) and v[j + 1] <= v[i] * max_ratio * (1 + 1e-12):
            j += 1
        off = abs((math.log(v[i]) + math.log(v[j])) / 2 - mid)
        if best is None or j - i + 1 > best[0] or (j - i + 1 == best[0] and off < best[1]):
            best = (j - i + 1, off, v[i], v[j])
    return (best[2], best[3]) if best[0] >= 2 else None


def _sig(x: float, up: bool, digits: int = 3) -> float:
    e = math.floor(math.log10(x)) - digits + 1
    f = (math.ceil if up else math.floor)(x / 10 ** e - (1e-9 if up else -1e-9))
    return round(f * 10 ** e, max(0, -e))


def rounded(lo: float, hi: float, max_ratio: float = MAX_RATIO) -> tuple[float, float]:
    lo, hi = _sig(lo, False), _sig(hi, True)
    if hi / lo > max_ratio:
        hi = _sig(lo * max_ratio, False)
    return lo, hi


# ---------- 모양 읽기 ----------
@dataclass
class Claim:
    piece: str
    on: str
    op: str
    reason: str
    dir: str | None = None
    state: str | None = None
    when: str | None = None


def claims_of(raws: list[dict]) -> list[Claim]:
    out = []
    for r in raws:
        if r.get("status", "hypothesis") == "retired":
            continue
        conds = r.get("conditions", [])
        for c in r.get("claims", []):
            out.append(Claim(r["id"], c["on"], c.get("op", "mul"), c.get("reason", ""), c.get("dir"),
                             conds[0] if conds and c.get("op", "mul") != "base" else None, c.get("when")))
    return out


def block_raws(path: Path = C.KNOWLEDGE) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    block = text.split(BLOCK_START, 1)[1].split(BLOCK_END, 1)[0]
    return tomllib.loads(block).get("hypothesis", [])


# ---------- 표본 ----------
@dataclass
class Samples:
    asset: str
    horizon: int
    readings: list[dict[str, str]] = field(default_factory=list)
    rets: list[float] = field(default_factory=list)
    first: dt.date | None = None
    last: dt.date | None = None


_READ_CACHE: dict = {}


def samples(asset: str, rows: tuple[PR.Row, ...], horizon: int, until: dt.date) -> Samples:
    """목표 종가 날짜가 until 이하인 (대 본 값, h봉 뒤 수익률) 표본."""
    rows = tuple(r for r in rows if r.date <= until)
    closes = [r.close for r in rows]
    key = (asset, len(rows), rows[-1].date if rows else None, rows[-1].close if rows else None)
    if key not in _READ_CACHE:
        _READ_CACHE.clear() if len(_READ_CACHE) > 12 else None
        _READ_CACHE[key] = P.read_series(closes)
    reads = _READ_CACHE[key]
    s = Samples(asset, horizon)
    for t in range(len(rows) - horizon):
        if reads[t] is None:
            continue
        s.readings.append(reads[t])
        s.rets.append(closes[t + horizon] / closes[t] - 1)
        s.first = s.first or rows[t].date
        s.last = rows[t + horizon].date
    return s


# ---------- 맞추기 ----------
@dataclass
class Fitted:
    """대상·기간 하나, 값(quantity) 하나의 결과."""
    asset: str
    horizon: int
    on: str
    base: tuple[float, float] | None
    muls: dict[str, tuple[float, float]]           # 조각 id → 범위
    dropped: dict[str, str]                         # 조각 id → 세우지 않은 까닭
    n: int
    n_eff: float
    note: str = ""


def _x(reading: dict[str, str], cl: Claim) -> int:
    return 1 if reading.get(cl.state) == cl.when else 0


def _violation(b: float, se: float, cl: Claim) -> tuple[float, str] | None:
    """(나쁨 정도, 까닭) 또는 None. 나쁨이 클수록 먼저 뺀다."""
    t = (b if cl.dir == "up" else -b) / se if se > 0 else float("inf")
    if 2 * se > math.log(MAX_RATIO):  # 로그로 비교한다: 조각 값이 표본에서 늘 같으면 표준오차가 매우 커서 exp가 넘친다
        width = f"{math.exp(2 * se):.2f}배" if se < 300 else "끝없이 넓음"
        return (100 + min(2 * se, 1e6), f"너무 불확실(범위 폭 {width} > 상한 {MAX_RATIO:g}배)")
    if t < 1:
        return (10 - t, f"방향이 데이터와 안 맞음(값 {math.exp(b):.3f}, 표준오차 안쪽이거나 반대쪽)")
    return None


def fit_one(s: Samples, on: str, claims: list[Claim]) -> Fitted:
    cls = [c for c in claims if c.on == on and c.op == "mul"]
    n = len(s.rets) if on == "up_odds" else sum(1 for r in s.rets if r != 0)
    n_eff = n / s.horizon
    dropped: dict[str, str] = {}
    if n < 30:
        return Fitted(s.asset, s.horizon, on, None, {}, {c.piece: "표본 부족" for c in cls}, n, n_eff, "표본 30개 미만")
    active = list(cls)
    scale = math.sqrt(s.horizon)
    while True:
        groups: dict[tuple[int, ...], list[float]] = {}
        for rd, r in zip(s.readings, s.rets):
            pat = tuple(_x(rd, c) for c in active)
            if on == "up_odds":
                g = groups.setdefault(pat, [0, 0])
                g[0] += 1
                g[1] += r > 0
            elif r != 0:
                y = math.log(abs(r))
                g = groups.setdefault(pat, [0, 0.0, 0.0])
                g[0] += 1
                g[1] += y
                g[2] += y * y
        try:
            beta, se = (logistic if on == "up_odds" else ols)(groups, len(active))
        except ZeroDivisionError:  # 어떤 조각이 늘 같은 값 → 그 조각부터 뺀다
            beta, se = [0.0] * (len(active) + 1), [float("inf")] * (len(active) + 1)
        se = [x * scale for x in se]
        worst = None
        for i, c in enumerate(active):
            v = _violation(beta[i + 1], se[i + 1], c)
            if v and (worst is None or v[0] > worst[0]):
                worst = (v[0], v[1], c)
        if worst is None and len(active) > n_eff / EFF_PER_MUL:
            i = min(range(len(active)), key=lambda i: abs(beta[i + 1]) / se[i + 1])
            worst = (0, f"곱하기 수 상한(유효 표본 {n_eff:.0f} ÷ {EFF_PER_MUL})을 넘어 가장 약한 것을 뺌", active[i])
        if worst is None:
            break
        dropped[worst[2].piece] = worst[1]
        active = [c for c in active if c is not worst[2]]
    muls = {c.piece: rounded(math.exp(beta[i + 1] - se[i + 1]), math.exp(beta[i + 1] + se[i + 1]))
            for i, c in enumerate(active)}
    base = None
    note = ""
    if on == "up_odds":
        lo, hi = math.exp(beta[0] - se[0]), math.exp(beta[0] + se[0])
        if hi / lo <= MAX_RATIO:
            base = rounded(lo, hi)
        else:
            note = f"기준 승산이 너무 불확실(폭 {hi / lo:.2f}배) → 모름"
    else:
        vals = []
        for rd, r in zip(s.readings, s.rets):
            if r == 0:
                continue
            k = sum(beta[i + 1] * _x(rd, c) for i, c in enumerate(active))
            vals.append(abs(r) * 100 / math.exp(k))
        w = window(vals)
        if w:
            base = rounded(*w)
        else:
            note = "움직임 기준값을 세울 수 없음"
    return Fitted(s.asset, s.horizon, on, base, muls, dropped, n, n_eff, note)


def fit_all(rows_by_asset: dict[str, tuple[PR.Row, ...]], claims: list[Claim],
            until: dt.date = C.FIT_UNTIL) -> list[Fitted]:
    out = []
    for asset in C.ASSET_IDS:
        rows = rows_by_asset.get(asset, ())
        for h in C.ASSETS[asset].horizons:
            s = samples(asset, rows, h, until)
            for on in ("up_odds", "move"):
                out.append(fit_one(s, on, claims))
    return out


# ---------- 효과 칸 만들기 ----------
def effects_for(raw: dict, fitted: list[Fitted], claims: list[Claim]) -> tuple[list[dict], str, list[str]]:
    """조각 하나의 effect 목록, fit 설명, not_set 목록."""
    pid = raw["id"]
    mine = [c for c in claims if c.piece == pid]
    effs, made, notset = [], [], []
    for f in fitted:
        for c in mine:
            if c.on != f.on:
                continue
            tag = f"{f.asset}:{f.horizon}"
            if c.op == "base":
                if f.base:
                    effs.append({"on": c.on, "op": "base", "range": list(f.base), "asset": f.asset, "horizon": f.horizon,
                                 "n": f.n, "reason": c.reason})
                    made.append(tag)
                else:
                    notset.append(f"{tag} {c.on}: {f.note}")
            elif c.piece in f.muls:
                effs.append({"on": c.on, "op": "mul", "range": list(f.muls[c.piece]), "dir": c.dir,
                             "when": {c.state: c.when}, "asset": f.asset, "horizon": f.horizon, "n": f.n,
                             "reason": c.reason})
                made.append(tag)
            elif c.piece in f.dropped:
                notset.append(f"{tag} {c.on}: {f.dropped[c.piece]}")
    total = len(made) + len(notset)
    text = f"목표 종가 {C.FIT_UNTIL} 이하 시세에서 맞춤: 세움 {len(made)}/{total}" + (
        f" ({', '.join(made)})" if made else "")
    return effs, text, notset


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def emit(raw: dict, effs: list[dict], fit_text: str, notset: list[str]) -> str:
    lines = ["[[hypothesis]]"]
    for k, v in raw.items():
        if k in ("effect", "fit", "not_set"):
            continue
        lines.append(f"{k} = {_toml(v)}")
    if raw.get("status", "hypothesis") != "retired":
        lines.append(f"fit = {_toml(fit_text)}")
        lines.append(f"not_set = {_toml(notset)}")
    for e in effs:
        lines.append("[[hypothesis.effect]]")
        lines += [f"{k} = {_toml(v)}" for k, v in e.items()]
    return "\n".join(lines)


def build_block(raws: list[dict], fitted: list[Fitted]) -> tuple[str, dict[str, list[dict]]]:
    claims = claims_of(raws)
    parts, by_piece = [], {}
    for r in raws:
        if r.get("status", "hypothesis") == "retired":
            parts.append(emit(r, r.get("effect", []), "", []))
            continue
        effs, text, notset = effects_for(r, fitted, claims)
        by_piece[r["id"]] = effs
        parts.append(emit(r, effs, text, notset))
    return "\n\n".join(parts), by_piece


def expected(path: Path = C.KNOWLEDGE, rows_by_asset: dict | None = None) -> dict[str, list[dict]]:
    """지금 시세로 맞추면 나올 조각별 effect 목록 (audit가 지식 파일과 비교한다)."""
    raws = block_raws(path)
    fitted = fit_all(rows_by_asset or PR.load_all(), claims_of(raws))
    return build_block(raws, fitted)[1]


def write(path: Path = C.KNOWLEDGE, rows_by_asset: dict | None = None) -> list[Fitted]:
    raws = block_raws(path)
    fitted = fit_all(rows_by_asset or PR.load_all(), claims_of(raws))
    body, _ = build_block(raws, fitted)
    text = path.read_text(encoding="utf-8")
    before, rest = text.split(BLOCK_START, 1)
    _, after = rest.split(BLOCK_END, 1)
    path.write_text(before + BLOCK_START + "\n" + body + "\n" + BLOCK_END + after, encoding="utf-8")
    return fitted


def show(fitted: list[Fitted]) -> str:
    lines = []
    for f in fitted:
        base = f"[{f.base[0]:g}, {f.base[1]:g}]" if f.base else "모름"
        lines.append(f"{f.asset} {f.horizon}봉 {f.on}: 표본 {f.n} (유효 {f.n_eff:.0f}), 기준 {base} {f.note}")
        for pid, (lo, hi) in f.muls.items():
            lines.append(f"    {pid} × [{lo:g}, {hi:g}]")
        for pid, why in f.dropped.items():
            lines.append(f"    {pid} 세우지 않음: {why}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if "--write" in argv:
        fitted = write()
        print(show(fitted))
        print("\nknowledge.toml 계산 조각 구역을 다시 썼다")
    else:
        raws = block_raws()
        print(show(fit_all(PR.load_all(), claims_of(raws))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
