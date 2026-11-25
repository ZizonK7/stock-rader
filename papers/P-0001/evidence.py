"""P-0001 근거 계산: "이 계좌가 크게 잃은 날과 주에는, 판단하던 때 미리 잴 수 있던 조건이 있었나?"

저장소 루트에서:  python papers/P-0001/evidence.py
- 읽기만 한다(records/, data/prices/). 아무 파일도 쓰지 않는다. 출력은 표준 출력뿐이다.
- 외부 패키지 없음(Python 3.11+). 시세 읽기·'판단 때 끝나 있던 봉'·시세 상태 계산은 저장소 코드
  (market.prices.view, market.pieces.read)를 그대로 불러 쓴다 — 매일 실행과 같은 잣대.
- 논문(paper.md)의 숫자는 모두 이 출력에서 왔다. 절 번호 [E1]…[E9]가 논문의 '근거 번호'다.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
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

KST = C.KST
REC = ROOT / "records"
ETFS = ("069500", "360750")
COIN = "KRW-BTC"
TARGETS = ETFS + (COIN,)
RECORD_START = dt.date(2026, 10, 21)   # 진짜 계좌 첫날
PRE_END = dt.date(2026, 10, 20)        # 문턱을 정하는 데 쓰는 시세의 끝(기록 시작 전날)
RECORD_END = dt.date(2026, 11, 25)     # 논문을 쓴 날 — 이 날짜까지의 기록 줄만 읽는다(하루 수익률은 11-24 판단까지)
FILL_END = dt.date(2026, 11, 23)       # 체결 뒤 묶음을 잴 수 있던 마지막 거래일(코인 11-23 일봉이 11-24 09:00에 끝남; 11-24 일봉은 논문 때 미완)
PCT_MAIN, PCT_ALT = 0.10, (0.05, 0.20)  # '크게 잃었다' = 묶음 수익률의 하위 10% (대안 5%·20%)
PAST_JUDGE = dt.time(8, 40)            # 과거 시세 시험의 판단 시각(2기 아침 흐름과 같음)
SHIFT_MIN = 60                          # 돌려 맞추기 시험에서 가까운 밀기를 뺀다(같은 국면을 그대로 겹치지 않게)
HORIZONS = (1, 5, 20)


def jl(name: str) -> list[dict]:
    """기록 한 종류. 논문을 쓴 날까지의 줄만(run ≤ RECORD_END) — 기록은 매일 늘어나므로 끝을 못 박아 같은 숫자가 나오게 한다."""
    with (REC / f"{name}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["run"] <= RECORD_END.isoformat()]


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def pct(x: float | None, nd: int = 2) -> str:
    return "–" if x is None else f"{x * 100:+.{nd}f}%"


def rate(a: int, n: int) -> str:
    return f"{a}/{n}" + (f"={a / n:.2f}" if n else "")


def quantile(xs: list[float], q: float) -> float:
    """가장 가까운 순위: 작은 쪽부터 ceil(q·n)번째 값."""
    s = sorted(xs)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def fisher_greater(a: int, b: int, c: int, d: int) -> float:
    """2×2 [[a,b],[c,d]] (행: 켜짐/꺼짐, 열: 잃음/아님) — 켜진 쪽 잃음이 a 이상일 한쪽 p(초기하)."""
    r1, c1, n = a + b, a + c, a + b + c + d
    den = math.comb(n, c1)
    return sum(math.comb(r1, x) * math.comb(n - r1, c1 - x) for x in range(a, min(r1, c1) + 1)) / den


def runs(days: list, flag: dict, gap: int = 1, index: dict | None = None) -> list[list]:
    """flag가 참인 날들을 이어진 구간으로 묶는다. index가 있으면 그 순번 차이(거래일), 없으면 달력 날짜 차이 ≤ gap."""
    out: list[list] = []
    for d in days:
        if not flag.get(d):
            continue
        if out:
            prev = out[-1][-1]
            dist = (index[d] - index[prev]) if index else (d - prev).days
            if dist <= gap:
                out[-1].append(d)
                continue
        out.append([d])
    return out


def majority(vals: list[str], want: str) -> str:
    """세 대상 값 → 둘 이상이 want면 '예', 둘 이상이 want가 아닌 아는 값이면 '아니오', 아니면 '모름'."""
    yes = sum(v == want for v in vals)
    no = sum(v not in (want, "모름") and v is not None for v in vals)
    return "예" if yes >= 2 else "아니오" if no >= 2 else "모름"


# 시세 후보: (id, 상태, 셋 중 둘 이상이 가질 값, 설명)
PRICE_CANDS = [
    ("C1_vol_rel", "S_vol_high", "예", "최근 20봉 흔들림 > 최근 250봉 흔들림 (셋 중 둘 이상)"),
    ("C2_drop5", "S_drop_5", "예", "최근 5봉에 평소 한 주 움직임보다 크게 내림 (셋 중 둘 이상)"),
    ("C3_down20", "S_trend_20", "아니오", "최근 20봉 수익률이 0 이하 (셋 중 둘 이상)"),
    ("C4_bigmove", "S_big_move", "예", "마지막 봉 움직임 > 평소 하루의 2배 (셋 중 둘 이상)"),
    ("C5_drawdown", "S_deep_drawdown", "예", "250봉 고점에서 평소 한 달 움직임 2배보다 깊이 빠짐 (셋 중 둘 이상)"),
]
C6 = ("C6_vol_abs", "세 대상 20봉 하루 흔들림 평균 > 그때까지 알려진 같은 값(거래일마다, 250개 이상)의 가운데 값")
# 조사 후보: 손 대 보기 상태 중 기록에 '예'와 '아니오'가 각각 3일 이상인 것(값 자체의 분포로만 고름 — 결과는 안 봄)
RESEARCH_MIN_SIDE = 3
# 계좌 후보
ACCOUNT_CANDS = [
    ("A1_cash_lt10", "판단 때 현금 비중 < 10% (거의 다 넣음)"),
    ("A2_one_ge40", "판단 때 한 대상(세 대상·종목 하나) 비중 ≥ 40% (한쪽에 몰림)"),
    ("A3_stock_held", "판단 때 개별 종목을 하나라도 듦"),
]


# ---------------------------------------------------------------- 시세
ROWS = {a: P.load(a) for a in TARGETS}
FINAL = {a: [C.final_dt(C.ASSETS[a], r.date) for r in ROWS[a]] for a in TARGETS}
BY_DATE = {a: {r.date: r for r in ROWS[a]} for a in TARGETS}
KRX = sorted(set(BY_DATE["069500"]) & set(BY_DATE["360750"]))
KIDX = {d: i for i, d in enumerate(KRX)}


def known_closes(a: str, at: dt.datetime) -> list[float]:
    """시각 at에 끝나 있던 봉의 종가(prices.view와 같은 규칙 — final_dt ≤ at)."""
    k = bisect.bisect_right(FINAL[a], at)
    return [r.close for r in ROWS[a][:k]]


def states_at(at: dt.datetime) -> dict[str, tuple[dict, dict]]:
    out = {}
    for a in TARGETS:
        c = known_closes(a, at)
        out[a] = pieces.read(c[-pieces.NEED:])
    return out


def basket_ret(i: int, h: int) -> float | None:
    """거래일 i에 시가로 사서(체결가) 거래일 i+h−1 종가까지. 코인은 같은 날짜의 일봉 시가 → 목표 날짜 일봉 종가(다음 날 09:00)."""
    if i + h - 1 >= len(KRX):
        return None
    d, t = KRX[i], KRX[i + h - 1]
    if d not in BY_DATE[COIN] or t not in BY_DATE[COIN]:
        return None
    rs = [BY_DATE[a][t].close / BY_DATE[a][d].open - 1 for a in ETFS]
    rs.append(BY_DATE[COIN][t].close / BY_DATE[COIN][d].open - 1)
    return fmean(rs)


def asset_ret(a: str, i_date: dt.date, h: int) -> float | None:
    rows = ROWS[a]
    idx = {r.date: j for j, r in enumerate(rows)}
    j = idx.get(i_date)
    if j is None or j + h - 1 >= len(rows):
        return None
    return rows[j + h - 1].close / rows[j].open - 1


def thresholds() -> dict:
    out = {}
    for h in HORIZONS:
        xs = [basket_ret(i, h) for i in range(len(KRX)) if i + h - 1 < len(KRX) and KRX[i + h - 1] <= PRE_END]
        xs = [x for x in xs if x is not None]
        out[h] = {"n": len(xs), "from": KRX[0],
                  "lo": {q: quantile(xs, q) for q in (PCT_MAIN,) + PCT_ALT},
                  "hi": {q: quantile(xs, 1 - q) for q in (PCT_MAIN,) + PCT_ALT}}
    return out


# ---------------------------------------------------------------- 기록
RUNS = {r["run"]: r for r in jl("run") if r.get("mode") == "live"}
DECIDED = {day(k): dt.datetime.fromisoformat(v["steps"]["decided_at"]) for k, v in RUNS.items()}


def account_returns(role: str) -> tuple[dict, dict]:
    """역할(live:hand 등)의 1기·2기 줄을 이어 하루 수익률을 만든다: 판단 d 줄 → 같은 계좌 id의 d+1 줄."""
    vals: dict[str, dict[dt.date, dict]] = defaultdict(dict)
    for r in jl("account"):
        if C.role_of(r["account"]) == role:
            vals[r["account"]][day(r["run"])] = r
    ret, row_at = {}, {}
    for acc, by in vals.items():
        for d, r in by.items():
            n = by.get(d + dt.timedelta(days=1))
            if n is not None:
                if d in ret:
                    raise SystemExit(f"같은 날 두 기간의 수익률: {role} {d}")
                ret[d] = n["value"] / r["value"] - 1
                row_at[d] = r
    return ret, row_at


def week(ret: dict, d: dt.date) -> float | None:
    v = 1.0
    for k in range(7):
        x = ret.get(d + dt.timedelta(days=k))
        if x is None:
            return None
        v *= 1 + x
    return v - 1


def weights(r: dict) -> dict:
    """계좌 줄 → 대상별 비중(평가 가격 × 수량 ÷ 평가액). 코인 수량은 1e-8 단위."""
    w = {}
    for a, q in r["positions"].items():
        if not q:
            continue
        px = r["prices"].get(a)
        if px is None:
            continue
        unit = 1e-8 if a == COIN else 1
        w[a] = q * unit * px[1] / r["value"]
    return w


# ---------------------------------------------------------------- 점수
def table(days, cond: dict, loss: dict, gap: int, index: dict | None = None) -> dict:
    """켜짐×잃음 2×2, 독립 사례(잃은 구간·켜진 구간), 설명함/예외/헛켜짐."""
    ds = [d for d in days if cond.get(d) in ("예", "아니오") and d in loss]
    on = {d: cond[d] == "예" for d in ds}
    ls = {d: loss[d] for d in ds}
    a = sum(on[d] and ls[d] for d in ds)
    b = sum(on[d] and not ls[d] for d in ds)
    c = sum((not on[d]) and ls[d] for d in ds)
    e = sum((not on[d]) and not ls[d] for d in ds)
    eps = runs(ds, ls, gap, index)
    expl = [ep for ep in eps if on[ep[0]]]
    on_runs = runs(ds, on, gap, index)
    off_runs = runs(ds, {d: not on[d] for d in ds}, gap, index)
    loss_set = {d for d in ds if ls[d]}
    false_runs = [rn for rn in on_runs if not (set(rn) & loss_set)]
    return {"n": len(ds), "a": a, "b": b, "c": c, "d": e,
            "p_on": a / (a + b) if a + b else None, "p_off": c / (c + e) if c + e else None,
            "fisher": fisher_greater(a, b, c, e) if ds else None,
            "episodes": len(eps), "explained": len(expl), "exceptions": len(eps) - len(expl),
            "on_runs": len(on_runs), "off_runs": len(off_runs), "false_runs": len(false_runs),
            "eps": eps, "on": on}


def shift_p(days: list, cond: dict, loss: dict, min_shift: int = SHIFT_MIN) -> float | None:
    """조건 줄을 통째로 k칸 돌려(이어진 모양은 그대로) 켜짐∧잃음 날 수가 실제 이상인 비율 — '찍기' 기준."""
    ds = [d for d in days if cond.get(d) in ("예", "아니오") and d in loss]
    n = len(ds)
    if n < 2 * min_shift + 10:
        return None
    on = [cond[d] == "예" for d in ds]
    ls = [loss[d] for d in ds]
    obs = sum(o and l for o, l in zip(on, ls))
    ks = range(min_shift, n - min_shift)
    ge = sum(sum(on[(j + k) % n] and ls[j] for j in range(n)) >= obs for k in ks)
    return ge / len(ks)


def line(t: dict) -> str:
    rr = (t["p_on"] / t["p_off"]) if t["p_on"] is not None and t["p_off"] else None
    return (f"날 {t['n']:4d} | 켜짐 잃음 {rate(t['a'], t['a'] + t['b']):>12} | 꺼짐 잃음 {rate(t['c'], t['c'] + t['d']):>12} | "
            f"비 {('%.2f' % rr) if rr is not None else '–':>5} | p {t['fisher']:.3f} | "
            f"잃은 구간 {t['episodes']:3d}: 설명 {t['explained']:3d} 예외 {t['exceptions']:3d} | "
            f"켜진 구간 {t['on_runs']:3d}(헛켜짐 {t['false_runs']:3d}) 꺼진 구간 {t['off_runs']:3d}")


# ================================================================ 실행
def main() -> int:
    TH = thresholds()
    print("=" * 100)
    print("[E1] '크게 잃었다'의 문턱 — 세 대상 같은 비중 묶음, 시가(d) → 종가(d+h−1), 시세", TH[1]["from"], "~", PRE_END)
    for h in HORIZONS:
        lo, hi = TH[h]["lo"], TH[h]["hi"]
        print(f"  {h:2d}거래일: 표본 {TH[h]['n']} | 하위 10% {pct(lo[0.10])} (5% {pct(lo[0.05])}, 20% {pct(lo[0.20])})"
              f" | 상위 10% {pct(hi[0.10])}")
    T1, T5 = TH[1]["lo"], TH[5]["lo"]

    # ---------------- 계좌
    ret, row_at = account_returns("live:hand")
    eq, _ = account_returns("baseline:equal_hold")
    calc, _ = account_returns("live:calc")
    days = sorted(ret)
    wk = {d: week(ret, d) for d in days}
    wk = {d: v for d, v in wk.items() if v is not None}
    wdays = sorted(wk)
    print("=" * 100)
    print(f"[E2] 손 계좌 하루(판단 줄 → 다음 판단 줄): {days[0]} ~ {days[-1]} {len(days)}일, 주(7일 곱): {wdays[0]} ~ {wdays[-1]} {len(wdays)}개")
    print("  날짜       요일 손계좌    나눠사두기  계산기    | 손 현금  최대한대상 종목수")
    wd = "월화수목금토일"
    for d in days:
        w = weights(row_at[d])
        cash = 1 - sum(w.values())
        mx = max(w.values()) if w else 0.0
        ns = sum(1 for a in w if a not in TARGETS)
        mark = " <-1일 하위10%" if ret[d] <= T1[0.10] else ""
        print(f"  {d} {wd[d.weekday()]}  {pct(ret[d]):>8}  {pct(eq.get(d)):>8}  {pct(calc.get(d)):>8} | {cash:6.1%} {mx:6.1%} {ns:3d}{mark}")
    print("  주(7일) 수익률:", ", ".join(f"{d.strftime('%m-%d')} {pct(wk[d], 1)}" for d in wdays))

    big1 = {q: {d: ret[d] <= T1[q] for d in days} for q in T1}
    big5 = {q: {d: wk[d] <= T5[q] for d in wdays} for q in T5}
    for q in (PCT_MAIN,) + PCT_ALT:
        b1 = [d for d in days if big1[q][d]]
        b5 = [d for d in wdays if big5[q][d]]
        print(f"  문턱 하위 {q:.0%}: 하루 {pct(T1[q])} 이하 {len(b1)}일 {[d.strftime('%m-%d') for d in b1]}"
              f" → 독립(이어진 날 묶음) {len(runs(days, big1[q]))}건 | 주 {pct(T5[q])} 이하 {len(b5)}개"
              f" → 독립(겹치는 주 묶음) {len(runs(wdays, big5[q], gap=6))}건")

    # 시장 전체 몫: 손 계좌가 크게 잃은 날 나눠 사 두기도 크게 잃었나
    b1 = [d for d in days if big1[PCT_MAIN][d]]
    both = [d for d in b1 if eq.get(d) is not None and eq[d] <= T1[PCT_MAIN]]
    print(f"  시장 몫: 손 계좌가 하루 하위10%인 {len(b1)}일 중 나눠 사 두기도 하위10%인 날 {len(both)}일;"
          f" 손 − 나눠 사 두기 차이 가운데 값 {pct(median([ret[d] - eq[d] for d in b1]))}"
          f" (모든 날 가운데 값 {pct(median([ret[d] - eq[d] for d in days if d in eq]))})")
    corr_days = [d for d in days if d in eq]
    xs, ys = [ret[d] for d in corr_days], [eq[d] for d in corr_days]
    mx_, my_ = fmean(xs), fmean(ys)
    cov = fmean([(x - mx_) * (y - my_) for x, y in zip(xs, ys)])
    sx = math.sqrt(fmean([(x - mx_) ** 2 for x in xs]))
    sy = math.sqrt(fmean([(y - my_) ** 2 for y in ys]))
    print(f"  손 계좌와 나눠 사 두기의 하루 수익률 상관 {cov / (sx * sy):.3f}, 기울기 {cov / sy ** 2:.3f}")

    # ---------------- 조건 값 (판단 때) + 시각 확인
    print("=" * 100)
    print("[E3] 조건 값의 시각 확인")
    reading = defaultdict(dict)
    for r in jl("reading"):
        if r["asset"] in TARGETS:
            reading[day(r["run"])][r["asset"]] = r
    late_reading, match, mismatch = 0, 0, []
    for d in days:
        at = DECIDED[d]
        st = states_at(at)
        for a in TARGETS:
            r = reading[d].get(a)
            if r is None:
                continue
            kt = day(r["known_through"])
            if C.final_dt(C.ASSETS[a], kt) > at:
                late_reading += 1
            same = all(st[a][0][k] == r["values"].get(k) for k in pieces.STATES)
            if same:
                match += 1
            else:
                mismatch.append((d, a))
    print(f"  시세 대 보기(reading, 세 대상): 본 마지막 봉이 판단 시각 뒤에 끝난 줄 {late_reading}개;"
          f" 시세 파일로 판단 시각에 다시 계산한 일곱 값이 기록과 같은 줄 {match}개, 다른 줄 {len(mismatch)}개 {mismatch[:6]}")

    hr = {day(r["run"]): r for r in jl("hand_reading")}
    research = defaultdict(list)
    for r in jl("research"):
        research[day(r["run"])].append(r)
    rc_late = defaultdict(set)
    for r in jl("reading_check"):
        if r.get("late"):
            rc_late[r["state"]].add(day(r["target"].split(":")[1]))
    # 조사 상태별: 값이 있는 날, 그날 조사 답의 as_of(있는 것) 중 판단 시각 뒤 것, as_of 없는 날, 다시 보기에서 '늦음'
    side = defaultdict(Counter)
    for d, r in hr.items():
        for s, v in r["values"].items():
            side[s][v] += 1
    research_states = sorted(s for s, c in side.items() if c["예"] >= RESEARCH_MIN_SIDE and c["아니오"] >= RESEARCH_MIN_SIDE)
    dropped_states = sorted(s for s in side if s not in research_states)
    timing_bad = {}
    for s in sorted(side):
        vdays = [d for d in days if d in hr and hr[d]["values"].get(s) in ("예", "아니오")]
        after = noasof = 0
        for d in vdays:
            rows = [x for x in research[d] if x.get("state") == s] or research[d]
            if any("as_of" not in x for x in rows):
                noasof += 1
            if any("as_of" in x and dt.datetime.fromisoformat(x["as_of"]) > DECIDED[d] for x in rows):
                after += 1
        late = sorted(rc_late.get(s, set()))
        timing_bad[s] = after > 0 or bool(late)
        print(f"  {s:24s} 예 {side[s]['예']:2d} 아니오 {side[s]['아니오']:2d} 기타 {sum(side[s].values()) - side[s]['예'] - side[s]['아니오']:2d}"
              f" | 계좌 날 중 값 {len(vdays):2d}일, as_of 판단 뒤 {after}일, as_of 없음(11-05 전) {noasof}일, 다시 보기 '늦음' {late}"
              f" | {'후보' if s in research_states and not timing_bad[s] else '뺌'}")
    research_states = [s for s in research_states if not timing_bad[s]]
    print(f"  조사 후보로 쓴 상태 {len(research_states)}개: {research_states}")
    print(f"  뺀 상태 {len(dropped_states)}개(한쪽 값이 3일 미만 또는 시각 문제): {dropped_states}")

    # 조건 값 사전
    conds: dict[str, dict] = {}
    descr: dict[str, str] = {}
    for cid, s, want, txt in PRICE_CANDS:
        conds[cid] = {d: majority([reading[d][a]["values"].get(s) for a in TARGETS if a in reading[d]], want)
                      for d in days if len(reading[d]) == 3}
        descr[cid] = txt
    # C6: 판단 시각에 다시 계산 (기록에 없는 새 값이라 시세에서만)
    krx_vol = []  # (날짜, 평균 vol20) 거래일마다 08:40 판단 기준

    def vmean(at):
        st = states_at(at)
        if any("vol20" not in st[a][1] for a in TARGETS):
            return None
        return fmean(st[a][1]["vol20"] for a in TARGETS)

    for d in KRX:
        if d < dt.date(2018, 1, 1):
            continue
        v = vmean(dt.datetime.combine(d, PAST_JUDGE, KST))
        if v is not None:
            krx_vol.append((d, v))
    vol_dates = [x[0] for x in krx_vol]

    def c6_value(d: dt.date, v: float | None) -> str:
        k = bisect.bisect_left(vol_dates, d)  # d 전 거래일까지
        prev = [x[1] for x in krx_vol[:k]]
        if v is None or len(prev) < 250:
            return "모름"
        return "예" if v > median(prev) else "아니오"

    conds[C6[0]] = {d: c6_value(d, vmean(DECIDED[d])) for d in days}
    descr[C6[0]] = C6[1]
    for k, s in enumerate(research_states, 1):
        conds[f"R{k}_" + s[4:]] = {d: hr[d]["values"].get(s, "모름") if d in hr else "모름" for d in days}
        descr[f"R{k}_" + s[4:]] = f"손 대 보기 {s} = 예 (그날 기록된 값)"
    # 간밤 격차 내림: 기록된 숫자(코드가 정한 값의 재료)에서 z ≤ −1
    od = {}
    for d in days:
        n = (hr.get(d, {}).get("numbers") or {}).get("S_q_overnight_gap") or {}
        x, sd = n.get("kr_overnight_pct"), n.get("kr_overnight_sd60_pct")
        od[d] = "모름" if x is None or not sd else ("예" if x / sd <= -1 else "아니오")
    rid = f"R{len(research_states) + 1}_overnight_down"
    conds[rid] = od
    descr[rid] = "기록된 kr_overnight_pct ÷ kr_overnight_sd60_pct ≤ −1 (국내 간밤 격차가 평소 하루의 1배 넘게 아래)"
    for cid, txt in ACCOUNT_CANDS:
        v = {}
        for d in days:
            w = weights(row_at[d])
            if cid == "A1_cash_lt10":
                v[d] = "예" if 1 - sum(w.values()) < 0.10 else "아니오"
            elif cid == "A2_one_ge40":
                v[d] = "예" if (max(w.values()) if w else 0) >= 0.40 else "아니오"
            else:
                v[d] = "예" if any(a not in TARGETS for a in w) else "아니오"
        conds[cid] = v
        descr[cid] = txt
    late_acc = [d for d in days if row_at[d].get("at") and dt.datetime.fromisoformat(row_at[d]["at"]).time() >= dt.time(9, 0)]
    no_at = [d for d in days if not row_at[d].get("at")]
    print(f"  계좌 후보 값 = 판단 d의 계좌 줄(그날 09:00 주문 전 보유, 판단 때 평가 가격). 줄 시각 09:00 이후 {len(late_acc)}일,"
          f" 시각 칸 없음(1기 옛 장부 {no_at[0] if no_at else '–'} ~ {no_at[-1] if no_at else '–'}) {len(no_at)}일 — 그날 run 줄 판단 시각으로 봄")
    bad_run = [d for d in days if DECIDED[d].time() >= dt.time(9, 0)]
    print(f"  판단 시각(run decided_at)이 09:00 이후인 날 {len(bad_run)}일")
    print(f"  해 본 후보 수 {len(conds)}: 시세 {len(PRICE_CANDS) + 1}, 조사 {len(research_states) + 1}, 계좌 {len(ACCOUNT_CANDS)}")

    # ---------------- 계좌 기록 대 보기
    print("=" * 100)
    print("[E4] 우리 기록(손 계좌) — 후보마다 켜짐×잃음 (문턱 하위 10%), 하루 = 이어진 날 묶음 / 주 = 겹치는 주(6일 안) 묶음")
    print("  덧붙인 두 결과: (시장) 같은 날 나눠 사 두기 계좌가 하위 10%로 잃었나 — 계좌 자신의 선택과 상관없는 결과로도 같은 표가 나오면"
          " 그 후보는 '시기 표시'일 뿐 / (체결 뒤) 거래일만, 묶음 시가(d) → 종가(d) — 판단이 바꿀 수 있던 몫(간밤 격차는 빠짐)")
    print("  돌려 맞추기(기록): 조건 줄을 3칸 이상 통째로 돌렸을 때 켜짐∧잃음 날 수가 실제 이상인 비율(35일이라 거칠다)")
    eq_big = {d: eq[d] <= T1[PCT_MAIN] for d in days if d in eq}
    fill_ret = {d: basket_ret(KIDX[d], 1) for d in days if d in KIDX and d <= FILL_END}
    fill_ret = {d: x for d, x in fill_ret.items() if x is not None}
    fill_days = sorted(fill_ret)
    fill_big = {d: x <= T1[PCT_MAIN] for d, x in fill_ret.items()}
    print(f"  체결 뒤 묶음: 거래일 {len(fill_days)}일({fill_days[0]} ~ {fill_days[-1]}), 하위 10% 잃은 날 {sum(fill_big.values())}일"
          f" {[d.strftime('%m-%d') for d in fill_days if fill_big[d]]}")
    rec_tab = {}
    for cid in conds:
        t1 = table(days, conds[cid], big1[PCT_MAIN], gap=1)
        t5 = table(wdays, conds[cid], big5[PCT_MAIN], gap=6)
        alt = [table(days, conds[cid], big1[q], gap=1) for q in PCT_ALT]
        alt5 = [table(wdays, conds[cid], big5[q], gap=6) for q in PCT_ALT]
        te = table(days, conds[cid], eq_big, gap=1)
        tf = table(fill_days, conds[cid], fill_big, gap=1, index=KIDX)
        sp = shift_p(days, conds[cid], big1[PCT_MAIN], min_shift=3)
        rec_tab[cid] = (t1, t5, alt, alt5, te, tf, sp)
        print(f"  {cid:20s} {descr[cid]}")
        print(f"      하루 {line(t1)}")
        print(f"      주   {line(t5)}")
        print("      대안 문턱 하루 설명/예외: " + ", ".join(f"{q:.0%} {x['explained']}/{x['exceptions']}" for q, x in zip(PCT_ALT, alt))
              + " | 주: " + ", ".join(f"{q:.0%} {x['explained']}/{x['exceptions']}" for q, x in zip(PCT_ALT, alt5)))
        print(f"      (시장) 켜짐 {rate(te['a'], te['a'] + te['b'])} 꺼짐 {rate(te['c'], te['c'] + te['d'])}"
              f" | (체결 뒤) 켜짐 {rate(tf['a'], tf['a'] + tf['b'])} 꺼짐 {rate(tf['c'], tf['c'] + tf['d'])} 설명 {tf['explained']} 예외 {tf['exceptions']}"
              f" | 돌려 맞추기 p {('%.2f' % sp) if sp is not None else '–'}")

    print("-" * 100)
    print("[E5] 사례 전체 표 — 손 계좌의 모든 날과 그날 판단 때 후보 값 (예 / 아 = 아니오 / 일 = 일부 / 모 = 모름·안 물음 — 2×2에서는 예·아니오만 셈), * = 하루 하위 10% 잃음")
    show = list(conds)
    print("  날짜  수익률    " + " ".join(f"{c.split('_')[0]:>3}" for c in show))
    print("         (열 순서) " + ", ".join(show))
    for d in days:
        mark = "*" if big1[PCT_MAIN][d] else " "
        print(f"  {d.strftime('%m-%d')}{mark}{pct(ret[d]):>8} " + " ".join(f"{conds[c].get(d, '모름')[:1]:>3}" for c in show))
    print("  주(7일) 하위 10% 잃은 주의 시작일과 값:")
    for d in wdays:
        if big5[PCT_MAIN][d]:
            print(f"  {d.strftime('%m-%d')} {pct(wk[d]):>8} " + " ".join(f"{conds[c].get(d, '모름')[:1]:>3}" for c in show))
    print("  켜진 날 수(계좌 날 중): " + ", ".join(f"{c} {sum(v == '예' for v in conds[c].values())}/{sum(v in ('예', '아니오') for v in conds[c].values())}" for c in show))

    # 돌아보기 분류: 크게 잃은 날 판단한 손 예측(1봉)의 돌아보기 why
    rv = jl("review")
    big_days = {d for d in days if big1[PCT_MAIN][d]}
    big_weeks = {d for d in wdays if big5[PCT_MAIN][d]}
    why = defaultdict(Counter)
    for r in rv:
        dd, _, _, h = r["prediction"].split(":")
        d0 = day(dd)
        group = "1봉" if h == "1" else "1주(5봉·7봉)" if h in ("5", "7") else f"{h}봉"
        why[(group, "모든 날")][r["why"]] += 1
        if (h == "1" and d0 in big_days) or (h in ("5", "7") and d0 in big_weeks):
            why[(group, "크게 잃은 날·주")][r["why"]] += 1
    print("  돌아보기 분류(손 예측, 기록된 why만 셈 — 글은 읽지 않음). 1봉은 하루 하위 10% 날, 1주는 주 하위 10% 주의 시작일에 판단한 것:")
    for k in sorted(why):
        c = why[k]
        print(f"    {k[0]:12s} {k[1]:10s} {sum(c.values()):4d}건 바깥 상황 {c['바깥 상황']:3d} | {dict(c.most_common())}")
    mr = Counter(r["why"] for r in rv if "Q_margin_relief" in r.get("wrong", []))
    print(f"  Q_margin_relief가 틀린 조각으로 적힌 돌아보기 {sum(mr.values())}건: {dict(mr.most_common())}")

    # ---------------- 과거 시세
    print("=" * 100)
    print("[E6] 과거 시세 — 세 대상 같은 비중 묶음, 거래일마다 08:40 판단(그때 끝난 봉만), 체결가 기준. 잃음 = 묶음 하위 10% 문턱(E1)")
    pdays_all = []
    pst = {}
    for i, d in enumerate(KRX):
        at = dt.datetime.combine(d, PAST_JUDGE, KST)
        st = states_at(at)
        if any(st[a][0]["S_vol_high"] == "모름" for a in TARGETS):
            continue
        pst[d] = st
        pdays_all.append(d)
    pc = {}
    for cid, s, want, _ in PRICE_CANDS:
        pc[cid] = {d: majority([pst[d][a][0][s] for a in TARGETS], want) for d in pdays_all}
    vm = {d: fmean(pst[d][a][1]["vol20"] for a in TARGETS) for d in pdays_all}
    pc[C6[0]] = {d: c6_value(d, vm[d]) for d in pdays_all}
    # 시기 잣대(판단 때 아는 값): 추세 = 세 대상 60봉 수익률 평균 부호, 흔들림 = 세 대상 60봉 흔들림 평균이 그때까지 가운데 값 위
    r60 = {d: fmean(pst[d][a][1]["r60"] for a in TARGETS) for d in pdays_all}
    v60 = {d: fmean(pst[d][a][1]["vol60"] for a in TARGETS) for d in pdays_all}
    v60_hist = []
    regime_vol = {}
    for d in pdays_all:
        regime_vol[d] = ("흔들림 큼" if v60[d] > median(v60_hist) else "흔들림 작음") if len(v60_hist) >= 250 else None
        v60_hist.append(v60[d])
    regime_trend = {d: "오르는 때" if r60[d] > 0 else "내리는 때" for d in pdays_all}
    print(f"  판단 날 {len(pdays_all)}일: {pdays_all[0]} ~ {pdays_all[-1]} (세 대상 모두 251봉이 찬 날부터)")
    past_out = {}
    past_loss, past_gain, past_days = {}, {}, {}
    for h in (1, 5, 20):
        gap = h  # 같은 사건: 시작일이 h거래일 안이면 겹치는 기간 → 한 건
        loss = {}
        gain = {}
        for d in pdays_all:
            x = basket_ret(KIDX[d], h)
            if x is not None:
                loss[d] = x <= TH[h]["lo"][PCT_MAIN]
                gain[d] = x >= TH[h]["hi"][PCT_MAIN]
        # 우리 기록과 나누려고 목표 봉이 기록 시작 전(~2026-10-20)에 끝난 날만
        pd_ = [d for d in pdays_all if d in loss and KRX[KIDX[d] + h - 1] <= PRE_END]
        past_loss[h], past_gain[h], past_days[h] = loss, gain, pd_
        print(f"  --- {h}거래일 (판단 날 {len(pd_)}: {pd_[0]} ~ {pd_[-1]}) 잃음 기저율 {rate(sum(loss[d] for d in pd_), len(pd_))}")
        for cid in pc:
            t = table(pd_, pc[cid], loss, gap=gap, index=KIDX)
            tg = table(pd_, pc[cid], gain, gap=gap, index=KIDX)
            sp = shift_p(pd_, pc[cid], loss)
            alt = []
            for q in PCT_ALT:
                lq = {d: basket_ret(KIDX[d], h) <= TH[h]["lo"][q] for d in pd_}
                ta = table(pd_, pc[cid], lq, gap=gap, index=KIDX)
                alt.append((q, ta["p_on"], ta["p_off"]))
            # 시기별
            slices = []
            for name, key in (("해", lambda d: str(d.year)), ("추세", lambda d: regime_trend[d]), ("흔들림", lambda d: regime_vol[d])):
                groups = defaultdict(list)
                for d in pd_:
                    k = key(d)
                    if k is not None:
                        groups[k].append(d)
                for k in sorted(groups):
                    ts = table(groups[k], pc[cid], loss, gap=gap, index=KIDX)
                    ok = ts["on_runs"] >= 5 and ts["off_runs"] >= 5 and ts["episodes"] >= 3
                    slices.append((name, k, ts, ok))
            past_out[(cid, h)] = (t, tg, sp, slices, alt)
            elig = [s for s in slices if s[3]]
            pos = [s for s in elig if s[2]["p_on"] is not None and s[2]["p_off"] is not None and s[2]["p_on"] > s[2]["p_off"]]
            print(f"  {cid:12s} {line(t)} | 돌려 맞추기 p {('%.3f' % sp) if sp is not None else '–'}")
            print(f"  {'':12s} 크게 번 날(상위10%) 켜짐 {rate(tg['a'], tg['a'] + tg['b'])} 꺼짐 {rate(tg['c'], tg['c'] + tg['d'])}"
                  f" | 대안 문턱 켜짐/꺼짐 잃음률: " + ", ".join(f"{q:.0%} {a_:.2f}/{b_:.2f}" for q, a_, b_ in alt if a_ is not None and b_ is not None)
                  + f" | 시기 칸 {len(elig)}개 중 켜짐 쪽이 더 잃음 {len(pos)}개")
            if h in (1, 5):
                for name, k, ts, ok in slices:
                    print(f"  {'':14s}{name:4s} {k:8s} {'' if ok else '(표본 모자람) '}켜짐 {rate(ts['a'], ts['a'] + ts['b']):>12} 꺼짐 {rate(ts['c'], ts['c'] + ts['d']):>12}"
                          f" 잃은 구간 {ts['episodes']:3d} 설명 {ts['explained']:3d}")

    # ---------------- 대상 하나씩, 더 긴 시세(시기를 더 넓게)
    print("=" * 100)
    print("[E7] 대상 하나씩(자기 상태 → 자기 수익률), 자기 시세 첫 251봉 뒤 ~, 문턱 = 그 대상 시가→종가 수익률의 ~2026-10-20 하위 10%")
    for a in TARGETS:
        rows = ROWS[a]
        dates = [r.date for r in rows]
        idx = {d: j for j, d in enumerate(dates)}
        for h in (1, 5):
            xs = [rows[j + h - 1].close / rows[j].open - 1 for j in range(len(rows) - h + 1) if rows[j + h - 1].date <= PRE_END]
            th = quantile(xs, PCT_MAIN)
            cond, loss = {}, {}
            for j, d in enumerate(dates):
                at = dt.datetime.combine(d, PAST_JUDGE, KST)
                c = known_closes(a, at)
                if len(c) < pieces.NEED or j + h - 1 >= len(rows) or rows[j + h - 1].date > PRE_END:
                    continue
                vals = pieces.read(c[-pieces.NEED:])[0]
                cond[d] = vals["S_vol_high"]
                loss[d] = rows[j + h - 1].close / rows[j].open - 1 <= th
            ds = sorted(cond)
            t = table(ds, cond, loss, gap=h, index=idx)
            years = defaultdict(list)
            for d in ds:
                years[d.year].append(d)
            ys = []
            same = opp = tie = 0
            for y in sorted(years):
                ty = table(years[y], cond, loss, gap=h, index=idx)
                if ty["p_on"] is not None and ty["p_off"] is not None:
                    ys.append(f"{y} {ty['p_on']:.2f}/{ty['p_off']:.2f}")
                    a2, b2 = round(ty["p_on"], 2), round(ty["p_off"], 2)
                    same, opp, tie = same + (a2 > b2), opp + (a2 < b2), tie + (a2 == b2)
            print(f"  {a:8s} {h}봉 문턱 {pct(th)} S_vol_high: {line(t)}")
            print(f"  {'':8s} 해마다 켜짐/꺼짐 잃음률(소수 둘째 자리로 견줌 — 켜짐 쪽이 더 잃은 해 {same}, 반대 {opp}, 같음 {tie}): " + ", ".join(ys))

    # ---------------- 견주기
    print("=" * 100)
    print("[E8] 한 잣대로 견주기 — (가) 우리 기록 하루: 설명 ≥ 예외 이고 켜짐 잃음률 > 꺼짐 잃음률"
          " (나) 과거 1거래일: 돌려 맞추기 p < 0.05 이고 시기 칸의 2/3 이상에서 켜짐 쪽이 더 잃음 (다) 과거 5거래일도 같은 쪽."
          " 셋 다 통과한 것 가운데 과거 1거래일 켜짐÷꺼짐 잃음률이 가장 큰 것을 고른다")
    rows_out = []
    for cid in conds:
        t1, t5 = rec_tab[cid][0], rec_tab[cid][1]
        if t1["a"] + t1["b"] == 0 or t1["c"] + t1["d"] == 0:
            ga = None  # 기록에서 한쪽 값만 나와 가를 수 없음
        else:
            ga = t1["explained"] >= t1["exceptions"] and t1["p_on"] > t1["p_off"] and t1["episodes"] > 0
        na = da = None
        rr_past = None
        if (cid, 1) in past_out:
            t, tg, sp, slices, _ = past_out[(cid, 1)]
            elig = [x for x in slices if x[3]]
            pos = [x for x in elig if x[2]["p_on"] > x[2]["p_off"]]
            na = sp is not None and sp < 0.05 and len(pos) * 3 >= 2 * len(elig)
            rr_past = t["p_on"] / t["p_off"] if t["p_off"] else None
            t5p = past_out[(cid, 5)][0]
            da = t5p["p_on"] > t5p["p_off"]
        rows_out.append((cid, ga, na, da, rr_past))
        gtxt = "판정 불가(한쪽 값뿐)" if ga is None else "통과" if ga else "못 함"
        print(f"  {cid:20s} (가) {gtxt:12s} [하루 설명 {t1['explained']}/예외 {t1['exceptions']}, 주 설명 {t5['explained']}/예외 {t5['exceptions']}]"
              f" (나) {'–' if na is None else '통과' if na else '못 함'} (다) {'–' if da is None else '같은 쪽' if da else '반대'}"
              f" 과거 1일 켜짐÷꺼짐 잃음률 {('%.2f' % rr_past) if rr_past else '–'}")
    passed = sorted((r for r in rows_out if r[1] and r[2] and r[3]), key=lambda r: -(r[4] or 0))
    print(f"  셋 다 통과: {[r[0] for r in passed]}")
    print(f"  (가)만 통과(과거 시험 없음 또는 못 함): {[r[0] for r in rows_out if r[1] and not (r[2] and r[3])]}")
    print(f"  (나)·(다)는 통과, (가)는 판정 불가: {[r[0] for r in rows_out if (r[2] and r[3]) and r[1] is None]}")
    print(f"  (나)·(다)는 통과, (가)는 못 함: {[r[0] for r in rows_out if (r[2] and r[3]) and r[1] is False]}")
    if not passed:
        print("  고른 것: 없음")
        print("끝.")
        return 0
    pick = passed[0][0]
    print(f"  고른 것: {pick} — {descr[pick]}")

    # ---------------- 고른 후보의 사례 전체 표
    print("-" * 100)
    print(f"[E8a] {pick} 우리 기록의 날마다 판정 (설명 = 켜짐∧잃음, 예외 = 꺼짐∧잃음, 헛켜짐 = 켜짐∧안 잃음, 맞게 꺼짐 = 꺼짐∧안 잃음)")
    cv = conds[pick]
    cnt = Counter()
    for d in days:
        on, ls = cv.get(d) == "예", big1[PCT_MAIN][d]
        v = "설명" if on and ls else "예외" if ls else "헛켜짐" if on else "맞게 꺼짐"
        cnt[v] += 1
        print(f"  {d} {cv.get(d)} {pct(ret[d]):>8} {v}")
    print(f"  셈: {dict(cnt)} | 켜진 구간 {runs(days, {d: cv.get(d) == '예' for d in days})[0][0] if any(cv.get(d) == '예' for d in days) else '–'} 부터"
          f" {len(runs(days, {d: cv.get(d) == '예' for d in days}))}개, 꺼진 구간 {len(runs(days, {d: cv.get(d) == '아니오' for d in days}))}개")

    print("-" * 100)
    print(f"[E8b] {pick} 과거 시세(1거래일) 켜진 구간 전부: 시작 ~ 끝, 거래일 수, 하위 10% 잃은 날, 상위 10% 번 날, 구간 동안 묶음 1거래일 수익률 합")
    pdh = past_days[1]
    pon = {d: pc[pick][d] == "예" for d in pdh}
    r1 = {d: basket_ret(KIDX[d], 1) for d in pdh}
    for rn in runs(pdh, pon, 1, KIDX):
        nl = sum(past_loss[1][d] for d in rn)
        ng = sum(past_gain[1][d] for d in rn)
        print(f"  {rn[0]} ~ {rn[-1]} {len(rn):4d}일 잃음 {nl:3d} 범 {ng:3d} 합 {pct(sum(r1[d] for d in rn), 1):>7}{'  ← 헛켜짐' if nl == 0 else ''}")
    worst = sorted(pdh, key=lambda d: r1[d])[:15]
    print("  반대 쪽 사례 — 과거 1거래일 묶음이 가장 크게 잃은 15일과 그날 값(아니오면 예외):")
    for d in worst:
        print(f"  {d} {pct(r1[d]):>8} {pc[pick][d]}")
    print(f"  15일 중 켜짐 {sum(pc[pick][d] == '예' for d in worst)}일, 꺼짐 {sum(pc[pick][d] == '아니오' for d in worst)}일")
    for cid in (pick, C6[0]):
        dd_ = [d for d in pdh if pc[cid].get(d) in ("예", "아니오")]
        t = table(dd_, pc[cid], past_loss[1], gap=1, index=KIDX)
        share_on = (t["a"] + t["b"]) / t["n"]
        print(f"  {cid}: 잃은 구간 {t['episodes']}건 중 켜진 때 시작 {t['explained']}건 = {t['explained'] / t['episodes']:.2f}"
              f" — 켜진 날 몫 {share_on:.2f}(조건이 아무 상관 없으면 기대되는 몫) | 꺼진 때 시작(예외) {t['exceptions']}건")
    off_l = [d for d in pdh if not pon[d] and past_loss[1][d]]
    print(f"  꺼진 날 잃음(예외 날) {len(off_l)}일, 그 가운데 큰 순서 다섯: " + ", ".join(f"{d} {pct(r1[d])}" for d in sorted(off_l, key=lambda d: r1[d])[:5]))

    # ---------------- 판정 계획 재료
    print("=" * 100)
    print("[E9] 판정 계획 재료 — 과거(~2026-10-20) 속도로 본 앞으로 쌓일 독립 사례")
    eps = runs(pdh, past_loss[1], 1, KIDX)
    yrs = (pdh[-1] - pdh[0]).days / 365.25
    on_r = runs(pdh, pon, 1, KIDX)
    off_r = runs(pdh, {d: not pon[d] for d in pdh}, 1, KIDX)
    print(f"  과거 {yrs:.1f}년: 1거래일 하위 10% 잃은 구간 {len(eps)}건(해마다 {len(eps) / yrs:.1f}건), 거래일 해마다 {len(pdh) / yrs:.0f}일,"
          f" {pick} 켜진 구간 {len(on_r)}개·꺼진 구간 {len(off_r)}개(해마다 {len(on_r) / yrs:.1f}·{len(off_r) / yrs:.1f}개),"
          f" 켜진 구간 길이 가운데 값 {median(len(x) for x in on_r):.0f}거래일")
    print(f"  우리 기록: 하루 하위 10% 잃은 구간 {len(runs(days, big1[PCT_MAIN]))}건 / {len(days)}일")
    print("끝.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
