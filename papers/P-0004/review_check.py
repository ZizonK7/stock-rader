"""P-0004 심사자 독립 검산 스크립트 (T-0001 v1, L06 ↔ L12).

저장소 루트(stock-rader/)에서:  python -X utf8 papers/P-0004/review_check.py
- 외부 패키지 없음(Python 3.11+ 표준 라이브러리만). 읽기만 하며 표준 출력만 낸다.
- 저자 스크립트(evidence.py)를 import하지 않고 시세 CSV·JSONL·기울기·범위 적중을 독립 재구현한다.
  (계산기 범위·시세 상태 대조만 저장소 코드 market.calc·market.pieces·market.config를 부른다.)
- 출력 표 번호 [R1]~[R7]이 papers/P-0004/review.md의 출처 번호다.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median, pstdev

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market import calc, config as C, pieces  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 상수 (저자 theory.md와 같은 경계)
RECORD_END = dt.date(2026, 11, 25)
PRICE_END = dt.date(2026, 11, 24)
JUDGE = dt.time(8, 40)
KST = C.KST
TARGETS = ("069500", "360750", "KRW-BTC")
NAME = {"069500": "국내", "360750": "해외", "KRW-BTC": "코인"}
HORIZONS = {"069500": (1, 5, 20), "360750": (1, 5, 20), "KRW-BTC": (1, 7, 28)}
LONG = {"069500": 20, "360750": 20, "KRW-BTC": 28}
STOCK_H = (1, 5, 20)
HALF_LIFE = 20
RHO = 0.5 ** (1 / HALF_LIFE)
M_FLOOR = 0.001
LW = math.log(2) / 2
BLOCK = 60
SEED = 20261125
FIT_END = dt.date(2026, 6, 30)
SHIFT_START = dt.date(2026, 7, 1)
REC_START = dt.date(2026, 10, 21)
REFIT_EVERY = 20
MIN_FIT = 250

SEED_L12 = {("KRW-BTC", 28, dt.date(2026, 10, 21)), ("069500", 20, dt.date(2026, 10, 22))}
SEED_L06 = {
    ("KRW-BTC", 7, dt.date(2026, 11, 9)), ("360750", 5, dt.date(2026, 11, 10)),
    ("069500", 5, dt.date(2026, 11, 11)), ("KRW-BTC", 1, dt.date(2026, 11, 16)),
    ("360750", 1, dt.date(2026, 11, 17)), ("KRW-BTC", 7, dt.date(2026, 11, 11)),
    ("003230", 1, dt.date(2026, 11, 18)), ("011200", 1, dt.date(2026, 11, 18)),
    ("360750", 1, dt.date(2026, 11, 19)), ("000270", 1, dt.date(2026, 11, 19)),
    ("KRW-BTC", 1, dt.date(2026, 11, 19)), ("KRW-BTC", 1, dt.date(2026, 11, 20)),
    ("KRW-BTC", 7, dt.date(2026, 11, 15)), ("KRW-BTC", 7, dt.date(2026, 11, 16)),
    ("360750", 5, dt.date(2026, 11, 18)),
}

PIECES, KNOW = calc.load_pieces()


# ---------------------------------------------------------------- 독립 읽기·기초 계산
def load_csv(asset: str) -> list[dict]:
    out = []
    with (ROOT / "data" / "prices" / f"{asset}.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = dt.date.fromisoformat(r["date"])
            if d <= PRICE_END:
                out.append({"date": d, "open": float(r["open"]), "close": float(r["close"])})
    return out


def read_jsonl(name: str) -> list[dict]:
    with (ROOT / "records" / f"{name}.jsonl").open(encoding="utf-8") as f:
        return [r for line in f if line.strip() and (r := json.loads(line))["run"] <= RECORD_END.isoformat()]


def w_of(h: int) -> float:
    return sum(RHO ** k for k in range(1, h + 1)) / h


def ylog(m: float) -> float:
    return math.log(m if m > M_FLOOR else M_FLOOR)


def pct90(vals: list[float]) -> tuple[float, float]:
    s = sorted(vals)
    return s[int(0.05 * (len(s) - 1))], s[int(0.95 * (len(s) - 1))]


def block_idx(n: int, rng: random.Random) -> list[int]:
    if n <= BLOCK:
        return [rng.randrange(n) for _ in range(n)]
    out: list[int] = []
    lim = n - BLOCK + 1
    while len(out) < n:
        s = rng.randrange(lim)
        out.extend(range(s, s + BLOCK))
    return out[:n]


def hinge_lad(lr: list[float], y: list[float], iters: int = 30, init: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    xm = [v if v < 0.0 else 0.0 for v in lr]
    xp = [v if v > 0.0 else 0.0 for v in lr]
    a, bm, bp = init if init is not None else (median(y), 0.0, 0.0)
    for _ in range(iters):
        sw = swm = swp = swmm = swpp = swy = swmy = swpy = 0.0
        for m_, p_, yy in zip(xm, xp, y):
            e = yy - a - bm * m_ - bp * p_
            ae = e if e >= 0.0 else -e
            w = 1.0 / (ae if ae > 1e-6 else 1e-6)
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
        if swmm > 0.0:
            num -= swm * swmy / swmm
            den -= swm * swm / swmm
        if swpp > 0.0:
            num -= swp * swpy / swpp
            den -= swp * swp / swpp
        a = num / den
        bm = (swmy - swm * a) / swmm if swmm > 0.0 else 0.0
        bp = (swpy - swp * a) / swpp if swpp > 0.0 else 0.0
    return a, bm, bp


def solve_lin(A: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        pc = M[c][c]
        mc = M[c]
        for r in range(n):
            if r != c:
                mr = M[r]
                f = mr[c] / pc
                if f:
                    for q in range(c, n + 1):
                        mr[q] -= f * mc[q]
    return [M[i][n] / M[i][i] for i in range(n)]


def lad_reg(cols: list[list[float]], y: list[float], iters: int = 30, init: list[float] | None = None) -> list[float]:
    """열 기반 고속 LAD 회귀(IRLS). cols = [x0, x1, ...], 길이 p."""
    p = len(cols)
    n = len(y)
    beta = init[:] if init is not None else [median(y)] + [0.0] * (p - 1)
    # 0이 아닌 열을 미리 행별로 묶어 빠르게 계산
    rows = list(zip(*cols))
    for _ in range(iters):
        A = [[0.0] * p for _ in range(p)]
        b = [0.0] * p
        for i in range(n):
            xi = rows[i]
            yi = y[i]
            pred = 0.0
            for u in range(p):
                pred += beta[u] * xi[u]
            e = yi - pred
            ae = e if e >= 0.0 else -e
            w = 1.0 / (ae if ae > 1e-6 else 1e-6)
            for u in range(p):
                xu = xi[u]
                if xu == 0.0:
                    continue
                wu = w * xu
                b[u] += wu * yi
                Au = A[u]
                for v in range(u, p):
                    xv = xi[v]
                    if xv != 0.0:
                        Au[v] += wu * xv
        for u in range(p):
            for v in range(u):
                A[u][v] = A[v][u]
        nb = solve_lin(A, b)
        if nb is None:
            break
        beta = nb
    return beta


def best_level_author(z: list[float]) -> float:
    s = sorted(z)
    best_n, best_c, lo = -1, 0.0, 0
    for i in range(len(s)):
        while s[i] - s[lo] > 2 * LW + 1e-12:
            lo += 1
        if i - lo + 1 > best_n:
            best_n, best_c = i - lo + 1, s[lo] + LW
    return best_c


def best_level_fitpy(z: list[float]) -> float:
    """market/fit.py window()의 로그 공간 동치(중간값에 가장 가까운 창 우선)."""
    s = sorted(z)
    mid = s[len(s) // 2]
    best = None
    j = 0
    n = len(s)
    for i in range(n):
        if j < i:
            j = i
        while j + 1 < n and s[j + 1] <= s[i] + 2 * LW + 1e-12:
            j += 1
        cen = (s[i] + s[j]) / 2
        off = abs(cen - mid)
        cnt = j - i + 1
        if best is None or cnt > best[0] or (cnt == best[0] and off < best[1]):
            best = (cnt, off, cen)
    return best[2]


def shape_logm(rule: str, d: dict, h: int) -> float:
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
    if rule == "R4b":
        rb = d["rrob"]
        return 0.5 * math.log(w * rb * rb + 1 - w)
    if rule == "R5":
        return math.log(d["h"][h]["mcalc"]) if r >= 1.0 else 0.5 * math.log(w * r * r + 1 - w)
    if rule.startswith("W"):
        ww = float(rule[1:])
        return 0.5 * math.log(ww * r * r + 1 - ww)
    raise ValueError(rule)


def expanding_sides(cd: list[dict], h: int, rule: str, level_fn=best_level_author) -> list[int | None]:
    z = [ylog(d["h"][h]["M"]) - shape_logm(rule, d, h) for d in cd]
    tf = [d["h"][h]["tfinal"] for d in cd]
    out: list[int | None] = []
    c, last_fit = None, -REFIT_EVERY
    for i, d in enumerate(cd):
        if i - last_fit >= REFIT_EVERY or c is None:
            done = [z[q] for q in range(i) if tf[q] <= d["at"]]
            if len(done) >= MIN_FIT:
                c = level_fn(done)
                last_fit = i
        if c is None:
            out.append(None)
            continue
        v = z[i]
        out.append(0 if abs(v - c) <= LW + 1e-12 else (1 if v > c else -1))
    return out


def prepare_independent(asset: str, horizons: tuple[int, ...], with_calc: bool) -> list[dict]:
    """CSV에서 독립 계산: vol20·vol60·vol250·r·r60·rrob·vol5_cc·M·RV(체결가 기준 & 종가→종가 기준)."""
    rows = load_csv(asset)
    spec = C.ASSETS[asset]
    final = [C.final_dt(spec, r["date"]) for r in rows]
    out = []
    for j, r in enumerate(rows):
        at = dt.datetime.combine(r["date"], JUDGE, KST)
        k = bisect.bisect_right(final, at)
        if k < 251 or r["open"] <= 0:
            continue
        closes = [x["close"] for x in rows[k - 251:k]]
        lr250 = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
        if len(lr250) < 250:
            continue
        # pieces.read가 round(..., 6)한 숫자를 쓰므로 독립 값과 pieces.read 값 둘 다 확인 가능하게 저장
        v250_raw = pstdev(lr250)
        v60_raw = pstdev(lr250[-60:])
        v20_raw = pstdev(lr250[-20:])
        v5_raw = math.sqrt(fmean(x * x for x in lr250[-5:]))
        v250 = round(v250_raw, 6)
        v60 = round(v60_raw, 6)
        v20 = round(v20_raw, 6)
        if v250 <= 0 or v20 <= 0:
            continue
        alr20 = [abs(x) for x in lr250[-20:]]
        vals, nums = pieces.read(closes)
        day = {
            "j": j, "date": r["date"], "at": at, "k": k, "vals": vals,
            "r": v20 / v250, "r_raw": v20_raw / v250_raw, "r60": v60 / v250,
            "r5_cc": v5_raw / v250_raw, "rrob": 1.4826 * median(alr20) / v250,
            "v250": v250, "h": {}, "cc": {},
        }
        for h in horizons:
            if j + h - 1 >= len(rows):
                continue
            t = rows[j + h - 1]
            if t["close"] <= 0 or r["close"] <= 0:
                continue
            ret = t["close"] / r["open"] - 1
            s = math.log(r["close"] / r["open"]) ** 2
            ok = True
            for i in range(j + 1, j + h):
                if rows[i]["close"] <= 0 or rows[i - 1]["close"] <= 0:
                    ok = False
                    break
                s += math.log(rows[i]["close"] / rows[i - 1]["close"]) ** 2
            unit = v250 * math.sqrt(h)
            x = {
                "R": ret, "M": abs(ret) / unit, "RV": math.sqrt(s) / unit if ok else None,
                "tdate": t["date"], "tfinal": final[j + h - 1], "abs": abs(ret) * 100, "unit": unit,
            }
            if with_calc:
                res = calc.predict(asset, h, vals, PIECES, nums)
                mv = res.move()
                if mv is None:
                    continue
                x["lo"], x["hi"] = mv
                x["mcalc"] = math.prod(e.center for e in res.move_muls) if res.move_muls else 1.0
            day["h"][h] = x
            if j > 0 and rows[j - 1]["close"] > 0:
                cc_ret = t["close"] / rows[j - 1]["close"] - 1
                cc_rv = math.sqrt(sum(math.log(rows[i]["close"] / rows[i - 1]["close"]) ** 2 for i in range(j, j + h)))
                day["cc"][h] = {"M": abs(cc_ret) / unit, "RV": cc_rv / unit}
        out.append(day)
    return out


def cell_days(days: list[dict], h: int) -> list[dict]:
    return [d for d in days if h in d["h"]]


def main() -> int:
    t0 = time.time()
    rng = random.Random(SEED)
    DAYS = {a: prepare_independent(a, HORIZONS[a], True) for a in TARGETS}
    CELLS = [(a, h) for a in TARGETS for h in HORIZONS[a]]

    # ------------------------------------------------------------ [R1] 독립 재구현·IRLS 수렴·격자 대조
    print("=" * 112)
    print(f"[R1] 독립 재구현 · CSV 무결성 · IRLS 수렴(30회 대 200회) · w 격자 대 R1 대조 (논리집 {KNOW})")
    for a in TARGETS:
        rows = load_csv(a)
        asc = all(rows[i]["date"] < rows[i + 1]["date"] for i in range(len(rows) - 1))
        bad = sum(1 for r in rows if r["open"] <= 0 or r["close"] <= 0)
        print(f"  CSV {a}: {len(rows)}줄 ({rows[0]['date']} ~ {rows[-1]['date']}) · 엄격 오름차순 {asc} · 0 이하 시가/종가 {bad}")
    print("  -- 효과 회귀 IRLS 수렴성(30회 = 저자 [E4] 대 200회) 및 M < 0.001 날 수:")
    EFF = {}
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        lr = [math.log(d["r"]) for d in cd]
        y = [ylog(d["h"][h]["M"]) for d in cd]
        n0 = sum(1 for d in cd if d["h"][h]["M"] < M_FLOOR)
        e30 = hinge_lad(lr, y, 30)
        e200 = hinge_lad(lr, y, 200)
        l30 = sum(abs(yy - (e30[0] + e30[1] * min(l, 0.0) + e30[2] * max(l, 0.0))) for l, yy in zip(lr, y))
        l200 = sum(abs(yy - (e200[0] + e200[1] * min(l, 0.0) + e200[2] * max(l, 0.0))) for l, yy in zip(lr, y))
        EFF[(a, h)] = e30
        print(f"  {NAME[a]} {h:2d}봉(n={len(cd)}, M<0.001 {n0:2d}일): 30회 β− {e30[1]:+.3f} · β+ {e30[2]:+.3f} | "
              f"200회 β− {e200[1]:+.3f} · β+ {e200[2]:+.3f} (차이 {abs(e200[1]-e30[1]):.3f}/{abs(e200[2]-e30[2]):.3f}, L1 {l200-l30:+.4f})")
    beat_r1 = []
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        z1 = [ylog(d["h"][h]["M"]) - shape_logm("R1", d, h) for d in cd]
        c1 = best_level_author(z1)
        h1 = fmean(abs(v - c1) <= LW + 1e-12 for v in z1)
        best_h, best_w = -1.0, 0.0
        for wi in range(11):
            w = wi / 10
            zw = [ylog(d["h"][h]["M"]) - shape_logm(f"W{w}", d, h) for d in cd]
            cw = best_level_author(zw)
            hw = fmean(abs(v - cw) <= LW + 1e-12 for v in zw)
            if hw > best_h:
                best_h, best_w = hw, w
        if best_h > h1 + 1e-12:
            beat_r1.append(f"{NAME[a]} {h}봉(w={best_w:.1f}: {best_h:.3f} vs R1 {h1:.3f}, {(best_h-h1)*100:+.1f}%p)")
    print(f"  -- [E6] w 격자 최댓값이 R1을 넘는 칸: {len(beat_r1)}/9 (논문 110·192줄 '넘는 칸이 없다'와 다름): " + "; ".join(beat_r1))

    # ------------------------------------------------------------ [R2] 판단 때 알 수 있었나 & 높이 맞추기 오류
    print("=" * 112)
    print("[R2] 판단 때 알 수 있었나 — reading.jsonl 연속값(vol20·vol60·vol250) 전수 대조 및 R1 정직한 높이 vs 계산기 그대로 분해")
    runs = {dt.date.fromisoformat(r["run"]): dt.datetime.fromisoformat(r["steps"]["decided_at"])
            for r in read_jsonl("run") if r.get("mode") == "live" and r.get("steps", {}).get("decided_at")}
    rows_all = {a: load_csv(a) for a in TARGETS}
    fin_all = {a: [C.final_dt(C.ASSETS[a], r["date"]) for r in rows_all[a]] for a in TARGETS}
    st_same = st_diff = num_tot = num_diff = late_cnt = 0
    btc_1021_info = ""
    for r in read_jsonl("reading"):
        if r["asset"] not in TARGETS:
            continue
        rd = dt.date.fromisoformat(r["run"])
        if rd not in runs:
            continue
        at = runs[rd]
        k = bisect.bisect_right(fin_all[r["asset"]], at)
        v, nums = pieces.read([x["close"] for x in rows_all[r["asset"]][:k]][-251:])
        ok = all(v[s] == r["values"].get(s) for s in pieces.STATES)
        st_same += ok
        st_diff += not ok
        late_cnt += C.final_dt(C.ASSETS[r["asset"]], dt.date.fromisoformat(r["known_through"])) > at
        rnums = r.get("numbers") or {}
        for kk in ("vol20", "vol60", "vol250"):
            if kk in rnums:
                num_tot += 1
                if abs(rnums[kk] - nums[kk]) > 1e-5:
                    num_diff += 1
        if r["run"] == "2026-10-21" and r["asset"] == "KRW-BTC":
            btc_1021_info = f"known_through={r['known_through']} vol20={rnums['vol20']} vol250={rnums['vol250']} r={rnums['vol20']/rnums['vol250']:.4f}"
    print(f"  reading 108줄: 상태 같음 {st_same}·다름 {st_diff}·늦은 봉 {late_cnt} | 연속값(vol20·vol60·vol250) {num_tot}개 중 다름(>1e-5) {num_diff}")
    print(f"  2026-10-21 KRW-BTC 실제 기록: {btc_1021_info} (돌아보기·논문 1절 '0.64' 대 [E15] '0.62')")

    # R1 expanding(author best_level) vs R1 expanding(fit.py window) vs R1 frozen(<=2026-06-30) vs calc
    r1e_all, r1f_all, r1frz_all, clc_all = [], [], [], []
    r1e_post, r1frz_post, clc_post = [], [], []
    kr20_detail = ""
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        s_e = expanding_sides(cd, h, "R1", best_level_author)
        s_f = expanding_sides(cd, h, "R1", best_level_fitpy)
        fit_cd = [d for d in cd if d["h"][h]["tdate"] <= FIT_END]
        z_fit = [ylog(d["h"][h]["M"]) - shape_logm("R1", d, h) for d in fit_cd]
        c_frz = best_level_author(z_fit)
        z_all = [ylog(d["h"][h]["M"]) - shape_logm("R1", d, h) for d in cd]
        s_frz = [0 if abs(v - c_frz) <= LW + 1e-12 else 1 for v in z_all]
        s_clc = [0 if d["h"][h]["lo"] <= d["h"][h]["abs"] <= d["h"][h]["hi"] else 1 for d in cd]
        ii = [i for i in range(len(cd)) if s_e[i] is not None]
        pi = [i for i in ii if cd[i]["date"] >= SHIFT_START]
        r1e_all.append(fmean(s_e[i] == 0 for i in ii))
        r1f_all.append(fmean(s_f[i] == 0 for i in ii))
        r1frz_all.append(fmean(s_frz[i] == 0 for i in ii))
        clc_all.append(fmean(s_clc[i] == 0 for i in ii))
        r1e_post.append(fmean(s_e[i] == 0 for i in pi))
        r1frz_post.append(fmean(s_frz[i] == 0 for i in pi))
        clc_post.append(fmean(s_clc[i] == 0 for i in pi))
        if (a, h) == ("069500", 20):
            base_eff = next(e for p in PIECES if p.id == "C_base_coin" for e in p.effects if e.on == "move" and e.asset == a and e.horizon == h)
            c_fp = best_level_fitpy(z_fit)
            kr20_detail = (f"국내 20봉 2026-06-30 기준 높이: author best_level exp(c)={math.exp(c_frz):.4f}, "
                           f"fit.py window exp(c)={math.exp(c_fp):.4f}, knowledge.toml C_base_coin={base_eff.center:.4f} "
                           f"| post-2607 적중 R1_exp {r1e_post[-1]:.3f} · R1_frz {r1frz_post[-1]:.3f} · calc {clc_post[-1]:.3f}")
    print(f"  9칸 평균 전부: R1 정직한 높이(author) {fmean(r1e_all):.4f} · (fit.py window) {fmean(r1f_all):.4f} · R1(≤26-06 고정) {fmean(r1frz_all):.4f} · 계산기 그대로 {fmean(clc_all):.4f}")
    print(f"  9칸 평균 판단≥07-01: R1 정직한 높이 {fmean(r1e_post):.4f} · R1(≤26-06 고정) {fmean(r1frz_post):.4f} · 계산기 그대로 {fmean(clc_post):.4f}")
    print(f"  {kr20_detail}")

    # ------------------------------------------------------------ [R3] [E15] 월요일 덮어쓰기 버그 & 빠진 사례
    print("=" * 112)
    print("[R3] 우리 기록([E15]) 누락 사례 — 월요일 덮어쓰기 버그(10/80건) · 종목 세운 재료 3건 · 옛 기준 94건")
    preds = read_jsonl("prediction")
    preds_by_id = {r["id"]: r for r in preds}
    sc_counted = {r["prediction"]: r for r in read_jsonl("score") if r.get("counted") and r.get("predictor") == "hand"}
    # 저자 방식(덮어쓰기) vs 정정 방식(counted 기준 직접 짝짓기)
    author_map = {}
    for r in preds:
        if r.get("predictor") == "hand" and r.get("base_kind") == "fill" and r["asset"] in TARGETS:
            author_map[(r["asset"], r["horizon"], r["base_date"])] = r["id"]
    fixed_map = {}
    old_counts = defaultdict(lambda: [0, 0])
    for pid, s in sc_counted.items():
        pr = preds_by_id.get(pid)
        if not pr or pr["asset"] not in TARGETS:
            continue
        if pr.get("base_kind") == "fill":
            fixed_map[(pr["asset"], pr["horizon"], pr["base_date"])] = (pr, s)
        elif s.get("size_hit") is not None:
            old_counts[(pr["asset"], pr["horizon"])][1] += 1
            old_counts[(pr["asset"], pr["horizon"])][0] += int(bool(s["size_hit"]))
    dropped = []
    for k, (pr, s) in sorted(fixed_map.items()):
        aid = author_map.get(k)
        if aid != pr["id"] and aid not in sc_counted:
            dropped.append((k, pr["id"], aid, s.get("size_hit"), s.get("ret")))
    a_hit = a_tot = f_hit = f_tot = 0
    for a, h in CELLS:
        cd = cell_days(DAYS[a], h)
        ii = [d for d in cd if d["date"] >= REC_START]
        ah = at_ = fh = ft_ = 0
        for d in ii:
            k = (a, h, d["date"].isoformat())
            aid = author_map.get(k)
            if aid and aid in sc_counted and sc_counted[aid].get("size_hit") is not None:
                at_ += 1
                ah += int(bool(sc_counted[aid]["size_hit"]))
            if k in fixed_map and fixed_map[k][1].get("size_hit") is not None:
                ft_ += 1
                fh += int(bool(fixed_map[k][1]["size_hit"]))
        a_hit += ah; a_tot += at_; f_hit += fh; f_tot += ft_
        if at_ != ft_:
            print(f"  {NAME[a]} {h:2d}봉 손(fill): 저자 [E15] {ah}/{at_} → 정정 {fh}/{ft_}")
    print(f"  체결가 기준 손 합계: 저자 [E15] {a_hit}/{a_tot} → 정정 {f_hit}/{f_tot} (누락 {len(dropped)}건, 모두 월요일 ETF: "
          + ", ".join(f"{NAME[k[0]]} {k[1]}봉 {k[2]}({'○' if sh else '×'})" for k, _, _, sh, _ in dropped) + ")")
    print(f"  [E15]에서 빠진 옛 기준(10-21~11-04) 손 채점 합계: {sum(v[0] for v in old_counts.values())}/{sum(v[1] for v in old_counts.values())}건 "
          f"(긴 기간 20·28봉: 국내 {old_counts[('069500',20)][0]}/{old_counts[('069500',20)][1]}, 해외 {old_counts[('360750',20)][0]}/{old_counts[('360750',20)][1]}, 코인 {old_counts[('KRW-BTC',28)][0]}/{old_counts[('KRW-BTC',28)][1]})")
    # 누락된 종목 세운 재료 3건
    stock_seeds = sorted(k for k in SEED_L06 if k[0] not in TARGETS)
    ss_txt = []
    for code, h, dd in stock_seeds:
        ds = prepare_independent(code, (1,), False)
        m_ = next((x for x in ds if x["date"] == dd), None)
        if m_:
            ss_txt.append(f"{code} {h}봉 {dd}(r {m_['r']:.2f}, M {m_['h'][1]['M']:.2f}, R {m_['h'][1]['R']*100:+.2f}%)")
    print(f"  SEED_L06 중 [E15]·논문 7절에서 빠진 종목 세운 재료 3건: " + "; ".join(ss_txt))

    # ------------------------------------------------------------ [R4] 쟁점 (1): E2 짝 부트스트랩·단위 일치(CC)·종목 구간·연속 MAE
    print("=" * 112)
    print("[R4] 쟁점 (1) '좁혀짐'·'딸린 갈래' 검정 — (가) E2 기간 감소 짝 부트스트랩(200회), (나) 단위 일치(종가→종가) E1·E2·E3·Q1, (다) 069500 종목 구간(≥2024-05), (라) 연속 MAE")
    for a in TARGETS:
        hl = LONG[a]
        ds1 = {d["date"]: d for d in cell_days(DAYS[a], 1)}
        dsl = {d["date"]: d for d in cell_days(DAYS[a], hl)}
        dts = sorted(set(ds1) & set(dsl))
        lr = [math.log(ds1[dd]["r"]) for dd in dts]
        y1 = [ylog(ds1[dd]["h"][1]["M"]) for dd in dts]
        yl = [ylog(dsl[dd]["h"][hl]["M"]) for dd in dts]
        e1 = hinge_lad(lr, y1, 30)
        el = hinge_lad(lr, yl, 30)
        dm, dp, dg = e1[1] - el[1], e1[2] - el[2], (el[2] - el[1]) - (e1[2] - e1[1])
        dms, dps, dgs = [], [], []
        for _ in range(200):
            idx = block_idx(len(dts), rng)
            lrb = [lr[i] for i in idx]
            b1 = hinge_lad(lrb, [y1[i] for i in idx], 15, e1)
            bl = hinge_lad(lrb, [yl[i] for i in idx], 15, el)
            dms.append(b1[1] - bl[1])
            dps.append(b1[2] - bl[2])
            dgs.append((bl[2] - bl[1]) - (b1[2] - b1[1]))
        im, ip, ig = pct90(dms), pct90(dps), pct90(dgs)
        print(f"  (가) E2 짝 부트스트랩 {NAME[a]}({len(dts)}일): Δβ− {dm:+.3f} ({im[0]:+.2f}, {im[1]:+.2f}, ≤0비율 {fmean(x<=0 for x in dms):.3f}) | "
              f"Δβ+ {dp:+.3f} ({ip[0]:+.2f}, {ip[1]:+.2f}, ≤0비율 {fmean(x<=0 for x in dps):.3f}) | Δg {dg:+.3f} ({ig[0]:+.2f}, {ig[1]:+.2f})")
    for a in TARGETS:
        hl = LONG[a]
        cc_m, cc_rv = {}, {}
        for h in HORIZONS[a]:
            cd = [d for d in cell_days(DAYS[a], h) if h in d["cc"]]
            lr = [math.log(d["r"]) for d in cd]
            cc_m[h] = hinge_lad(lr, [ylog(d["cc"][h]["M"]) for d in cd], 30)
            cc_rv[h] = hinge_lad(lr, [ylog(d["cc"][h]["RV"]) for d in cd], 30)
        den_p = cc_m[1][2] - cc_m[hl][2]
        num_p = cc_rv[1][2] - cc_rv[hl][2]
        dp_str = f"{1 - num_p / den_p:+.2f}" if den_p > 0 else "분모≤0"
        print(f"  (나) 단위 일치(CC) {NAME[a]}: 1봉 β−/β+ {cc_m[1][1]:+.3f}/{cc_m[1][2]:+.3f} → {hl}봉 {cc_m[hl][1]:+.3f}/{cc_m[hl][2]:+.3f} (g긴 {cc_m[hl][2]-cc_m[hl][1]:+.3f}) | "
              f"RV β+ {cc_rv[1][2]:+.3f}→{cc_rv[hl][2]:+.3f} | Q1 D(+) {dp_str}")
    st_kr = []
    for h in (1, 5, 20):
        cd = [d for d in cell_days(DAYS["069500"], h) if d["date"] >= dt.date(2024, 5, 1)]
        e = hinge_lad([math.log(d["r"]) for d in cd], [ylog(d["h"][h]["M"]) for d in cd], 30)
        st_kr.append(f"{h}봉({len(cd)}일) β− {e[1]:+.3f} · β+ {e[2]:+.3f} · g {e[2]-e[1]:+.3f}")
    print("  (다) 069500 종목 구간(≥2024-05): " + " | ".join(st_kr))
    mae_rows = []
    r4_lose_r1 = 0
    r1_maes = {}
    for rule in ("R0", "R1", "R2", "R3", "R4", "R4b", "R5"):
        ms = []
        for a, h in CELLS:
            cd = cell_days(DAYS[a], h)
            z = [ylog(d["h"][h]["M"]) - shape_logm(rule, d, h) for d in cd]
            c = median(z)
            mval = fmean(abs(v - c) for v in z)
            ms.append(mval)
            if rule == "R1":
                r1_maes[(a, h)] = mval
            elif rule == "R4" and mval > r1_maes[(a, h)]:
                r4_lose_r1 += 1
        mae_rows.append(f"{rule} {fmean(ms):.4f}")
    print(f"  (라) 연속 잣대 MAE(|ln M − ln 가운데|) 9칸 평균: " + " · ".join(mae_rows) + f" (R4가 R1보다 나쁜 칸 {r4_lose_r1}/9)")

    # ------------------------------------------------------------ [R5] 쟁점 (2): L06 방아쇠 누설·HAR 장중 변동성·종목 60개·인과 OOS
    print("=" * 112)
    print("[R5] 쟁점 (2) L06 방아쇠([E8]) 해부 — (가) 해외 20봉 특이행렬 버그 수정, (나) 인과 경계·구간별·연속 길이 k=1..4, (다) HAR 장중 변동성 통제 + 전체 재추정 부트스트랩 + 인과 OOS, (라) 종목 60개")
    # (가) 해외 20봉 특이행렬 수정
    cd_us20 = cell_days(DAYS["360750"], 20)
    hist, pend, dn_flags = [], [], []
    for i, d in enumerate(cd_us20):
        x = d["h"][20]
        keep = []
        for (q, s, tf) in pend:
            if tf <= d["at"]:
                hist.append((q, s))
            else:
                keep.append((q, s, tf))
        pend = keep
        p3 = []
        for (q, s) in reversed(hist):
            if not p3 or p3[-1][0] - q >= 20:
                p3.append((q, s))
            if len(p3) == 3:
                break
        dn_flags.append(1.0 if [s for _, s in p3] == [-1, -1, -1] else 0.0)
        s_ = 0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1)
        pend.append((i, s_, x["tfinal"]))
    lr_u20 = [math.log(d["r"]) for d in cd_us20]
    b_u20 = lad_reg([[1.0] * len(cd_us20), [min(v, 0.0) for v in lr_u20], [max(v, 0.0) for v in lr_u20], dn_flags],
                    [ylog(d["h"][20]["M"]) for d in cd_us20], 30)
    print(f"  (가) 해외 20봉 특이행렬 수정(up3=0열 제거, dn3={int(sum(dn_flags))}일): 실제 γd {b_u20[3]:+.3f} (evidence.py [E8]은 +0.000 출력)")

    # (나) 짧은 기간 6칸 인과 경계(expanding R1) & post-2607 건수
    for a, h in [(t, hh) for t in TARGETS for hh in HORIZONS[t][:2]]:
        cd = cell_days(DAYS[a], h)
        ex_r1 = expanding_sides(cd, h, "R1", best_level_author)
        hist, pend, ex_hist, ex_pend = [], [], [], []
        f_calc, f_ex = [], []
        for i, d in enumerate(cd):
            x = d["h"][h]
            hist, pend = hist + [(q, s) for q, s, tf in pend if tf <= d["at"]], [(q, s, tf) for q, s, tf in pend if tf > d["at"]]
            ex_hist, ex_pend = ex_hist + [(q, s) for q, s, tf in ex_pend if tf <= d["at"]], [(q, s, tf) for q, s, tf in ex_pend if tf > d["at"]]

            def pick3(hl_):
                p = []
                for q, s in reversed(hl_):
                    if not p or p[-1][0] - q >= h:
                        p.append((q, s))
                    if len(p) == 3:
                        break
                return [s for _, s in p]

            sc3, se3 = pick3(hist), pick3(ex_hist)
            f_calc.append((1.0 if sc3 == [1, 1, 1] else 0.0, 1.0 if sc3 == [-1, -1, -1] else 0.0))
            f_ex.append((1.0 if se3 == [1, 1, 1] else 0.0, 1.0 if se3 == [-1, -1, -1] else 0.0))
            s_ = 0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1)
            pend.append((i, s_, x["tfinal"]))
            if ex_r1[i] is not None:
                ex_pend.append((i, ex_r1[i], x["tfinal"]))
        v_ex = [i for i in range(len(cd)) if ex_r1[i] is not None]
        lrv = [math.log(cd[i]["r"]) for i in v_ex]
        be = lad_reg([[1.0] * len(v_ex), [min(v, 0.0) for v in lrv], [max(v, 0.0) for v in lrv],
                      [f_ex[i][0] for i in v_ex], [f_ex[i][1] for i in v_ex]],
                     [ylog(cd[i]["h"][h]["M"]) for i in v_ex], 30)
        post_u = sum(1 for i, d in enumerate(cd) if d["date"] >= SHIFT_START and f_calc[i][0])
        post_d = sum(1 for i, d in enumerate(cd) if d["date"] >= SHIFT_START and f_calc[i][1])
        print(f"  (나) 인과 R1 경계 {NAME[a]} {h}봉(up3 {int(sum(f_ex[i][0] for i in v_ex))}·dn3 {int(sum(f_ex[i][1] for i in v_ex))}): "
              f"γu {be[3]:+.3f} · γd {be[4]:+.3f} | 판단≥26-07 calc 기준 up3 {post_u}일 · dn3 {post_d}일")

    # (다) 1봉 세 대상: 연속 길이 k=1..4 + HAR 장중 변동성(m3_oc, m20_oc) 통제 + 전체 재추정 부트스트랩(150회) + 인과 OOS 적중·MAE
    for a in TARGETS:
        cd = cell_days(DAYS[a], 1)
        ss = [0 if d["h"][1]["lo"] <= d["h"][1]["abs"] <= d["h"][1]["hi"] else (1 if d["h"][1]["abs"] > d["h"][1]["hi"] else -1) for d in cd]
        lag = 2 if a == "KRW-BTC" else 1
        est = EFF[(a, 1)]
        res_all = [ylog(d["h"][1]["M"]) - (est[0] + est[1] * min(math.log(d["r"]), 0.0) + est[2] * max(math.log(d["r"]), 0.0)) for d in cd]
        ktxt = []
        for kr in (1, 2, 3, 4):
            uv = [res_all[i] for i in range(lag + kr - 1, len(cd)) if all(ss[i - lag - q] == 1 for q in range(kr))]
            dv = [res_all[i] for i in range(lag + kr - 1, len(cd)) if all(ss[i - lag - q] == -1 for q in range(kr))]
            ktxt.append(f"k={kr} u{median(uv):+.3f}({len(uv)})/d{median(dv):+.3f}({len(dv)})")
        print(f"  (나-연속길이) {NAME[a]} 1봉: " + " | ".join(ktxt))

        # HAR_oc + full-refit bootstrap + causal expanding OOS
        hist, pend, och, ocp = [], [], [], []
        rows_d = []
        for i, d in enumerate(cd):
            x = d["h"][1]
            hist, pend = hist + [(q, s) for q, s, tf in pend if tf <= d["at"]], [(q, s, tf) for q, s, tf in pend if tf > d["at"]]
            och, ocp = och + [(q, mv) for q, mv, tf in ocp if tf <= d["at"]], [(q, mv, tf) for q, mv, tf in ocp if tf > d["at"]]
            if len(hist) >= 3 and len(och) >= 20:
                sd3 = [s for _, s in hist[-3:]]
                fu = 1.0 if sd3 == [1, 1, 1] else 0.0
                fd = 1.0 if sd3 == [-1, -1, -1] else 0.0
                m3 = math.log(max(fmean(mv for _, mv in och[-3:]), M_FLOOR))
                m20 = math.log(max(fmean(mv for _, mv in och[-20:]), M_FLOOR))
                lr = math.log(d["r"])
                r5 = math.log(max(d["r5_cc"], 1e-4))
                rows_d.append((min(lr, 0.0), max(lr, 0.0), m3, m20, r5, fu, fd, ylog(x["M"]), x["tfinal"], d["at"]))
            s_ = 0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1)
            pend.append((i, s_, x["tfinal"]))
            ocp.append((i, x["M"], x["tfinal"]))
        n = len(rows_d)
        c1 = [1.0] * n
        cm = [r[0] for r in rows_d]
        cp = [r[1] for r in rows_d]
        cm3 = [r[2] for r in rows_d]
        cm20 = [r[3] for r in rows_d]
        cr5 = [r[4] for r in rows_d]
        cfu = [r[5] for r in rows_d]
        cfd = [r[6] for r in rows_d]
        yy = [r[7] for r in rows_d]
        b_raw = lad_reg([c1, cm, cp, cfu, cfd], yy, 30)
        b_oc = lad_reg([c1, cm, cp, cm3, cm20, cfu, cfd], yy, 30)
        b_all = lad_reg([c1, cm, cp, cr5, cm3, cm20, cfu, cfd], yy, 30)
        # In-sample MAE comparison
        b_r_only = lad_reg([c1, cm, cp], yy, 30)
        b_oc_only = lad_reg([c1, cm, cp, cm3, cm20], yy, 30)
        mae_r = fmean(abs(yy[i] - (b_r_only[0] + b_r_only[1] * cm[i] + b_r_only[2] * cp[i])) for i in range(n))
        mae_rl = fmean(abs(yy[i] - (b_raw[0] + b_raw[1] * cm[i] + b_raw[2] * cp[i] + b_raw[3] * cfu[i] + b_raw[4] * cfd[i])) for i in range(n))
        mae_oc = fmean(abs(yy[i] - (b_oc_only[0] + b_oc_only[1] * cm[i] + b_oc_only[2] * cp[i] + b_oc_only[3] * cm3[i] + b_oc_only[4] * cm20[i])) for i in range(n))
        mae_ocl = fmean(abs(yy[i] - (b_oc[0] + b_oc[1] * cm[i] + b_oc[2] * cp[i] + b_oc[3] * cm3[i] + b_oc[4] * cm20[i] + b_oc[5] * cfu[i] + b_oc[6] * cfd[i])) for i in range(n))
        # Full-refit block bootstrap (150회)
        bu_r, bd_r, bu_o, bd_o = [], [], [], []
        for _ in range(150):
            idx = block_idx(n, rng)
            fu_b = [cfu[i] for i in idx]
            fd_b = [cfd[i] for i in idx]
            if sum(fu_b) == 0.0 or sum(fd_b) == 0.0:
                continue
            cm_b = [cm[i] for i in idx]
            cp_b = [cp[i] for i in idx]
            y_b = [yy[i] for i in idx]
            br = lad_reg([c1, cm_b, cp_b, fu_b, fd_b], y_b, 15, b_raw)
            bo = lad_reg([c1, cm_b, cp_b, [cm3[i] for i in idx], [cm20[i] for i in idx], fu_b, fd_b], y_b, 15, b_oc)
            bu_r.append(br[3]); bd_r.append(br[4])
            bu_o.append(bo[5]); bd_o.append(bo[6])
        # Causal expanding OOS (60일마다 재추정, 최소 250일)
        oos = {}
        for mode in ("r", "r+L06", "HAR_oc"):
            hits, errs = [], []
            coef, c_lvl, last_f = None, None, -60
            for i in range(n):
                if i >= 250 and (i - last_f >= 60 or coef is None):
                    di = [q for q in range(i) if rows_d[q][8] <= rows_d[i][9]]
                    if len(di) >= 250:
                        c1_t = [1.0] * len(di)
                        ym = [yy[q] for q in di]
                        if mode == "r":
                            cols_t = [c1_t, [cm[q] for q in di], [cp[q] for q in di]]
                        elif mode == "r+L06":
                            fu_t = [cfu[q] for q in di]
                            fd_t = [cfd[q] for q in di]
                            cols_t = [c1_t, [cm[q] for q in di], [cp[q] for q in di], fu_t, fd_t] if (sum(fu_t) > 0 and sum(fd_t) > 0) else [c1_t, [cm[q] for q in di], [cp[q] for q in di]]
                        else:
                            cols_t = [c1_t, [cm3[q] for q in di], [cm20[q] for q in di]]
                        coef = lad_reg(cols_t, ym, 15)
                        z_tr = [ym[k_] - sum(coef[u] * cols_t[u][k_] for u in range(len(coef))) for k_ in range(len(di))]
                        c_lvl = best_level_author(z_tr)
                        last_f = i
                if coef is None:
                    continue
                if mode == "r":
                    xvec = (1.0, cm[i], cp[i])
                elif mode == "r+L06":
                    xvec = (1.0, cm[i], cp[i], cfu[i], cfd[i]) if len(coef) == 5 else (1.0, cm[i], cp[i])
                else:
                    xvec = (1.0, cm3[i], cm20[i])
                pred_med = sum(b * v for b, v in zip(coef, xvec))
                hits.append(abs(yy[i] - (pred_med + c_lvl)) <= LW + 1e-12)
                errs.append(abs(yy[i] - pred_med))
            oos[mode] = (fmean(hits), fmean(errs), len(hits))
        print(f"  (다) {NAME[a]} 1봉(n={n}, boot={len(bu_r)}):")
        print(f"       전체재추정 원형: γu {b_raw[3]:+.3f} ({pct90(bu_r)[0]:+.2f}, {pct90(bu_r)[1]:+.2f}) · γd {b_raw[4]:+.3f} ({pct90(bd_r)[0]:+.2f}, {pct90(bd_r)[1]:+.2f}) | MAE r {mae_r:.4f}→+L06 {mae_rl:.4f} ({mae_rl-mae_r:+.4f})")
        print(f"       +HAR_oc(m3+m20): γu {b_oc[5]:+.3f} ({pct90(bu_o)[0]:+.2f}, {pct90(bu_o)[1]:+.2f}) · γd {b_oc[6]:+.3f} ({pct90(bd_o)[0]:+.2f}, {pct90(bd_o)[1]:+.2f}) | MAE HAR_oc {mae_oc:.4f}→+L06 {mae_ocl:.4f} ({mae_ocl-mae_oc:+.4f}) | +vol5_cc: γu {b_all[6]:+.3f} · γd {b_all[7]:+.3f}")
        print(f"       인과 확장 OOS({oos['r'][2]}일): r 적중 {oos['r'][0]:.3f}(MAE {oos['r'][1]:.4f}) | r+L06 적중 {oos['r+L06'][0]:.3f}(MAE {oos['r+L06'][1]:.4f}) | HAR_oc 적중 {oos['HAR_oc'][0]:.3f}(MAE {oos['HAR_oc'][1]:.4f})")

    # (라) 60개 개별 종목 L06 방아쇠 시험
    stock_codes = sorted(p.stem for p in (ROOT / "data" / "prices").glob("*.csv") if p.stem.isdigit() and p.stem not in TARGETS)
    SDAYS = {}
    for code in stock_codes:
        try:
            SDAYS[code] = prepare_independent(code, STOCK_H, False)
        except Exception:
            pass
    for h in STOCK_H:
        cm_s, cp_s, cm3_s, cfu_s, cfd_s, y_s = [], [], [], [], [], []
        for code, days in SDAYS.items():
            cd = cell_days(days, h)
            if len(cd) < 100:
                continue
            z0 = [ylog(d["h"][h]["M"]) for d in cd]
            c0 = best_level_author(z0)
            sd0 = [0 if abs(v - c0) <= LW + 1e-12 else (1 if v > c0 else -1) for v in z0]
            hist, pend, mh, mp = [], [], [], []
            for i, d in enumerate(cd):
                x = d["h"][h]
                hist, pend = hist + [(q, s) for q, s, tf in pend if tf <= d["at"]], [(q, s, tf) for q, s, tf in pend if tf > d["at"]]
                mh, mp = mh + [(q, mv) for q, mv, tf in mp if tf <= d["at"]], [(q, mv, tf) for q, mv, tf in mp if tf > d["at"]]
                p3, pm3 = [], []
                for q, s in reversed(hist):
                    if not p3 or p3[-1][0] - q >= h:
                        p3.append((q, s))
                    if len(p3) == 3:
                        break
                for q, mv in reversed(mh):
                    if not pm3 or pm3[-1][0] - q >= h:
                        pm3.append((q, mv))
                    if len(pm3) == 3:
                        break
                if len(p3) == 3:
                    sd3 = [s for _, s in p3]
                    lr = math.log(d["r"])
                    cm_s.append(min(lr, 0.0)); cp_s.append(max(lr, 0.0))
                    cm3_s.append(fmean(ylog(v) for _, v in pm3))
                    cfu_s.append(1.0 if sd3 == [1, 1, 1] else 0.0)
                    cfd_s.append(1.0 if sd3 == [-1, -1, -1] else 0.0)
                    y_s.append(ylog(x["M"]))
                pend.append((i, sd0[i], x["tfinal"]))
                mp.append((i, x["M"], x["tfinal"]))
        c1_s = [1.0] * len(y_s)
        bs0 = lad_reg([c1_s, cm_s, cp_s, cfu_s, cfd_s], y_s, 25)
        bs1 = lad_reg([c1_s, cm_s, cp_s, cm3_s, cfu_s, cfd_s], y_s, 25)
        print(f"  (라) 종목 {len(SDAYS)}개 {h:2d}봉(n={len(y_s)}, up3 {int(sum(cfu_s))}·dn3 {int(sum(cfd_s))}): 원형 γu {bs0[3]:+.3f} · γd {bs0[4]:+.3f} | +m3({bs1[3]:+.3f}): γu {bs1[4]:+.3f} · γd {bs1[5]:+.3f}")

    # ------------------------------------------------------------ [R6] 쟁점 (3): 코인 4주(28봉) 두 흔적 충돌 해부
    print("=" * 112)
    print("[R6] 쟁점 (3) 코인 4주(28봉) 두 흔적 충돌(β− -0.332 대 δ -0.204) — ln M = ln(RV_28/단위) + ln(|R_28|/RV_28) 분해")
    for a in TARGETS:
        h = LONG[a]
        cd = cell_days(DAYS[a], h)
        lr = [math.log(d["r"]) for d in cd]
        y_ef = [math.log(max(d["h"][h]["M"], 1e-4) / d["h"][h]["RV"]) for d in cd]
        eh = hinge_lad(lr, y_ef, 30)
        q = [d for d in cd if d["r"] < 1.0]
        c1q = [1.0] * len(q)
        lrq = [math.log(d["r"]) for d in q]
        d60 = [1.0 if d["r60"] < 1.0 else 0.0 for d in q]
        bM = lad_reg([c1q, lrq, d60], [ylog(d["h"][h]["M"]) for d in q], 30)
        bRV = lad_reg([c1q, lrq, d60], [ylog(d["h"][h]["RV"]) for d in q], 30)
        bEF = lad_reg([c1q, lrq, d60], [math.log(max(d["h"][h]["M"], 1e-4) / d["h"][h]["RV"]) for d in q], 30)
        print(f"  {NAME[a]} {h}봉 전체 ln(|R|/RV) 기울기 β− {eh[1]:+.3f} · β+ {eh[2]:+.3f} | "
              f"r < 1(I[r60<1] 포함): M(β {bM[1]:+.3f}, δ {bM[2]:+.3f}) = RV(β {bRV[1]:+.3f}, δ {bRV[2]:+.3f}) + 효율(β {bEF[1]:+.3f}, δ {bEF[2]:+.3f})")

    cd_b = cell_days(DAYS["KRW-BTC"], 28)
    print("  -- 코인 28봉 층화표 (r 칸 × r60 < 1 / r60 ≥ 1):")
    for lo, hi in ((0.0, 0.70), (0.70, 0.85), (0.85, 1.00), (1.00, 1.20), (1.20, 1.50), (1.50, 99.0)):
        for calm60 in (True, False):
            sub = [d for d in cd_b if lo <= d["r"] < hi and (d["r60"] < 1.0) == calm60]
            if not sub:
                continue
            print(f"     r[{lo:.2f},{hi:.2f}) r60{'<1 ' if calm60 else '≥1'} n={len(sub):4d} | 가운데 M {median(d['h'][28]['M'] for d in sub):.3f} · "
                  f"RV {median(d['h'][28]['RV'] for d in sub):.3f} · |R|/RV {median(d['h'][28]['M']/d['h'][28]['RV'] for d in sub):.3f} · 상승비율 {fmean(d['h'][28]['R']>0 for d in sub):.2f}")
    # 연속 ln(r60) & 층별 기울기 & 원시 ln(v20), ln(v60), ln(v250) 회귀 + 블록 부트스트랩(200회)
    qb = [d for d in cd_b if d["r"] < 1.0]
    c1q = [1.0] * len(qb)
    lrq = [math.log(d["r"]) for d in qb]
    lr60q = [math.log(d["r60"]) for d in qb]
    d60q = [1.0 if d["r60"] < 1.0 else 0.0 for d in qb]
    yMq = [ylog(d["h"][28]["M"]) for d in qb]
    yRVq = [ylog(d["h"][28]["RV"]) for d in qb]
    yEFq = [math.log(max(d["h"][28]["M"], 1e-4) / d["h"][28]["RV"]) for d in qb]
    bMc = lad_reg([c1q, lrq, lr60q], yMq, 30)
    bRVc = lad_reg([c1q, lrq, lr60q], yRVq, 30)
    qc = [d for d in qb if d["r60"] < 1.0]
    ql = [d for d in qb if d["r60"] >= 1.0]
    sc_m = lad_reg([[1.0] * len(qc), [math.log(d["r"]) for d in qc]], [ylog(d["h"][28]["M"]) for d in qc], 30)[1]
    sc_rv = lad_reg([[1.0] * len(qc), [math.log(d["r"]) for d in qc]], [ylog(d["h"][28]["RV"]) for d in qc], 30)[1]
    sl_m = lad_reg([[1.0] * len(ql), [math.log(d["r"]) for d in ql]], [ylog(d["h"][28]["M"]) for d in ql], 30)[1]
    sl_rv = lad_reg([[1.0] * len(ql), [math.log(d["r"]) for d in ql]], [ylog(d["h"][28]["RV"]) for d in ql], 30)[1]
    c1b = [1.0] * len(cd_b)
    lv20 = [math.log(d["r"] * d["v250"]) for d in cd_b]
    lv60 = [math.log(d["r60"] * d["v250"]) for d in cd_b]
    lv250 = [math.log(d["v250"]) for d in cd_b]
    br_m = lad_reg([c1b, lv20, lv60, lv250], [math.log(max(abs(d["h"][28]["R"]), 1e-5)) for d in cd_b], 30)
    br_rv = lad_reg([c1b, lv20, lv60, lv250], [math.log(d["h"][28]["RV"] * d["h"][28]["unit"]) for d in cd_b], 30)
    print(f"  -- 코인 28봉 r<1 연속 ln(r60): M (β_r {bMc[1]:+.3f}, β_r60 {bMc[2]:+.3f}) | RV (β_r {bRVc[1]:+.3f}, β_r60 {bRVc[2]:+.3f})")
    print(f"  -- 코인 28봉 층별 기울기: r60<1({len(qc)}일, r 가운데 {median(d['r'] for d in qc):.3f}) M {sc_m:+.3f} · RV {sc_rv:+.3f} | "
          f"r60≥1({len(ql)}일, r 가운데 {median(d['r'] for d in ql):.3f}) M {sl_m:+.3f} · RV {sl_rv:+.3f}")
    print(f"  -- 코인 28봉 전체 원시 회귀(~ ln v20 + ln v60 + ln v250): ln|R_28| ({br_m[1]:+.3f}, {br_m[2]:+.3f}, {br_m[3]:+.3f}) | ln RV_28 ({br_rv[1]:+.3f}, {br_rv[2]:+.3f}, {br_rv[3]:+.3f})")
    # 부트스트랩(150회)
    lrb_all = [math.log(d["r"]) for d in cd_b]
    yef_all = [math.log(max(d["h"][28]["M"], 1e-4) / d["h"][28]["RV"]) for d in cd_b]
    eh_b = hinge_lad(lrb_all, yef_all, 30)
    bm_ef, bp_ef, b_rv_r, b_rv_d, b_ef_r, b_ef_d = [], [], [], [], [], []
    init_rv = lad_reg([c1q, lrq, d60q], yRVq, 30)
    init_ef = lad_reg([c1q, lrq, d60q], yEFq, 30)
    for _ in range(150):
        idx = block_idx(len(cd_b), rng)
        ehi = hinge_lad([lrb_all[i] for i in idx], [yef_all[i] for i in idx], 15, eh_b)
        bm_ef.append(ehi[1]); bp_ef.append(ehi[2])
        iq = block_idx(len(qb), rng)
        d60_b = [d60q[i] for i in iq]
        if sum(d60_b) in (0.0, float(len(iq))):
            continue
        lrq_b = [lrq[i] for i in iq]
        rv_i = lad_reg([c1q, lrq_b, d60_b], [yRVq[i] for i in iq], 15, init_rv)
        ef_i = lad_reg([c1q, lrq_b, d60_b], [yEFq[i] for i in iq], 15, init_ef)
        b_rv_r.append(rv_i[1]); b_rv_d.append(rv_i[2])
        b_ef_r.append(ef_i[1]); b_ef_d.append(ef_i[2])
    print(f"  -- 코인 28봉 블록 부트스트랩 90% 구간(150회): 전체 ln(|R|/RV) β− ({pct90(bm_ef)[0]:+.2f}, {pct90(bm_ef)[1]:+.2f}) · β+ ({pct90(bp_ef)[0]:+.2f}, {pct90(bp_ef)[1]:+.2f}) | "
          f"r<1 RV β ({pct90(b_rv_r)[0]:+.2f}, {pct90(b_rv_r)[1]:+.2f}) · δ ({pct90(b_rv_d)[0]:+.2f}, {pct90(b_rv_d)[1]:+.2f}) | "
          f"r<1 효율 β ({pct90(b_ef_r)[0]:+.2f}, {pct90(b_ef_r)[1]:+.2f}) · δ ({pct90(b_ef_d)[0]:+.2f}, {pct90(b_ef_d)[1]:+.2f})")
    # 한 해씩 빼기 & 큰 사건 방향
    yrs_b = sorted({d["date"].year for d in cd_b})
    lo_bms, lo_ds = [], []
    for yo in yrs_b:
        sub = [d for d in cd_b if d["date"].year != yo]
        lo_bms.append(hinge_lad([math.log(d["r"]) for d in sub], [ylog(d["h"][28]["M"]) for d in sub], 30)[1])
        qs = [d for d in sub if d["r"] < 1.0]
        lo_ds.append(lad_reg([[1.0] * len(qs), [math.log(d["r"]) for d in qs], [1.0 if d["r60"] < 1.0 else 0.0 for d in qs]],
                             [ylog(d["h"][28]["M"]) for d in qs], 30)[2])
    big_c = [d for d in cd_b if d["r"] < 0.70 and d["h"][28]["M"] >= 1.5]
    eps: list[list[dict]] = []
    for d in big_c:
        if not eps or (d["date"] - eps[-1][-1]["date"]).days > 28:
            eps.append([d])
        else:
            eps[-1].append(d)
    up_ep = sum(1 for ep in eps if max(ep, key=lambda d: d["h"][28]["M"])["h"][28]["R"] > 0)
    print(f"  -- 한 해 빼기(9개 해): β− 범위 [{min(lo_bms):+.3f} ~ {max(lo_bms):+.3f}] · δ 범위 [{min(lo_ds):+.3f} ~ {max(lo_ds):+.3f}] | "
          f"r<0.70 & M≥1.5 독립 사건 {len(eps)}건: 상승 {up_ep}건 · 하락 {len(eps)-up_ep}건")

    # ------------------------------------------------------------ [R7] 10절 '다음 날들의 시험' 모의시험
    print("=" * 112)
    print("[R7] 10절 '다음 날들의 시험' 검정력·실행 가능성 — (가) 10절 2의 60일 1봉 회귀(2/3 이상 β−·β+ ≤ 0), (나) 10절 4의 up3 이어진 덩이 30개 대기 기간")
    for a in TARGETS:
        cd = cell_days(DAYS[a], 1)
        tot = both0 = dm0 = dp0 = 0
        for s in range(0, len(cd) - 60 + 1, 5):
            sub = cd[s:s + 60]
            lr = [math.log(d["r"]) for d in sub]
            dm0 += not any(v < 0 for v in lr)
            dp0 += not any(v > 0 for v in lr)
            e = hinge_lad(lr, [ylog(d["h"][1]["M"]) for d in sub], 20)
            tot += 1
            both0 += (e[1] <= 0.0 and e[2] <= 0.0)
        print(f"  (가-단독) {NAME[a]} 1봉 60일 창 {tot}개: r<1 없음 {dm0}({dm0/tot:.1%}) · r>1 없음 {dp0}({dp0/tot:.1%}) · β−·β+ 모두 ≤0(거짓 기각) {both0}({both0/tot:.1%})")
    dkr = {d["date"]: d for d in cell_days(DAYS["069500"], 1)}
    dus = {d["date"]: d for d in cell_days(DAYS["360750"], 1)}
    com = sorted(set(dkr) & set(dus))
    bt1 = cell_days(DAYS["KRW-BTC"], 1)
    btd = [d["date"] for d in bt1]
    krl, usl = [dkr[dd] for dd in com], [dus[dd] for dd in com]
    ns = rej = deg = 0
    for i in range(0, len(com) - 60 + 1, 5):
        bi = bisect.bisect_left(btd, com[i])
        if bi + 60 > len(bt1):
            break
        fails = 0
        for sub in (krl[i:i + 60], usl[i:i + 60], bt1[bi:bi + 60]):
            lr = [math.log(d["r"]) for d in sub]
            if not any(v < 0 for v in lr) or not any(v > 0 for v in lr):
                deg += 1
            e = hinge_lad(lr, [ylog(d["h"][1]["M"]) for d in sub], 20)
            if e[1] <= 0.0 and e[2] <= 0.0:
                fails += 1
        ns += 1
        rej += fails >= 2
    print(f"  (가-합동 2021-08~2026-08) 60일 시작점 {ns}곳: 한쪽만 있는 퇴화 창 {deg}/{ns*3}({deg/(ns*3):.1%}) · 셋 중 둘 이상 기각(거짓 기각) {rej}/{ns}({rej/ns:.1%})")
    for a, h in [(t, hh) for t in TARGETS for hh in HORIZONS[t][:2]]:
        cd = cell_days(DAYS[a], h)
        yrs_span = (cd[-1]["date"] - cd[0]["date"]).days / 365.25
        hist, pend, fl = [], [], []
        for i, d in enumerate(cd):
            x = d["h"][h]
            hist, pend = hist + [(q, s) for q, s, tf in pend if tf <= d["at"]], [(q, s, tf) for q, s, tf in pend if tf > d["at"]]
            p3 = []
            for q, s in reversed(hist):
                if not p3 or p3[-1][0] - q >= h:
                    p3.append((q, s))
                if len(p3) == 3:
                    break
            fl.append(1 if [s for _, s in p3] == [1, 1, 1] else 0)
            s_ = 0 if x["lo"] <= x["abs"] <= x["hi"] else (1 if x["abs"] > x["hi"] else -1)
            pend.append((i, s_, x["tfinal"]))
        ru = sum(1 for i in range(len(fl)) if fl[i] and (i == 0 or not fl[i - 1]))
        rate = ru / yrs_span
        print(f"  (나) {NAME[a]} {h}봉 ({yrs_span:.1f}년): up3 이어진 덩이 {ru}개(연 {rate:.1f}개) → 30개 대기 약 {30/rate:.1f}년")

    print(f"끝. ({time.time() - t0:.0f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
