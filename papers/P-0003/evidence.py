"""P-0003 근거 계산: "뿌리 목록의 교훈 가운데 과거 시세로 잴 수 있는 것은, 과거 여러 해에도 교훈대로 하는 쪽이
안 하는 쪽보다 나았나, 아니었나?"

저장소 루트에서:  python -X utf8 papers/P-0003/evidence.py
- 읽기만 한다(records/, data/prices/, knowledge.toml). 아무 파일도 쓰지 않는다. 출력은 표준 출력뿐이다.
- 외부 패키지 없음(Python 3.11+). 시세 읽기·끝난 봉·시세 상태·계산기는 저장소 코드
  (market.prices.load, market.config.final_dt, market.pieces.read, market.calc.load_pieces/predict)를 그대로 부른다.
- 정의·규칙은 같은 폴더 plan.md(먼저 커밋)를 따른다. 바뀐 곳은 paper.md 6절.
- 가져온 코드: P-0002 evidence.py의 jl·quantile·known_closes 모양.
- 표 번호 [E1]…가 paper.md의 근거 번호다.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market import calc, config as C  # noqa: E402
from market import pieces, prices as P  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 경계 (plan.md 2절)
RECORD_END = dt.date(2026, 11, 25)
FIT_END = dt.date(2026, 6, 30)
HOLD_START, HOLD_END = dt.date(2026, 7, 1), dt.date(2026, 10, 19)
REC_START = dt.date(2026, 10, 21)
REC_TARGET_END = {"069500": dt.date(2026, 11, 24), "360750": dt.date(2026, 11, 24), "KRW-BTC": dt.date(2026, 11, 23)}
JUDGE = dt.time(8, 40)
TARGETS = ("069500", "360750", "KRW-BTC")
HORIZONS = {"069500": (1, 5, 20), "360750": (1, 5, 20), "KRW-BTC": (1, 7, 28)}
LONG = {"069500": 20, "360750": 20, "KRW-BTC": 28}
BLOCK = 60
FLIPS = 2000
SEED = 20261125
KST = C.KST
REC = ROOT / "records"

# 세운 재료: 교훈을 세운 돌아보기의 (대상, 기간, 판단 날) — plan.md 첫 문단
MATERIAL = {
    "L12": {("KRW-BTC", 28, dt.date(2026, 10, 21))},
    "L06": {("360750", 1, dt.date(2026, 11, 19))},
    "L11": {("069500", 1, dt.date(2026, 11, 20))},
    "L13": {("KRW-BTC", 1, dt.date(2026, 11, 17) + dt.timedelta(days=k)) for k in range(5)},
}


def jl(name):
    with (REC / f"{name}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["run"] <= RECORD_END.isoformat()]


def day(s):
    return dt.date.fromisoformat(s)


def pct(x, nd=1):
    return "–" if x is None else f"{x * 100:+.{nd}f}%p"


# ---------------------------------------------------------------- 시세와 계산기
ROWS = {a: P.load(a) for a in TARGETS}
FINAL = {a: [C.final_dt(C.ASSETS[a], r.date) for r in ROWS[a]] for a in TARGETS}
PIECES, KNOW = calc.load_pieces()


def prepare(a):
    """대상 a의 봉마다(판단 = 그 봉 날짜 08:40): 상태·숫자, 기간마다 계산기 범위·p·방향 곱하기, 결과."""
    rows = ROWS[a]
    out = []
    for j, r in enumerate(rows):
        at = dt.datetime.combine(r.date, JUDGE, KST)
        k = bisect.bisect_right(FINAL[a], at)
        if k < pieces.NEED:
            out.append(None)
            continue
        closes = [x.close for x in rows[:k]]
        vals, nums = pieces.read(closes[-pieces.NEED:])
        if "vol250" not in nums:
            out.append(None)
            continue
        known = rows[k - 60:k]
        oc = [abs(x.close / x.open - 1) * 100 for x in known if x.open > 0]
        cc = [abs(rows[i].close / rows[i - 1].close - 1) * 100 for i in range(k - 60, k)]
        cell = {"j": j, "date": r.date, "at": at, "k": k, "vals": vals, "nums": nums,
                "r": nums["vol20"] / nums["vol250"] if nums["vol250"] > 0 else None,
                "oc60": median(oc), "cc60": median(cc), "h": {}}
        for h in HORIZONS[a]:
            res = calc.predict(a, h, vals, PIECES, nums)
            mv = res.move()
            if mv is None or j + h - 1 >= len(rows) or r.open <= 0:
                continue
            t = rows[j + h - 1]
            ret = t.close / r.open - 1
            muls = [e.center for e in res.odds_muls]
            cell["h"][h] = {"lo": mv[0], "hi": mv[1], "p": res.p_up(), "muls": muls,
                            "ret": ret, "abs": abs(ret) * 100, "tdate": t.date,
                            "tfinal": FINAL[a][j + h - 1], "usual": nums["vol60"] * math.sqrt(h) * 100}
        out.append(cell)
    return out


def side(lo, hi, x):
    return 0 if lo <= x <= hi else (1 if x > hi else -1)


def period(a, c, h):
    x = c["h"][h]
    if x["tdate"] <= FIT_END:
        return "fit"
    if c["date"] >= HOLD_START and x["tdate"] <= HOLD_END:
        return "hold"
    if c["date"] >= REC_START and x["tdate"] <= REC_TARGET_END[a]:
        return "rec"
    return None


# ---------------------------------------------------------------- L06 절차 (plan 3절)
def run_l06(a, cells, h, trig, shift, gate=None):
    """칸 (a, h)에서 날마다 쓴 범위 배수 m. gate(cell) → 'l06'(절차 적용) / 'one'(m = 1). 빗나감은 실제로 쓴 범위로 센다."""
    used = {}
    m = 1.0
    hist = []          # (i, side) 끝난 예측, 판단 순서
    pending = []       # 아직 안 끝난 (i, lo, hi, abs, tfinal)
    since = 0          # hist에서 마지막 따짐 뒤 시작 위치
    allfin = []        # 끝난 창의 |수익| (최근 60개용)
    moves = 0
    for c in cells:
        if c is None or h not in c["h"]:
            continue
        x = c["h"][h]
        # 오늘 판단 때 끝난 예측을 넣는다
        keep = []
        for (i, lo, hi, ab, tf) in pending:
            if tf <= c["at"]:
                hist.append((i, side(lo, hi, ab)))
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
            sides = [s for _, s in picked]
            trig_side = 0
            if trig == "T1" and len(sides) >= 3 and sides[0] == sides[1] == sides[2] != 0:
                trig_side = sides[0]
            if trig == "T2":
                up, dn = sides.count(1), sides.count(-1)
                if up >= 3 or dn >= 3:
                    trig_side = 1 if up > dn else -1 if dn > up else sides[next(q for q, s in enumerate(sides) if s != 0)]
            if trig_side and len(allfin) >= 60:
                last = allfin[-60:]
                cen = math.sqrt(x["lo"] * x["hi"])
                cur_lo, cur_hi = x["lo"] * m, x["hi"] * m
                cur_n = sum(cur_lo <= v <= cur_hi for v in last)
                if shift == "S1":
                    best = None
                    for kk in range(-30, 31):
                        cc_ = cen * 1.05 ** kk
                        n_ = sum(cc_ / math.sqrt(2) <= v <= cc_ * math.sqrt(2) for v in last)
                        if best is None or n_ > best[0] or (n_ == best[0] and abs(kk) < abs(best[1])):
                            best = (n_, kk, cc_)
                    cand_n, cand_m = best[0], best[2] / cen
                else:
                    rr = c["r"]
                    cand_n = sum(x["lo"] * rr <= v <= x["hi"] * rr for v in last)
                    cand_m = rr
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


# ---------------------------------------------------------------- 견주기
def compare(a, cells, h, rule, base, days_filter=None, metric="size"):
    """rule·base: j → (lo, hi) 또는 p. 구간마다 차이 줄(날 순서). metric size: 적중 차이, brier: 확률 점수 차이(base − rule, 클수록 rule이 나음)."""
    out = defaultdict(list)
    for c in cells:
        if c is None or h not in c["h"] or c["j"] not in rule or c["j"] not in base:
            continue
        if days_filter and not days_filter(c):
            continue
        pr = period(a, c, h)
        if pr is None:
            continue
        x = c["h"][h]
        if metric == "size":
            r_, b_ = rule[c["j"]], base[c["j"]]
            sr, sb = side(*r_, x["abs"]), side(*b_, x["abs"])
            out[pr].append({"c": c, "d": (sr == 0) - (sb == 0), "sr": sr, "sb": sb})
        else:
            up = 1.0 if x["ret"] > 0 else 0.0
            pr_, pb_ = rule[c["j"]], base[c["j"]]
            out[pr].append({"c": c, "d": (pb_ - up) ** 2 - (pr_ - up) ** 2, "pr": pr_, "pb": pb_, "up": up})
    return out


def summary(rows, h, metric="size"):
    n = len(rows)
    if not n:
        return "날 0"
    if metric == "size":
        hr = sum(r["sr"] == 0 for r in rows) / n
        hb = sum(r["sb"] == 0 for r in rows) / n
        ur, dr = sum(r["sr"] == 1 for r in rows) / n, sum(r["sr"] == -1 for r in rows) / n
        ub, db = sum(r["sb"] == 1 for r in rows) / n, sum(r["sb"] == -1 for r in rows) / n
        return (f"날 {n}(독립 약 {n / h:.0f}) | 한 경우 적중 {hr:.3f}(위 {ur:.3f}·아래 {dr:.3f}) | 안 한 경우 {hb:.3f}(위 {ub:.3f}·아래 {db:.3f})"
                f" | 차이 {pct(hr - hb)}")
    br = fmean((r["pr"] - r["up"]) ** 2 for r in rows)
    bb = fmean((r["pb"] - r["up"]) ** 2 for r in rows)
    hit_b = [((r["pb"] > 0.5) == (r["up"] == 1)) for r in rows if r["pb"] != 0.5]
    return (f"날 {n}(독립 약 {n / h:.0f}) | 한 경우 확률 점수 {br:.4f} | 안 한 경우 {bb:.4f} | 차이(안 − 한) {bb - br:+.4f}"
            f" | 안 한 경우 방향 적중 {sum(hit_b)}/{len(hit_b)}" + (f"={sum(hit_b) / len(hit_b):.3f}" if hit_b else ""))


def flip_p(series_list, rng):
    """series_list: 변형마다 날 차이 줄(같은 날들). 60개 덩어리 부호 뒤집기. 변형마다 p와 '가장 나은 변형' p."""
    blocks = []
    for s in series_list:
        bl = [sum(s[i:i + BLOCK]) for i in range(0, len(s), BLOCK)]
        blocks.append(bl)
    nb = len(blocks[0])
    obs = [sum(b) for b in blocks]
    best_obs = max(obs)
    ge = [0] * len(blocks)
    ge_best = 0
    for _ in range(FLIPS):
        sg = [1 if rng.random() < 0.5 else -1 for _ in range(nb)]
        tots = [sum(x * y for x, y in zip(b, sg)) for b in blocks]
        for q, t in enumerate(tots):
            ge[q] += t >= obs[q]
        ge_best += max(tots) >= best_obs
    return [g / FLIPS for g in ge], ge_best / FLIPS


def drop_block(rows):
    """차이 합에 가장 크게 보탠 60개 덩어리 하나(합의 부호 쪽)를 뺀 평균 차이."""
    s = [r["d"] for r in rows]
    tot = sum(s)
    sg = 1 if tot >= 0 else -1
    bl = [(sum(s[i:i + BLOCK]), i) for i in range(0, len(s), BLOCK)]
    b, i0 = max(bl, key=lambda t: t[0] * sg)
    rest = s[:i0] + s[i0 + BLOCK:]
    return (tot / len(s), (tot - b) / len(rest) if rest else None, rows[i0]["c"]["date"], rows[min(i0 + BLOCK, len(rows)) - 1]["c"]["date"])


def by_year(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["c"]["date"].year].append(r["d"])
    return ", ".join(f"{y} {fmean(v):+.3f}({len(v)})" for y, v in sorted(g.items()))


def by_vol(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["c"]["vals"]["S_vol_high"]].append(r["d"])
    return ", ".join(f"S_vol_high={k} {fmean(v):+.3f}({len(v)})" for k, v in sorted(g.items()))


SUMMARY = []


def mean_d(rows):
    return fmean(r["d"] for r in rows) if rows else None


def report(title, a, h, variants, metric="size", rng=None, extra=None):
    """variants: [(이름, rows by period)]. 맞추는·떼어 둔·우리 기록을 따로."""
    print(f"  -- {title} [{a} {h}봉]")
    ps = None
    for name, rows in variants:
        for pr, lab in (("fit", "맞추는"), ("hold", "떼어 둔"), ("rec", "우리 기록")):
            rs = rows.get(pr, [])
            if pr == "rec" and extra:
                mat = [r for r in rs if (a, h, r["c"]["date"]) in extra]
                rs = [r for r in rs if (a, h, r["c"]["date"]) not in extra]
                if mat:
                    print(f"     {name:22s} {lab}(세운 재료 뺌, 뺀 날 {len(mat)}: {', '.join(str(r['c']['date']) for r in mat)} — 그 날 차이 {sum(r['d'] for r in mat):+.3f})")
            print(f"     {name:22s} {lab:5s} {summary(rs, h, metric)}")
        fr = rows.get("fit", [])
        recr = [r for r in rows.get("rec", []) if not (extra and (a, h, r["c"]["date"]) in extra)]
        SUMMARY.append(("row", title, a, h, name, mean_d(fr), len(fr), mean_d(rows.get("hold", [])), len(rows.get("hold", [])),
                        mean_d(recr), len(recr), drop_block(fr)[1] if fr else None))
        if fr:
            m0, m1, d0, d1 = drop_block(fr)
            print(f"     {'':22s} 맞추는: 해마다 평균 차이 {by_year(fr)}")
            print(f"     {'':22s} 맞추는: 가장 크게 보탠 60개({d0} ~ {d1}) 빼면 평균 차이 {m0:+.4f} → {m1:+.4f} | {by_vol(fr)}")
    if rng is not None:
        fits = [rows.get("fit", []) for _, rows in variants]
        if fits and all(len(f) == len(fits[0]) and f for f in fits):
            ps, pb = flip_p([[r["d"] for r in f] for f in fits], rng)
            SUMMARY.append(("best", title, a, h, len(variants), pb))
            for (n_, _), p_ in zip(variants, ps):
                SUMMARY.append(("p", title, a, h, n_, p_))
            print(f"     우연히 그만큼 나올 비율(맞추는, 60개 덩어리 부호 뒤집기 {FLIPS}번): "
                  + ", ".join(f"{n} {p:.3f}" for (n, _), p in zip(variants, ps)) + (f" | 변형 {len(variants)}개 가운데 가장 나은 것 {pb:.3f}" if len(variants) > 1 else ""))


# ================================================================ 실행
def main() -> int:
    rng = random.Random(SEED)
    print("=" * 110)
    print(f"[E1] 재료 — 논리집 지문 {KNOW}, 계산기 조각 {len(PIECES)}개. 대상마다 봉 날짜 08:40 판단, 끝난 봉만. 결과 = 시가(d) → 종가(d+h−1)")
    CELLS = {a: prepare(a) for a in TARGETS}
    for a in TARGETS:
        for h in HORIZONS[a]:
            cnt = Counter(period(a, c, h) for c in CELLS[a] if c and h in c["h"])
            first = next(c["date"] for c in CELLS[a] if c and h in c["h"])
            print(f"  {a} {h}봉: 첫 판단 {first}, 맞추는 {cnt['fit']}일 · 떼어 둔 {cnt['hold']}일 · 우리 기록 {cnt['rec']}일")
    base = {a: {h: {c["j"]: (c["h"][h]["lo"], c["h"][h]["hi"]) for c in CELLS[a] if c and h in c["h"]} for h in HORIZONS[a]} for a in TARGETS}

    # ---------- [E2] 확인 1: 판단 때 아는 값 — 우리 기록의 시세 대 보기와 다시 계산한 값
    print("=" * 110)
    print("[E2] 판단 때 아는 값 — 우리 기록 판단 날의 reading(세 대상)을 그날 decided_at에 끝난 봉으로 다시 계산해 견줌")
    runs_ = {day(r["run"]): dt.datetime.fromisoformat(r["steps"]["decided_at"]) for r in jl("run") if r.get("mode") == "live"}
    same = diff = late = 0
    for r in jl("reading"):
        if r["asset"] not in TARGETS:
            continue
        d = day(r["run"])
        at = runs_[d]
        k = bisect.bisect_right(FINAL[r["asset"]], at)
        v, _ = pieces.read([x.close for x in ROWS[r["asset"]][:k]][-pieces.NEED:])
        same += all(v[s] == r["values"].get(s) for s in pieces.STATES)
        diff += not all(v[s] == r["values"].get(s) for s in pieces.STATES)
        late += C.final_dt(C.ASSETS[r["asset"]], day(r["known_through"])) > at
    print(f"  reading 줄 같음 {same} · 다름 {diff} · 본 마지막 봉이 판단 뒤에 끝난 줄 {late}")
    print("  과거 시세: 범위·r·평소·60봉 가운데 값은 판단 시각에 끝난 봉만, 지난 빗나감·지난 움직임은 목표 봉 final_dt ≤ 판단 시각인 예측만(run_l06·L13)")

    # ---------- [E3] L06
    print("=" * 110)
    print("[E3] L06 — 빗나간 뒤 범위 끝 옮기기. 안 한 경우 N0 = 계산기. 변형: 방아쇠 T1(최근 독립 셋이 같은 쪽)·T2(같은 쪽 셋 이상) × 옮길 범위 S1(최근 60개를 가장 많이 담는 2배 폭)·S2(계산기 × r)"
          " · 씨앗 문장 N1(|ln r| > ln 1.2면 m = r)")
    l06 = {}
    for a in TARGETS:
        for h in HORIZONS[a]:
            vs = []
            for tr in ("T1", "T2"):
                for sh in ("S1", "S2"):
                    used, mv = run_l06(a, CELLS[a], h, tr, sh)
                    l06[(a, h, tr + sh)] = used
                    vs.append((f"L06-{tr}{sh}(옮김 {mv})", compare(a, CELLS[a], h, used, base[a][h])))
            n1 = {c["j"]: ((c["h"][h]["lo"] * c["r"], c["h"][h]["hi"] * c["r"]) if abs(math.log(c["r"])) > math.log(1.2) else (c["h"][h]["lo"], c["h"][h]["hi"]))
                  for c in CELLS[a] if c and h in c["h"]}
            vs.append(("N1 씨앗(비율로)", compare(a, CELLS[a], h, n1, base[a][h])))
            report("L06 대 N0", a, h, vs, rng=rng, extra=MATERIAL["L06"])

    # ---------- [E4] L12
    print("=" * 110)
    print("[E4] L12 — 긴 기간(ETF 20봉·코인 28봉)이고 r < 1인 날만. 한 경우 = 계산기(m = 1), 안 한 경우 N-a m = r, N-b m = √r. 차이 = 한 경우 − 안 한 경우")
    for a in TARGETS:
        h = LONG[a]
        dom = lambda c: c["r"] is not None and c["r"] < 1
        na = {c["j"]: (c["h"][h]["lo"] * c["r"], c["h"][h]["hi"] * c["r"]) for c in CELLS[a] if c and h in c["h"]}
        nb = {c["j"]: (c["h"][h]["lo"] * math.sqrt(c["r"]), c["h"][h]["hi"] * math.sqrt(c["r"])) for c in CELLS[a] if c and h in c["h"]}
        vs = [("L12 대 N-a(× r)", compare(a, CELLS[a], h, base[a][h], na, dom)),
              ("L12 대 N-b(× √r)", compare(a, CELLS[a], h, base[a][h], nb, dom))]
        report("L12", a, h, vs, rng=rng, extra=MATERIAL["L12"])

    # ---------- [E5] 충돌 L06↔L12 — 가른 조건대로
    print("=" * 110)
    print("[E5] 충돌 L06↔L12 — 가른 조건대로 G: 긴 기간 & r < 1 → m = 1 / 1봉·1주 또는 r > 1.2 → L06 절차 / 그 밖 m = 1. 견줄 것: N0(계산기)와 L06만(모든 칸 L06 절차)")
    for a in TARGETS:
        for h in HORIZONS[a]:
            def gate(c, h=h, a=a):
                if h == LONG[a] and c["r"] < 1:
                    return "one"
                if h != LONG[a] or c["r"] > 1.2:
                    return "l06"
                return "one"
            vs, vs2 = [], []
            for v in ("T1S1", "T1S2", "T2S1", "T2S2"):
                used, mv = run_l06(a, CELLS[a], h, v[:2], v[2:], gate)
                vs.append((f"G-{v}(옮김 {mv}) 대 N0", compare(a, CELLS[a], h, used, base[a][h])))
                vs2.append((f"G-{v} 대 L06만", compare(a, CELLS[a], h, used, l06[(a, h, v)])))
            report("G 대 N0", a, h, vs, rng=rng)
            if h == LONG[a]:
                report("G 대 L06만(긴 기간 칸에서만 다름)", a, h, vs2, rng=rng)

    # ---------- [E6] L11
    print("=" * 110)
    print("[E6] L11 — 1봉. 한 경우 Y = 최근 60봉 |시가→종가| 가운데 값 × [0.7, 1.4]. 안 한 경우 N-a 계산기, N-b 최근 60봉 |종가→종가| 가운데 값 × [0.7, 1.4]")
    for a in TARGETS:
        h = 1
        y = {c["j"]: (c["oc60"] * 0.7, c["oc60"] * 1.4) for c in CELLS[a] if c and h in c["h"]}
        nb = {c["j"]: (c["cc60"] * 0.7, c["cc60"] * 1.4) for c in CELLS[a] if c and h in c["h"]}
        vs = [("Y 대 N-a(계산기)", compare(a, CELLS[a], h, y, base[a][h])),
              ("Y 대 N-b(종가→종가)", compare(a, CELLS[a], h, y, nb))]
        report("L11", a, h, vs, rng=rng, extra=MATERIAL["L11"])

    # ---------- [E7] L13 (해석 ㄱ)
    print("=" * 110)
    print("[E7] L13 해석 ㄱ — 시세 상태 S_big_move·S_drop_5가 켜진 날만. 한 경우 = 지난 켜진 날(750봉·끝난 것·5개 이상) |수익|÷평소 가운데 값 × 오늘 평소가"
          " 계산기 범위 안이면 계산기, 밖이면 그 값을 가운데로 2배 폭. 안 한 경우 N-a 평소 × [1.25, 2.5], N-b 계산기 × 2. 기준 N0 = 계산기")
    for st in ("S_big_move", "S_drop_5"):
        for a in TARGETS:
            for h in HORIZONS[a]:
                l13, moved = {}, 0
                on_hist = []   # (tfinal, multiple)
                for c in CELLS[a]:
                    if c is None or h not in c["h"]:
                        continue
                    x = c["h"][h]
                    if c["vals"][st] == "예":
                        past = [mlt for (tf, j0, mlt) in on_hist if tf <= c["at"] and c["j"] - j0 <= 750]
                        if len(past) >= 5:
                            med = median(past) * x["usual"]
                            if x["lo"] <= med <= x["hi"]:
                                l13[c["j"]] = (x["lo"], x["hi"])
                            else:
                                l13[c["j"]] = (med / math.sqrt(2), med * math.sqrt(2))
                                moved += 1
                        else:
                            l13[c["j"]] = (x["lo"], x["hi"])
                        on_hist.append((x["tfinal"], c["j"], x["abs"] / x["usual"] if x["usual"] > 0 else 0))
                on = lambda c: c["vals"][st] == "예"
                na = {c["j"]: (c["h"][h]["usual"] * 1.25, c["h"][h]["usual"] * 2.5) for c in CELLS[a] if c and h in c["h"]}
                nb = {c["j"]: (c["h"][h]["lo"] * 2, c["h"][h]["hi"] * 2) for c in CELLS[a] if c and h in c["h"]}
                vs = [(f"L13(옮김 {moved}) 대 N-a", compare(a, CELLS[a], h, l13, na, on)),
                      ("L13 대 N-b(× 2)", compare(a, CELLS[a], h, l13, nb, on)),
                      ("L13 대 N0(계산기)", compare(a, CELLS[a], h, l13, base[a][h], on))]
                report(f"L13 {st}", a, h, vs, rng=rng, extra=MATERIAL["L13"])

    # ---------- [E8] L03 반대 조각
    print("=" * 110)
    print("[E8] L03 — 계산기 오를 확률 곱하기 가운데 > 1인 것과 < 1인 것이 둘 다 켜진 날만. 한 경우 p = 0.5, 안 한 경우 계산기 p. 차이 = 안 한 경우 확률 점수 − 한 경우(양수면 0.5가 나음)")
    dirs = Counter()
    for pc in PIECES:
        for e in pc.effects:
            if e.on == "up_odds" and e.op == "mul":
                dirs[(pc.id, "> 1" if e.center > 1 else "< 1" if e.center < 1 else "= 1")] += 1
    print("  계산기 오를 확률 곱하기의 방향(조각, 가운데 값, 대상·기간 칸 수): " + ", ".join(f"{k[0]} {k[1]} {v}칸" for k, v in sorted(dirs.items())))
    for a in TARGETS:
        for h in HORIZONS[a]:
            conf = lambda c, h=h: any(m > 1 for m in c["h"][h]["muls"]) and any(m < 1 for m in c["h"][h]["muls"])
            half = {c["j"]: 0.5 for c in CELLS[a] if c and h in c["h"]}
            cp = {c["j"]: c["h"][h]["p"] for c in CELLS[a] if c and h in c["h"]}
            nconf = sum(1 for c in CELLS[a] if c and h in c["h"] and conf(c))
            if nconf == 0:
                print(f"  -- L03 [{a} {h}봉] 반대 조각이 같이 켜진 날 0 — 잴 날이 없음")
                continue
            report("L03", a, h, [("p=0.5 대 계산기", compare(a, CELLS[a], h, half, cp, conf, metric="brier"))], metric="brier", rng=rng)

    # ---------- [E9] 나. 갈래 — 우리 기록
    print("=" * 110)
    print("[E9] 나. 갈래(우리 기록에만) — lesson.jsonl tried 합(기록된 값), L13 해석 ㄴ·L05 공포 부분, L02")
    tried = defaultdict(Counter)
    for r in jl("lesson"):
        for k, v in (r.get("tried") or {}).items():
            tried[r["root"]][k] += v
    for root in ("L02", "L04", "L05", "L07", "L13", "L14", "L03", "L06", "L11", "L12"):
        t = tried.get(root, Counter())
        print(f"  {root}: 해 봄 나아짐 {t['나아짐']} · 모름 {t['모름']} · 나빠짐 {t['나빠짐']}")
    hr = {day(r["run"]): r for r in jl("hand_reading")}
    sc = {}
    for r in jl("score"):
        if r.get("counted"):
            sc[r["prediction"]] = r
    print("  L13 해석 ㄴ·L05(공포 지수): S_q_fear_spike '예'인 날 코인 1봉 — 손 / 계산기 크기 적중 (* = 세운 재료 날)")
    hits = Counter()
    for d in sorted(hr):
        if hr[d]["values"].get("S_q_fear_spike") != "예":
            continue
        hh, cc_ = sc.get(f"{d}:hand:KRW-BTC:1"), sc.get(f"{d}:calc:KRW-BTC:1")
        mat = (("KRW-BTC", 1, d) in MATERIAL["L13"])
        mark = "*" if mat else " "
        print(f"   {mark}{d} 손 {hh['size_hit'] if hh else '–'} · 계산기 {cc_['size_hit'] if cc_ else '–'}")
        if hh and cc_ and not mat:
            hits["n"] += 1
            hits["hand"] += bool(hh["size_hit"])
            hits["calc"] += bool(cc_["size_hit"])
    print(f"   세운 재료 뺀 날 {hits['n']}: 손 적중 {hits['hand']} · 계산기 적중 {hits['calc']}")
    preds = {}
    with (REC / "prediction.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["run"] <= RECORD_END.isoformat() and r["predictor"] == "hand" and r.get("layer") == "기타":
                preds[r["id"]] = r
    lay = Counter()
    with (REC / "prediction.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["run"] <= RECORD_END.isoformat() and r["predictor"] == "hand":
                lay[(r.get("layer") or "칸 없음(2026-11-04 전)", "p = 0.5" if r.get("p_up") == 0.5 else "p ≠ 0.5")] += 1
    print("  L02 재료: 손 예측 줄의 layer × p (모든 줄): " + ", ".join(f"{k[0]}·{k[1]} {v}" for k, v in sorted(lay.items())))
    g = defaultdict(list)
    for pid, r in preds.items():
        s = sc.get(pid)
        if not s or r.get("p_up") in (None, 0.5) or s.get("up") is None:
            continue
        up = 1.0 if s["up"] else 0.0
        g["1봉" if r["horizon"] == 1 else "1주" if r["horizon"] in (5, 7) else "4주"].append(((r["p_up"] - up) ** 2, 0.25))
    print("  L02: 손 예측 layer '기타'(조각 없이 판단 때 정보)이고 p ≠ 0.5, 한 번만 센 채점 — 확률 점수 대 0.5(0.25)")
    for k in ("1봉", "1주", "4주"):
        v = g.get(k, [])
        if v:
            print(f"   {k}: {len(v)}건, 확률 점수 {fmean(x for x, _ in v):.4f} 대 0.5 → 0.2500 (차이 {0.25 - fmean(x for x, _ in v):+.4f}, 양수면 기운 쪽이 나음)")

    # ---------- [E10] 판정 계획을 과거 여러 시작점에서 (8절)
    print("=" * 110)
    print(f"[E10] 판정 계획을 과거에서 — 맞추는 + 떼어 둔 구간, 250봉 창을 60봉씩 밀며 창마다 '한 경우 − 안 한 경우' 평균 차이의 부호(나음 / 같음 / 못함)")

    def windows(rows_by_pr):
        rows = rows_by_pr.get("fit", []) + rows_by_pr.get("hold", [])
        res = Counter()
        for i in range(0, max(0, len(rows) - 250) + 1, 60):
            s = sum(r["d"] for r in rows[i:i + 250])
            res["나음" if s > 0 else "못함" if s < 0 else "같음"] += 1
        return res

    plans = []
    for a in TARGETS:
        for h in HORIZONS[a]:
            plans.append((f"L06-T2S1 대 N0 [{a} {h}]", compare(a, CELLS[a], h, l06[(a, h, "T2S1")], base[a][h])))
        h = LONG[a]
        na = {c["j"]: (c["h"][h]["lo"] * c["r"], c["h"][h]["hi"] * c["r"]) for c in CELLS[a] if c and h in c["h"]}
        plans.append((f"L12 대 N-a [{a} {h}]", compare(a, CELLS[a], h, base[a][h], na, lambda c: c["r"] < 1)))
        y = {c["j"]: (c["oc60"] * 0.7, c["oc60"] * 1.4) for c in CELLS[a] if c and 1 in c["h"]}
        plans.append((f"L11 Y 대 계산기 [{a} 1]", compare(a, CELLS[a], 1, y, base[a][1])))
    for name, rows in plans:
        w = windows(rows)
        tot = sum(w.values())
        if tot:
            print(f"  {name}: 창 {tot} — 나음 {w['나음']} ({w['나음'] / tot:.2f}) · 같음 {w['같음']} · 못함 {w['못함']} ({w['못함'] / tot:.2f})")
    # ---------- [E11] 요약
    print("=" * 110)
    print("[E11] 요약 — 칸마다 평균 차이(한 경우 − 안 한 경우; 크기는 적중 비율 차이, 세운 재료 날 뺌), 맞추는 구간 덩어리 부호 뒤집기 비율, 가장 크게 보탠 60개를 뺀 맞추는 차이")
    pmap = {(t, a, h, n): p for kind, t, a, h, n, p in [x for x in SUMMARY if x[0] == "p"]}
    for x in SUMMARY:
        if x[0] != "row":
            continue
        _, t, a, h, n, f, nf, ho, nh, rc, nr, dr = x
        fm = lambda v: "–" if v is None else f"{v * 100:+.1f}"
        p_ = pmap.get((t, a, h, n))
        print(f"  {t[:18]:18s} {a:7s} {h:2d} {n[:26]:26s} 맞추는 {fm(f):>6}({nf}) 떼어 둔 {fm(ho):>6}({nh}) 우리 기록 {fm(rc):>6}({nr})"
              f" | 비율 {('%.3f' % p_) if p_ is not None else '–':>5} | 한 덩어리 뺌 {fm(dr):>6}")
    print("  변형 가운데 가장 나은 것이 우연히 그만큼 나올 비율: " + "; ".join(f"{x[1][:14]} {x[2]} {x[3]} ({x[4]}개) {x[5]:.3f}" for x in SUMMARY if x[0] == "best"))
    # ---------- [E12] 교훈별 부호 세기
    print("=" * 110)
    print("[E12] 교훈별 부호 세기 — E11의 줄(칸 × 변형)마다 평균 차이가 + / 0 / − 인 수, 구간마다. 칸 평균은 같은 무게")
    grp = defaultdict(list)
    for x in SUMMARY:
        if x[0] == "row":
            key = x[1].split(" 대 ")[0] if not x[1].startswith("G 대 L06만") else "G 대 L06만"
            if x[1] == "G 대 N0":
                key = "G 대 N0"
            if key.startswith("L13") or key in ("L12", "L11"):
                var = "대 " + x[4].split(" 대 ")[-1].split("(")[0]
            else:
                var = x[4].split("(")[0].split(" 대 ")[0]
            grp[(key, var)].append(x)
    for (k, v), xs in sorted(grp.items()):
        parts = []
        for idx, lab in ((5, "맞추는"), (7, "떼어 둔"), (9, "우리 기록")):
            vals = [x[idx] for x in xs if x[idx] is not None]
            pos = sum(1 for z in vals if z > 1e-12)
            neg = sum(1 for z in vals if z < -1e-12)
            parts.append(f"{lab} +{pos}/0 {len(vals) - pos - neg}/−{neg}" + (f" 평균 {fmean(vals) * 100:+.1f}%p" if vals else ""))
        print(f"  {k:14s} {v:16s} 줄 {len(xs):2d} | " + " | ".join(parts))
    print("끝.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
