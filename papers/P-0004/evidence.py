"""P-0004 근거 계산: T-0001 v1 — "다음 h봉 움직임 크기는 최근 흔들림을 일부만 따라가고, 따라가는 몫은 기간이 길수록
줄며, 긴 기간에서는 시끄러울 때만 따라간다."

저장소 루트에서:  python -X utf8 papers/P-0004/evidence.py
- 읽기만 한다(records/, data/prices/, knowledge.toml). 아무 파일도 쓰지 않는다. 출력은 표준 출력뿐이다.
- 외부 패키지 없음(Python 3.11+). 시세 읽기·끝난 봉·시세 상태·계산기는 저장소 코드
  (market.prices.load, market.config.final_dt, market.pieces.read, market.calc.load_pieces/predict)를 그대로 부른다.
- 정의·규칙은 같은 폴더 theory.md(먼저 커밋)를 따른다. 바뀐 곳은 paper.md 6절.
- 가져온 코드: P-0003 evidence.py의 jl·판단 때 아는 값 대 보기([E2])·L06 절차(run_l06)·덩어리 부호 뒤집기 모양.
- 표 번호 [E1]…가 paper.md의 근거 번호다.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market import calc, config as C  # noqa: E402
from market import pieces, prices as P  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 경계·매개변수 (theory.md 4·8절)
RECORD_END = dt.date(2026, 11, 25)          # run ≤ 이 날짜의 기록만
PRICE_END = dt.date(2026, 11, 24)           # 이 날짜까지의 봉만(파일 끝; 코인은 11-23)
JUDGE = dt.time(8, 40)
TARGETS = ("069500", "360750", "KRW-BTC")
NAME = {"069500": "국내", "360750": "해외", "KRW-BTC": "코인"}
HORIZONS = {"069500": (1, 5, 20), "360750": (1, 5, 20), "KRW-BTC": (1, 7, 28)}
LONG = {"069500": 20, "360750": 20, "KRW-BTC": 28}
STOCK_H = (1, 5, 20)
HALF_LIFE = 20                              # B6
RHO = 0.5 ** (1 / HALF_LIFE)
BINS = (0.7, 0.85, 1.0, 1.2, 1.5)           # B10
M_FLOOR = 0.001                             # B3
IRLS = 30                                   # B11
BLOCK = 60
BOOT = 200
FLIPS = 2000
SEED = 20261125
W_GRID = [i / 10 for i in range(11)]
W_MECH = [i / 20 for i in range(21)]
REFIT_EVERY = 20                            # B7 뒤쪽(행동)
MIN_FIT = 250
REC_START = dt.date(2026, 10, 21)
SHIFT_START, SHIFT_END = dt.date(2026, 7, 1), dt.date(2026, 10, 19)
FIT_END = dt.date(2026, 6, 30)              # P-0003 구간(4-4 다시 하기)
P3_HOLD_START, P3_HOLD_END = dt.date(2026, 7, 1), dt.date(2026, 10, 19)
LW = math.log(2) / 2                         # 2배 폭 범위의 로그 반폭
KST = C.KST
REC = ROOT / "records"

# 세운 재료: (대상, 기간, 판단 날 d) — theory.md 8절
SEED_L12 = {("KRW-BTC", 28, dt.date(2026, 10, 21)), ("069500", 20, dt.date(2026, 10, 22))}
SEED_L06 = {("KRW-BTC", 7, dt.date(2026, 11, 9)), ("360750", 5, dt.date(2026, 11, 10)), ("069500", 5, dt.date(2026, 11, 11)),
            ("KRW-BTC", 1, dt.date(2026, 11, 16)), ("360750", 1, dt.date(2026, 11, 17)), ("KRW-BTC", 7, dt.date(2026, 11, 11)),
            ("003230", 1, dt.date(2026, 11, 18)), ("011200", 1, dt.date(2026, 11, 18)), ("360750", 1, dt.date(2026, 11, 19)),
            ("000270", 1, dt.date(2026, 11, 19)), ("KRW-BTC", 1, dt.date(2026, 11, 19)), ("KRW-BTC", 1, dt.date(2026, 11, 20)),
            ("KRW-BTC", 7, dt.date(2026, 11, 15)), ("KRW-BTC", 7, dt.date(2026, 11, 16)), ("360750", 5, dt.date(2026, 11, 18))}
SEEDS = {k: "L12" for k in SEED_L12} | {k: "L06" for k in SEED_L06}


def jl(name):
    with (REC / f"{name}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["run"] <= RECORD_END.isoformat()]


def day_of(s):
    return dt.date.fromisoformat(s)


def w_of(h):
    """B6: w_h = (1/h) Σ_{k=1..h} ρ^k."""
    return sum(RHO ** k for k in range(1, h + 1)) / h


def f3(x, nd=3):
    return "–" if x is None else f"{x:+.{nd}f}"


def pp(x):
    return "–" if x is None else f"{x * 100:+.1f}"


# ---------------------------------------------------------------- 시세·판단일
PIECES, KNOW = calc.load_pieces()


def load_rows(a):
    return [r for r in P.load(a) if r.date <= PRICE_END]


def prepare(a, horizons, with_calc):
    """대상 a의 봉마다(판단 = 그 봉 날짜 08:40, 끝난 봉만): r·r60·r_rob·상태, 기간마다 결과 M·R·RV(그리고 계산기 범위)."""
    rows = load_rows(a)
    final = [C.final_dt(C.ASSETS[a], r.date) for r in rows]
    out = []
    for j, r in enumerate(rows):
        at = dt.datetime.combine(r.date, JUDGE, KST)
        k = bisect.bisect_right(final, at)
        if k < pieces.NEED or r.open <= 0:
            continue
        closes = [x.close for x in rows[k - pieces.NEED:k]]
        vals, nums = pieces.read(closes)
        v250 = nums.get("vol250")
        if not v250 or v250 <= 0 or nums["vol20"] <= 0:
            continue
        lr20 = [abs(math.log(y / x)) for x, y in zip(closes[-21:], closes[-20:]) if x > 0 and y > 0]
        sq = [v * v for v in lr20]
        day = {"j": j, "date": r.date, "at": at, "k": k, "vals": vals, "r": nums["vol20"] / v250,
               "r60": nums["vol60"] / v250, "rrob": 1.4826 * median(lr20) / v250, "v250": v250,
               "js": max(sq) / sum(sq) if sum(sq) > 0 else 0.0, "h": {}}
        for h in horizons:
            if j + h - 1 >= len(rows):
                continue
            t = rows[j + h - 1]
            if t.close <= 0 or r.close <= 0:
                continue
            ret = t.close / r.open - 1
            s, ok = math.log(r.close / r.open) ** 2, True
            for i in range(j + 1, j + h):
                if rows[i].close <= 0 or rows[i - 1].close <= 0:
                    ok = False
                    break
                s += math.log(rows[i].close / rows[i - 1].close) ** 2
            unit = v250 * math.sqrt(h)
            x = {"R": ret, "M": abs(ret) / unit, "RV": math.sqrt(s) / unit if ok else None, "tdate": t.date,
                 "tfinal": final[j + h - 1], "abs": abs(ret) * 100, "unit": unit}
            if with_calc:
                res = calc.predict(a, h, vals, PIECES, nums)
                mv = res.move()
                if mv is None:
                    continue
                x["lo"], x["hi"] = mv
                x["mcalc"] = math.prod(e.center for e in res.move_muls) if res.move_muls else 1.0
            day["h"][h] = x
        out.append(day)
    return out


def cell_days(days, h):
    return [d for d in days if h in d["h"]]


def ylog(M):
    return math.log(max(M, M_FLOOR))


# ---------------------------------------------------------------- 가운데 값(최소 절대 편차) 회귀
def hinge(lr, y, iters=IRLS, init=None):
    """ln M = a + β−·min(ln r, 0) + β+·max(ln r, 0)의 최소 절대 편차 회귀(반복 가중 최소제곱). (a, β−, β+)."""
    xm = [v if v < 0 else 0.0 for v in lr]
    xp = [v if v > 0 else 0.0 for v in lr]
    a, bm, bp = init if init else (median(y), 0.0, 0.0)
    for _ in range(iters):
        sw = swm = swp = swmm = swpp = swy = swmy = swpy = 0.0
        for m_, p_, yy in zip(xm, xp, y):
            e = yy - a - bm * m_ - bp * p_
            w = 1.0 / (e if e > 1e-6 else (-e if e < -1e-6 else 1e-6))
            sw += w
            swy += w * yy
            if m_:
                wm = w * m_
                swm += wm
                swmm += wm * m_
                swmy += wm * yy
            elif p_:
                wp = w * p_
                swp += wp
                swpp += wp * p_
                swpy += wp * yy
        num, den = swy, sw
        if swmm > 0:
            num -= swm * swmy / swmm
            den -= swm * swm / swmm
        if swpp > 0:
            num -= swp * swpy / swpp
            den -= swp * swp / swpp
        a = num / den
        bm = (swmy - swm * a) / swmm if swmm > 0 else 0.0
        bp = (swpy - swp * a) / swpp if swpp > 0 else 0.0
    return a, bm, bp


def solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for q in range(c, n + 1):
                    M[r][q] -= f * M[c][q]
    return [M[i][n] / M[i][i] for i in range(n)]


def lad(X, y, iters=IRLS):
    """일반 최소 절대 편차 회귀(열 수 p ≤ 6). X는 줄마다 [1, x1, …]."""
    p = len(X[0])
    beta = [median(y)] + [0.0] * (p - 1)
    for _ in range(iters):
        A = [[0.0] * p for _ in range(p)]
        b = [0.0] * p
        for xi, yi in zip(X, y):
            e = yi - sum(bb * xx for bb, xx in zip(beta, xi))
            w = 1.0 / max(abs(e), 1e-6)
            for u in range(p):
                wu = w * xi[u]
                if wu == 0.0:
                    continue
                b[u] += wu * yi
                for v in range(u, p):
                    A[u][v] += wu * xi[v]
        for u in range(p):
            for v in range(u):
                A[u][v] = A[v][u]
        nb = solve(A, b)
        if nb is None:
            break
        beta = nb
    return beta


def blocks_boot(n, rng):
    """판단일 60일 덩어리 이동 부트스트랩의 색인 한 벌."""
    if n <= BLOCK:
        return [rng.randrange(n) for _ in range(n)]
    idx = []
    while len(idx) < n:
        s = rng.randrange(n - BLOCK + 1)
        idx.extend(range(s, s + BLOCK))
    return idx[:n]


def pct_int(vals):
    s = sorted(vals)
    return s[int(0.05 * (len(s) - 1))], s[int(0.95 * (len(s) - 1))]


# ---------------------------------------------------------------- 범위(2배 폭) 적중
def best_level(z):
    """같은 자료에서 적중이 가장 많은 로그 높이(2배 폭 = 로그 폭 ln 2). 같으면 먼저 찾은 것."""
    s = sorted(z)
    best_n, best_c, lo = -1, 0.0, 0
    for i in range(len(s)):
        while s[i] - s[lo] > 2 * LW + 1e-12:
            lo += 1
        if i - lo + 1 > best_n:
            best_n, best_c = i - lo + 1, s[lo] + LW
    return best_c


def sides(z, c):
    """+1 위로 빗나감(실제가 큼), −1 아래로, 0 안."""
    return [0 if abs(v - c) <= LW + 1e-12 else (1 if v > c else -1) for v in z]


def hitstat(sd):
    n = len(sd)
    return (sum(1 for s in sd if s == 0) / n, sum(1 for s in sd if s == 1) / n, sum(1 for s in sd if s == -1) / n) if n else (None, None, None)


def flip_p(series_list, rng):
    """날마다 차이 줄(같은 날들) 여럿 → 60일 덩어리 부호 뒤집기. 줄마다 비율과 '가장 나은 줄' 비율."""
    blocks = [[sum(s[i:i + BLOCK]) for i in range(0, len(s), BLOCK)] for s in series_list]
    obs = [sum(b) for b in blocks]
    best_obs = max(obs)
    ge, ge_best = [0] * len(blocks), 0
    nb = len(blocks[0])
    for _ in range(FLIPS):
        sg = [1 if rng.random() < 0.5 else -1 for _ in range(nb)]
        tots = [sum(x * y for x, y in zip(b, sg)) for b in blocks]
        for q, t in enumerate(tots):
            ge[q] += t >= obs[q]
        ge_best += max(tots) >= best_obs
    return [g / FLIPS for g in ge], ge_best / FLIPS


def shape_logm(rule, d, h):
    """규칙의 ln m(r) — theory.md 8절의 R0 ~ R4."""
    r = d["r"]
    w = w_of(h)
    if rule == "R0":
        return 0.0
    if rule == "R1":
        return math.log(d["h"][h]["mcalc"])
    if rule == "R2":
        return math.log(r)
    if rule == "R3":
        return w * math.log(r)
    if rule == "R4":
        return 0.5 * math.log(w * r * r + 1 - w)
    if rule == "R4b":   # 결과를 본 뒤(6절): 같은 꼴, 상태만 큰 날에 덜 흔들리는 r_rob
        rb = d["rrob"]
        return 0.5 * math.log(w * rb * rb + 1 - w)
    if rule == "R5":    # 결과를 본 뒤(6절): r ≥ 1이면 계산기 계단, r < 1이면 이론 꼴로 좁힘
        return math.log(d["h"][h]["mcalc"]) if r >= 1 else 0.5 * math.log(w * r * r + 1 - w)
    if rule.startswith("W"):
        ww = float(rule[1:])
        return 0.5 * math.log(ww * r * r + 1 - ww)
    raise ValueError(rule)


RULES = ("R0", "R1", "R2", "R3", "R4")
RULE_NAME = {"R0": "R0 늘 긴 평균", "R1": "R1 계산기 꼴", "R2": "R2 다 따라감", "R3": "R3 로그 대칭 섞기", "R4": "R4 이론"}


def fitted_sides(cd, h, rule, fit_on=None):
    """규칙의 높이를 fit_on(없으면 cd 전부)에서 맞추고 cd의 날마다 빗나간 쪽."""
    z_all = [ylog(d["h"][h]["M"]) - shape_logm(rule, d, h) for d in cd]
    src = z_all if fit_on is None else [ylog(d["h"][h]["M"]) - shape_logm(rule, d, h) for d in fit_on]
    c = best_level(src)
    return sides(z_all, c), c


def calc_sides(cd, h):
    return [0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1) for x in (d["h"][h] for d in cd)]


# ---------------------------------------------------------------- L06 절차 (P-0003 run_l06 그대로, 하루 구조만 맞춤)
def run_l06(cells, h, trig, shift, gate=None):
    used = {}
    m = 1.0
    hist, pending, since, allfin, moves = [], [], 0, [], 0
    for c in cells:
        if h not in c["h"]:
            continue
        x = c["h"][h]
        keep = []
        for (i, lo, hi, ab, tf) in pending:
            if tf <= c["at"]:
                hist.append((i, 0 if lo <= ab <= hi else (1 if ab > hi else -1)))
                allfin.append(ab)
            else:
                keep.append((i, lo, hi, ab, tf))
        pending = keep
        g = gate(c) if gate else "l06"
        if g == "l06":
            seg = hist[since:]
            picked = []
            for (i, s) in reversed(seg):
                if not picked or picked[-1][0] - i >= h:
                    picked.append((i, s))
            sd = [s for _, s in picked]
            trig_side = 0
            if trig == "T1" and len(sd) >= 3 and sd[0] == sd[1] == sd[2] != 0:
                trig_side = sd[0]
            if trig == "T2":
                up, dn = sd.count(1), sd.count(-1)
                if up >= 3 or dn >= 3:
                    trig_side = 1 if up > dn else -1 if dn > up else sd[next(q for q, s in enumerate(sd) if s != 0)]
            if trig_side and len(allfin) >= 60:
                last = allfin[-60:]
                cen = math.sqrt(x["lo"] * x["hi"])
                cur_n = sum(x["lo"] * m <= v <= x["hi"] * m for v in last)
                if shift == "S1":
                    best = None
                    for kk in range(-30, 31):
                        cc_ = cen * 1.05 ** kk
                        n_ = sum(cc_ / math.sqrt(2) <= v <= cc_ * math.sqrt(2) for v in last)
                        if best is None or n_ > best[0] or (n_ == best[0] and abs(kk) < abs(best[1])):
                            best = (n_, kk, cc_)
                    cand_n, cand_m = best[0], best[2] / cen
                else:
                    cand_n = sum(x["lo"] * c["r"] <= v <= x["hi"] * c["r"] for v in last)
                    cand_m = c["r"]
                if cand_n > cur_n:
                    m = cand_m
                    moves += 1
                since = len(hist)
            mm = m
        else:
            mm = 1.0
        used[c["j"]] = (x["lo"] * mm, x["hi"] * mm)
        pending.append((c["j"], x["lo"] * mm, x["hi"] * mm, x["abs"], x["tfinal"]))
    return used, moves


def gate_g(a, h):
    def g(c):
        if h == LONG[a] and c["r"] < 1:
            return "one"
        if h != LONG[a] or c["r"] > 1.2:
            return "l06"
        return "one"
    return g


# ---------------------------------------------------------------- 판단 때 끝난 창만으로 맞춘 높이(행동)
def expanding_sides(cd, h, rule):
    """20 판단일마다, 그 판단 시각에 목표가 끝난 창(250개 이상)만으로 높이를 다시 맞춘다. 높이가 없는 날은 None."""
    z = [ylog(d["h"][h]["M"]) - shape_logm(rule, d, h) for d in cd]
    tf = [d["h"][h]["tfinal"] for d in cd]
    out, c, last_fit = [], None, -REFIT_EVERY
    for i, d in enumerate(cd):
        if i - last_fit >= REFIT_EVERY or c is None:
            done = [z[q] for q in range(i) if tf[q] <= d["at"]]
            if len(done) >= MIN_FIT:
                c = best_level(done)
                last_fit = i
        if c is None:
            out.append(None)
            continue
        v = z[i]
        out.append(0 if abs(v - c) <= LW + 1e-12 else (1 if v > c else -1))
    return out


# ================================================================ 실행
def main() -> int:
    t0 = time.time()
    rng = random.Random(SEED)
    print("=" * 112)
    print(f"[E1] 재료 — 논리집 지문 {KNOW}, 계산기 조각 {len(PIECES)}개. 판단 = 봉 날짜 08:40, 끝난 봉만. 결과 M = |시가(d) → 종가(d+h−1)| ÷ (vol250 × √h)."
          f" 시세 ≤ {PRICE_END}, 기록 run ≤ {RECORD_END}. 반감기 {HALF_LIFE}봉 → w_h: "
          + ", ".join(f"w_{h} {w_of(h):.3f}" for h in (1, 5, 7, 20, 28)))
    DAYS = {a: prepare(a, HORIZONS[a], True) for a in TARGETS}
    CELLS = [(a, h) for a in TARGETS for h in HORIZONS[a]]
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        rs = [d["r"] for d in cd]
        Ms = [d["h"][h]["M"] for d in cd]
        print(f"  {NAME[a]} {h:2d}봉: 판단일 {len(cd)}(독립 약 {len(cd) / h:.0f}) {cd[0]['date']} ~ {cd[-1]['date']} | r < 1 비율 {sum(r < 1 for r in rs) / len(rs):.3f}"
              f" · r 가운데 {median(rs):.3f} · r 5–95% {pct_int(rs)[0]:.2f}–{pct_int(rs)[1]:.2f} | M 가운데 {median(Ms):.3f} · M 평균 {fmean(Ms):.3f}"
              f" | r60 < 1 비율 {sum(d['r60'] < 1 for d in cd) / len(cd):.3f} · r_rob < 1 비율 {sum(d['rrob'] < 1 for d in cd) / len(cd):.3f}")

    # ---------- [E2] A1 판단 때 아는 값
    print("=" * 112)
    print("[E2] A1 판단 때 아는 값 — 우리 기록 판단 날의 reading(세 대상)을 그날 decided_at에 끝난 봉으로 다시 계산해 견줌(P-0003 [E2]와 같은 방법)")
    runs_ = {day_of(r["run"]): dt.datetime.fromisoformat(r["steps"]["decided_at"]) for r in jl("run") if r.get("mode") == "live" and r.get("steps", {}).get("decided_at")}
    same = diff = late = 0
    rows_all = {a: load_rows(a) for a in TARGETS}
    fin_all = {a: [C.final_dt(C.ASSETS[a], r.date) for r in rows_all[a]] for a in TARGETS}
    for r in jl("reading"):
        if r["asset"] not in TARGETS or day_of(r["run"]) not in runs_:
            continue
        at = runs_[day_of(r["run"])]
        k = bisect.bisect_right(fin_all[r["asset"]], at)
        v, _ = pieces.read([x.close for x in rows_all[r["asset"]][:k]][-pieces.NEED:])
        ok = all(v[s] == r["values"].get(s) for s in pieces.STATES)
        same += ok
        diff += not ok
        late += C.final_dt(C.ASSETS[r["asset"]], day_of(r["known_through"])) > at
    print(f"  reading 줄 같음 {same} · 다름 {diff} · 본 마지막 봉이 판단 뒤에 끝난 줄 {late}")
    print("  구조: r·r60·r_rob·vol250·상태는 bisect(final_dt ≤ 판단 시각)로 자른 끝난 봉 251개만, 행동의 높이와 L06 절차의 지난 빗나감은 목표 봉 final_dt ≤ 판단 시각인 창만")

    # ---------- [E3] 모양 표
    print("=" * 112)
    print("[E3] 모양 — r 칸(경계 " + " · ".join(map(str, BINS)) + ")마다 M의 가운데 값(날 수). 이 이론이면 r < 1 쪽은 긴 기간일수록 평평하고 r > 1 쪽은 오른다")
    edges = (0.0,) + BINS + (99.0,)
    labs = [f"{edges[i]:.2f}–{edges[i + 1]:.2f}" if i < len(edges) - 2 else f"≥{edges[i]:.2f}" for i in range(len(edges) - 1)]
    print(f"  {'칸':10s} " + " ".join(f"{l:>15s}" for l in labs))
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        parts = []
        for i in range(len(edges) - 1):
            v = [d["h"][h]["M"] for d in cd if edges[i] <= d["r"] < edges[i + 1]]
            parts.append(f"{median(v):.3f}({len(v):4d})" if v else "–")
        print(f"  {NAME[a]} {h:2d}봉   " + " ".join(f"{p:>15s}" for p in parts))

    # ---------- [E4] 효과: 꺾인 기울기
    print("=" * 112)
    print(f"[E4] 효과 — 가운데 값 회귀 ln M = a + β−·min(ln r,0) + β+·max(ln r,0). 괄호는 60일 덩어리 부트스트랩 {BOOT}번 5–95%. g = β+ − β−."
          " '계산기 뺀 나머지' = ln M − ln(계산기 곱하기)로 같은 회귀(4-2 (ㄱ))")
    EFF = {}
    tb = time.time()
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        lr = [math.log(d["r"]) for d in cd]
        y = [ylog(d["h"][h]["M"]) for d in cd]
        est = hinge(lr, y)
        bs = []
        for _ in range(BOOT):
            idx = blocks_boot(len(cd), rng)
            bs.append(hinge([lr[i] for i in idx], [y[i] for i in idx], iters=15, init=est))
        bm_i, bp_i = pct_int([b[1] for b in bs]), pct_int([b[2] for b in bs])
        g_i = pct_int([b[2] - b[1] for b in bs])
        yres = [yy - math.log(d["h"][h]["mcalc"]) for yy, d in zip(y, cd)]
        res = hinge(lr, yres)
        EFF[(a, h)] = {"a": est[0], "bm": est[1], "bp": est[2], "g": est[2] - est[1], "bm_i": bm_i, "bp_i": bp_i, "g_i": g_i, "res": res}
        print(f"  {NAME[a]} {h:2d}봉: β− {est[1]:+.3f} ({bm_i[0]:+.2f}, {bm_i[1]:+.2f}) | β+ {est[2]:+.3f} ({bp_i[0]:+.2f}, {bp_i[1]:+.2f})"
              f" | g {est[2] - est[1]:+.3f} ({g_i[0]:+.2f}, {g_i[1]:+.2f}) | w_h {w_of(h):.3f} | 계산기 뺀 나머지 β− {res[1]:+.3f} · β+ {res[2]:+.3f}")
    print(f"  (부트스트랩 {time.time() - tb:.0f}초)")
    # 예측 셈
    print("  -- 5절 예측 셈")
    e1 = sum(1 for k, v in EFF.items() if 0 < v["bp"] < 1 and v["bp_i"][0] > 0)
    e1_le0 = sum(1 for v in EFF.values() if v["bp"] <= 0)
    e1_ge1 = sum(1 for v in EFF.values() if v["bp"] >= 1)
    print(f"  E1: 0 < β+ < 1이고 구간 아래 끝 > 0인 칸 {e1}/9 · β+ ≤ 0인 칸 {e1_le0} · β+ ≥ 1인 칸 {e1_ge1}")
    for a in TARGETS:
        s, l = EFF[(a, 1)], EFF[(a, LONG[a])]
        mid = EFF[(a, HORIZONS[a][1])]
        print(f"  E2 {NAME[a]}: β+ 1봉 {s['bp']:+.3f} → 중간 {mid['bp']:+.3f} → 긴 {l['bp']:+.3f} ({'줄어듦' if s['bp'] > l['bp'] else '안 줄어듦'})"
              f" | β− 1봉 {s['bm']:+.3f} → 중간 {mid['bm']:+.3f} → 긴 {l['bm']:+.3f} ({'줄어듦' if s['bm'] > l['bm'] else '안 줄어듦'})")
        print(f"  E3 {NAME[a]}: g 긴 {l['g']:+.3f}({'> 0' if l['g'] > 0 else '≤ 0'}) · g 1봉 {s['g']:+.3f} → g(긴) − g(1) {l['g'] - s['g']:+.3f}"
              f" · β−(긴) {l['bm']:+.3f}({'≤ 0.3' if l['bm'] <= 0.3 else '> 0.3'})")

    # ---------- [E5] 범위 꼴 — 규칙마다 높이를 맞춤
    print("=" * 112)
    print("[E5] 범위 꼴(보조 B5·B6) — 규칙마다·칸마다 높이 하나를 같은 자료에서 맞춘 2배 폭 적중(위로 · 아래로 빗나감). '계산기 그대로' = 높이까지 2026-06 맞춘 것."
          " 차이(R4 − 다른 꼴)는 %p, 뒤집기 비율은 R4가 나은 쪽이 우연히 그만큼 나올 비율")
    HITS = {}
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        line, sds = [], {}
        for rule in RULES:
            sd, _ = fitted_sides(cd, h, rule)
            sds[rule] = sd
            hi, up, dn = hitstat(sd)
            HITS[(a, h, rule)] = hi
            line.append(f"{rule} {hi:.3f}({up:.2f}·{dn:.2f})")
        cs = calc_sides(cd, h)
        hi, up, dn = hitstat(cs)
        HITS[(a, h, "calc")] = hi
        line.append(f"계산기 그대로 {hi:.3f}({up:.2f}·{dn:.2f})")
        diffs = {k: [(sds["R4"][i] == 0) - (sds[k][i] == 0) for i in range(len(cd))] for k in ("R0", "R1", "R2", "R3")}
        ps, _ = flip_p([diffs[k] for k in ("R0", "R1", "R2", "R3")], rng)
        print(f"  {NAME[a]} {h:2d}봉: " + " | ".join(line))
        print(f"           R4 − R0 {pp(fmean(diffs['R0']))} · − R1 {pp(fmean(diffs['R1']))} · − R2 {pp(fmean(diffs['R2']))} · − R3 {pp(fmean(diffs['R3']))}"
              f" | 뒤집기 비율 대 R0 {ps[0]:.3f} · 대 R1 {ps[1]:.3f} · 대 R2 {ps[2]:.3f} · 대 R3 {ps[3]:.3f}")
    print("  -- 9칸 같은 무게 평균 적중: " + " · ".join(f"{RULE_NAME[r]} {fmean(HITS[(a, h, r)] for a, h in CELLS):.4f}" for r in RULES)
          + f" · 계산기 그대로 {fmean(HITS[(a, h, 'calc')] for a, h in CELLS):.4f}")
    for k in ("R0", "R1", "R2", "R3"):
        ds = [HITS[(a, h, "R4")] - HITS[(a, h, k)] for a, h in CELLS]
        print(f"  -- R4 − {k}: 칸 + {sum(d > 1e-12 for d in ds)} / 0 {sum(abs(d) <= 1e-12 for d in ds)} / − {sum(d < -1e-12 for d in ds)}, 평균 {pp(fmean(ds))}%p")

    # ---------- [E6] w 격자
    print("=" * 112)
    print("[E6] w 격자 — 이론 꼴 √(w r² + 1 − w)에서 w만 바꾼 적중(높이는 w마다 맞춤). * = 그 칸의 가장 높은 값, [ ] = 미리 정한 w_h에 가장 가까운 칸")
    print(f"  {'칸':8s} " + " ".join(f"{w:>6.1f}" for w in W_GRID) + "   가장 좋은 w | 미리 정한 w_h 적중 | 가장 좋은 것 − 미리 정한 것 | w 격자 11개 가운데 가장 좋은 것 대 R1 뒤집기 비율")
    BESTW = {}
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        hits, series = [], []
        r1, _ = fitted_sides(cd, h, "R1")
        for w in W_GRID:
            sd, _ = fitted_sides(cd, h, f"W{w}")
            hits.append(hitstat(sd)[0])
            series.append([(sd[i] == 0) - (r1[i] == 0) for i in range(len(cd))])
        _, pbest = flip_p(series, rng)
        bi = max(range(len(W_GRID)), key=lambda i: hits[i])
        near = min(range(len(W_GRID)), key=lambda i: abs(W_GRID[i] - w_of(h)))
        BESTW[(a, h)] = W_GRID[bi]
        cells_txt = []
        for i, hv in enumerate(hits):
            t = f"{hv:.3f}" + ("*" if i == bi else "")
            cells_txt.append(f"[{t}]" if i == near else t)
        print(f"  {NAME[a]} {h:2d}봉 " + " ".join(f"{t:>6s}" for t in cells_txt)
              + f"   {W_GRID[bi]:.1f} | {HITS[(a, h, 'R4')]:.3f} | {pp(hits[bi] - HITS[(a, h, 'R4')])}%p | {pbest:.3f}")

    # ---------- [E7] 4-4 앞 근거 다시 하기 — P-0003 L12
    print("=" * 112)
    print("[E7] 4-4 P-0003 L12 다시 하기 — 긴 기간 & r < 1인 날, 맞추는(목표 ≤ 2026-06-30)·떼어 둔(시작 ≥ 07-01 & 목표 ≤ 10-19) 구간."
          " (가) P-0003 방법 그대로: 계산기 그대로 − 계산기 × r / × √r. (나) 높이를 규칙마다 그 칸 전부에서 맞춘 뒤 같은 날들에서: R0 − (m = r) / (m = √r) / R4")
    for a in TARGETS:
        h = LONG[a]
        cd = cell_days(DAYS[a], h)

        def per(d):
            x = d["h"][h]
            if x["tdate"] <= FIT_END:
                return "fit"
            if d["date"] >= P3_HOLD_START and x["tdate"] <= P3_HOLD_END:
                return "hold"
            return None
        dom = [i for i, d in enumerate(cd) if d["r"] < 1 and per(d)]
        # (가)
        out = []
        for lab in ("fit", "hold"):
            ii = [i for i in dom if per(cd[i]) == lab]
            if not ii:
                continue
            res = {}
            for nm, f in (("계산기", lambda d: 1.0), ("× r", lambda d: d["r"]), ("× √r", lambda d: math.sqrt(d["r"])),
                          ("× 이론", lambda d: math.sqrt(w_of(h) * d["r"] ** 2 + 1 - w_of(h)))):
                hit = 0
                for i in ii:
                    x = cd[i]["h"][h]
                    m = f(cd[i])
                    hit += x["lo"] * m <= x["abs"] <= x["hi"] * m
                res[nm] = hit / len(ii)
            out.append(f"{'맞추는' if lab == 'fit' else '떼어 둔'}({len(ii)}일): 계산기 {res['계산기']:.3f} · − × r {pp(res['계산기'] - res['× r'])} · − × √r {pp(res['계산기'] - res['× √r'])}"
                       f" · 계산기 × 이론 m {res['× 이론']:.3f}(계산기 대비 {pp(res['× 이론'] - res['계산기'])})")
        print(f"  (가) {NAME[a]} {h}봉: " + " | ".join(out))
        # (나)
        lvl = {}
        for nm, rule_fn in (("R0", lambda d: 0.0), ("r", lambda d: math.log(d["r"])), ("√r", lambda d: 0.5 * math.log(d["r"])),
                            ("R4", lambda d: shape_logm("R4", d, h))):
            z = [ylog(d["h"][h]["M"]) - rule_fn(d) for d in cd]
            c = best_level(z)
            lvl[nm] = [abs(v - c) <= LW + 1e-12 for v in z]
        out = []
        for lab in ("fit", "hold"):
            ii = [i for i in dom if per(cd[i]) == lab]
            if not ii:
                continue
            hr = {nm: sum(lvl[nm][i] for i in ii) / len(ii) for nm in lvl}
            out.append(f"{'맞추는' if lab == 'fit' else '떼어 둔'}({len(ii)}일): R0 {hr['R0']:.3f} · R0 − r {pp(hr['R0'] - hr['r'])} · R0 − √r {pp(hr['R0'] - hr['√r'])} · R4 − R0 {pp(hr['R4'] - hr['R0'])}")
        print(f"  (나) {NAME[a]} {h}봉: " + " | ".join(out))

    # ---------- [E8] 4-2 (ㄴ) L06 방아쇠가 r 밖의 정보를 갖나
    print("=" * 112)
    print("[E8] 4-2 (ㄴ)·A10 L06 방아쇠 — 계산기 그대로 범위로, 판단 때 끝난 겹치지 않는 지난 세 예측이 모두 위(up3)·모두 아래(dn3)로 빗나간 날."
          " 가운데 값 회귀 ln M = a + β−·min + β+·max + γu·up3 + γd·dn3. L06이면 γu > 0 · γd < 0, 이 이론이면 둘 다 ≈ 0")
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        hist, pend, flags = [], [], []
        for i, d in enumerate(cd):
            x = d["h"][h]
            keep = []
            for (q, s, tf) in pend:
                if tf <= d["at"]:
                    hist.append((q, s))
                else:
                    keep.append((q, s, tf))
            pend = keep
            picked = []
            for (q, s) in reversed(hist):
                if not picked or picked[-1][0] - q >= h:
                    picked.append((q, s))
                if len(picked) == 3:
                    break
            sd = [s for _, s in picked]
            flags.append((1 if len(sd) == 3 and sd == [1, 1, 1] else 0, 1 if len(sd) == 3 and sd == [-1, -1, -1] else 0))
            s_ = 0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1)
            pend.append((i, s_, x["tfinal"]))
        X = [[1.0, min(math.log(d["r"]), 0.0), max(math.log(d["r"]), 0.0), fu, fd] for d, (fu, fd) in zip(cd, flags)]
        y = [ylog(d["h"][h]["M"]) for d in cd]
        b = lad(X, y)
        est = EFF[(a, h)]
        res = [yy - (est["a"] + est["bm"] * xx[1] + est["bp"] * xx[2]) for yy, xx in zip(y, X)]
        nu, nd = sum(f[0] for f in flags), sum(f[1] for f in flags)
        su = [r_ for r_, f in zip(res, flags) if f[0]]
        sdn = [r_ for r_, f in zip(res, flags) if f[1]]
        # 결과를 본 뒤 더함(6절): 표시한 날 나머지 가운데 값의 60일 덩어리 부트스트랩 5–95%(회귀 선은 고정)
        bu, bd = [], []
        for _ in range(BOOT):
            idx = blocks_boot(len(cd), rng)
            u_ = [res[i] for i in idx if flags[i][0]]
            d_ = [res[i] for i in idx if flags[i][1]]
            if u_:
                bu.append(median(u_))
            if d_:
                bd.append(median(d_))
        iu = f"({pct_int(bu)[0]:+.2f}, {pct_int(bu)[1]:+.2f})" if len(bu) > 20 else ""
        idn = f"({pct_int(bd)[0]:+.2f}, {pct_int(bd)[1]:+.2f})" if len(bd) > 20 else ""
        runs_u = sum(1 for i in range(len(flags)) if flags[i][0] and (i == 0 or not flags[i - 1][0]))
        print(f"  {NAME[a]} {h:2d}봉: up3 {nu}일(이어진 덩이 {runs_u}) · dn3 {nd}일 | γu {b[3]:+.3f} · γd {b[4]:+.3f} | 꺾인 회귀 나머지의 가운데 값: up3 날 {f3(median(su) if su else None)} {iu}"
              f"(+ 비율 {sum(v > 0 for v in su) / len(su) if su else float('nan'):.2f}) · dn3 날 {f3(median(sdn) if sdn else None)} {idn}(+ 비율 {sum(v > 0 for v in sdn) / len(sdn) if sdn else float('nan'):.2f})")

    # ---------- [E9] 메커니즘 Q1 — 실현 흔들림 RV_h
    print("=" * 112)
    print("[E9] 메커니즘 Q1 — 같은 꺾인 회귀를 ln(RV_h ÷ (vol250 √h))에. 방향 몫 D = 1 − [β_RV(1) − β_RV(긴)] ÷ [β_|R|(1) − β_|R|(긴)] (r ≥ 1 쪽). Q1a면 D ≈ 0, Q1b면 D ≈ 1")
    RVB = {}
    for a, h in CELLS:
        cd = [d for d in cell_days(DAYS[a], h) if d["h"][h]["RV"] is not None]
        lr = [math.log(d["r"]) for d in cd]
        y = [ylog(d["h"][h]["RV"]) for d in cd]
        e = hinge(lr, y)
        RVB[(a, h)] = e
        print(f"  {NAME[a]} {h:2d}봉: RV 기울기 β− {e[1]:+.3f} · β+ {e[2]:+.3f} | |R| 기울기 β− {EFF[(a, h)]['bm']:+.3f} · β+ {EFF[(a, h)]['bp']:+.3f}")
    for a in TARGETS:
        s, l = 1, LONG[a]
        den = EFF[(a, s)]["bp"] - EFF[(a, l)]["bp"]
        num = RVB[(a, s)][2] - RVB[(a, l)][2]
        dm = EFF[(a, s)]["bm"] - EFF[(a, l)]["bm"]
        nm = RVB[(a, s)][1] - RVB[(a, l)][1]
        print(f"  D {NAME[a]}: r ≥ 1 쪽 " + (f"{1 - num / den:+.2f}" if den > 0 else "분모 ≤ 0 — 셈에서 뺌")
              + f" (|R| 감소 {den:+.3f}, RV 감소 {num:+.3f}) | r < 1 쪽 " + (f"{1 - nm / dm:+.2f}" if dm > 0 else "분모 ≤ 0") + f" (|R| 감소 {dm:+.3f}, RV 감소 {nm:+.3f})")

    # ---------- [E10] 메커니즘 Q2
    print("=" * 112)
    print("[E10] 메커니즘 Q2 — (가) Q2a: 꼴 √(w r² + 1 − w)를 양쪽 따로(w−, w+, 높이 하나) 가운데 값 회귀, 격자 0.05. 해마다(판단일 100일 이상) w− < w+인 해."
          " (나) Q2b: r < 1인 날 ln M = a + β ln r + δ[r60 < 1]; 같은 쪽 안 M 위 10%의 내림 비율. (다) Q2c: r_rob로 꺾인 회귀")
    def wfit(cd, h):
        y = [ylog(d["h"][h]["M"]) for d in cd]
        neg = [d["r"] < 1 for d in cd]
        f = {w: [0.5 * math.log(w * d["r"] ** 2 + 1 - w) for d in cd] for w in W_MECH}
        best = None
        for wm in W_MECH:
            for wp in W_MECH:
                z = [yy - (f[wm][i] if neg[i] else f[wp][i]) for i, yy in enumerate(y)]
                c = median(z)
                loss = sum(abs(v - c) for v in z)
                if best is None or loss < best[0] - 1e-12:
                    best = (loss, wm, wp)
        return best[1], best[2]
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        wm, wp = wfit(cd, h)
        yrs = defaultdict(list)
        for d in cd:
            yrs[d["date"].year].append(d)
        lt = tot = 0
        ytxt = []
        if h == LONG[a] or h == 1:
            for yv, dd in sorted(yrs.items()):
                if len(dd) < 100 or not any(d["r"] < 1 for d in dd) or not any(d["r"] >= 1 for d in dd):
                    continue
                a1, b1 = wfit(dd, h)
                tot += 1
                lt += a1 < b1
                ytxt.append(f"{yv} {a1:.2f}/{b1:.2f}")
        print(f"  (가) {NAME[a]} {h:2d}봉: w− {wm:.2f} · w+ {wp:.2f} · w+ − w− {wp - wm:+.2f} | 미리 정한 w_h {w_of(h):.3f}"
              + (f" | 해마다 w− < w+ {lt}/{tot}: " + ", ".join(ytxt) if tot else ""))
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        q = [d for d in cd if d["r"] < 1]
        X = [[1.0, math.log(d["r"]), 1.0 if d["r60"] < 1 else 0.0] for d in q]
        y = [ylog(d["h"][h]["M"]) for d in q]
        b = lad(X, y)
        n1 = sum(1 for d in q if d["r60"] < 1)

        def down_share(ds):
            if not ds:
                return None, 0
            ms = sorted(ds, key=lambda d: d["h"][h]["M"], reverse=True)[:max(1, len(ds) // 10)]
            return sum(1 for d in ms if d["h"][h]["R"] < 0) / len(ms), len(ms)
        dq, nq = down_share(q)
        dl, nl = down_share([d for d in cd if d["r"] >= 1])
        print(f"  (나) {NAME[a]} {h:2d}봉: r < 1 {len(q)}일(r60 < 1 {n1}) δ {b[2]:+.3f} (β {b[1]:+.3f}) | 위 10% 내림 비율 r < 1 쪽 {dq:.2f}({nq}) · r ≥ 1 쪽 {dl:.2f}({nl})")
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        lr = [math.log(d["rrob"]) for d in cd]
        y = [ylog(d["h"][h]["M"]) for d in cd]
        e = hinge(lr, y)
        base = EFF[(a, h)]
        print(f"  (다) {NAME[a]} {h:2d}봉: r_rob β− {e[1]:+.3f} · β+ {e[2]:+.3f} · g {e[2] - e[1]:+.3f} | r(표준편차) β− {base['bm']:+.3f} · g {base['g']:+.3f}"
              f" | r_rob < 1 비율 {sum(d['rrob'] < 1 for d in cd) / len(cd):.3f}")

    # ---------- [E11] A3 이미 아는 것을 뺀 나머지
    print("=" * 112)
    print("[E11] A3 — (가) S_big_move '아니오'인 날만, S_deep_drawdown '아니오'인 날만의 꺾인 회귀. (나) 범위 R4 − R1(높이는 칸 전부에서 맞춤)을 S_vol_high 예/아니오로 나눔")
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        out = []
        for st in ("S_big_move", "S_deep_drawdown"):
            q = [d for d in cd if d["vals"][st] == "아니오"]
            e = hinge([math.log(d["r"]) for d in q], [ylog(d["h"][h]["M"]) for d in q])
            out.append(f"{st}=아니오 {len(q)}일 β− {e[1]:+.3f} · β+ {e[2]:+.3f}")
        s4, _ = fitted_sides(cd, h, "R4")
        s1, _ = fitted_sides(cd, h, "R1")
        sp = []
        for v in ("아니오", "예"):
            ii = [i for i, d in enumerate(cd) if d["vals"]["S_vol_high"] == v]
            sp.append(f"S_vol_high={v} {pp(fmean((s4[i] == 0) - (s1[i] == 0) for i in ii)) if ii else '–'}({len(ii)})")
        print(f"  {NAME[a]} {h:2d}봉: " + " | ".join(out) + " | R4 − R1: " + " · ".join(sp))

    # ---------- [E12] A7 공통 분모, A8 겹치지 않는 표본
    print("=" * 112)
    print("[E12] A7 — ln(|R_h| ÷ √h) = a + b·ln vol250 + β−·min(ln r,0) + β+·max(ln r,0) (vol250 계수를 풀어 둠). A8 — h 간격으로 고른 겹치지 않는 표본, 출발 위치 h개의 β± 가운데 값 [가장 작은 ~ 가장 큰]")
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        X = [[1.0, math.log(d["v250"]), min(math.log(d["r"]), 0.0), max(math.log(d["r"]), 0.0)] for d in cd]
        y = [math.log(max(abs(d["h"][h]["R"]), M_FLOOR * d["h"][h]["unit"]) / math.sqrt(h)) for d in cd]
        b = lad(X, y)
        bms, bps = [], []
        for o in range(h):
            q = cd[o::h]
            if len(q) < 30:
                continue
            e = hinge([math.log(d["r"]) for d in q], [ylog(d["h"][h]["M"]) for d in q])
            bms.append(e[1])
            bps.append(e[2])
        print(f"  {NAME[a]} {h:2d}봉: A7 b {b[1]:+.3f} · β− {b[2]:+.3f} · β+ {b[3]:+.3f} (본 회귀 β− {EFF[(a, h)]['bm']:+.3f} · β+ {EFF[(a, h)]['bp']:+.3f})"
              f" | A8 출발 {len(bps)}개, 표본 약 {len(cd) // h}: β− {median(bms):+.3f} [{min(bms):+.2f} ~ {max(bms):+.2f}] · β+ {median(bps):+.3f} [{min(bps):+.2f} ~ {max(bps):+.2f}]")

    # ---------- [E13] A5 한 구간에 기대나
    print("=" * 112)
    print("[E13] A5 — (가) 해마다(판단일 100일 이상) β−/β+, (나) R4 − R1 적중 차이에 가장 크게 보탠 60일 덩어리를 뺀 평균, (다) 시기가 바뀐 구간(판단 2026-07-01 ~ 10-19)의 β±와 적중(높이는 칸 전부에서 맞춤)")
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        yrs = defaultdict(list)
        for d in cd:
            yrs[d["date"].year].append(d)
        yt, pos, gp, cnt = [], 0, 0, 0
        for yv, dd in sorted(yrs.items()):
            if len(dd) < 100:
                continue
            e = hinge([math.log(d["r"]) for d in dd], [ylog(d["h"][h]["M"]) for d in dd])
            yt.append(f"{yv} {e[1]:+.2f}/{e[2]:+.2f}")
            cnt += 1
            pos += e[2] > 0
            gp += e[2] > e[1]
        print(f"  (가) {NAME[a]} {h:2d}봉: β+ > 0 {pos}/{cnt} · β+ > β− {gp}/{cnt} | " + ", ".join(yt))
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        s4, _ = fitted_sides(cd, h, "R4")
        s1, _ = fitted_sides(cd, h, "R1")
        s0, _ = fitted_sides(cd, h, "R0")
        dd = [(s4[i] == 0) - (s1[i] == 0) for i in range(len(cd))]
        tot = sum(dd)
        sg = 1 if tot >= 0 else -1
        bl = [(sum(dd[i:i + BLOCK]), i) for i in range(0, len(dd), BLOCK)]
        bsum, i0 = max(bl, key=lambda t: t[0] * sg)
        rest = len(dd) - len(dd[i0:i0 + BLOCK])
        ii = [i for i, d in enumerate(cd) if SHIFT_START <= d["date"] <= SHIFT_END]
        sh = ""
        if ii:
            e = hinge([math.log(cd[i]["r"]) for i in ii], [ylog(cd[i]["h"][h]["M"]) for i in ii]) if len(ii) >= 30 else None
            cs = calc_sides(cd, h)
            sh = (f" | (다) {len(ii)}일 r 가운데 {median(cd[i]['r'] for i in ii):.2f}" + (f" β− {e[1]:+.2f} · β+ {e[2]:+.2f}" if e else "")
                  + f" · 적중 R4 {fmean(s4[i] == 0 for i in ii):.3f} · R1 {fmean(s1[i] == 0 for i in ii):.3f} · R0 {fmean(s0[i] == 0 for i in ii):.3f} · 계산기 그대로 {fmean(cs[i] == 0 for i in ii):.3f}"
                  + f" · R4 위/아래 {fmean(s4[i] == 1 for i in ii):.2f}/{fmean(s4[i] == -1 for i in ii):.2f}")
        print(f"  (나) {NAME[a]} {h:2d}봉: R4 − R1 {pp(tot / len(dd))} → 덩어리({cd[i0]['date']} ~) 빼면 {pp((tot - bsum) / rest) if rest else '–'}" + sh)

    # ---------- [E14] A9 종목
    print("=" * 112)
    stock_codes = sorted(p.stem for p in (ROOT / "data" / "prices").glob("*.csv") if p.stem.isdigit() and p.stem not in TARGETS)
    print(f"[E14] A9 다른 대상 — 국내 종목 {len(stock_codes)}개(시세 2024-05 ~), 기간 1·5·20봉. (가) 묶어서 꺾인 회귀(같은 날 종목들은 한 사건 — 독립은 날 수), (나) 종목마다 회귀의 셈")
    SDAYS = {}
    for code in stock_codes:
        try:
            SDAYS[code] = prepare(code, STOCK_H, False)
        except Exception as ex:  # 시세 모양이 다른 종목은 빼고 적는다
            print(f"  {code}: 뺌({type(ex).__name__})")
    for h in STOCK_H:
        lr, y, dates = [], [], set()
        per_stock = []
        for code, days in SDAYS.items():
            cd = cell_days(days, h)
            if len(cd) < 100:
                continue
            l_ = [math.log(d["r"]) for d in cd]
            y_ = [ylog(d["h"][h]["M"]) for d in cd]
            lr += l_
            y += y_
            dates |= {d["date"] for d in cd}
            per_stock.append(hinge(l_, y_, iters=20))
        e = hinge(lr, y)
        bp_pos = sum(1 for v in per_stock if v[2] > 0)
        bp_lt1 = sum(1 for v in per_stock if v[2] < 1)
        g_pos = sum(1 for v in per_stock if v[2] > v[1])
        print(f"  {h:2d}봉 (가) 종목-날 {len(y)} · 날 {len(dates)} · r < 1 비율 {sum(v < 0 for v in lr) / len(lr):.3f} | β− {e[1]:+.3f} · β+ {e[2]:+.3f} · g {e[2] - e[1]:+.3f}"
              f" | (나) 종목 {len(per_stock)}: β+ > 0 {bp_pos} · β+ < 1 {bp_lt1} · β+ > β− {g_pos} · β− 가운데 {median(v[1] for v in per_stock):+.3f} · β+ 가운데 {median(v[2] for v in per_stock):+.3f}")

    # ---------- [E15] A6 우리 기록
    print("=" * 112)
    print("[E15] A6 우리 기록 — 판단 ≥ 2026-10-21, 목표 ≤ 시세 끝. R4 = 판단 때 끝난 창만으로 맞춘 높이(행동과 같음), 계산기 그대로, G = 지금 손 규칙(충돌대로 L06-T1S1),"
          " 손 = score.jsonl 한 번만 센 손 예측(체결가 기준 줄만). ○ 적중 · ↑ 위로 · ↓ 아래로 빗나감. # = 세운 재료")
    sc = {}
    for r in jl("score"):
        if r.get("counted") and r.get("predictor") == "hand":
            sc[r["prediction"]] = r
    preds, pred_hi = {}, {}
    for r in jl("prediction"):
        if r.get("predictor") == "hand" and r.get("base_kind") == "fill":
            preds[(r["asset"], r["horizon"], r["base_date"])] = r["id"]
            pred_hi[r["id"]] = r.get("size_hi")
    mark = {0: "○", 1: "↑", -1: "↓", None: "–"}
    REC_ROWS = []
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        ex = expanding_sides(cd, h, "R4")
        cs = calc_sides(cd, h)
        gu, _ = run_l06(DAYS[a], h, "T1", "S1", gate_g(a, h))
        gs = []
        for d in cd:
            lo, hi = gu[d["j"]]
            ab = d["h"][h]["abs"]
            gs.append(0 if lo <= ab <= hi else (1 if ab > hi else -1))
        ii = [i for i, d in enumerate(cd) if d["date"] >= REC_START]
        if not ii:
            continue
        hand = []
        for i in ii:
            d = cd[i]
            pid = preds.get((a, h, d["date"].isoformat()))
            s = sc.get(pid) if pid else None
            if s is None or s.get("size_hit") is None:
                hand.append(None)
                continue
            if s["size_hit"]:
                hand.append(0)
            else:
                hand.append(1 if abs(s["ret"]) * 100 > float(pred_hi[pid]) else -1)
        cnt = lambda arr: (sum(1 for v in arr if v == 0), sum(1 for v in arr if v is not None))
        seeds = [i for i in ii if (a, h, cd[i]["date"]) in SEEDS]
        print(f"  {NAME[a]} {h:2d}봉: {len(ii)}일(독립 약 {len(ii) / h:.1f}) | 적중 R4 {cnt([ex[i] for i in ii])} · 계산기 {cnt([cs[i] for i in ii])} · G {cnt([gs[i] for i in ii])} · 손 {cnt(hand)}"
              + (f" | 세운 재료 {len(seeds)}" if seeds else ""))
        for q, i in enumerate(ii):
            d = cd[i]
            row = (a, h, d["date"], d["r"], d["h"][h]["M"], d["h"][h]["R"], ex[i], cs[i], gs[i], hand[q], SEEDS.get((a, h, d["date"]), ""))
            REC_ROWS.append(row)
    print("  -- 날마다(긴 기간은 모두, 짧은 기간은 R4와 계산기·손이 갈린 날과 세운 재료만): 대상 기간 판단일 r M 수익 | R4 계산기 G 손 | 재료")
    for (a, h, dd, r_, M_, R_, e_, c_, g_, hd, sd_) in REC_ROWS:
        if h == LONG[a] or sd_ or e_ != c_ or (hd is not None and e_ != hd):
            print(f"   {NAME[a]} {h:2d} {dd} r {r_:.2f} M {M_:.2f} {R_ * 100:+6.2f}% | {mark[e_]} {mark[c_]} {mark[g_]} {mark[hd]} | {('#' + sd_) if sd_ else ''}")

    # ---------- [E16] 행동 — 판단 때 끝난 창만으로
    print("=" * 112)
    print(f"[E16] 행동(9절) — 판단 때 끝난 창만으로 {REFIT_EVERY} 판단일마다 맞춘 높이(끝난 창 {MIN_FIT}개 이상)의 R4, 계산기 그대로, G(지금 손 규칙). 셋 다 있는 날만. 적중(위·아래)")
    allrow, recrow, postrow = defaultdict(list), defaultdict(list), defaultdict(list)
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        ex = expanding_sides(cd, h, "R4")
        e0 = expanding_sides(cd, h, "R0")
        e1 = expanding_sides(cd, h, "R1")   # 결과를 본 뒤 더함(6절): 계산기 꼴도 같은 정직한 높이로
        cs = calc_sides(cd, h)
        gu, _ = run_l06(DAYS[a], h, "T1", "S1", gate_g(a, h))
        gs = []
        for d in cd:
            lo, hi = gu[d["j"]]
            ab = d["h"][h]["abs"]
            gs.append(0 if lo <= ab <= hi else (1 if ab > hi else -1))
        ii = [i for i in range(len(cd)) if ex[i] is not None]
        arrs = (("R4", ex), ("R0", e0), ("R1", e1), ("calc", cs), ("G", gs))
        st = {nm: hitstat([arr[i] for i in ii]) for nm, arr in arrs}
        diff = [(ex[i] == 0) - (cs[i] == 0) for i in ii]
        ps, _ = flip_p([diff], rng)
        for nm in st:
            allrow[nm].append(st[nm][0])
        ri = [i for i in ii if cd[i]["date"] >= REC_START]
        pi = [i for i in ii if cd[i]["date"] >= SHIFT_START]
        for nm, arr in arrs:
            if ri:
                recrow[nm].append((sum(arr[i] == 0 for i in ri), len(ri)))
            if pi:
                postrow[nm].append(fmean(arr[i] == 0 for i in pi))
        print(f"  {NAME[a]} {h:2d}봉 ({len(ii)}일, {cd[ii[0]]['date']} ~): R4 {st['R4'][0]:.3f}({st['R4'][1]:.2f}·{st['R4'][2]:.2f}) · R0 {st['R0'][0]:.3f} · R1 {st['R1'][0]:.3f}"
              f" · 계산기 {st['calc'][0]:.3f}({st['calc'][1]:.2f}·{st['calc'][2]:.2f}) · G {st['G'][0]:.3f}({st['G'][1]:.2f}·{st['G'][2]:.2f})"
              f" | R4 − 계산기 {pp(fmean(diff))} (뒤집기 {ps[0]:.3f}) · R4 − G {pp(st['R4'][0] - st['G'][0])} · R4 − R1 {pp(st['R4'][0] - st['R1'][0])}"
              f" | 판단 ≥ 2026-07-01 {len(pi)}일: R4 {fmean(ex[i] == 0 for i in pi):.3f} · R1 {fmean(e1[i] == 0 for i in pi):.3f} · 계산기 {fmean(cs[i] == 0 for i in pi):.3f} · G {fmean(gs[i] == 0 for i in pi):.3f}")
    print("  -- 9칸 같은 무게 평균(전부): " + " · ".join(f"{nm} {fmean(v):.4f}" for nm, v in allrow.items()))
    print("  -- 9칸 같은 무게 평균(판단 ≥ 2026-07-01, 계산기 높이도 맞춘 뒤의 날): " + " · ".join(f"{nm} {fmean(v):.4f}" for nm, v in postrow.items()))
    print("  -- 우리 기록 날(9칸 합): " + " · ".join(f"{nm} {sum(x for x, _ in v)}/{sum(n for _, n in v)}" for nm, v in recrow.items()))

    # ---------- [E17] 결과를 본 뒤 더한 꼴 (6절)
    print("=" * 112)
    print("[E17] 결과를 본 뒤 더한 꼴(해 본 수에 셈, 같은 자료라 시험이 아님) — R4b = 이론 꼴을 r_rob로, R5 = r ≥ 1이면 계산기 계단·r < 1이면 이론 꼴로 좁힘."
          " (가) 높이를 같은 자료에서 맞춘 적중, (나) 정직한 높이(행동과 같음) 적중 — 전부 / 판단 ≥ 2026-07-01 / 시기가 바뀐 구간(판단 07-01 ~ 10-19)")
    agg = defaultdict(list)
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        out = []
        for rule in ("R1", "R4", "R4b", "R5"):
            sd, _ = fitted_sides(cd, h, rule)
            ex_ = expanding_sides(cd, h, rule)
            ii = [i for i in range(len(cd)) if ex_[i] is not None]
            pi = [i for i in ii if cd[i]["date"] >= SHIFT_START]
            si = [i for i in ii if SHIFT_START <= cd[i]["date"] <= SHIFT_END]
            v = (hitstat(sd)[0], fmean(ex_[i] == 0 for i in ii), fmean(ex_[i] == 0 for i in pi), fmean(ex_[i] == 0 for i in si))
            for q, x in enumerate(v):
                agg[(rule, q)].append(x)
            out.append(f"{rule} {v[0]:.3f} / {v[1]:.3f} · {v[2]:.3f} · {v[3]:.3f}")
        print(f"  {NAME[a]} {h:2d}봉: " + " | ".join(out))
    for q, lab in enumerate(("(가) 같은 자료 높이", "(나) 정직한 높이 전부", "(나) 판단 ≥ 07-01", "(나) 시기가 바뀐 구간")):
        print(f"  -- 9칸 평균 {lab}: " + " · ".join(f"{rule} {fmean(agg[(rule, q)]):.4f}" for rule in ("R1", "R4", "R4b", "R5")))

    # ---------- [E18] 결과를 본 뒤 더한 메커니즘 흔적 (6절)
    print("=" * 112)
    print("[E18] 결과를 본 뒤 더한 흔적 — r ≥ 1인 날에서 20봉 창의 한 날이 그 창 분산의 1/3 이상을 차지하면(J = 1) 뒤 움직임이 작은가:"
          " ln M = a + β ln r + κ·J (가운데 값 회귀). '단발 큰 날은 이어지지 않는다'면 κ < 0")
    for a, h in CELLS:
        cd = [d for d in cell_days(DAYS[a], h) if d["r"] >= 1]
        X = [[1.0, math.log(d["r"]), 1.0 if d["js"] >= 1 / 3 else 0.0] for d in cd]
        y = [ylog(d["h"][h]["M"]) for d in cd]
        b = lad(X, y)
        nj = sum(1 for x in X if x[2])
        print(f"  {NAME[a]} {h:2d}봉: r ≥ 1 {len(cd)}일 · J = 1 {nj}일 | κ {b[2]:+.3f} · β {b[1]:+.3f}")

    # ---------- [E19] 해 본 수
    print("=" * 112)
    print("[E19] 해 본 수 — 미리 정한 것: 효과 회귀 9칸(+ 종목 3) · 범위 꼴 5(R0 ~ R4) + 계산기 그대로 · w 격자 11 · 4-4 다시 하기 2방법 × 4 · L06 방아쇠 1 · 메커니즘 Q1 1, Q2 3"
          " · 공격 A3 2+1, A5 3, A7 1, A8 1, A9 2 · 행동 3규칙. 결과를 본 뒤 더한 것: 행동의 R0·R1(정직한 높이)·판단 ≥ 07-01 나눔, L06 방아쇠의 부트스트랩, 꼴 R4b·R5([E17]), 흔적 J([E18]) — 꼴 후보는 모두 7(R0 ~ R5, R4b) + w 격자 11")
    print(f"  격자 11개 가운데 가장 좋은 w(칸마다): " + ", ".join(f"{NAME[a]} {h} {BESTW[(a, h)]:.1f}" for a, h in CELLS))
    print(f"끝. ({time.time() - t0:.0f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
