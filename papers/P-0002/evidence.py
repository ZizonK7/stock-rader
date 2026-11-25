"""P-0002 근거 계산: "시장 전체(세 대상 같은 비중 묶음)가 5거래일 동안 크게 내린 때와 크게 오른 때를,
판단하던 때 잴 수 있던 값으로 평소와 가를 수 있었나, 없었나?"

저장소 루트에서:  python -X utf8 papers/P-0002/evidence.py
- 읽기만 한다(records/, data/prices/). 아무 파일도 쓰지 않는다. 출력은 표준 출력뿐이다.
- 외부 패키지 없음(Python 3.11+). 시세 읽기·'판단 때 끝나 있던 봉'·시세 상태는 저장소 코드
  (market.prices.load, market.config.final_dt, market.pieces.read)를 그대로 부른다.
- 정의·후보·잣대는 같은 폴더 plan.md(먼저 커밋)를 따른다. 바뀐 곳은 paper.md 6절.
- 가져온 코드: P-0001 evidence.py의 jl·quantile·runs·majority·known_closes·states_at(같은 모양, gap 처리를 고침 —
  결과 쪽 사건은 gap = h − 1, 조건 구간은 gap = 1, 거래일 순번으로).
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
from market import config as C  # noqa: E402
from market import pieces, prices as P  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 경계 (plan.md 1절)
RECORD_END = dt.date(2026, 11, 25)      # 이 날짜까지의 run 줄만 읽는다
FIT_END = dt.date(2026, 6, 30)          # 맞추는 구간: 창 끝 ≤ 이 날
HOLD_START, HOLD_END = dt.date(2026, 7, 1), dt.date(2026, 10, 19)   # 떼어 둔 구간
REC_START, REC_TARGET_END = dt.date(2026, 10, 21), dt.date(2026, 11, 23)  # 우리 기록(창 끝 ≤ 11-23)
H = 5
TAIL, TAIL_ALT = 0.10, (0.05, 0.20)
SHIFT_MIN = 60
JUDGE = dt.time(8, 40)
FEE = {"069500": 0.00015, "360750": 0.00015, "KRW-BTC": 0.0005}  # market/rules.py krx_fee·upbit_fee

KST = C.KST
REC = ROOT / "records"
ETFS = ("069500", "360750")
COIN = "KRW-BTC"
TARGETS = ETFS + (COIN,)


def jl(name: str) -> list[dict]:
    with (REC / f"{name}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["run"] <= RECORD_END.isoformat()]


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def pct(x, nd=2):
    return "–" if x is None else f"{x * 100:+.{nd}f}%"


def fr(a, n):
    return f"{a}/{n}=" + (f"{a / n:.3f}" if n else "–")


def quantile(xs, q):
    s = sorted(xs)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def majority(vals, want="예"):
    yes = sum(v == want for v in vals)
    no = sum(v is not None and v not in (want, "모름") for v in vals)
    return "예" if yes >= 2 else "아니오" if no >= 2 else "모름"


# ---------------------------------------------------------------- 시세
ROWS = {a: P.load(a) for a in TARGETS}
FINAL = {a: [C.final_dt(C.ASSETS[a], r.date) for r in ROWS[a]] for a in TARGETS}
BY = {a: {r.date: r for r in ROWS[a]} for a in TARGETS}
KRX = sorted(set(BY["069500"]) & set(BY["360750"]))
KIDX = {d: i for i, d in enumerate(KRX)}


def known_closes(a, at):
    k = bisect.bisect_right(FINAL[a], at)
    return [r.close for r in ROWS[a][:k]]


def states_at(at):
    return {a: pieces.read(known_closes(a, at)[-pieces.NEED:]) for a in TARGETS}


def r_score(i):
    """본 창: ETF 시가(d) → 종가(d+4), 코인 날짜 d 일봉 시가 → 날짜 d+4 일봉 종가. 세 대상 평균."""
    if i + H - 1 >= len(KRX):
        return None
    d, t = KRX[i], KRX[i + H - 1]
    if d not in BY[COIN] or t not in BY[COIN]:
        return None
    rs = [BY[a][t].close / BY[a][d].open - 1 for a in ETFS] + [BY[COIN][t].close / BY[COIN][d].open - 1]
    return fmean(rs)


def r_hold(i):
    """곁 창: 세 대상 모두 시가(d) → 시가(d+5). 코인 09:00 값 = 그 날짜 일봉 시가."""
    if i + H >= len(KRX):
        return None
    d, t = KRX[i], KRX[i + H]
    if d not in BY[COIN] or t not in BY[COIN]:
        return None
    rs = [BY[a][t].open / BY[a][d].open - 1 for a in ETFS] + [BY[COIN][t].open / BY[COIN][d].open - 1]
    return fmean(rs)


def end_date(i):
    return KRX[i + H - 1]


# ---------------------------------------------------------------- 통계
def runs(days, flag, gap):
    out = []
    for d in days:
        if not flag.get(d):
            continue
        if out and KIDX[d] - KIDX[out[-1][-1]] <= gap:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def stats(days, cond, y, r=None):
    """days: 거래일 순서. cond: d → 예/아니오/모름. y: d → bool(꼬리). 예·아니오인 날만 센다."""
    ds = [d for d in days if cond.get(d) in ("예", "아니오") and d in y]
    on = {d: cond[d] == "예" for d in ds}
    n_on = sum(on.values())
    n_off = len(ds) - n_on
    a = sum(on[d] and y[d] for d in ds)
    c = sum((not on[d]) and y[d] for d in ds)
    p_on = a / n_on if n_on else None
    p_off = c / n_off if n_off else None
    z = zstat(a, n_on, c, n_off)
    eps = runs(ds, {d: y[d] for d in ds}, H - 1)
    expl = sum(1 for e in eps if on[e[0]])
    on_runs = runs(ds, on, 1)
    off_runs = runs(ds, {d: not on[d] for d in ds}, 1)
    yset = {d for d in ds if y[d]}
    false = sum(1 for rn in on_runs if not (set(rn) & yset))
    m_on = fmean([r[d] for d in ds if on[d]]) if r and n_on else None
    m_off = fmean([r[d] for d in ds if not on[d]]) if r and n_off else None
    return {"ds": ds, "on": on, "n": len(ds), "n_on": n_on, "n_off": n_off, "a": a, "c": c, "p_on": p_on,
            "p_off": p_off, "ratio": (p_on / p_off) if p_on is not None and p_off else None, "z": z,
            "eps": len(eps), "expl": expl, "on_share": n_on / len(ds) if ds else None,
            "on_runs": len(on_runs), "off_runs": len(off_runs), "false": false, "m_on": m_on, "m_off": m_off,
            "on_run_list": on_runs, "off_run_list": off_runs}


def zstat(a, n1, c, n2):
    if not n1 or not n2:
        return None
    p = (a + c) / (n1 + n2)
    if p in (0, 1):
        return 0.0
    return (a / n1 - c / n2) / math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))


def fmt(s, name=""):
    rr = f"{s['ratio']:.2f}" if s["ratio"] is not None else "–"
    z = f"{s['z']:+.2f}" if s["z"] is not None else "–"
    es = f"{s['expl']}/{s['eps']}" if s["eps"] else "0/0"
    return (f"{name}켜짐 {fr(s['a'], s['n_on'])} 꺼짐 {fr(s['c'], s['n_off'])} 배 {rr} z {z} | 사건 {s['eps']} 중 켜진 때 시작 {es}"
            f" (켜진 날 몫 {s['on_share']:.2f}) | 켜진 구간 {s['on_runs']}(헛울림 {s['false']}) 꺼진 구간 {s['off_runs']}")


def rot_counts(on_list, y_list, ks):
    """조건 줄을 k칸 순환 이동했을 때 켜짐∧꼬리 수. on_list·y_list는 같은 길이의 0/1."""
    n = len(on_list)
    pos = [j for j, v in enumerate(on_list) if v]
    out = {}
    for k in ks:
        out[k] = sum(y_list[(p + k) % n] for p in pos)
    return out


# ================================================================ 실행
def main() -> int:
    # ---------- 판단 날과 상태
    past = {}      # d → states (08:40)
    for d in KRX:
        past[d] = states_at(dt.datetime.combine(d, JUDGE, KST))
    valid = [d for d in KRX if all(past[d][a][0]["S_vol_high"] != "모름" for a in TARGETS)]
    vm_series = [(d, fmean(past[d][a][1]["vol20"] for a in TARGETS)) for d in valid]
    vm_dates = [x[0] for x in vm_series]

    def b1_value(d, v):
        k = bisect.bisect_left(vm_dates, d)
        prev = [x[1] for x in vm_series[:k]]
        if v is None or len(prev) < 250:
            return "모름"
        return "예" if v > median(prev) else "아니오"

    # 달의 마지막 3거래일(거래소 달력 = 시세 파일의 거래일)
    last3 = set()
    by_month = defaultdict(list)
    for d in KRX:
        by_month[(d.year, d.month)].append(d)
    for (y, m), ds in by_month.items():
        if (y, m) != (KRX[-1].year, KRX[-1].month):  # 끝 달은 아직 다 안 나옴
            last3.update(ds[-3:])
    last3.update({dt.date(2026, 11, 26), dt.date(2026, 11, 27), dt.date(2026, 11, 30)})  # 거래소 달력(평일, 휴장 없음)

    STATE_IDS = [("A1", "S_vol_high"), ("A2", "S_trend_60"), ("A3", "S_above_ma200"), ("A4", "S_trend_20"),
                 ("A5", "S_drop_5"), ("A6", "S_deep_drawdown"), ("A7", "S_big_move")]
    CANDS = [c for c, _ in STATE_IDS] + ["B1", "B2", "K1", "K2"]
    DESC = {c: f"{s} '예' 셋 중 둘 이상" for c, s in STATE_IDS}
    DESC["B1"] = "세 대상 vol20 평균 > 그때까지의 가운데 값(250개 이상)"
    DESC["B2"] = "국내·해외 ETF 둘 다 S_vol_high '예'(결과를 보고 고른 후보)"
    DESC["K1"] = "판단 날이 그 달 마지막 3거래일"
    DESC["K2"] = "판단 날이 11~4월"

    def cond_values(d, st):
        out = {}
        for c, s in STATE_IDS:
            out[c] = majority([st[a][0][s] for a in TARGETS])
        out["B1"] = b1_value(d, fmean(st[a][1]["vol20"] for a in TARGETS) if all("vol20" in st[a][1] for a in TARGETS) else None)
        e = [st[a][0]["S_vol_high"] for a in ETFS]
        out["B2"] = "모름" if "모름" in e else ("예" if e == ["예", "예"] else "아니오")
        out["K1"] = "예" if d in last3 else "아니오"
        out["K2"] = "예" if d.month in (11, 12, 1, 2, 3, 4) else "아니오"
        return out

    cv = {d: cond_values(d, past[d]) for d in valid}

    # ---------- 결과
    rs = {d: r_score(KIDX[d]) for d in KRX}
    rh = {d: r_hold(KIDX[d]) for d in KRX}
    thr_days = [d for d in KRX if rs[d] is not None and end_date(KIDX[d]) <= FIT_END]
    xs = [rs[d] for d in thr_days]
    LO = {q: quantile(xs, q) for q in (TAIL,) + TAIL_ALT}
    HI = {q: quantile(xs, 1 - q) for q in (TAIL,) + TAIL_ALT}
    fit = [d for d in valid if rs[d] is not None and end_date(KIDX[d]) <= FIT_END]
    hold = [d for d in valid if rs[d] is not None and d >= HOLD_START and end_date(KIDX[d]) <= HOLD_END]
    print("=" * 110)
    print(f"[E1] 결과의 정의 — 본 창 5거래일(ETF 시가 d → 종가 d+4, 코인 d 일봉 시가 → d+4 일봉 종가), 세 대상 평균")
    print(f"  문턱 표본: 판단 {thr_days[0]} ~ {thr_days[-1]} {len(thr_days)}일(창 끝 ≤ {FIT_END})")
    for q in (TAIL,) + TAIL_ALT:
        print(f"  하위 {q:.0%} {pct(LO[q])} / 상위 {q:.0%} {pct(HI[q])}")
    print(f"  맞추는 구간 판단 날 {len(fit)}일: {fit[0]} ~ {fit[-1]} | 떼어 둔 구간 {len(hold)}일: {hold[0]} ~ {hold[-1]}")
    # z5
    sig = {d: fmean(past[d][a][1]["vol20"] for a in TARGETS) for d in valid}
    z5 = {d: rs[d] / (sig[d] * math.sqrt(H)) for d in valid if rs[d] is not None and sig[d] > 0}
    zx = [z5[d] for d in fit]
    ZLO, ZHI = quantile(zx, TAIL), quantile(zx, 1 - TAIL)
    print(f"  흔들림으로 나눈 결과 z5 = 본 창 수익 ÷ (세 대상 vol20 평균 × √5): 맞추는 구간 하위 10% {ZLO:+.3f}, 상위 10% {ZHI:+.3f}")
    print(f"  곁 창(시가 d → 시가 d+5) 문턱은 본 창 문턱을 그대로 쓴다")

    def tails(days, r, lo, hi):
        return ({d: r[d] <= lo for d in days if r.get(d) is not None},
                {d: r[d] >= hi for d in days if r.get(d) is not None})

    dn_f, up_f = tails(fit, rs, LO[TAIL], HI[TAIL])
    print(f"  맞추는 구간 기저율: 크게 내림 {fr(sum(dn_f.values()), len(dn_f))}, 크게 오름 {fr(sum(up_f.values()), len(up_f))},"
          f" 사건 수 내림 {len(runs(fit, dn_f, H - 1))} · 오름 {len(runs(fit, up_f, H - 1))}, 평균 {pct(fmean(rs[d] for d in fit))}")

    # ---------- [E2] 맞추는 구간 표 (확인 2)
    print("=" * 110)
    print("[E2] 맞추는 구간 — 후보마다 크게 내림·크게 오름·평균 (확인 2: 반대쪽)")
    res = {}
    for c in CANDS:
        cc = {d: cv[d][c] for d in fit}
        sd = stats(fit, cc, dn_f, rs)
        su = stats(fit, cc, up_f, rs)
        res[c] = (sd, su)
        print(f"  {c} {DESC[c]}")
        print(f"     내림 {fmt(sd)}")
        print(f"     오름 {fmt(su)}")
        print(f"     평균 켜짐 {pct(sd['m_on'])} 꺼짐 {pct(sd['m_off'])} | 켜진 날 {sd['n_on']}/{sd['n']}")

    # ---------- [E3] 돌려 맞추기: 개별 p와 해 본 수 보정 (확인 4)
    print("=" * 110)
    print("[E3] 돌려 맞추기 — 개별 p(양쪽, |z|)와 22개 검정 동시 보정 p(같은 k에서 가장 큰 |z|)")
    n_min = min(res[c][0]["n"] for c in CANDS)
    ks = list(range(SHIFT_MIN, n_min - SHIFT_MIN))
    zk = {}  # (c, tail) → {k: z}
    obs = {}
    for c in CANDS:
        for tail, s, yy in (("내림", res[c][0], dn_f), ("오름", res[c][1], up_f)):
            ds = s["ds"]
            on_l = [1 if s["on"][d] else 0 for d in ds]
            y_l = [1 if yy[d] else 0 for d in ds]
            cnt = rot_counts(on_l, y_l, ks)
            n_on, n = s["n_on"], s["n"]
            tot = sum(y_l)
            zk[(c, tail)] = {k: zstat(cnt[k], n_on, tot - cnt[k], n - n_on) for k in ks}
            obs[(c, tail)] = s["z"]
    maxz = {k: max(abs(zk[key][k]) for key in zk) for k in ks}
    corr = {}
    for key in zk:
        zo = abs(obs[key])
        ind = sum(abs(zk[key][k]) >= zo for k in ks) / len(ks)
        cp = sum(maxz[k] >= zo for k in ks) / len(ks)
        corr[key] = (ind, cp)
    print(f"  이동 k = {ks[0]} … {ks[-1]} ({len(ks)}개), 검정 {len(zk)}개")
    for key in sorted(zk, key=lambda x: corr[x][1]):
        c, tail = key
        s = res[c][0] if tail == "내림" else res[c][1]
        print(f"  {c:3s} {tail} z {obs[key]:+.2f} 배 {s['ratio']:.2f} | 개별 p {corr[key][0]:.3f} | 보정 p {corr[key][1]:.3f}")
    best = {}
    for tail in ("내림", "오름"):
        keys = [k for k in zk if k[1] == tail]
        b = min(keys, key=lambda k: (corr[k][1], -abs(obs[k])))
        best[tail] = b[0]
        print(f"  {tail} 꼬리 가장 좋은 후보: {b[0]} (보정 p {corr[b][1]:.3f}, 개별 p {corr[b][0]:.3f}, z {obs[b]:+.2f})")
    # 평균 차이의 돌려 맞추기(개별)
    print("  평균 차이(켜짐 − 꺼짐)의 개별 돌려 맞추기 p:")
    mean_p = {}
    for c in CANDS:
        s = res[c][0]
        ds = s["ds"]
        on_l = [s["on"][d] for d in ds]
        r_l = [rs[d] for d in ds]
        n = len(ds)
        tot = sum(r_l)
        n_on = sum(on_l)
        pos = [j for j, v in enumerate(on_l) if v]
        obs_d = s["m_on"] - s["m_off"]
        ge = 0
        for k in ks:
            so = sum(r_l[(p + k) % n] for p in pos)
            dk = so / n_on - (tot - so) / (n - n_on)
            ge += abs(dk) >= abs(obs_d)
        mean_p[c] = ge / len(ks)
        print(f"    {c} 차이 {pct(obs_d, 3)} p {mean_p[c]:.3f}")

    # ---------- [E4] 대안 문턱과 곁 창 (확인 2 보조)
    print("=" * 110)
    print("[E4] 대안 문턱(5%·20%)과 곁 창(시가 → 시가 d+5) — 켜짐/꺼짐 비율")
    dn_h, up_h = tails(fit, rh, LO[TAIL], HI[TAIL])
    for c in CANDS:
        cc = {d: cv[d][c] for d in fit}
        parts = []
        for q in TAIL_ALT:
            a_, b_ = tails(fit, rs, LO[q], HI[q])
            s1, s2 = stats(fit, cc, a_), stats(fit, cc, b_)
            parts.append(f"{q:.0%} 내림 {s1['p_on']:.3f}/{s1['p_off']:.3f} 오름 {s2['p_on']:.3f}/{s2['p_off']:.3f}")
        s1, s2 = stats(fit, cc, dn_h, rh), stats(fit, cc, up_h, rh)
        parts.append(f"곁 창 내림 {s1['p_on']:.3f}/{s1['p_off']:.3f} 오름 {s2['p_on']:.3f}/{s2['p_off']:.3f} 평균 {pct(s1['m_on'])}/{pct(s1['m_off'])}")
        print(f"  {c}: " + " | ".join(parts))

    # ---------- [E5] 이미 아는 것을 뺀 나머지 (확인 3)
    print("=" * 110)
    print("[E5] 이미 아는 것을 뺀 나머지 — (가) A1(흔들림)로 나눔(A1은 B1로) (나) z5 꼬리 (다) A2(60봉 추세)로 나눔(A2는 A3로)")
    zdn, zup = tails(fit, z5, ZLO, ZHI)

    def strat(c, by, y):
        out = []
        num = den = 0.0
        for sv in ("예", "아니오"):
            ds = [d for d in fit if cv[d][by] == sv]
            s = stats(ds, {d: cv[d][c] for d in ds}, y)
            if s["n_on"] and s["n_off"]:
                w = s["n_on"] * s["n_off"] / (s["n_on"] + s["n_off"])
                num += w * (s["p_on"] - s["p_off"])
                den += w
            out.append(f"{by}={sv}: {fr(s['a'], s['n_on'])} 대 {fr(s['c'], s['n_off'])}")
        return out, (num / den if den else None)

    resid = {}
    for c in CANDS:
        byv = "B1" if c == "A1" else "A1"
        byt = "A3" if c == "A2" else "A2"
        line = [f"  {c}"]
        for tail, y, zy in (("내림", dn_f, zdn), ("오름", up_f, zup)):
            sv, wd = strat(c, byv, y)
            st, wt = strat(c, byt, y)
            sz = stats(fit, {d: cv[d][c] for d in fit}, zy)
            raw = (res[c][0] if tail == "내림" else res[c][1])
            resid[(c, tail)] = (raw["p_on"] - raw["p_off"], wd, sz["p_on"] - sz["p_off"] if sz["p_off"] is not None else None, wt, sz)
            line.append(f"    {tail}: 원래 차이 {raw['p_on'] - raw['p_off']:+.3f} | (가) {byv}로 나눈 가중 차이 {wd:+.3f} [{'; '.join(sv)}]"
                        f" | (나) z5 {fr(sz['a'], sz['n_on'])} 대 {fr(sz['c'], sz['n_off'])} 차이 {sz['p_on'] - sz['p_off']:+.3f}"
                        f" | (다) {byt}로 나눈 가중 차이 {wt:+.3f}")
        print("\n".join(line))

    # ---------- [E6] 떼어 둔 구간 (확인 5)
    print("=" * 110)
    print(f"[E6] 떼어 둔 구간 — 판단 {hold[0]} ~ {hold[-1]} {len(hold)}일, 문턱은 맞추는 구간 것")
    dn_o, up_o = tails(hold, rs, LO[TAIL], HI[TAIL])
    print(f"  기저율: 크게 내림 {fr(sum(dn_o.values()), len(dn_o))} (사건 {len(runs(hold, dn_o, H - 1))}), 크게 오름 {fr(sum(up_o.values()), len(up_o))}"
          f" (사건 {len(runs(hold, up_o, H - 1))}), 평균 {pct(fmean(rs[d] for d in hold))}")
    hold_res = {}
    for c in CANDS:
        cc = {d: cv[d][c] for d in hold}
        sd, su = stats(hold, cc, dn_o, rs), stats(hold, cc, up_o, rs)
        hold_res[c] = (sd, su)
        print(f"  {c} 내림 {fmt(sd)}")
        print(f"  {'':2s} 오름 {fmt(su)} | 평균 {pct(sd['m_on'])}/{pct(sd['m_off'])}")

    # ---------- [E7] 가장 크게 보탠 구간 하나 빼기, 시기별 (확인 5)
    print("=" * 110)
    print("[E7] 맞추는 구간에서 차이에 가장 크게 보탠 연속 구간 하나를 뺀 결과, 시기별 표")
    r60 = {d: fmean(past[d][a][1]["r60"] for a in TARGETS) for d in valid}
    v60 = {d: fmean(past[d][a][1]["vol60"] for a in TARGETS) for d in valid}
    reg_vol, hist = {}, []
    for d in valid:
        reg_vol[d] = (("흔들림 큼" if v60[d] > median(hist) else "흔들림 작음") if len(hist) >= 250 else None)
        hist.append(v60[d])
    reg_tr = {d: "오르는 때" if r60[d] > 0 else "내리는 때" for d in valid}
    show = sorted(set(["A1", "A2", "A3", best["내림"], best["오름"]]), key=CANDS.index)
    drop_res = {}
    for c in show:
        for tail, y in (("내림", dn_f), ("오름", up_f)):
            s = res[c][0] if tail == "내림" else res[c][1]
            base = s["p_on"] - s["p_off"]
            bestcut = None
            for rn in s["on_run_list"] + s["off_run_list"]:
                cut = set(rn)
                ds = [d for d in fit if d not in cut]
                t = stats(ds, {d: cv[d][c] for d in ds}, y)
                if t["p_on"] is None or t["p_off"] is None:
                    continue
                diff = t["p_on"] - t["p_off"]
                sgn = math.copysign(1, base) if base else 1.0
                if bestcut is None or diff * sgn < bestcut[0] * sgn:  # 원래 쪽 차이를 가장 많이 깎는 구간
                    bestcut = (diff, rn, t)
            diff, rn, t = bestcut
            drop_res[(c, tail)] = (base, diff, rn, t)
            print(f"  {c} {tail}: 원래 차이 {base:+.3f} → 뺀 뒤 {diff:+.3f} (뺀 구간 {rn[0]} ~ {rn[-1]} {len(rn)}일,"
                  f" {'켜진' if s['on'][rn[0]] else '꺼진'} 구간) 뺀 뒤 {fr(t['a'], t['n_on'])} 대 {fr(t['c'], t['n_off'])} z {t['z']:+.2f}")
    for c in show:
        for tail, y in (("내림", dn_f), ("오름", up_f)):
            print(f"  -- {c} {tail} 시기별 (켜짐 / 꺼짐, 표본 모자람 = 켜진·꺼진 구간 3개 미만)")
            for name, key in (("해", lambda d: str(d.year)), ("추세", lambda d: reg_tr[d]), ("흔들림", lambda d: reg_vol[d])):
                g = defaultdict(list)
                for d in fit:
                    k = key(d)
                    if k is not None:
                        g[k].append(d)
                cells = []
                for k in sorted(g):
                    t = stats(g[k], {d: cv[d][c] for d in g[k]}, y)
                    few = t["on_runs"] < 3 or t["off_runs"] < 3
                    side = "–" if t["p_on"] is None or t["p_off"] is None else ("같음" if t["p_on"] == t["p_off"] else ("켜짐↑" if t["p_on"] > t["p_off"] else "꺼짐↑"))
                    cells.append(f"{k}{'(모자람)' if few else ''} {fr(t['a'], t['n_on'])}/{fr(t['c'], t['n_off'])} {side}")
                print(f"     {name}: " + " | ".join(cells))

    # ---------- [E8] 판단 때 아는 값 (확인 1)
    print("=" * 110)
    print("[E8] 판단 때 아는 값 — 우리 기록의 시세 대 보기(reading)를 판단 시각에 다시 계산해 견줌, 조사 값의 시각")
    runs_ = {day(r["run"]): r for r in jl("run") if r.get("mode") == "live"}
    decided = {d: dt.datetime.fromisoformat(r["steps"]["decided_at"]) for d, r in runs_.items()}
    reading = defaultdict(dict)
    for r in jl("reading"):
        if r["asset"] in TARGETS:
            reading[day(r["run"])][r["asset"]] = r
    rec_days = [d for d in KRX if d >= REC_START and d + dt.timedelta(0) <= RECORD_END and KIDX[d] + H - 1 < len(KRX)
                and end_date(KIDX[d]) <= REC_TARGET_END]
    late = match = mism = 0
    rec_states = {}
    for d in rec_days:
        st = states_at(decided[d])
        rec_states[d] = st
        for a in TARGETS:
            r = reading[d][a]
            if C.final_dt(C.ASSETS[a], day(r["known_through"])) > decided[d]:
                late += 1
            if all(st[a][0][k] == r["values"].get(k) for k in pieces.STATES):
                match += 1
            else:
                mism += 1
    print(f"  우리 기록 판단 날 {len(rec_days)}일({rec_days[0]} ~ {rec_days[-1]}, 거래일): 세 대상 reading 줄 {3 * len(rec_days)}개 중"
          f" 본 마지막 봉이 판단 뒤에 끝난 줄 {late}, 다시 계산한 일곱 값이 같은 줄 {match}, 다른 줄 {mism}")
    print(f"  판단 시각 09:00 이후인 날 {sum(decided[d].time() >= dt.time(9) for d in rec_days)}")
    print(f"  과거 시세: 조건은 final_dt ≤ 판단 시각(08:40)인 봉만 읽음(known_closes). B1 가운데 값은 판단 날 전 값만, K1은 거래소 달력.")
    hr = {day(r["run"]): r for r in jl("hand_reading")}
    research = defaultdict(list)
    for r in jl("research"):
        research[day(r["run"])].append(r)
    rc_late = defaultdict(set)
    for r in jl("reading_check"):
        if r.get("late"):
            rc_late[r["state"]].add(day(r["target"].split(":")[1]))
    side = defaultdict(Counter)
    for d, r in hr.items():
        for s, v in r["values"].items():
            side[s][v] += 1
    rstates = sorted(s for s, cc in side.items() if cc["예"] >= 3 and cc["아니오"] >= 3)
    for s in rstates:
        vd = [d for d in rec_days if d in hr and hr[d]["values"].get(s) in ("예", "아니오")]
        after = sum(any("as_of" in x and dt.datetime.fromisoformat(x["as_of"]) > decided[d] for x in research[d]) for d in vd)
        noas = sum(any("as_of" not in x for x in research[d]) for d in vd)
        print(f"  {s}: 우리 기록 판단 날 중 값 {len(vd)}일, 그날 조사 답 as_of 판단 뒤 {after}일, as_of 없는 날 {noas}일, 다시 보기 늦음 {sorted(rc_late.get(s, []))}")

    # ---------- [E9] 우리 기록 (확인 6)
    print("=" * 110)
    print(f"[E9] 우리 기록 — 판단 {rec_days[0]} ~ {rec_days[-1]} 거래일 {len(rec_days)}일, 본 창(창 끝 ≤ {REC_TARGET_END}), 문턱은 맞추는 구간 것")
    rcv = {}
    for d in rec_days:
        vals = {}
        for c, s in STATE_IDS:
            vals[c] = majority([reading[d][a]["values"][s] for a in TARGETS])  # 기록된 값
        extra = cond_values(d, rec_states[d])
        for c in ("B1", "B2", "K1", "K2"):
            vals[c] = extra[c]
        for k, s in enumerate(rstates, 1):
            vals[f"R{k}"] = hr[d]["values"].get(s, "모름") if d in hr else "모름"
        n = (hr.get(d, {}).get("numbers") or {}).get("S_q_overnight_gap") or {}
        x, sd_ = n.get("kr_overnight_pct"), n.get("kr_overnight_sd60_pct")
        vals[f"R{len(rstates) + 1}"] = "모름" if x is None or not sd_ else ("예" if x / sd_ <= -1 else "아니오")
        rcv[d] = vals
    rnames = {f"R{k}": s for k, s in enumerate(rstates, 1)}
    rnames[f"R{len(rstates) + 1}"] = "kr_overnight_pct ÷ sd60 ≤ −1"
    cols = CANDS + list(rnames)
    dn_r = {d: rs[d] <= LO[TAIL] for d in rec_days}
    up_r = {d: rs[d] >= HI[TAIL] for d in rec_days}
    print("  R 열: " + ", ".join(f"{k} = {v}" for k, v in rnames.items()))
    print("  날짜   본 창    곁 창   꼬리 " + " ".join(f"{c:>3}" for c in cols))
    for d in rec_days:
        tl = "내림" if dn_r[d] else "오름" if up_r[d] else "  - "
        print(f"  {d.strftime('%m-%d')} {pct(rs[d]):>8} {pct(rh[d]) if rh[d] is not None and KRX[KIDX[d] + H] <= REC_TARGET_END else '    –   ':>8} {tl} "
              + " ".join(f"{rcv[d][c][:1]:>3}" for c in cols))
    print(f"  크게 내림 {sum(dn_r.values())}일 → 사건 {len(runs(rec_days, dn_r, H - 1))}, 크게 오름 {sum(up_r.values())}일 → 사건 {len(runs(rec_days, up_r, H - 1))},"
          f" 평균 {pct(fmean(rs[d] for d in rec_days))}")
    rec_res = {}
    for c in cols:
        cc = {d: rcv[d][c] for d in rec_days}
        sd, su = stats(rec_days, cc, dn_r, rs), stats(rec_days, cc, up_r, rs)
        rec_res[c] = (sd, su)
        if sd["n_on"] and sd["n_off"]:
            print(f"  {c} 내림 {fmt(sd)}")
            print(f"  {'':3s} 오름 {fmt(su)} | 평균 {pct(sd['m_on'])}/{pct(sd['m_off'])}")
        else:
            print(f"  {c}: 한쪽 값뿐(켜짐 {sd['n_on']} · 꺼짐 {sd['n_off']}, 예·아니오인 날 {sd['n']}) — 가를 수 없음")

    # ---------- [E10] 답을 정하는 규칙 (plan 7절)
    print("=" * 110)
    print("[E10] 답 — 꼬리마다 보정 p < 0.05이고 떼어 둔 구간 같은 쪽이면 '있었다', 보정 p < 0.05인데 반대면 '모른다', 아니면 '없었다'")
    answer = {}
    for tail in ("내림", "오름"):
        c = best[tail]
        cp = corr[(c, tail)][1]
        s = res[c][0] if tail == "내림" else res[c][1]
        ho = hold_res[c][0] if tail == "내림" else hold_res[c][1]
        same = None
        if ho["ratio"] is not None and s["ratio"] is not None:
            same = (ho["ratio"] - 1) * (s["ratio"] - 1) > 0
        ans = "있었다" if cp < 0.05 and same else "모른다" if cp < 0.05 else "없었다"
        answer[tail] = ans
        print(f"  {tail}: 가장 좋은 후보 {c} 보정 p {cp:.3f}, 맞추는 구간 배 {s['ratio']:.2f}, 떼어 둔 구간 배 "
              f"{('%.2f' % ho['ratio']) if ho['ratio'] is not None else '–'} → {ans}")
    # 개별 p < 0.05인 검정 목록(보정 전)
    print("  보정 전 개별 p < 0.05인 검정: " + ", ".join(f"{c}·{t}(z {obs[(c, t)]:+.2f}, 개별 {corr[(c, t)][0]:.3f}, 보정 {corr[(c, t)][1]:.3f})"
                                              for (c, t) in sorted(zk, key=lambda x: corr[x][0]) if corr[(c, t)][0] < 0.05))

    # ---------- [E11] 행동 (7절)
    print("=" * 110)
    print("[E11] 행동 — 거래일마다 판단 → 그날 09:00 시가에 묶음 비중 맞춤(세 대상 1/3씩), 시가 → 다음 거래일 시가, 수수료 국내 0.015%·코인 0.05%"
          " (해외 ETF 판 이익 세금 15.4%는 넣지 않음)")

    def sim(days, wfun):
        nav, hold_v = 1.0, {a: 0.0 for a in TARGETS}
        navs, fees, ws = [1.0], 0.0, []
        for d in days:
            i = KIDX[d]
            if i + 1 >= len(KRX):
                break
            t = KRX[i + 1]
            if d not in BY[COIN] or t not in BY[COIN]:
                break
            w = wfun(d)
            ws.append(w)
            tgt = {a: nav * w / 3 for a in TARGETS}
            f = sum(abs(tgt[a] - hold_v[a]) * FEE[a] for a in TARGETS)
            fees += f
            nav -= f
            tgt = {a: nav * w / 3 for a in TARGETS}
            cash = nav - sum(tgt.values())
            hold_v = {a: tgt[a] * BY[a][t].open / BY[a][d].open for a in TARGETS}
            nav = cash + sum(hold_v.values())
            navs.append(nav)
        peak, mdd = navs[0], 0.0
        for v in navs:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        blocks = [navs[j + 5] / navs[j] - 1 for j in range(0, len(navs) - 5, 5)]
        return {"ret": navs[-1] - 1, "mdd": mdd, "fees": fees, "w": fmean(ws) if ws else 0,
                "bad": sum(b <= LO[TAIL] for b in blocks), "good": sum(b >= HI[TAIL] for b in blocks), "nb": len(blocks),
                "worst": min(blocks) if blocks else None}

    def show_sim(name, days, rules):
        print(f"  -- {name} ({days[0]} ~ {days[-1]}, {len(days)}거래일)")
        for label, wf in rules:
            s = sim(days, wf)
            print(f"     {label:34s} 누적 {pct(s['ret'], 1):>8} 최대 낙폭 {pct(s['mdd'], 1):>7} 평균 비중 {s['w']:.3f} 수수료 {s['fees'] * 100:.2f}%"
                  f" | 겹치지 않는 5거래일 {s['nb']}개 중 문턱 아래 {s['bad']}·위 {s['good']}, 가장 나쁜 {pct(s['worst'], 1)}")

    sim_c = sorted(set(["A1", best["내림"], best["오름"]]), key=CANDS.index)
    nxt = {d: KRX[KIDX[d] + 1] for d in valid if KIDX[d] + 1 < len(KRX)}
    all_fit = [d for d in valid if d in nxt and nxt[d] <= FIT_END]
    all_hold = [d for d in valid if d in nxt and d >= HOLD_START and nxt[d] <= HOLD_END]
    for nm, ds in (("맞추는 구간", all_fit), ("떼어 둔 구간", all_hold)):
        rules = [("늘 100%", lambda d: 1.0), ("늘 2/3", lambda d: 2 / 3), ("늘 1/2", lambda d: 0.5)]
        for c in sim_c:
            s_dn = res[c][0]
            shrink_on = s_dn["ratio"] is not None and s_dn["ratio"] > 1   # 내림 꼬리가 켜짐 쪽에서 많으면 켜질 때 줄임
            rule = (lambda c_, so: (lambda d: (0.5 if (cv[d][c_] == "예") == so else 1.0) if cv[d][c_] in ("예", "아니오") else 1.0))(c, shrink_on)
            w_avg = sim(ds, rule)["w"]
            rules.append((f"{c} {'켜지면' if shrink_on else '꺼지면'} 50%", rule))
            rules.append((f"  비교: 늘 {w_avg:.3f}(같은 평균 비중)", (lambda w_: (lambda d: w_))(w_avg)))
        show_sim(nm, ds, rules)

    # ---------- [E12] 판정 계획을 과거 여러 시작점에서
    print("=" * 110)
    print("[E12] 판정 계획을 과거에서 돌림 — 시작점마다 그 뒤 겹치지 않는 5거래일 묶음 N개(판단 날 = 시작점 + 5j)."
          " 지표: (꼬리) 크게 내림 비율, (크기) |본 창 수익| 평균 — 켜짐·꺼짐 차이와 묶음 라벨 섞기 p(1,000번, 씨앗 고정)."
          " 켜진·꺼진 묶음이 각 5개 미만이면 모름 / 주장 쪽 & p < 0.10이면 통과 / 반대 쪽이면 버림 / 그 밖 모름")
    base_days = [d for d in valid if rs[d] is not None and end_date(KIDX[d]) <= HOLD_END]
    rng = random.Random(20261125)

    def plan_run(c, side, start_idx, N, metric):
        blk = base_days[start_idx::5][:N]
        if len(blk) < N:
            return None
        lab, val = [], []
        for d in blk:
            if cv[d][c] in ("예", "아니오"):
                lab.append(cv[d][c] == side)  # 주장이 '많다'고 하는 쪽 = True
                val.append(1.0 if (metric == "tail" and rs[d] <= LO[TAIL]) else (0.0 if metric == "tail" else abs(rs[d])))
        a_ = [v for v, l in zip(val, lab) if l]
        b_ = [v for v, l in zip(val, lab) if not l]
        if len(a_) < 5 or len(b_) < 5:
            return "모름"
        obs_d = fmean(a_) - fmean(b_)
        if obs_d <= 0:
            return "버림"
        ge = 0
        lab2 = lab[:]
        for _ in range(1000):
            rng.shuffle(lab2)
            x = [v for v, l in zip(val, lab2) if l]
            y_ = [v for v, l in zip(val, lab2) if not l]
            if x and y_:
                ge += fmean(x) - fmean(y_) >= obs_d
        return "통과" if ge / 1000 < 0.10 else "모름"

    plans = [("A3", "아니오", "tail", "A3 꺼짐(200봉 평균 아래 둘 이상) 쪽에 크게 내림이 많다"),
             ("A3", "아니오", "abs", "A3 꺼짐 쪽 |수익|이 크다(움직임 크기)"),
             ("A1", "예", "abs", "A1 켜짐 쪽 |수익|이 크다(C_vol_high의 묶음 5거래일 꼴)")]
    for c, side, metric, label in plans:
        for N in (50, 100):
            cnt = Counter()
            for st in range(0, len(base_days) - 5 * N, 5):
                v = plan_run(c, side, st, N, metric)
                if v:
                    cnt[v] += 1
            tot = sum(cnt.values())
            print(f"  {label}, 묶음 {N}개(약 {N * 5 / 244:.1f}년): 시작점 {tot}곳 — 통과 {cnt['통과']} ({cnt['통과'] / tot:.2f})"
                  f" · 모름 {cnt['모름']} ({cnt['모름'] / tot:.2f}) · 버림 {cnt['버림']} ({cnt['버림'] / tot:.2f})")

    # ---------- [E13] 곁가지: 묶음 꼬리에 대상마다 보탠 몫
    print("=" * 110)
    print("[E13] 곁가지 — 맞추는 구간 크게 내림·크게 오름 날의 대상별 평균 창 수익(묶음 = 셋의 평균)")
    for tail, y in (("내림", dn_f), ("오름", up_f)):
        ds = [d for d in fit if y[d]]
        i_ = KIDX
        parts = []
        for a in TARGETS:
            vals = []
            for d in ds:
                t = KRX[i_[d] + H - 1]
                vals.append(BY[a][t].close / BY[a][d].open - 1)
            parts.append(f"{a} {pct(fmean(vals))}(묶음 몫 {fmean(vals) / 3 / fmean(rs[d] for d in ds):.2f})")
        print(f"  {tail} {len(ds)}일: " + ", ".join(parts) + f" | 묶음 {pct(fmean(rs[d] for d in ds))}")
    # ---------- [E14] 계획 밖 추가(결과를 본 뒤): 대상 하나씩 자기 200봉 평균 → 자기 5봉 꼬리, 긴 시세
    print("=" * 110)
    print("[E14] 계획 밖 추가(E2·E6·E7을 본 뒤 더함) — 대상 하나씩: 자기 S_above_ma200(판단 08:40, 끝난 봉만) → 자기 5봉 창"
          "(시가 첫 봉 → 종가 5봉째; 코인은 날짜 봉) 꼬리. 문턱 = 자기 창 수익의 ~2026-06-30 하위·상위 10%")
    for a in TARGETS:
        rows = ROWS[a]
        dates = [r.date for r in rows]
        xs_, info = [], {}
        for j_, d in enumerate(dates):
            if j_ + H - 1 >= len(rows):
                break
            r5 = rows[j_ + H - 1].close / rows[j_].open - 1
            endd = rows[j_ + H - 1].date
            c_ = known_closes(a, dt.datetime.combine(d, JUDGE, KST))
            if endd <= FIT_END:
                xs_.append(r5)
            if len(c_) < pieces.NEED:
                continue
            info[d] = (pieces.read(c_[-pieces.NEED:])[0]["S_above_ma200"], r5, endd)
        lo_, hi_ = quantile(xs_, TAIL), quantile(xs_, 1 - TAIL)
        idx = {d: j_ for j_, d in enumerate(dates)}
        for nm, sel in (("맞추는", lambda d, e: e <= FIT_END), ("떼어 둔", lambda d, e: d >= HOLD_START and e <= HOLD_END)):
            ds = [d for d, v in info.items() if sel(d, v[2])]
            cond = {d: info[d][0] for d in ds}
            on = [d for d in ds if cond[d] == "아니오"]   # 아래 = 주장이 '많다'고 한 쪽
            off = [d for d in ds if cond[d] == "예"]
            def rt(g, f):
                return f"{sum(f(info[d][1]) for d in g)}/{len(g)}=" + (f"{sum(f(info[d][1]) for d in g) / len(g):.3f}" if g else "–")
            print(f"  {a} {nm} 구간({ds[0] if ds else '–'} ~ {ds[-1] if ds else '–'}): 아래 {len(on)}일 · 위 {len(off)}일 |"
                  f" 크게 내림 아래 {rt(on, lambda x: x <= lo_)} 위 {rt(off, lambda x: x <= lo_)} |"
                  f" 크게 오름 아래 {rt(on, lambda x: x >= hi_)} 위 {rt(off, lambda x: x >= hi_)}")
            if nm == "맞추는":
                yrs = defaultdict(list)
                for d in ds:
                    yrs[d.year].append(d)
                cells, same, opp, one = [], 0, 0, 0
                for y in sorted(yrs):
                    g_on = [d for d in yrs[y] if cond[d] == "아니오"]
                    g_off = [d for d in yrs[y] if cond[d] == "예"]
                    if not g_on or not g_off:
                        one += 1
                        cells.append(f"{y} 한쪽뿐")
                        continue
                    p1 = sum(info[d][1] <= lo_ for d in g_on) / len(g_on)
                    p2 = sum(info[d][1] <= lo_ for d in g_off) / len(g_off)
                    same += p1 > p2
                    opp += p1 < p2
                    cells.append(f"{y} {p1:.2f}/{p2:.2f}(아래 {len(g_on)}일)")
                print(f"     해마다 크게 내림 아래/위 — 아래 쪽이 더 많은 해 {same}, 반대 {opp}, 한쪽뿐 {one}: " + ", ".join(cells))

    # ---------- [E15] 구간 표(4-2): 맞추는 구간의 켜진 구간·꺼진 구간을 나란히
    print("=" * 110)
    print("[E15] 구간 표 — 맞추는 구간, 같은 값이 이어진 거래일 구간마다 크게 내림·크게 오름 판단 날 수와 본 창 평균")
    for c in ("A3", "A1"):
        s_ = res[c][0]
        allr = sorted(s_["on_run_list"] + s_["off_run_list"], key=lambda rn: rn[0])
        print(f"  -- {c} {DESC[c]} (구간 {len(allr)}개)")
        worst = {True: None, False: None}
        for rn in allr:
            on_ = s_["on"][rn[0]]
            nd = sum(dn_f[d] for d in rn)
            nu = sum(up_f[d] for d in rn)
            m = fmean(rs[d] for d in rn)
            key = (nd, nd / len(rn))  # 크게 내린 판단 날 수가 가장 많은 구간(같으면 비율)
            if worst[on_] is None or key > worst[on_][0]:
                worst[on_] = (key, rn)
            print(f"     {'켜짐' if on_ else '꺼짐'} {rn[0]} ~ {rn[-1]} {len(rn):4d}일 크게 내림 {nd:3d} 크게 오름 {nu:3d} 평균 {pct(m)}")
        for on_ in (True, False):
            (nd, fr_), rn = worst[on_]
            print(f"     크게 내린 판단 날이 가장 많은 {'켜진' if on_ else '꺼진'} 구간: {rn[0]} ~ {rn[-1]} ({nd}일 / {len(rn)}일 = {fr_:.2f})")

    print("끝.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
