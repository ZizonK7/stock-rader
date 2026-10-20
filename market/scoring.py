"""예측 채점과 모아 보기, 기준선 예측.

- 목표 종가 = 기준 종가(판단 때 알려진 마지막 끝난 종가)에서 h봉 뒤의 종가. ETF는 거래일, 코인은 날.
- 오름 = 수익률 > 0 (같으면 오르지 않음으로 센다).
- 방향 적중: 확률 > 0.5면 오름, < 0.5면 내림을 말한 것. 정확히 0.5는 말하지 않은 것(null, 적중 수에서 빠진다).
- 확률 점수(Brier) = (확률 − 결과)². 0이 완벽, 늘 50%면 0.25.
- 크기 적중: |수익률|(%)이 말한 범위 [size_lo, size_hi] 안.
- 기준선 예측: baseline:half(늘 50%), baseline:base_rate(최근 250봉에서 h봉 수익률이 오른 비율, 크기는 같은 창의
  |수익률| 2배 폭 창). 둘 다 판단 때 알려진 시세만 쓴다.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import fmean

from . import fit as F
from .prices import History

BASE_RATE_WINDOW = 250


def score_due(preds: list[dict], hists: dict[str, History], run: dt.date, done: set[str]) -> list[dict]:
    """아직 채점 안 된 예측 중 목표 종가가 알려진 것을 채점한다."""
    out = []
    for p in preds:
        if p["id"] in done:
            continue
        h = hists[p["asset"]]
        i = h.index_of(dt.date.fromisoformat(p["base_date"]))
        if i is None or i + p["horizon"] >= len(h):
            continue
        target = h.rows[i + p["horizon"]]
        ret = target.close / p["base_close"] - 1
        up = ret > 0
        prob = p.get("p_up")
        hit = None if prob is None or prob == 0.5 else ((prob > 0.5) == up)
        size = None
        if p.get("size_lo") is not None:
            size = p["size_lo"] <= abs(ret) * 100 <= p["size_hi"]
        out.append({"id": f"score:{p['id']}", "run": run.isoformat(), "prediction": p["id"],
                    "predictor": p["predictor"], "asset": p["asset"], "horizon": p["horizon"],
                    "target_date": target.date.isoformat(), "target_close": target.close, "ret": round(ret, 6),
                    "up": up, "direction_hit": hit,
                    "brier": round((prob - up) ** 2, 6) if prob is not None else None, "size_hit": size})
        done.add(p["id"])
    return out


def base_rate(hist: History, h: int) -> tuple[float | None, tuple[float, float] | None]:
    """최근 250봉 안에서 끝난 h봉 수익률이 오른 비율과 |수익률|(%)의 2배 폭 창."""
    c = hist.closes
    if len(c) < BASE_RATE_WINDOW + h:
        return None, None
    rets = [c[t + h] / c[t] - 1 for t in range(len(c) - BASE_RATE_WINDOW - h, len(c) - h)]
    w = F.window([abs(r) * 100 for r in rets])
    return sum(r > 0 for r in rets) / len(rets), w


# ---------- 모아 보기 ----------
def summarize(scores: list[dict], key) -> dict:
    groups: dict = defaultdict(list)
    for s in scores:
        groups[key(s)].append(s)
    out = {}
    for k, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        briers = [r["brier"] for r in rows if r["brier"] is not None]
        calls = [r["direction_hit"] for r in rows if r["direction_hit"] is not None]
        sizes = [r["size_hit"] for r in rows if r["size_hit"] is not None]
        out[k] = {"n": len(rows), "brier": round(fmean(briers), 4) if briers else None, "brier_n": len(briers),
                  "calls": len(calls), "hits": sum(calls), "size_n": len(sizes), "size_hits": sum(sizes),
                  "up_rate": round(fmean([r["up"] for r in rows]), 3)}
    return out


def piece_table(preds: list[dict], scores: list[dict], states: list[str]) -> dict:
    """조각별 (상태 × 대상 × 기간): 예/아니오일 때 오른 비율과 |수익률| 가운데 값. 예측 하나(계산기)마다 한 표본."""
    by_pred = {p["id"]: p for p in preds}
    table: dict = {}
    for s in scores:
        p = by_pred.get(s["prediction"])
        if not p or p["predictor"] != "calc":
            continue
        for st in states:
            v = p["readings"].get(st)
            if v not in ("예", "아니오"):
                continue
            cell = table.setdefault((st, s["asset"], s["horizon"]), {"예": [], "아니오": []})
            cell[v].append(s["ret"])
    out = {}
    for k, cell in table.items():
        row = {}
        for v in ("예", "아니오"):
            rets = cell[v]
            row[v] = {"n": len(rets), "up_rate": round(fmean([r > 0 for r in rets]), 3) if rets else None,
                      "abs_med": round(sorted(abs(r) for r in rets)[len(rets) // 2] * 100, 3) if rets else None}
        out[k] = row
    return out


# ---------- 한 번만 세기 (결정 1, 2026-10-21) ----------
# 예측은 날마다 적는다(주말·휴일에도). 그런데 새 일봉이 없는 날의 ETF 예측은 기준 종가가 같아서 같은 결과로 여러 번
# 채점된다. 채점·성적표에서는 같은 (예측기·대상·기간·기준 날짜)를 **처음 말한 것 하나만** 센다.
# 처음 것을 고른 까닭: 고르는 규칙이 결과를 보기 전에 정해지고, 나중 것을 고르면 '더 본 뒤의 말'에 무게가 쏠린다.
def key_of(p: dict) -> tuple:
    return (p["predictor"], p["asset"], p["horizon"], p["base_date"])


def first_only(pairs: list[tuple]) -> list[tuple]:
    """(…, 예측, 채점) 묶음 목록에서 같은 열쇠는 판단 날짜(run)가 가장 이른 예측 하나만 남긴다. 순서는 유지."""
    best: dict[tuple, tuple[str, str]] = {}
    for t in pairs:
        p = t[-2]
        k = key_of(p)
        if k not in best or (p["run"], p["id"]) < best[k]:
            best[k] = (p["run"], p["id"])
    return [t for t in pairs if best[key_of(t[-2])] == (t[-2]["run"], t[-2]["id"])]


def mark_counted(new_scores: list[dict], preds_by_id: dict[str, dict], counted_keys: set[tuple]) -> None:
    """새 채점 줄에 counted 칸을 단다(같은 열쇠 중 판단 날짜가 가장 이른 것만 True). counted_keys에 더한다."""
    order = sorted(new_scores, key=lambda s: (preds_by_id[s["prediction"]]["run"], s["prediction"]))
    for s in order:
        k = key_of(preds_by_id[s["prediction"]])
        s["counted"] = k not in counted_keys
        counted_keys.add(k)


def typical_move(hist: History, h: int) -> float | None:
    """평소 움직임(수익률, 비율): 60봉 하루 변동성 × √h. 시세가 모자라면 None."""
    import math
    c = hist.closes
    if len(c) < 62:
        return None
    rets = [math.log(b / a) for a, b in zip(c[-61:], c[-60:]) if a > 0 and b > 0]
    m = fmean(rets)
    sd = math.sqrt(fmean([(r - m) ** 2 for r in rets]))
    return sd * math.sqrt(h)


def verdict_of(s: dict) -> str:
    """돌아보기에 쓰는 맞음/틀림: 방향이나 크기 중 하나라도 틀리면 '틀림'. 말하지 않은 칸(None)은 빼고 본다."""
    bad = s.get("direction_hit") is False or s.get("size_hit") is False
    return "틀림" if bad else "맞음"
