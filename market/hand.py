"""메인의 입력(조사 결과, 손 대 보기, 손 예측, 목표 비중, 돌아보기, 대 보기 다시 보기)을 검사하고 기록 줄로 만든다.

입력 파일(JSON)은 매일 실행의 메인이 쓴다. 모양은 `python -m market.daily prepare`가 만드는 틀
(out/daily/<날짜>/input.json)과 docs/routine-prompt.md "입력 파일"에 있다.

검사하는 것 (오류면 아무것도 쓰지 않는다):
- 조사: 칸·결과 낱말·링크·요약 길이(기사 본문 금지). 목록에 없는 볼 곳은 new = true일 때만.
- 손 대 보기: 살아 있는 조사 조각의 상태마다 값·이유. 값이 모름이 아니면 쓴 볼 곳이 있어야 한다.
- 손 예측: 오늘 예측할 수 있는 대상 × 기간마다 하나. 확률 0~1(모름이면 null), 크기 범위 폭 2배 이하, 이유, 확신도.
  쓴 조각은 '조각 id=값'이고 값은 그날 대 본 값과 같아야 한다(조사 조각은 손 대 보기, 계산 조각은 시세 대 보기).
  결론을 바꾼 조각(decisive)은 쓴 조각 안에서.
- 목표 비중: 0~1, 합 1 이하, 대상마다 이유.
- 돌아보기: 기한이 된 손 예측(한 번만 세는 것)마다. 까닭 낱말, 틀렸는데 '맞음' 금지, '잡음'은 코드가 잡음이라 본 것만,
  조건 흐림·이유 틀림·바깥 상황·대 보기 틀림이면 틀린 조각·상태 id.
- 대 보기 다시 보기: 다시 볼 차례인 것만.
빠진 돌아보기·다시 보기, 새 곳 시도 몫 부족은 경고(기록의 run notes에 남는다).
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from . import config as C
from . import records as R

CONF = ("낮음", "보통", "높음")
MAX_RATIO = 2.0


def load_input(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)


def _piece_index(raw: dict) -> dict[str, dict]:
    """살아 있는 조각 id → {'state': 첫 조건 상태, 'research': bool}. 기준값 조각(조건 없음)은 state None."""
    out = {}
    for h in raw.get("hypothesis", []) + raw.get("rule", []):
        if h.get("status", "hypothesis") == "retired" or not h.get("claims"):
            continue
        conds = h.get("conditions") or []
        out[h["id"]] = {"state": conds[0] if conds else None, "research": h.get("read_by") == "조사",
                        "scope": [c.get("scope", "all") for c in h.get("claims", [])]}
    return out


def validate(inp: dict, ctx: dict) -> tuple[list[str], list[str]]:
    """ctx: day, raw(논리집), research_states, places(ids), stale(ids), predictable(대상 목록), calc_values(대상 → 상태 값),
    due_reviews(예측 id → 정보), due_checks((대상 id, 상태) → 정보), node_ids(모든 노드 id)."""
    errs, warns = [], []
    day = ctx["day"]
    if inp.get("day") != day:
        errs.append(f"입력의 day '{inp.get('day')}'가 오늘 '{day}'와 다름 — 지난 날짜로 예측하지 않는다")
    pieces = _piece_index(ctx["raw"])
    places = set(ctx["places"])

    # 조사
    for i, r in enumerate(inp.get("research", [])):
        line = research_line(day, i, r)
        errs += R.line_errors("research", line, f"research[{i}]")
        for lk in r.get("looked", []):
            if isinstance(lk, dict) and lk.get("place") not in places and not lk.get("new"):
                errs.append(f"research[{i}]: 볼 곳 '{lk.get('place')}'가 places.toml에 없는데 new = false")
    if not inp.get("research"):
        warns.append("조사 기록이 하나도 없음")

    # 손 대 보기
    readings = inp.get("readings", {})
    for st in ctx["research_states"]:
        r = readings.get(st)
        if not isinstance(r, dict):
            errs.append(f"readings: 조사 상태 '{st}'의 대 보기가 없음 (모르면 value = \"모름\"과 이유)")
            continue
        v = r.get("value")
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(f"readings.{st}: value가 비었음 (예/아니오/일부/모름 또는 값)")
        if not str(r.get("why", "")).strip():
            errs.append(f"readings.{st}: why(이유)가 비었음")
        if str(v) != "모름" and not r.get("places"):
            errs.append(f"readings.{st}: 값을 정했으면 쓴 볼 곳(places)이 있어야 함")
    for st in readings:
        if st not in ctx["research_states"]:
            errs.append(f"readings: '{st}'는 살아 있는 조사 조각의 상태가 아님")

    # 손 예측
    seen = set()
    for i, h in enumerate(inp.get("hand", [])):
        w = f"hand[{i}]"
        a, hz = h.get("asset"), h.get("horizon")
        if a not in ctx["predictable"]:
            errs.append(f"{w}: 대상 '{a}'는 오늘 예측할 수 있는 대상이 아님 ({', '.join(ctx['predictable']) or '없음'})")
            continue
        if hz not in C.ASSETS[a].horizons:
            errs.append(f"{w}: 기간 {hz}은 {C.ASSETS[a].horizons} 중 하나")
            continue
        if (a, hz) in seen:
            errs.append(f"{w}: {a}:{hz}가 두 번")
        seen.add((a, hz))
        p = h.get("p_up")
        if p is not None and not (isinstance(p, (int, float)) and 0 <= p <= 1):
            errs.append(f"{w}: p_up은 0~1 또는 null(모름)")
        lo, hi = h.get("size_lo"), h.get("size_hi")
        if (lo is None) != (hi is None):
            errs.append(f"{w}: size_lo·size_hi는 둘 다 있거나 둘 다 null")
        elif lo is not None:
            if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo < hi):
                errs.append(f"{w}: 크기 범위는 0 < size_lo < size_hi (%, 움직임 절댓값)")
            elif hi / lo > MAX_RATIO + 1e-9:
                errs.append(f"{w}: 크기 범위 폭 {hi / lo:.2f}배가 상한 {MAX_RATIO:g}배를 넘음 — 넓혀서 맞추지 않는다")
        if not str(h.get("reason", "")).strip():
            errs.append(f"{w}: 이유(reason)가 없음 — 이유 없는 숫자는 안 된다")
        if h.get("confidence") not in CONF:
            errs.append(f"{w}: confidence는 {list(CONF)} 중 하나")
        used = {}
        for u in h.get("used", []):
            pid, _, val = str(u).partition("=")
            info = pieces.get(pid)
            if info is None:
                errs.append(f"{w}: 쓴 조각 '{pid}'는 살아 있는 조각이 아님")
                continue
            used[pid] = val
            if info["state"] is None:
                continue  # 기준값
            today = (readings.get(info["state"], {}).get("value") if info["research"]
                     else ctx["calc_values"].get(a, {}).get(info["state"]))
            if str(today) != val:
                errs.append(f"{w}: '{pid}={val}'인데 오늘 대 본 값은 '{today}' — 쓴 조각의 값은 그날 대 본 값과 같아야 한다")
            if info["research"] and not any(C.in_scope(a, s) for s in info["scope"]):
                warns.append(f"{w}: 조사 조각 '{pid}'의 주장 범위에 {a}가 들지 않음")
        for d in h.get("decisive", []):
            if str(d).partition("=")[0] not in used:
                errs.append(f"{w}: decisive '{d}'는 used에 없음 (빼면 결론이 바뀌는 조각만, 쓴 조각 안에서)")
    for a in ctx["predictable"]:
        for hz in C.ASSETS[a].horizons:
            if (a, hz) not in seen:
                errs.append(f"hand: {a}:{hz} 예측이 없음 (모르면 p_up = null과 이유)")

    # 목표 비중
    total = 0.0
    for a, wgt in (inp.get("weights") or {}).items():
        if a not in ctx["predictable"]:
            errs.append(f"weights: '{a}'는 오늘 주문할 수 있는 대상이 아님")
            continue
        t = wgt.get("target") if isinstance(wgt, dict) else None
        if not (isinstance(t, (int, float)) and 0 <= t <= 1):
            errs.append(f"weights.{a}: target은 0~1 (계좌 평가액 대비 비중)")
            continue
        total += t
        if not str(wgt.get("reason", "")).strip():
            errs.append(f"weights.{a}: 이유(reason)가 없음")
    if total > 1 + 1e-9:
        errs.append(f"weights: 비중 합 {total:.3f} > 1 — 빚을 내서 사지 않는다")

    # 돌아보기
    got = set()
    for i, rv in enumerate(inp.get("reviews", [])):
        w = f"reviews[{i}]"
        pid = rv.get("prediction")
        info = ctx["due_reviews"].get(pid)
        if info is None:
            errs.append(f"{w}: '{pid}'는 돌아볼 차례의 손 예측이 아님")
            continue
        got.add(pid)
        why = rv.get("why")
        if why not in R.WHY:
            errs.append(f"{w}: why는 {list(R.WHY)} 중 하나")
        if info["verdict"] == "틀림" and why == "맞음":
            errs.append(f"{w}: 틀린 예측(방향 {info['direction_hit']}, 크기 {info['size_hit']})에 '맞음'")
        if why == "잡음" and not info["noise"]:
            errs.append(f"{w}: '잡음'은 움직임이 평소의 {C.NOISE_SHARE:g}배보다 작을 때만 (이번 {info['ret_pct']:+.2f}%,"
                        f" 평소 {info['typical_pct']}%)")
        if why in ("조건 흐림", "이유 틀림", "바깥 상황", "대 보기 틀림"):
            wrong = rv.get("wrong") or []
            if not wrong:
                errs.append(f"{w}: '{why}'면 틀린 조각·상태 id(wrong)를 적는다")
            for x in wrong:
                if x not in ctx["node_ids"]:
                    errs.append(f"{w}: wrong의 '{x}'는 논리집에 없는 id")
        if not str(rv.get("reason", "")).strip():
            errs.append(f"{w}: 이유(reason)가 없음 — 맞았어도 이유를 적는다")
    missing = sorted(set(ctx["due_reviews"]) - got)
    if missing:
        warns.append(f"돌아보지 않은 손 예측 {len(missing)}건: {', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}")

    # 대 보기 다시 보기
    gotc = set()
    for i, ck in enumerate(inp.get("reading_checks", [])):
        w = f"reading_checks[{i}]"
        key = (ck.get("target"), ck.get("state"))
        if key not in ctx["due_checks"]:
            errs.append(f"{w}: {key}는 다시 볼 차례가 아님")
            continue
        gotc.add(key)
        if ck.get("hit") not in (True, False, None):
            errs.append(f"{w}: hit는 true/false/null(모름)")
        if not str(ck.get("now", "")).strip() or not str(ck.get("reason", "")).strip():
            errs.append(f"{w}: now(지금 보니 그때 값)와 reason이 있어야 함")
        for pid in ck.get("late", []):
            if pid not in places:
                warns.append(f"{w}: late의 '{pid}'는 places.toml에 없음")
    # 정정: 지난 기록 id와 설명 (지난 줄은 고치지 않고 정정 줄을 덧붙인다)
    for i, c in enumerate(inp.get("corrections", [])):
        if c.get("target") not in ctx.get("record_ids", set()):
            errs.append(f"corrections[{i}]: target '{c.get('target')}'는 있는 기록 id가 아님")
        if not str(c.get("note", "")).strip():
            errs.append(f"corrections[{i}]: note(무엇이 왜 틀렸고 맞는 값은 무엇인지)가 없음")
    if set(ctx["due_checks"]) - gotc:
        warns.append(f"다시 보지 않은 손 대 보기 {len(set(ctx['due_checks']) - gotc)}건")
    return errs, warns


# ---------- 기록 줄 만들기 ----------
def research_line(day: str, i: int, r: dict) -> dict:
    return {"id": f"research:{day}:{i + 1}", "run": day, "kind": "research", "question": str(r.get("question", "")),
            "state": str(r.get("state", "")), "angle": str(r.get("angle", "")), "asked": str(r.get("asked", "")),
            "answer": str(r.get("answer", "")), "looked": list(r.get("looked", [])), "agent": str(r.get("agent", ""))}


def lines(inp: dict, ctx: dict, digest: str, base: dict[str, dict]) -> dict[str, list[dict]]:
    """검사를 통과한 입력 → 기록 종류별 줄. base: 대상 → {base_date, base_close} (판단 때 알려진 마지막 끝난 종가)."""
    day = ctx["day"]
    out: dict[str, list[dict]] = {"research": [], "hand_reading": [], "prediction": [], "review": [], "reading_check": [],
                                  "correction": []}
    for i, c in enumerate(inp.get("corrections", [])):
        out["correction"].append({"id": f"correction:{day}:{i + 1}", "run": day, "target": c["target"],
                                  "note": str(c["note"])})
    for i, r in enumerate(inp.get("research", [])):
        line = research_line(day, i, r)
        line.pop("kind")
        out["research"].append(line)
    readings = inp.get("readings", {})
    if readings:
        out["hand_reading"].append({
            "id": f"hand_reading:{day}", "run": day,
            "values": {k: v["value"] for k, v in readings.items()},
            "why": {k: v["why"] for k, v in readings.items()},
            "places": {k: list(v.get("places", [])) for k, v in readings.items()},
            "research": [r["id"] for r in out["research"]]})
    hand_values = {k: v["value"] for k, v in readings.items()}
    for h in inp.get("hand", []):
        a, hz = h["asset"], h["horizon"]
        out["prediction"].append({
            "id": f"{day}:hand:{a}:{hz}", "run": day, "predictor": "hand", "asset": a, "horizon": hz,
            **base[a], "p_up": h.get("p_up"),
            "p_range": None, "size_lo": h.get("size_lo"), "size_hi": h.get("size_hi"),
            "readings": {**ctx["calc_values"].get(a, {}), **hand_values},
            "used": list(h.get("used", [])), "decisive": list(h.get("decisive", [])),
            "reason": str(h["reason"]), "knowledge": digest, "confidence": h["confidence"]})
    for rv in inp.get("reviews", []):
        out["review"].append({"id": f"review:{rv['prediction']}", "run": day, "prediction": rv["prediction"],
                              "wrong": list(rv.get("wrong", [])), "why": rv["why"], "reason": str(rv["reason"])})
    for ck in inp.get("reading_checks", []):
        info = ctx["due_checks"][(ck["target"], ck["state"])]
        out["reading_check"].append({"id": f"reading_check:{ck['target']}:{ck['state']}", "run": day,
                                     "target": ck["target"], "state": ck["state"], "then": info["then"],
                                     "now": str(ck["now"]), "hit": ck.get("hit"), "late": list(ck.get("late", [])),
                                     "reason": str(ck["reason"])})
    return out
