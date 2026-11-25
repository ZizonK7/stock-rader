"""P-0001 심사 검산 스크립트 (읽기만, 외부 패키지 없음, 표준 출력만).

저장소 루트에서:  python papers/P-0001/review_check.py
- records/, data/prices/, papers/P-0001/을 읽기만 하고 아무 파일도 쓰지 않는다.
- 논문의 재계산 대조, 판단 시각·문턱 누출 점검, 반례 탐색, 다른 설명 (가)~(라),
  빠진 시세 상태(S_trend_60·S_above_ma200), 판정 계획 검정력까지 직접 계산해 출력한다.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median, pstdev

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
PRE_END = dt.date(2026, 10, 20)
RECORD_END = dt.date(2026, 11, 25)
FILL_END = dt.date(2026, 11, 23)
PAST_JUDGE = dt.time(8, 40)
SHIFT_MIN = 60


def jl(name: str) -> list[dict]:
    with (REC / f"{name}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["run"] <= RECORD_END.isoformat()]


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def pct(x: float | None, nd: int = 2) -> str:
    return "–" if x is None else f"{x * 100:+.{nd}f}%"


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def fisher_greater(a: int, b: int, c: int, d: int) -> float:
    r1, c1, n = a + b, a + c, a + b + c + d
    den = math.comb(n, c1)
    return sum(math.comb(r1, x) * math.comb(n - r1, c1 - x) for x in range(a, min(r1, c1) + 1)) / den


def runs(days: list, flag: dict, gap: int = 1, index: dict | None = None) -> list[list]:
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


def table_strict(days: list, cond: dict, loss: dict, loss_gap: int = 1, run_gap: int = 1,
                 index: dict | None = None) -> dict:
    ds = [d for d in days if cond.get(d) in ("예", "아니오") and d in loss]
    on = {d: cond[d] == "예" for d in ds}
    ls = {d: loss[d] for d in ds}
    a = sum(on[d] and ls[d] for d in ds)
    b = sum(on[d] and not ls[d] for d in ds)
    c = sum((not on[d]) and ls[d] for d in ds)
    e = sum((not on[d]) and not ls[d] for d in ds)
    eps = runs(ds, ls, loss_gap, index)
    expl = [ep for ep in eps if on[ep[0]]]
    on_runs = runs(ds, on, run_gap, index)
    off_runs = runs(ds, {d: not on[d] for d in ds}, run_gap, index)
    p_on = a / (a + b) if a + b else None
    p_off = c / (c + e) if c + e else None
    rr = (p_on / p_off) if (p_on is not None and p_off) else None
    return {
        "n": len(ds), "a": a, "b": b, "c": c, "d": e,
        "p_on": p_on, "p_off": p_off, "rr": rr,
        "fisher": fisher_greater(a, b, c, e) if ds else None,
        "episodes": len(eps), "explained": len(expl), "exceptions": len(eps) - len(expl),
        "on_runs": len(on_runs), "off_runs": len(off_runs),
        "eps": eps, "on_run_list": on_runs, "off_run_list": off_runs,
    }


def _bits(flags: list[bool]) -> int:
    m = 0
    for i, f in enumerate(flags):
        if f:
            m |= 1 << i
    return m


def _rot_right(mask: int, k: int, n: int) -> int:
    """on[(j + k) % n]에 해당하는 비트 마스크 (왼쪽에서 오른쪽으로 k칸 순환 이동)."""
    low = mask & ((1 << k) - 1)
    return (mask >> k) | (low << (n - k))


def shift_p_vec(on: list[bool], ev: list[bool], min_shift: int = SHIFT_MIN) -> float | None:
    n = len(on)
    if n < 2 * min_shift + 10:
        return None
    on_m, ev_m = _bits(on), _bits(ev)
    obs = (on_m & ev_m).bit_count()
    ks = range(min_shift, n - min_shift)
    ge = sum((_rot_right(on_m, k, n) & ev_m).bit_count() >= obs for k in ks)
    return ge / len(ks)


def shift_p(days: list, cond: dict, ev: dict, min_shift: int = SHIFT_MIN) -> float | None:
    ds = [d for d in days if cond.get(d) in ("예", "아니오") and d in ev]
    return shift_p_vec([cond[d] == "예" for d in ds], [ev[d] for d in ds], min_shift)


def logrets(closes: list[float], n: int) -> list[float]:
    seg = closes[-(n + 1):]
    return [math.log(b / a) for a, b in zip(seg, seg[1:]) if a > 0 and b > 0]


def majority(vals: list[str], want: str, k_need: int = 2) -> str:
    known = [v for v in vals if v is not None and v != "모름"]
    if len(known) < len(vals):
        return "모름"
    return "예" if sum(v == want for v in vals) >= k_need else "아니오"


def fmt_stat(label: str, t: dict, sp: float | None = None) -> str:
    p_on = f"{t['p_on']:.3f}" if t["p_on"] is not None else "  –  "
    p_off = f"{t['p_off']:.3f}" if t["p_off"] is not None else "  –  "
    rr = f"{t['rr']:.2f}" if t["rr"] is not None else "  –  "
    fp = f"{t['fisher']:.3f}" if t["fisher"] is not None else "  –  "
    sps = f"{sp:.3f}" if sp is not None else "  –  "
    return (f"  {label:32s} n={t['n']:4d} | ON {t['a']:3d}/{t['a']+t['b']:4d}({p_on}) "
            f"OFF {t['c']:3d}/{t['c']+t['d']:4d}({p_off}) | rr={rr} | Fisher={fp} | shift_p={sps} "
            f"| ep={t['episodes']:3d}(설명 {t['explained']:3d}/예외 {t['exceptions']:3d})")


def main() -> int:
    ROWS = {a: P.load(a) for a in TARGETS}
    FINAL = {a: [C.final_dt(C.ASSETS[a], r.date) for r in ROWS[a]] for a in TARGETS}
    BY_DATE = {a: {r.date: r for r in ROWS[a]} for a in TARGETS}
    KRX = sorted(set(BY_DATE["069500"]) & set(BY_DATE["360750"]))
    KIDX = {d: i for i, d in enumerate(KRX)}

    def known_rows(a: str, at: dt.datetime):
        k = bisect.bisect_right(FINAL[a], at)
        return ROWS[a][:k]

    def known_closes(a: str, at: dt.datetime) -> list[float]:
        return [r.close for r in known_rows(a, at)]

    def basket_ret(i: int, h: int) -> float | None:
        if i + h - 1 >= len(KRX):
            return None
        d, t = KRX[i], KRX[i + h - 1]
        if d not in BY_DATE[COIN] or t not in BY_DATE[COIN]:
            return None
        rs = [BY_DATE[a][t].close / BY_DATE[a][d].open - 1 for a in ETFS]
        rs.append(BY_DATE[COIN][t].close / BY_DATE[COIN][d].open - 1)
        return fmean(rs)

    def asset_ret_krx(a: str, i: int, h: int) -> float | None:
        if i + h - 1 >= len(KRX):
            return None
        d, t = KRX[i], KRX[i + h - 1]
        if d not in BY_DATE[a] or t not in BY_DATE[a]:
            return None
        return BY_DATE[a][t].close / BY_DATE[a][d].open - 1

    # ------------------------------------------------------------ [R1] 코드 로직·정렬·누출 경계
    print("=" * 100)
    print("[R1] 시세 정렬·판단 시각 경계·기록 시각 정밀 점검")
    for a in TARGETS:
        dates = [r.date for r in ROWS[a]]
        strictly_sorted = all(dates[i] < dates[i + 1] for i in range(len(dates) - 1))
        print(f"  {a:8s}: 줄 {len(ROWS[a]):4d} ({dates[0]} ~ {dates[-1]}), 날짜 엄격 오름차순={strictly_sorted}")

    # 과거 시험 1266일 모두에 대해 마지막 본 봉의 시각 확인
    pdays_all = []
    pst = {}
    closes_at_day = {}
    leak_bars = 0
    max_etf_lag_violation = 0
    max_btc_lag_violation = 0
    for d in KRX:
        at = dt.datetime.combine(d, PAST_JUDGE, KST)
        st = {}
        ok = True
        for a in TARGETS:
            kr = known_rows(a, at)
            if len(kr) < pieces.NEED:
                ok = False
                break
            last_bar = kr[-1]
            if C.final_dt(C.ASSETS[a], last_bar.date) > at:
                leak_bars += 1
            if a in ETFS and last_bar.date >= d:
                max_etf_lag_violation += 1
            if a == COIN and last_bar.date >= d - dt.timedelta(days=1):
                max_btc_lag_violation += 1
            c = [r.close for r in kr]
            st[a] = pieces.read(c[-pieces.NEED:])
            if d <= PRE_END:
                closes_at_day[(d, a)] = c
        if ok and d <= PRE_END:
            pst[d] = st
            pdays_all.append(d)

    pd1 = [d for d in pdays_all if basket_ret(KIDX[d], 1) is not None and KRX[KIDX[d]] <= PRE_END]
    pd5 = [d for d in pdays_all if KIDX[d] + 4 < len(KRX) and KRX[KIDX[d] + 4] <= PRE_END and basket_ret(KIDX[d], 5) is not None]
    print(f"  과거 시험 1거래일={len(pd1)}일({pd1[0]}~{pd1[-1]}), 5거래일={len(pd5)}일({pd5[0]}~{pd5[-1]})")
    print(f"  시세 봉 시각 누출(final_dt > 08:40): {leak_bars}건 | ETF 당일봉 혼입: {max_etf_lag_violation}건 | BTC d-1봉 혼입: {max_btc_lag_violation}건")

    # 문턱 계산 및 기본 벡터
    xs1_pre = [basket_ret(i, 1) for i in range(len(KRX)) if KRX[i] <= PRE_END and basket_ret(i, 1) is not None]
    xs5_pre = [basket_ret(i, 5) for i in range(len(KRX)) if i + 4 < len(KRX) and KRX[i + 4] <= PRE_END and basket_ret(i, 5) is not None]
    th1_10 = quantile(xs1_pre, 0.10)
    th1_05 = quantile(xs1_pre, 0.05)
    th1_15 = quantile(xs1_pre, 0.15)
    th1_20 = quantile(xs1_pre, 0.20)
    th1_hi10 = quantile(xs1_pre, 0.90)
    th5_10 = quantile(xs5_pre, 0.10)
    print(f"  1거래일 문턱(n={len(xs1_pre)}): 5%={pct(th1_05)}, 10%={pct(th1_10)}, 15%={pct(th1_15)}, 20%={pct(th1_20)}, 상위10%={pct(th1_hi10)}")

    r1 = {d: basket_ret(KIDX[d], 1) for d in pd1}
    r5 = {d: basket_ret(KIDX[d], 5) for d in pd5}
    loss1 = {d: r1[d] <= th1_10 for d in pd1}
    gain1 = {d: r1[d] >= th1_hi10 for d in pd1}
    loss5 = {d: r5[d] <= th5_10 for d in pd5}

    c1 = {d: majority([pst[d][a][0]["S_vol_high"] for a in TARGETS], "예", 2) for d in pd1}

    # evidence.py의 gap=h 버그 점검 (5거래일·20거래일)
    print("\n  [코드 로직 점검] gap=h vs gap=h-1 (5거래일 손실 구간, 조건 구간 및 시기 칸 표본 판정 차이)")
    t5_bug = table_strict(pd5, c1, loss5, loss_gap=5, run_gap=5, index=KIDX)
    t5_fix = table_strict(pd5, c1, loss5, loss_gap=4, run_gap=1, index=KIDX)
    print(f"    evidence.py 방식(loss_gap=5, run_gap=5): 잃은 구간 {t5_bug['episodes']}(설명 {t5_bug['explained']}/예외 {t5_bug['exceptions']}), "
          f"켜진 구간 {t5_bug['on_runs']}, 꺼진 구간 {t5_bug['off_runs']}")
    print(f"    겹침 기준(loss_gap=4, run_gap=1):        잃은 구간 {t5_fix['episodes']}(설명 {t5_fix['explained']}/예외 {t5_fix['exceptions']}), "
          f"켜진 구간 {t5_fix['on_runs']}, 꺼진 구간 {t5_fix['off_runs']}")
    # 5거래일 해별 시기 칸에서 run_gap=5 vs run_gap=1에 따른 '표본 참(on_runs>=5 & off_runs>=5 & ep>=3)' 변화
    for yr in (2022, 2024, 2025, 2026):
        yds = [d for d in pd5 if d.year == yr]
        tb = table_strict(yds, c1, loss5, loss_gap=5, run_gap=5, index=KIDX)
        tf = table_strict(yds, c1, loss5, loss_gap=4, run_gap=1, index=KIDX)
        ok_b = tb["on_runs"] >= 5 and tb["off_runs"] >= 5 and tb["episodes"] >= 3
        ok_f = tf["on_runs"] >= 5 and tf["off_runs"] >= 5 and tf["episodes"] >= 3
        print(f"    5거래일 {yr}년: gap=5(on={tb['on_runs']},off={tb['off_runs']},ep={tb['episodes']},참={ok_b}) "
              f"vs gap=1(on={tf['on_runs']},off={tf['off_runs']},ep={tf['episodes']},참={ok_f}) | ON {tb['p_on']:.2f} vs OFF {tb['p_off']:.2f}")

    # 기록 시각 세부 확인: account.at vs run.decided_at, research.state, reading_check
    runs_live = {r["run"]: r for r in jl("run") if r.get("mode") == "live"}
    decided = {day(k): dt.datetime.fromisoformat(v["steps"]["decided_at"]) for k, v in runs_live.items()}
    acc_hand = defaultdict(dict)
    for r in jl("account"):
        if C.role_of(r["account"]) == "live:hand":
            acc_hand[r["account"]][day(r["run"])] = r
    at_after_decided = []
    btc_changed_days = []
    for acc, by in acc_hand.items():
        for d, r in sorted(by.items()):
            if d not in decided:
                continue
            if r.get("at"):
                at_dt = dt.datetime.fromisoformat(r["at"])
                if at_dt > decided[d]:
                    at_after_decided.append((d, acc, decided[d].strftime("%H:%M:%S"), at_dt.strftime("%H:%M:%S"),
                                             len(r.get("pending", [])), r["cash"], r["positions"].get("KRW-BTC", 0)))
    print(f"\n  [계좌 줄 시각 점검] account.at > run.decided_at인 손 계좌 줄: {len(at_after_decided)}건")
    for row in at_after_decided[:6]:
        print(f"    {row[0]} ({row[1]}): decided={row[2]} < account.at={row[3]}, pending={row[4]}건, cash={row[5]:,}, BTC_sat={row[6]:,}")
    if len(at_after_decided) > 6:
        print(f"    ... 외 {len(at_after_decided)-6}건 (마지막: {at_after_decided[-1][0]} decided={at_after_decided[-1][2]} < at={at_after_decided[-1][3]})")

    # 2026-11-10 -> 11-11 BTC 평가 기준 점프 및 2026-11-18 2기 시작 현금 비중 확인
    fills = jl("fill")
    now_fills_by_day = defaultdict(list)
    for fl in fills:
        if C.role_of(fl.get("account", "")) == "live:hand":
            now_fills_by_day[fl["run"]].append((fl.get("asset"), fl.get("side"), fl.get("fill_rule"), fl.get("filled_at")))
    print("  [11-18 2기 시작일 체결 점검] live:hand@2의 2026-11-18 체결:", now_fills_by_day.get("2026-11-18", []))

    # research.jsonl에서 state 매칭 vs fallback 확인, reading_check hit=false 확인
    res_by_day = defaultdict(list)
    for r in jl("research"):
        res_by_day[day(r["run"])].append(r)
    hr = {day(r["run"]): r for r in jl("hand_reading")}
    state_direct_hits = Counter()
    state_fallbacks = Counter()
    for d, r in hr.items():
        if d >= RECORD_END:
            continue
        for s, v in r["values"].items():
            if v in ("예", "아니오"):
                direct = [x for x in res_by_day[d] if x.get("state") == s]
                if direct:
                    state_direct_hits[s] += 1
                else:
                    state_fallbacks[s] += 1
    rc_false = [(r["target"], r["state"], r["then"], r.get("hit"), r.get("late")) for r in jl("reading_check") if r.get("hit") is False or r.get("late")]
    print(f"  [조사 시각 점검] state 직접 매칭 없고 당일 전체 fallback된 일수 합: {sum(state_fallbacks.values())}건 {dict(state_fallbacks)}")
    print(f"  [reading_check 점검] hit=False 또는 late 비어있지 않음: {len(rc_false)}건")
    for x in rc_false:
        print(f"    {x}")

    # 우리 기록에서 주말·현금100%·2기첫날을 제외한 거래일만 볼 때 C1 성적
    acc_ret = {}
    acc_row = {}
    for acc, by in acc_hand.items():
        for d, r in by.items():
            nxt = by.get(d + dt.timedelta(days=1))
            if nxt is not None:
                acc_ret[d] = nxt["value"] / r["value"] - 1
                acc_row[d] = r
    rec_days = sorted(acc_ret)
    reading = defaultdict(dict)
    for r in jl("reading"):
        if r["asset"] in TARGETS:
            reading[day(r["run"])][r["asset"]] = r
    c1_rec = {d: majority([reading[d][a]["values"]["S_vol_high"] for a in TARGETS], "예", 2) for d in rec_days}
    loss_rec = {d: acc_ret[d] <= th1_10 for d in rec_days}
    rec_weekdays = [d for d in rec_days if d.weekday() < 5]
    rec_invested_wd = [d for d in rec_weekdays if d not in (dt.date(2026, 10, 21), dt.date(2026, 10, 22), dt.date(2026, 11, 18))]
    print("\n  [우리 기록 달력/주말/현금100% 효과 점검]")
    t_rec_all = table_strict(rec_days, c1_rec, loss_rec, 1, 1)
    t_rec_wd = table_strict(rec_weekdays, c1_rec, loss_rec, 1, 1, KIDX)
    t_rec_inv = table_strict(rec_invested_wd, c1_rec, loss_rec, 1, 1, KIDX)
    print(fmt_stat("우리 기록 전체 35일(원본)", t_rec_all, shift_p(rec_days, c1_rec, loss_rec, 3)))
    print(fmt_stat("평일(월-금) 25일만(거래일 묶음)", t_rec_wd, shift_p(rec_weekdays, c1_rec, loss_rec, 3)))
    print(fmt_stat("평일 중 초기현금일(10-21·22·11-18) 제외 22일", t_rec_inv, shift_p(rec_invested_wd, c1_rec, loss_rec, 3)))
    print("    평일 25일 독립 손실 구간:", [[d.strftime("%m-%d") for d in ep] for ep in t_rec_wd["eps"]])
    print("    (참고) KRX에 없는 평일:", [d.isoformat() for d in rec_weekdays if d not in KIDX])
    # A1(현금<10%)이 account.jsonl의 당일 BTC 체결 전후로 바뀐 날 있는지 확인
    btc_fills = {fl["run"]: fl for fl in fills if C.role_of(fl.get("account", "")) == "live:hand" and fl.get("asset") == COIN}
    print("    [계좌 A1/A2/A3 당일 BTC 체결 점검] 11-11~11-24 중 당일 BTC 체결일:", sorted(d for d in btc_fills if d >= "2026-11-11"))

    # ------------------------------------------------------------ [R2] 문턱 표본 누출 점검
    print("=" * 100)
    print("[R2] 손실 문턱(-1.13%)의 전체 표본 사용 및 시점 고정/확장 창 민감도")
    # (a) 점별 과거만 쓴 확장 창 문턱 (d 이전 거래일 수익률만으로 하위 10%)
    all_ret_by_idx = [(KRX[i], basket_ret(i, 1)) for i in range(len(KRX)) if KRX[i] <= PRE_END and basket_ret(i, 1) is not None]
    all_ret_dates = [x[0] for x in all_ret_by_idx]
    all_ret_vals = [x[1] for x in all_ret_by_idx]
    loss1_exp = {}
    loss1_roll250 = {}
    for d in pd1:
        k = bisect.bisect_left(all_ret_dates, d)
        prev_vals = all_ret_vals[:k]
        loss1_exp[d] = r1[d] <= quantile(prev_vals, 0.10)
        loss1_roll250[d] = r1[d] <= quantile(prev_vals[-250:], 0.10)

    # (b) 2026-07~10 폭락 제외 문턱 (2020-08-07 ~ 2026-06-30) 및 평가도 2026-06-30까지
    xs_fit = [basket_ret(i, 1) for i in range(len(KRX)) if KRX[i] <= C.FIT_UNTIL and basket_ret(i, 1) is not None]
    th_fit = quantile(xs_fit, 0.10)
    pd1_fit = [d for d in pd1 if d <= C.FIT_UNTIL]
    loss1_fit_on_fit = {d: r1[d] <= th_fit for d in pd1_fit}
    loss1_orig_on_fit = {d: r1[d] <= th1_10 for d in pd1_fit}

    # (c) 전반부(2020-08~2023-12) 문턱으로 후반부(2024~2026-10) 평가
    xs_first = [basket_ret(i, 1) for i in range(len(KRX)) if KRX[i] <= dt.date(2023, 12, 31) and basket_ret(i, 1) is not None]
    th_first = quantile(xs_first, 0.10)
    pd1_second = [d for d in pd1 if d >= dt.date(2024, 1, 1)]
    loss1_oos = {d: r1[d] <= th_first for d in pd1_second}
    pd1_second_nofit = [d for d in pd1 if dt.date(2024, 1, 1) <= d <= C.FIT_UNTIL]
    loss1_oos_nofit = {d: r1[d] <= th_first for d in pd1_second_nofit}

    print(f"  문턱 비교: 전체(~26-10-20) {pct(th1_10)} | 26-06-30까지 {pct(th_fit)} | 2023말까지 {pct(th_first)}")
    print(fmt_stat("원본(전체 문턱 -1.13%)", table_strict(pd1, c1, loss1, 1, 1, KIDX), shift_p(pd1, c1, loss1)))
    print(fmt_stat("확장창 문턱(d 전까지만, 누출0)", table_strict(pd1, c1, loss1_exp, 1, 1, KIDX), shift_p(pd1, c1, loss1_exp)))
    print(fmt_stat("최근250일 이동 문턱(국면보정)", table_strict(pd1, c1, loss1_roll250, 1, 1, KIDX), shift_p(pd1, c1, loss1_roll250)))
    print(fmt_stat("26-07~10 제외(~26-06-30, -1.13%)", table_strict(pd1_fit, c1, loss1_orig_on_fit, 1, 1, KIDX), shift_p(pd1_fit, c1, loss1_orig_on_fit)))
    print(fmt_stat("26-07~10 제외(문턱도 ~26-06-30)", table_strict(pd1_fit, c1, loss1_fit_on_fit, 1, 1, KIDX), shift_p(pd1_fit, c1, loss1_fit_on_fit)))
    print(fmt_stat("표본밖(2024~26-10, ~23문턱)", table_strict(pd1_second, c1, loss1_oos, 1, 1, KIDX), shift_p(pd1_second, c1, loss1_oos)))
    print(fmt_stat("표본밖(2024~26-06, ~23문턱)", table_strict(pd1_second_nofit, c1, loss1_oos_nofit, 1, 1, KIDX), shift_p(pd1_second_nofit, c1, loss1_oos_nofit)))

    # ------------------------------------------------------------ [R3] 반례 직접 탐색
    print("=" * 100)
    print("[R3] 반례 탐색 — 켜졌는데 조용/상승한 긴 구간 & 꺼졌는데 크게 잃은 구간")
    # (1) 켜진 상태 연속 무손실(큰 손실 0일) 최장 스트릭
    streaks = []
    cur = []
    for d in pd1:
        if c1[d] == "예" and not loss1[d]:
            cur.append(d)
        else:
            if cur:
                streaks.append(cur)
                cur = []
    if cur:
        streaks.append(cur)
    streaks.sort(key=len, reverse=True)
    print("  [반례 A] C1 켜짐 상태 연속 큰손실 0일 최장 구간 TOP 8:")
    for st in streaks[:8]:
        cum = math.prod(1 + r1[d] for d in st) - 1
        ng = sum(gain1[d] for d in st)
        mn = min(r1[d] for d in st)
        print(f"    {st[0]} ~ {st[-1]} ({len(st):2d}거래일) 큰손실 0일, 큰이익 {ng}일, 누적 {pct(cum)}, 최저일 {pct(mn)}")

    # (2) 켜진 구간(29개) 중 수익률 합이 큰 상승 구간 및 2023년 전체
    t1_c1 = table_strict(pd1, c1, loss1, 1, 1, KIDX)
    on_summary = []
    for rn in t1_c1["on_run_list"]:
        nl = sum(loss1[d] for d in rn)
        ng = sum(gain1[d] for d in rn)
        cum = math.prod(1 + r1[d] for d in rn) - 1
        on_summary.append((rn[0], rn[-1], len(rn), nl, ng, nl / len(rn), cum))
    print("  [반례 B] C1 켜진 구간 중 10거래일 이상이면서 큰손실 비율 < 기저율(9.4%)인 구간:")
    for s0, s1, ln, nl, ng, lr, cum in sorted(on_summary, key=lambda x: (x[5], -x[2])):
        if ln >= 10 and lr < 0.094:
            print(f"    {s0} ~ {s1} ({ln:2d}거래일) 큰손실 {nl:2d}일({lr:.1%}), 큰이익 {ng:2d}일, 복리누적 {pct(cum)}")

    # (3) 꺼진 구간(30개) 중 큰손실이 몰린 구간
    off_summary = []
    for rn in t1_c1["off_run_list"]:
        nl = sum(loss1[d] for d in rn)
        ng = sum(gain1[d] for d in rn)
        cum = math.prod(1 + r1[d] for d in rn) - 1
        worst_d = min(rn, key=lambda d: r1[d])
        off_summary.append((rn[0], rn[-1], len(rn), nl, ng, nl / len(rn), cum, worst_d, r1[worst_d]))
    print("  [반례 C] C1 꺼진 구간 중 큰손실이 많거나 비율이 높았던 TOP 10:")
    for s0, s1, ln, nl, ng, lr, cum, wd_, wr in sorted(off_summary, key=lambda x: (-x[3], x[6]))[:10]:
        print(f"    {s0} ~ {s1} ({ln:3d}거래일) 큰손실 {nl:2d}일({lr:.1%}), 복리누적 {pct(cum)}, 최악일 {wd_}({pct(wr)})")

    # (4) 꺼진 상태에서 10거래일 창 안에 큰손실 3회 이상 몰린 집중 폭락
    print("  [반례 D] C1 꺼진 날만으로 10거래일 창 안에 큰손실 3일 이상 몰린 구간:")
    off_days_only = [d for d in pd1 if c1[d] == "아니오"]
    off_loss_days = [d for d in pd1 if c1[d] == "아니오" and loss1[d]]
    # Find clusters of OFF loss days within 10 trading days
    seen_cluster = set()
    for i, d in enumerate(off_loss_days):
        win = [x for x in off_loss_days if 0 <= KIDX[x] - KIDX[d] <= 10]
        if len(win) >= 3:
            key = (win[0], win[-1])
            if key not in seen_cluster:
                seen_cluster.add(key)
                # verify all days between win[0] and win[-1] are OFF
                span = [x for x in pd1 if win[0] <= x <= win[-1]]
                all_off = all(c1[x] == "아니오" for x in span)
                cum = math.prod(1 + r1[x] for x in span) - 1
                print(f"    {win[0]} ~ {win[-1]} ({len(span)}거래일, 전부꺼짐={all_off}) 큰손실 {len(win)}일 {[str(x)[5:]+':'+pct(r1[x]) for x in win]}, 구간누적 {pct(cum)}")

    # ------------------------------------------------------------ [R4] 다른 설명 (가)~(라)
    print("=" * 100)
    print("[R4-가] 흔들림 뭉침(대칭 움직임) 대 하방 편향(크게 잃음 전용인가)")
    sym_gain1 = {d: r1[d] >= abs(th1_10) for d in pd1}
    big_abs1 = {d: abs(r1[d]) >= abs(th1_10) for d in pd1}
    t_loss = table_strict(pd1, c1, loss1, 1, 1, KIDX)
    t_gain = table_strict(pd1, c1, gain1, 1, 1, KIDX)
    t_symg = table_strict(pd1, c1, sym_gain1, 1, 1, KIDX)
    t_abs = table_strict(pd1, c1, big_abs1, 1, 1, KIDX)
    print(fmt_stat("크게 잃음 (r <= -1.13%, 하위10%)", t_loss, shift_p(pd1, c1, loss1)))
    print(fmt_stat("크게 벎   (r >= +1.29%, 상위10%)", t_gain, shift_p(pd1, c1, gain1)))
    print(fmt_stat("대칭 이익 (r >= +1.13%)", t_symg, shift_p(pd1, c1, sym_gain1)))
    print(fmt_stat("큰 움직임 (|r| >= 1.13%)", t_abs, shift_p(pd1, c1, big_abs1)))

    # 큰 움직임이 일어난 날 중 '손실 비중' 비교 (하방 편향 검정)
    on_r = [r1[d] for d in pd1 if c1[d] == "예"]
    off_r = [r1[d] for d in pd1 if c1[d] == "아니오"]
    p_asym_10 = fisher_greater(t_loss["a"], t_gain["a"], t_loss["c"], t_gain["c"])
    p_asym_sym = fisher_greater(t_loss["a"], t_symg["a"], t_loss["c"], t_symg["c"])
    neg_on = sum(x < 0 for x in on_r)
    neg_off = sum(x < 0 for x in off_r)
    p_neg = fisher_greater(neg_on, len(on_r) - neg_on, neg_off, len(off_r) - neg_off)
    print(f"  큰 움직임 중 손실 비중(상하위10%): ON {t_loss['a']}/{t_loss['a']+t_gain['a']}={t_loss['a']/(t_loss['a']+t_gain['a']):.3f} "
          f"vs OFF {t_loss['c']}/{t_loss['c']+t_gain['c']}={t_loss['c']/(t_loss['c']+t_gain['c']):.3f} (Fisher p={p_asym_10:.3f})")
    print(f"  큰 움직임 중 손실 비중(±1.13%):   ON {t_loss['a']}/{t_loss['a']+t_symg['a']}={t_loss['a']/(t_loss['a']+t_symg['a']):.3f} "
          f"vs OFF {t_loss['c']}/{t_loss['c']+t_symg['c']}={t_loss['c']/(t_loss['c']+t_symg['c']):.3f} (Fisher p={p_asym_sym:.3f})")
    print(f"  전체 하루 음수 비율:              ON {neg_on}/{len(on_r)}={neg_on/len(on_r):.3f} "
          f"vs OFF {neg_off}/{len(off_r)}={neg_off/len(off_r):.3f} (Fisher p={p_neg:.3f})")
    print(f"  1거래일 수익률 요약:  ON 평균 {pct(fmean(on_r), 3)}, 중앙 {pct(median(on_r), 3)}, 표준편차 {pct(pstdev(on_r), 3)}")
    print(f"                        OFF 평균 {pct(fmean(off_r), 3)}, 중앙 {pct(median(off_r), 3)}, 표준편차 {pct(pstdev(off_r), 3)} "
          f"(표준편차 비 {pstdev(on_r)/pstdev(off_r):.2f}배)")
    # 평균 수익률 차이 돌려 맞추기 p (ON 평균이 OFF 평균보다 낮은 정도)
    on_bool = [c1[d] == "예" for d in pd1]
    r1_vec = [r1[d] for d in pd1]
    obs_sum_r = sum(x for o, x in zip(on_bool, r1_vec) if o)
    ks = range(SHIFT_MIN, len(pd1) - SHIFT_MIN)
    mean_shift_p = sum(sum(r1_vec[j] for j in range(len(pd1)) if on_bool[(j + k) % len(pd1)]) <= obs_sum_r for k in ks) / len(ks)
    print(f"  1거래일 평균 수익률 하방 차이(ON-OFF={pct(fmean(on_r)-fmean(off_r), 3)}): 돌려 맞추기 p={mean_shift_p:.3f}")

    # 변동성 표준화 수익률 r_t / basket_vol20_t (변동성 뭉침을 걷어낸 뒤에도 C1이 하방 꼬리를 남기나)
    bvol20 = {}
    for d in pd1:
        k = bisect.bisect_left(all_ret_dates, d)
        bvol20[d] = pstdev(all_ret_vals[k - 20:k])
    zret = {d: r1[d] / bvol20[d] for d in pd1}
    z_th10 = quantile(list(zret.values()), 0.10)
    z_loss = {d: zret[d] <= z_th10 for d in pd1}
    print(f"  [변동성 보정 검정] r_t / 직전20일 묶음표준편차 <= 하위10%({z_th10:.2f}σ):")
    print(fmt_stat("변동성 보정 뒤 C1 하방 꼬리", table_strict(pd1, c1, z_loss, 1, 1, KIDX), shift_p(pd1, c1, z_loss)))

    # 단순 '어제 흔들림' 기준선 비교: 직전 1일 |r_{t-1}| > 과거 중앙값, 또는 묶음 직전 20일 vol > 250일 vol
    yest_big = {}
    bvol_rel = {}
    for d in pd1:
        k = bisect.bisect_left(all_ret_dates, d)
        prev_abs = [abs(x) for x in all_ret_vals[:k]]
        yest_big[d] = "예" if abs(all_ret_vals[k - 1]) > median(prev_abs) else "아니오"
        bvol_rel[d] = "예" if pstdev(all_ret_vals[k - 20:k]) > pstdev(all_ret_vals[k - 250:k]) else "아니오"
    print(fmt_stat("단순 비교: 어제 |r| > 중앙값", table_strict(pd1, yest_big, loss1, 1, 1, KIDX), shift_p(pd1, yest_big, loss1)))
    print(fmt_stat("단순 비교: 묶음자체 vol20>vol250", table_strict(pd1, bvol_rel, loss1, 1, 1, KIDX), shift_p(pd1, bvol_rel, loss1)))

    # ------------------------------------------------------------ [R4-나] 민감도 점검
    print("=" * 100)
    print("[R4-나] 문턱(5·10·15·20%) · 단기 창(5·10·20·40·60일) · 합산 규칙(1·2·3개) · shift_min 민감도")
    print("  1) 손실 문턱별 (C1 원본):")
    for q, th in ((0.05, th1_05), (0.10, th1_10), (0.15, th1_15), (0.20, th1_20)):
        lq = {d: r1[d] <= th for d in pd1}
        print(fmt_stat(f"하위 {q:4.0%} ({pct(th)})", table_strict(pd1, c1, lq, 1, 1, KIDX), shift_p(pd1, c1, lq)))

    print("  2) 단기 흔들림 창별 (vol_W > vol_250, 셋 중 둘 이상, 문턱 -1.13%):")
    for w in (5, 10, 20, 40, 60):
        cw = {}
        for d in pd1:
            vals = []
            for a in TARGETS:
                cl = closes_at_day[(d, a)]
                vs = pstdev(logrets(cl, w))
                vl = pstdev(logrets(cl, 250))
                vals.append("예" if vs > vl else "아니오")
            cw[d] = majority(vals, "예", 2)
        print(fmt_stat(f"vol_{w:2d} > vol_250 (2/3)", table_strict(pd1, cw, loss1, 1, 1, KIDX), shift_p(pd1, cw, loss1)))

    print("  3) 합산 규칙별 (S_vol_high 켜진 대상 수 >= k, 문턱 -1.13%):")
    cnt_on = {d: sum(pst[d][a][0]["S_vol_high"] == "예" for a in TARGETS) for d in pd1}
    for k_need, nm in ((1, "하나 이상 (>=1)"), (2, "둘 이상 (>=2, 원본)"), (3, "셋 모두 (==3)")):
        ck = {d: ("예" if cnt_on[d] >= k_need else "아니오") for d in pd1}
        print(fmt_stat(nm, table_strict(pd1, ck, loss1, 1, 1, KIDX), shift_p(pd1, ck, loss1)))
    print("  3-보조) 켜진 대상 수(0·1·2·3)별 정확 손실률 (단조 증가 확인):")
    for m in (0, 1, 2, 3):
        ds_m = [d for d in pd1 if cnt_on[d] == m]
        nl = sum(loss1[d] for d in ds_m)
        ng = sum(gain1[d] for d in ds_m)
        print(f"    켜진 대상 {m}개: {len(ds_m):4d}일 | 큰손실 {nl:2d}일({nl/len(ds_m):.3f}) | 큰이익 {ng:2d}일({ng/len(ds_m):.3f}) | 평균수익 {pct(fmean(r1[d] for d in ds_m), 3)}")

    print("  4) shift_p 최소 밀기(min_shift) 민감도 (C1 원본):")
    for ms in (1, 20, 60, 120):
        print(f"    min_shift={ms:3d}: shift_p={shift_p(pd1, c1, loss1, ms):.4f}")

    # ------------------------------------------------------------ [R4-다] 단일 대상 주도 여부
    print("=" * 100)
    print("[R4-다] 세 대상 가운데 하나가 결과를 혼자 끄는지 점검")
    ar1 = {a: {d: asset_ret_krx(a, KIDX[d], 1) for d in pd1} for a in TARGETS}
    print("  1) 대상별 1거래일(시가→종가) 표준편차 및 묶음 큰손실 119일 기여도:")
    big_loss_days = [d for d in pd1 if loss1[d]]
    tot_loss_sum = sum(r1[d] for d in big_loss_days)
    for a in TARGETS:
        sd_all = pstdev(ar1[a][d] for d in pd1)
        sd_fit = pstdev(ar1[a][d] for d in pd1 if d <= C.FIT_UNTIL)
        sd_26r = pstdev(ar1[a][d] for d in pd1 if d > C.FIT_UNTIL)
        avg_on_loss = fmean(ar1[a][d] for d in big_loss_days)
        share = sum(ar1[a][d] / 3 for d in big_loss_days) / tot_loss_sum
        print(f"    {a:8s}: 전체SD {pct(sd_all)} (~26-06 {pct(sd_fit)}, 26-07~10 {pct(sd_26r)}) | 묶음큰손실일 평균 {pct(avg_on_loss)} (손실기여 {share:.1%})")

    print("  2) 단일 대상 S_vol_high / 두 대상 조합 → 묶음 1거래일 큰손실(-1.13%):")
    for a in TARGETS:
        ca = {d: pst[d][a][0]["S_vol_high"] for d in pd1}
        print(fmt_stat(f"단일 조건: {a}", table_strict(pd1, ca, loss1, 1, 1, KIDX), shift_p(pd1, ca, loss1)))
    for a, b in (("069500", "360750"), ("069500", "KRW-BTC"), ("360750", "KRW-BTC")):
        cab_and = {d: ("예" if pst[d][a][0]["S_vol_high"] == "예" and pst[d][b][0]["S_vol_high"] == "예" else "아니오") for d in pd1}
        cab_or = {d: ("예" if pst[d][a][0]["S_vol_high"] == "예" or pst[d][b][0]["S_vol_high"] == "예" else "아니오") for d in pd1}
        print(fmt_stat(f"2대상 AND: {a}+{b}", table_strict(pd1, cab_and, loss1, 1, 1, KIDX), shift_p(pd1, cab_and, loss1)))
        print(fmt_stat(f"2대상 OR : {a}+{b}", table_strict(pd1, cab_or, loss1, 1, 1, KIDX), shift_p(pd1, cab_or, loss1)))

    print("  3) 결과 묶음에서 한 대상을 뺀 2대상 동일비중 묶음(자체 하위10%)에 C1 대입:")
    for drop_a in TARGETS:
        kept = [a for a in TARGETS if a != drop_a]
        sub_all = [fmean(BY_DATE[a][KRX[i]].close / BY_DATE[a][KRX[i]].open - 1 for a in kept)
                   for i in range(len(KRX)) if KRX[i] <= PRE_END and all(KRX[i] in BY_DATE[a] for a in kept)]
        th_sub = quantile(sub_all, 0.10)
        sub_r = {d: fmean(ar1[a][d] for a in kept) for d in pd1}
        sub_l = {d: sub_r[d] <= th_sub for d in pd1}
        print(fmt_stat(f"결과에서 {drop_a} 제외(문턱 {pct(th_sub)})", table_strict(pd1, c1, sub_l, 1, 1, KIDX), shift_p(pd1, c1, sub_l)))
    print("  4) 동일 1,266거래일(2021-08-12~2026-10-20) 내 자기 S_vol_high → 자기 1거래일 하위10%:")
    for a in TARGETS:
        th_a = quantile([ar1[a][d] for d in pd1], 0.10)
        ca = {d: pst[d][a][0]["S_vol_high"] for d in pd1}
        la = {d: ar1[a][d] <= th_a for d in pd1}
        print(fmt_stat(f"동일기간 {a} 자체(문턱 {pct(th_a)})", table_strict(pd1, ca, la, 1, 1, KIDX), shift_p(pd1, ca, la)))

    # ------------------------------------------------------------ [R4-라] 다중 비교 보정
    print("=" * 100)
    print("[R4-라] 다중 비교 보정 (Bonferroni / Šidák / 동시 돌려맞추기 max-stat)")
    # 시세 후보 C1~C6 + 누락된 2개 상태(S_trend_60, S_above_ma200)
    PRICE_ALL = [
        ("C1_vol_rel", "S_vol_high", "예"),
        ("C2_drop5", "S_drop_5", "예"),
        ("C3_down20", "S_trend_20", "아니오"),
        ("C4_bigmove", "S_big_move", "예"),
        ("C5_drawdown", "S_deep_drawdown", "예"),
    ]
    pc = {cid: {d: majority([pst[d][a][0][s] for a in TARGETS], want, 2) for d in pd1} for cid, s, want in PRICE_ALL}
    # C6 재계산
    krx_vol = []
    for d in KRX:
        if d < dt.date(2018, 1, 1):
            continue
        at = dt.datetime.combine(d, PAST_JUDGE, KST)
        st_ = {a: pieces.read(known_closes(a, at)[-pieces.NEED:]) for a in TARGETS}
        if all("vol20" in st_[a][1] for a in TARGETS):
            krx_vol.append((d, fmean(st_[a][1]["vol20"] for a in TARGETS)))
    vdates = [x[0] for x in krx_vol]
    c6 = {}
    for d in pd1:
        k = bisect.bisect_left(vdates, d)
        prev = [x[1] for x in krx_vol[:k]]
        vm = fmean(pst[d][a][1]["vol20"] for a in TARGETS)
        c6[d] = "모름" if len(prev) < 250 else ("예" if vm > median(prev) else "아니오")
    pc["C6_vol_abs"] = c6

    # Bonferroni / Sidak for p=0.016
    p0 = shift_p(pd1, c1, loss1)
    for m, desc in ((6, "과거 시험 시세 후보 6개 (1거래일)"),
                    (18, "전체 후보 18개 (또는 시세6×기한3)"),
                    (54, "시세6 × 기한3 × 문턱3")):
        bonf = min(1.0, p0 * m)
        sidak = 1 - (1 - p0) ** m
        print(f"  p0={p0:.4f}, m={m:2d} ({desc}): Bonferroni={bonf:.3f}, Šidák={sidak:.3f}")

    # 동시 돌려맞추기 (Westfall-Young min-p / max-z across C1..C5 on 1266d, and C1..C6 on 1016d)
    for label, ds_sub, cids in (
        ("C1~C5 동시 (1,266일)", pd1, ["C1_vol_rel", "C2_drop5", "C3_down20", "C4_bigmove", "C5_drawdown"]),
        ("C1~C6 동시 (1,016일, C6 유효구간)", [d for d in pd1 if c6[d] in ("예", "아니오")],
         ["C1_vol_rel", "C2_drop5", "C3_down20", "C4_bigmove", "C5_drawdown", "C6_vol_abs"]),
    ):
        n_s = len(ds_sub)
        ev_s = [loss1[d] for d in ds_sub]
        ev_m = _bits(ev_s)
        tot_ev = ev_m.bit_count()
        masks = {cid: _bits([pc[cid][d] == "예" for d in ds_sub]) for cid in cids}
        ks_s = list(range(SHIFT_MIN, n_s - SHIFT_MIN))
        # Standardized z-score for excess co-occurrence under fixed marginals
        def zscore(on_m: int, shift_k: int = 0) -> float:
            m_on = on_m.bit_count()
            if m_on == 0 or m_on == n_s:
                return -999.0
            rot = _rot_right(on_m, shift_k, n_s) if shift_k else on_m
            obs = (rot & ev_m).bit_count()
            exp = m_on * tot_ev / n_s
            var = m_on * (n_s - m_on) * tot_ev * (n_s - tot_ev) / (n_s * n_s * (n_s - 1))
            return (obs - exp) / math.sqrt(var) if var > 0 else 0.0

        obs_z = {cid: zscore(masks[cid], 0) for cid in cids}
        max_z_per_k = [max(zscore(masks[cid], k) for cid in cids) for k in ks_s]
        c1_adj_p = sum(mz >= obs_z["C1_vol_rel"] for mz in max_z_per_k) / len(ks_s)
        best_cid = max(cids, key=lambda c: obs_z[c])
        best_adj_p = sum(mz >= obs_z[best_cid] for mz in max_z_per_k) / len(ks_s)
        print(f"  [동시 돌려맞추기 max-z] {label}: C1 z={obs_z['C1_vol_rel']:.2f} → 보정 p={c1_adj_p:.3f} | "
              f"최고({best_cid} z={obs_z[best_cid]:.2f}) → 보정 p={best_adj_p:.3f}")

    # 빠진 기존 시세 상태 2개(S_trend_60, S_above_ma200) 점검
    print("\n  [빠진 기존 시세 상태 2개 점검 — market/pieces.py STATES에 있으나 논문 후보에서 제외됨]")
    for s, want, desc in (("S_trend_60", "아니오", "60봉 수익률 <= 0 (2/3)"),
                          ("S_above_ma200", "아니오", "200봉 이동평균 아래 (2/3)")):
        cs = {d: majority([pst[d][a][0][s] for a in TARGETS], want, 2) for d in pd1}
        print(fmt_stat(desc, table_strict(pd1, cs, loss1, 1, 1, KIDX), shift_p(pd1, cs, loss1)))

    # ------------------------------------------------------------ [R5] 판정 계획 검정력 분석
    print("=" * 100)
    print("[R5] 판정 계획 실행 가능성·검정력 분석 (과거 1,266일 내 모든 시작점에서 7절 조건 충족 시점)")
    # 7절 판정 조건: 켜진 구간>=5, 꺼진 구간>=5, 잃은 구간>=15가 처음 충족되는 창 (O(N^2) 점진 집계)
    waits = []
    verdicts = Counter()
    on_arr = [c1[d] == "예" for d in pd1]
    ls_arr = [loss1[d] for d in pd1]
    for start in range(len(pd1) - 100):
        on_r = 1 if on_arr[start] else 0
        off_r = 0 if on_arr[start] else 1
        ep_c = 1 if ls_arr[start] else 0
        found_end = None
        for end in range(start + 1, len(pd1)):
            if on_arr[end] != on_arr[end - 1]:
                if on_arr[end]:
                    on_r += 1
                else:
                    off_r += 1
            if ls_arr[end] and not ls_arr[end - 1]:
                ep_c += 1
            if on_r >= 5 and off_r >= 5 and ep_c >= 15:
                found_end = end
                break
        if found_end is None:
            continue
        sub = pd1[start:found_end + 1]
        waits.append(len(sub))
        t = table_strict(sub, c1, loss1, 1, 1, KIDX)
        share_on = (t["a"] + t["b"]) / t["n"]
        share_expl = t["explained"] / t["episodes"]
        sp20 = shift_p(sub, c1, loss1, min_shift=20)
        l05 = {d: r1[d] <= th1_05 for d in sub}
        l20 = {d: r1[d] <= th1_20 for d in sub}
        t05 = table_strict(sub, c1, l05, 1, 1, KIDX)
        t20 = table_strict(sub, c1, l20, 1, 1, KIDX)
        alt_both_fail = (t05["p_on"] <= t05["p_off"]) and (t20["p_on"] <= t20["p_off"])
        if t["p_on"] <= t["p_off"] or alt_both_fail:
            verdicts["버림"] += 1
        elif t["p_on"] > t["p_off"] and share_expl > share_on and sp20 is not None and sp20 < 0.10:
            verdicts["가설로 남김"] += 1
        else:
            verdicts["모름(10구간 대기)"] += 1
    n_win = len(waits)
    print(f"  과거 시세에서 7절 조건(켜짐5·꺼짐5·손실15)이 처음 차는 시작점 {n_win}개:")
    print(f"    소요 거래일: 최소 {min(waits)}일, 중앙 {median(waits):.0f}일({median(waits)/244:.2f}년), 최대 {max(waits)}일({max(waits)/244:.2f}년)")
    print(f"    그 시점 판정 분포: " + ", ".join(f"{k} {v}회({v/n_win:.1%})" for k, v in verdicts.most_common()))

    # 225거래일 고정 창에서의 이론적 표준오차 (p_on=0.116, p_off=0.082, 켜짐비율 0.354)
    p_on_t, p_off_t = t_loss["p_on"], t_loss["p_off"]
    n_on_11m, n_off_11m = 225 * 0.354, 225 * 0.646
    se_diff = math.sqrt(p_on_t * (1 - p_on_t) / n_on_11m + p_off_t * (1 - p_off_t) / n_off_11m)
    print(f"  이론 검정력(11개월≈225거래일, 독립 가정): 효과크기 {p_on_t-p_off_t:+.3f}, 표준오차 {se_diff:.3f} → 신호대잡음비 {(p_on_t-p_off_t)/se_diff:.2f}σ")
    # 연속 결과(실제 |r_t| 크기)로 검정할 때의 신호대잡음비 비교 (더 빨리 가르는 대안)
    abs_on = [abs(r1[d]) for d in pd1 if c1[d] == "예"]
    abs_off = [abs(r1[d]) for d in pd1 if c1[d] == "아니오"]
    sd_abs_on, sd_abs_off = pstdev(abs_on), pstdev(abs_off)
    se_abs_60 = math.sqrt(sd_abs_on**2 / (60 * 0.354) + sd_abs_off**2 / (60 * 0.646))
    se_abs_225 = math.sqrt(sd_abs_on**2 / n_on_11m + sd_abs_off**2 / n_off_11m)
    print(f"  연속 결과(|r_t| 평균) 비교: ON {pct(fmean(abs_on), 3)} vs OFF {pct(fmean(abs_off), 3)} "
          f"(차이 {pct(fmean(abs_on)-fmean(abs_off), 3)}) → 60거래일(3개월) {(fmean(abs_on)-fmean(abs_off))/se_abs_60:.2f}σ, "
          f"225거래일 {(fmean(abs_on)-fmean(abs_off))/se_abs_225:.2f}σ")

    # ------------------------------------------------------------ [R6] 행동으로 옮겼을 때의 결과 (비중 축소 시뮬레이션)
    print("=" * 100)
    print("[R6] 행동 시뮬레이션 — C1 켜짐 때 비중을 줄였다면 실제로 나았나 (08:40 판단 → 09:00 시가 체결)")
    # (A) 실제 체결 기준 open(d) -> open(d+1) 묶음 수익률 (간밤 포함, 일별 동일비중 리밸런싱 기준, 수수료 ETF 0.015%·BTC 0.05%)
    avg_fee = fmean(C.ASSETS[a].fee for a in TARGETS)  # 0.0002667

    def open_to_open(i: int) -> float | None:
        if i + 1 >= len(KRX):
            return None
        d, nxt = KRX[i], KRX[i + 1]
        if d not in BY_DATE[COIN] or nxt not in BY_DATE[COIN]:
            return None
        rs = [BY_DATE[a][nxt].open / BY_DATE[a][d].open - 1 for a in TARGETS]
        return fmean(rs)

    o2o = {d: open_to_open(KIDX[d]) for d in pd1}
    pd_sim_all = [d for d in pd1 if o2o[d] is not None]
    pd_sim_fit = [d for d in pd_sim_all if d <= C.FIT_UNTIL]

    def sim_strategy(ds: list[dt.date], ret_map: dict[dt.date, float], on_weight: float,
                     off_weight: float = 1.0, use_fee: bool = True) -> dict:
        eq = 1.0
        peak = 1.0
        mdd = 0.0
        w_prev = off_weight
        switches = 0
        total_fee = 0.0
        daily = []
        for d in ds:
            w = on_weight if c1[d] == "예" else off_weight
            dw = abs(w - w_prev)
            if dw > 0:
                switches += 1
            fee = dw * avg_fee if use_fee else 0.0
            total_fee += fee
            r = w * ret_map[d] - fee
            eq *= (1 + r)
            if eq > peak:
                peak = eq
            dd = eq / peak - 1
            if dd < mdd:
                mdd = dd
            daily.append(r)
            w_prev = w
        yrs = (ds[-1] - ds[0]).days / 365.25
        cagr = eq ** (1 / yrs) - 1 if yrs > 0 else 0.0
        ann_vol = pstdev(daily) * math.sqrt(len(ds) / yrs) if yrs > 0 else 0.0
        sharpe = cagr / ann_vol if ann_vol > 0 else 0.0
        big_l = sum(x <= th1_10 for x in daily)
        return {"tot": eq - 1, "cagr": cagr, "mdd": mdd, "vol": ann_vol, "sharpe": sharpe,
                "sw": switches, "fee": total_fee, "big_l": big_l, "n": len(ds)}

    for label, ds, rmap in (
        ("과거 전체 5.2년 (open→next open, 실제 보유·간밤 포함)", pd_sim_all, o2o),
        ("26-07~10 제외 4.9년 (~26-06-30, open→next open)", pd_sim_fit, o2o),
        ("과거 전체 5.2년 (open→close, 논문과 같은 장중 창)", pd1, r1),
        ("26-07~10 제외 4.9년 (~26-06-30, open→close)", pd1_fit, r1),
    ):
        print(f"  --- {label} (n={len(ds)}) ---")
        # ON/OFF 구간별 평균·누적 비교
        on_v = [rmap[d] for d in ds if c1[d] == "예"]
        off_v = [rmap[d] for d in ds if c1[d] == "아니오"]
        f_on = len(on_v) / len(ds)
        print(f"    참고 일별평균: ON {pct(fmean(on_v), 3)}(n={len(on_v)}, 단순합 {pct(sum(on_v), 1)}) "
              f"vs OFF {pct(fmean(off_v), 3)}(n={len(off_v)}, 단순합 {pct(sum(off_v), 1)})")
        for nm, w_on, w_off in (
            ("늘 100% 보유(기준)", 1.0, 1.0),
            ("C1 켜지면 50% 축소", 0.5, 1.0),
            (f"비교: 늘 {(1 - 0.5 * f_on):.1%} 고정", 1 - 0.5 * f_on, 1 - 0.5 * f_on),
            ("C1 켜지면 0%(전액 현금)", 0.0, 1.0),
            (f"비교: 늘 {(1 - f_on):.1%} 고정", 1 - f_on, 1 - f_on),
        ):
            s = sim_strategy(ds, rmap, w_on, w_off, use_fee=True)
            print(f"    {nm:24s} 누적 {pct(s['tot'], 1):>7} | 연 {pct(s['cagr'], 1):>6} | 최대낙폭 {pct(s['mdd'], 1):>7} "
                  f"| 연변동 {pct(s['vol'], 1):>6} | 수익/변동 {s['sharpe']:+.2f} | 큰손실일 {s['big_l']:3d} | 전환 {s['sw']:2d}회(수수료 {pct(s['fee'], 2)})")

    # (B) 우리 기록 35일 (손 계좌 하루 수익률 기준)
    print("  --- 우리 기록 35일 (2026-10-21 ~ 11-24, 손 계좌 하루 수익률 기준; C1은 11-07 1회 전환) ---")
    for nm, w_on in (("손 계좌 그대로", 1.0), ("C1 켜지면 50% 축소", 0.5), ("C1 켜지면 0%(전액 현금)", 0.0)):
        eq_v = peak = 1.0
        mdd = 0.0
        w_prev = 1.0
        big_l = 0
        for d in rec_days:
            w = w_on if c1_rec[d] == "예" else 1.0
            fee = abs(w - w_prev) * avg_fee
            r = w * acc_ret[d] - fee
            eq_v *= (1 + r)
            peak = max(peak, eq_v)
            mdd = min(mdd, eq_v / peak - 1)
            if r <= th1_10:
                big_l += 1
            w_prev = w
        print(f"    {nm:24s} 35일 누적 {pct(eq_v - 1, 2):>7} | 최대낙폭 {pct(mdd, 2):>7} | 큰손실일 {big_l:2d}일")
    # 참고: 우리 기록 10-21~11-06(OFF) vs 11-07~11-24(ON) 실제 누적
    off_rec_cum = math.prod(1 + acc_ret[d] for d in rec_days if c1_rec[d] == "아니오") - 1
    on_rec_cum = math.prod(1 + acc_ret[d] for d in rec_days if c1_rec[d] == "예") - 1
    print(f"    (참고) 손 계좌 구간별 복리: 꺼짐 17일(10-21~11-06) {pct(off_rec_cum, 2)}, 켜짐 18일(11-07~11-24) {pct(on_rec_cum, 2)}")
    print("끝.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
