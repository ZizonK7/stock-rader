"""메인의 입력(조사 결과, 손 대 보기, 손 예측, 목표 비중, 돌아보기, 대 보기 다시 보기)을 검사하고 기록 줄로 만든다.

입력 파일(JSON)은 매일 실행의 메인이 쓴다. 모양은 `python -m market.daily prepare`가 만드는 틀
(out/daily/<날짜>/input.json)과 docs/routine-prompt.md "입력 파일"에 있다.

검사하는 것 (오류면 아무것도 쓰지 않는다):
- 조사: 칸·결과 낱말·링크·요약 길이(기사 본문 금지). 목록에 없는 볼 곳은 new = true일 때만. research.FAST_SINCE(2026-11-05)
  판단부터 모든 답에 numbers(숫자 이름_단위 → 값)와 as_of(정보 시각, 시간대 붙은 ISO)가 있어야 하고, as_of는 판단 시각보다
  늦으면 안 된다(판단 뒤 정보). numbers가 비면 경고.
- 숫자 상태(2026-11-04, 상태에 number): 값은 코드가 숫자로 정한다(resolve_numbers). 숫자는 readings[상태].numbers → 그 상태를
  물은 조사 답의 numbers → 그날 다른 조사 답의 numbers 순서로 찾는다. 메인이 적은 값이 숫자로 정한 값과 다르면 거절.
  readings에 없어도 숫자가 있으면 코드가 채운다(이유·볼 곳도). 숫자가 없으면 '모름'(씨앗 상태는 '안 물음').
- 손 대 보기: 살아 있는 조사 조각의 상태마다 값·이유. 값이 모름이 아니면 쓴 볼 곳이 있어야 한다. 상태에 answers가 있으면
  값은 그 안에서만(2026-11-03: 값 기준으로 대 보는 상태는 '일부'를 쓰지 않는다 — 검사가 '일부'를 셀 수 없다).
- 손 예측: 오늘 예측할 수 있는 대상 × 기간마다 하나. 확률 0~1(모름이면 null), 크기 범위 폭 2배 이하, 이유, 확신도.
  쓴 조각은 '조각 id=값'이고 값은 그날 대 본 값과 같아야 한다(조사 조각은 손 대 보기, 계산 조각은 시세 대 보기).
  결론을 바꾼 조각(decisive)은 쓴 조각 안에서.
  info(2026-11-04, 결론을 정한 것: 조각 / 출발점 / 기타)는 decisive가 비면 반드시 적는다 — 손 1봉 45개 중 41개가 조각 없이
  판단 때 정보로 정했는데 그 사실이 기록에 없었다(분석 보고서 F1). decisive가 있으면 비워도 되고 '조각'으로 채운다
  (decisive가 출발점 조각뿐이면 '출발점'). 기록의 예측 줄 layer 칸이 된다.
- 씨앗 조각(seed = true)만 쓰는 조사 상태는 관련 있는 날만 대 본다: 손 대 보기에 없어도 된다(안 물음).
- 목표 비중: 0~1, 합 1 이하, 대상마다 이유.
- 종목(2026-10-29): 손 계좌가 든 종목은 매일 손 예측 셋과 비중을 낸다(팔려면 0). 목표 > 0이면 손 예측 셋이 있고, 1주
  오를 확률 > 0.5이고, 1주 예측에 결론 조각(decisive)이 있어야 한다(이유 없이 사지 않는다). 한 종목 상한
  config.STOCK_MAX_WEIGHT, 종목 수 상한 config.STOCK_MAX_COUNT. 종목마다 대 보는 조사 상태(per_asset)는 stock_readings에
  종목마다 값을 적고, 그 조각을 쓴 손 예측의 값은 그 종목의 값과 같아야 한다. 그 상태에 '예'인 종목이 있는 날은 '아니오'인
  종목(비교군)도 config.STOCK_CONTROL_MIN개 이상 (2026-11-03).
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
from . import research as RS

CONF = ("낮음", "보통", "높음")
INFO = ("조각", "출발점", "기타")   # 손 예측의 결론을 정한 것 (2026-11-04)
MAX_RATIO = 2.0


def load_input(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)


def _piece_index(raw: dict) -> dict[str, dict]:
    """살아 있는 조각 id → {'state': 첫 조건 상태, 'research': bool, 'per_asset': 종목마다 대 보는 상태인가}.
    기준값 조각(조건 없음)은 state None."""
    per_asset = {st["id"] for st in raw.get("state", []) if st.get("per_asset")}
    out = {}
    for h in raw.get("hypothesis", []) + raw.get("rule", []):
        if h.get("status", "hypothesis") == "retired" or not h.get("claims"):
            continue
        conds = h.get("conditions") or []
        out[h["id"]] = {"state": conds[0] if conds else None, "research": h.get("read_by") == "조사",
                        "per_asset": bool(conds) and conds[0] in per_asset,
                        "scope": [c.get("scope", "all") for c in h.get("claims", [])],
                        "start": any(c.get("op") == "start" for c in h.get("claims", [])),
                        "seed": bool(h.get("seed"))}
    return out


def info_of(h: dict, pieces: dict[str, dict]) -> str | None:
    """손 예측의 layer: 적은 info, 없으면 decisive에서(출발점 조각뿐이면 '출발점', 아니면 '조각'). decisive도 없으면 None."""
    info = str(h.get("info") or "").strip()
    if info:
        return info
    dec = [str(d).partition("=")[0] for d in h.get("decisive", [])]
    if not dec:
        return None
    return "출발점" if all((pieces.get(d) or {}).get("start") for d in dec) else "조각"


def _find_number(key: str, own: dict, research: list[dict], st: str) -> tuple[float | None, int | None, list[float]]:
    """숫자 하나 찾기: 대 보기의 numbers → 그 상태를 물은 조사 답 → 다른 조사 답. (값, 나온 조사 답 자리, 조사 답들이 준 값들).
    값은 적힌 그대로(정수는 정수로) 돌려준다 — 기록에 옮겨 적을 때 모양이 바뀌지 않게."""
    if isinstance(own.get(key), (int, float)) and not isinstance(own.get(key), bool):
        return own[key], None, []
    order = [i for i, r in enumerate(research) if r.get("state") == st] + \
            [i for i, r in enumerate(research) if r.get("state") != st]
    got = [(i, research[i]["numbers"][key]) for i in order if isinstance(research[i].get("numbers"), dict)
           and isinstance(research[i]["numbers"].get(key), (int, float)) and not isinstance(research[i]["numbers"][key], bool)]
    if not got:
        return None, None, []
    return got[0][1], got[0][0], [float(v) for _, v in got]


def resolve_numbers(inp: dict, raw: dict, states: list[str], seeds: set[str],
                    stock_states: list[str] = ()) -> tuple[dict, dict, dict, list[str], list[str]]:
    """숫자 상태(상태에 number)의 손 대 보기 값을 코드가 정한다(2026-11-04, 결정 E5 — 대 보기를 객관으로).
    돌려줌: (readings 사본 — 숫자 상태를 채움, stock_readings 사본, numbers — 상태 → {숫자 이름: 값}, 오류, 경고).
    종목마다 대 보는 숫자 상태는 그 종목 대 보기의 numbers에서만 찾는다(조사 답의 숫자는 종목을 가리지 않아서)."""
    specs = RS.numeric_states(raw)
    readings = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (inp.get("readings") or {}).items()}
    stock_readings = {a: ({st: (dict(r) if isinstance(r, dict) else r) for st, r in rs.items()} if isinstance(rs, dict) else rs)
                      for a, rs in (inp.get("stock_readings") or {}).items()}
    research = [r for r in inp.get("research", []) if isinstance(r, dict)]
    numbers, errs, warns = {}, [], []

    def settle(where: str, spec: dict, rd, found: dict, src: int | None):
        value, x = RS.number_value(spec, found)
        given = rd.get("value") if isinstance(rd, dict) else None
        if isinstance(given, str) and not given.strip():
            given = None
        if given is not None and str(given) != value:
            errs.append(f"{where}: 값 '{given}'인데 숫자로 정한 값은 '{value}' ({RS.number_why(spec, found, x)}) — 숫자 상태는 "
                        f"코드가 정한다. 값을 비우거나 숫자(numbers)를 고친다")
        rd = dict(rd) if isinstance(rd, dict) else {}
        rd["value"] = value
        if not str(rd.get("why", "")).strip():
            rd["why"] = RS.number_why(spec, found, x)
        if not rd.get("places") and src is not None:
            rd["places"] = list(dict.fromkeys(lk.get("place") for lk in research[src].get("looked", [])
                                              if isinstance(lk, dict) and lk.get("result") == "찾음" and lk.get("place")))
        return rd

    for st in states:
        spec = specs.get(st)
        if spec is None:
            continue
        rd = readings.get(st)
        own = rd.get("numbers") if isinstance(rd, dict) and isinstance(rd.get("numbers"), dict) else {}
        found, src = {}, None
        for k in RS.number_keys(spec):
            v, i, alls = _find_number(k, own, research, st)
            if v is None:
                continue
            found[k] = v
            src = src if src is not None else i
            if len(set(alls)) > 1:
                warns.append(f"readings.{st}: 조사 답들이 {k}를 다르게 줌 {sorted(set(alls))} — 먼저 찾은 {v:g}를 썼다")
        if rd is None and RS.number_of(spec, found) is None and st in seeds:
            continue  # 숫자 씨앗: 숫자가 없고 메인도 안 적었으면 '안 물음'
        readings[st] = settle(f"readings.{st}", spec, rd, found, src)
        numbers[st] = found
    for st, rd in readings.items():  # 메인이 대 보기에 적은 숫자도 남긴다(숫자 상태가 아니어도)
        if isinstance(rd, dict) and isinstance(rd.get("numbers"), dict) and (rd["numbers"] or st in numbers):
            numbers[st] = {**rd["numbers"], **numbers.get(st, {})}
    numbers = {st: v for st, v in numbers.items() if v}
    for a, rs in stock_readings.items():
        if not isinstance(rs, dict):
            continue
        for st in stock_states:
            spec = specs.get(st)
            rd = rs.get(st)
            if spec is None or rd is None:
                continue
            own = rd.get("numbers") if isinstance(rd, dict) and isinstance(rd.get("numbers"), dict) else {}
            rs[st] = settle(f"stock_readings.{a}.{st}", spec, rd, dict(own), None)
    return readings, stock_readings, numbers, errs, warns


def _reading_errors(where: str, r, *, need_place: bool = True) -> list[str]:
    if not isinstance(r, dict):
        return [f"{where}: 대 보기가 없음 (모르면 value = \"모름\"과 이유)"]
    errs = []
    v = r.get("value")
    if v is None or (isinstance(v, str) and not v.strip()):
        errs.append(f"{where}: value가 비었음 (예/아니오/일부/모름 또는 값)")
    if not str(r.get("why", "")).strip():
        errs.append(f"{where}: why(이유)가 비었음")
    if need_place and str(v) != "모름" and not r.get("places"):
        errs.append(f"{where}: 값을 정했으면 쓴 볼 곳(places)이 있어야 함")
    return errs


def validate(inp: dict, ctx: dict) -> tuple[list[str], list[str]]:
    """ctx: day, raw(논리집), research_states, places(ids), stale(ids), predictable(대상 목록), calc_values(대상 → 상태 값),
    due_reviews(예측 id → 정보), due_checks((대상 id, 상태) → 정보), node_ids(모든 노드 id)."""
    errs, warns = [], []
    day = ctx["day"]
    if inp.get("day") != day:
        errs.append(f"입력의 day '{inp.get('day')}'가 오늘 '{day}'와 다름 — 지난 날짜로 예측하지 않는다")
    pieces = _piece_index(ctx["raw"])
    places = set(ctx["places"])

    # 조사 (2026-11-05 판단부터 모든 답에 numbers·as_of — 오늘 판단용 빠른 정보, 결정 E4)
    fast = str(day) >= RS.FAST_SINCE
    decided = R.as_of_time(str(ctx["at"])) if ctx.get("at") else None
    for i, r in enumerate(inp.get("research", [])):
        line = research_line(day, i, r)
        errs += R.line_errors("research", line, f"research[{i}]")
        for lk in r.get("looked", []):
            if isinstance(lk, dict) and lk.get("place") not in places and not lk.get("new"):
                errs.append(f"research[{i}]: 볼 곳 '{lk.get('place')}'가 places.toml에 없는데 new = false")
        q = r.get("question", "")
        if fast:
            if not isinstance(r.get("numbers"), dict):
                errs.append(f"research[{i}] {q}: numbers(숫자 이름_단위 → 값)가 없음 — {RS.FAST_SINCE}부터 모든 조사 답에 "
                            f"숫자 칸을 둔다(못 찾았으면 빈 {{}}와 answer에 까닭)")
            elif not r["numbers"]:
                warns.append(f"research[{i}] {q}: 숫자 없는 답(numbers가 빔)")
            if not str(r.get("as_of", "")).strip():
                errs.append(f"research[{i}] {q}: as_of(답의 숫자가 가리키는 시각, 예: {day}T07:20+09:00)가 없음")
        when = R.as_of_time(str(r.get("as_of", ""))) if str(r.get("as_of", "")).strip() else None
        if when is not None and decided is not None and when > decided:
            errs.append(f"research[{i}] {q}: as_of {r['as_of']}가 판단 시각 {ctx['at']}보다 늦음 — 판단 뒤 정보는 쓰지 않는다")
    if not inp.get("research"):
        warns.append("조사 기록이 하나도 없음")

    # 손 대 보기 (씨앗 조각만 쓰는 상태는 관련 있는 날만 — 없으면 '안 물음'; 숫자 상태는 코드가 값을 정한다)
    answers = {s["id"]: s["answers"] for s in ctx["raw"].get("state", []) if s.get("answers")}
    seed_states = set(ctx.get("seed_states", []))
    readings, stock_filled, numbers, nerrs, nwarns = resolve_numbers(inp, ctx["raw"], list(ctx["research_states"]),
                                                                     seed_states, list(ctx.get("stock_states", [])))
    errs += nerrs
    warns += nwarns
    for st, nums in numbers.items():
        errs += R.number_errors(nums, f"readings.{st}.numbers")
    for a, rs in stock_filled.items():
        for st, r in (rs.items() if isinstance(rs, dict) else ()):
            if isinstance(r, dict) and "numbers" in r:
                errs += R.number_errors(r["numbers"], f"stock_readings.{a}.{st}.numbers")
    for st in ctx["research_states"]:
        if st in seed_states and st not in readings:
            continue
        errs += _reading_errors(f"readings.{st}", readings.get(st))
        v = (readings.get(st) or {}).get("value") if isinstance(readings.get(st), dict) else None
        if st in answers and v is not None and str(v) not in answers[st]:
            errs.append(f"readings.{st}: 값 '{v}'는 {answers[st]} 중 하나여야 함 — 이 상태는 값 기준으로 대 본다(상태 text·ask)")
    for st in readings:
        if st not in ctx["research_states"]:
            errs.append(f"readings: '{st}'는 살아 있는 조사 조각의 (시장 전체) 상태가 아님"
                        + (" — 종목마다 대 보는 상태는 stock_readings에" if st in ctx.get("stock_states", []) else ""))
    stocks = list(ctx.get("stocks", []))
    stock_states = list(ctx.get("stock_states", []))
    stock_readings = stock_filled
    for a, rs in stock_readings.items():
        if a not in stocks:
            errs.append(f"stock_readings: '{a}'는 오늘 쓸 수 있는 종목이 아님")
            continue
        for st in rs:
            if st not in stock_states:
                errs.append(f"stock_readings.{a}: '{st}'는 종목마다 대 보는 조사 상태가 아님")
        for st in stock_states:
            if st in seed_states and st not in rs:
                continue
            errs += _reading_errors(f"stock_readings.{a}.{st}", rs.get(st))
    # 종목 비교군 (2026-11-03): '예' 종목만 대 보면 '아니오' 쪽이 없어 그 조각이 시험되지 않는다
    for st in stock_states:
        got = [str(((rs or {}).get(st) or {}).get("value")) for a, rs in stock_readings.items() if a in stocks
               and isinstance(rs, dict) and isinstance(rs.get(st), dict)]
        if "예" in got and got.count("아니오") < C.STOCK_CONTROL_MIN:
            errs.append(f"stock_readings: '{st}'가 '예'인 종목이 있는데 '아니오'인 종목이 {got.count('아니오')}개 — 비교군을 "
                        f"{C.STOCK_CONTROL_MIN}개 이상 넣는다(직전 열흘 안 실적을 낸 다른 회사 먼저, 모자라면 틀의 _stock_control 순서대로; "
                        f"손 예측·비중은 없어도 된다). 비교군이 없으면 그 조각은 시험되지 않는다")
    valid = list(ctx["predictable"]) + stocks

    # 손 예측
    seen = set()
    for i, h in enumerate(inp.get("hand", [])):
        w = f"hand[{i}]"
        a, hz = h.get("asset"), h.get("horizon")
        if a not in valid:
            errs.append(f"{w}: 대상 '{a}'는 오늘 예측할 수 있는 대상이 아님 (세 대상 {', '.join(ctx['predictable']) or '없음'}"
                        f"; 종목은 stocks.toml에 있고 시세가 낡지 않은 것 — briefing '종목')")
            continue
        if a in stocks and stock_states and a not in stock_readings:
            errs.append(f"{w}: 종목 {a}의 stock_readings가 없음 (종목마다 대 보는 조사 상태 {', '.join(stock_states)}; 모르면 '모름'과 이유)")
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
            if info["per_asset"]:
                if a not in stocks:
                    errs.append(f"{w}: '{pid}'는 종목마다 대 보는 조각이라 종목에만 쓴다")
                    continue
                today = ((stock_readings.get(a) or {}).get(info["state"]) or {}).get("value")
            elif info["research"]:
                today = readings.get(info["state"], {}).get("value")
            else:
                today = ctx["calc_values"].get(a, {}).get(info["state"])
            if str(today) != val:
                errs.append(f"{w}: '{pid}={val}'인데 오늘 대 본 값은 '{today}' — 쓴 조각의 값은 그날 대 본 값과 같아야 한다")
            if info["research"] and not any(C.in_scope(a, s) for s in info["scope"]):
                warns.append(f"{w}: 조사 조각 '{pid}'의 주장 범위에 {a}가 들지 않음")
        for d in h.get("decisive", []):
            if str(d).partition("=")[0] not in used:
                errs.append(f"{w}: decisive '{d}'는 used에 없음 (빼면 결론이 바뀌는 조각만, 쓴 조각 안에서)")
        info = str(h.get("info") or "").strip()
        if info and info not in INFO:
            errs.append(f"{w}: info는 {list(INFO)} 중 하나 (결론을 정한 것)")
        elif not info and not h.get("decisive"):
            errs.append(f"{w}: 결론 조각(decisive)이 없으면 info(결론을 정한 것: {'/'.join(INFO)})를 적는다 — 판단 때 가격·간밤 값으로 "
                        f"정했으면 '출발점', 그 밖이면 '기타'(모름 0.5도)")
        elif info == "조각" and not h.get("decisive"):  # '조각' = decisive에 적은 조각이 정함 — 없으면 기록의 layer가 거짓이 된다
            errs.append(f"{w}: info '조각'은 결론 조각(decisive)이 있을 때만 — 조각 없이 정했으면 '출발점' 또는 '기타'")
    weights = inp.get("weights") or {}
    held = list(ctx.get("held", []))
    targeted = [a for a, wgt in weights.items() if a in stocks and isinstance(wgt, dict)
                and isinstance(wgt.get("target"), (int, float)) and wgt["target"] > 0]
    for a in list(ctx["predictable"]) + sorted(set(held) | set(targeted)):
        for hz in C.ASSETS[a].horizons:
            if (a, hz) not in seen:
                why = "모르면 p_up = null과 이유" if a in ctx["predictable"] else (
                    "든 종목은 매일 예측한다" if a in held else "사려는 종목은 기간 셋을 다 예측한다")
                errs.append(f"hand: {a}:{hz} 예측이 없음 ({why})")

    # 목표 비중
    total = 0.0
    for a, wgt in weights.items():
        if a not in valid:
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
    # 종목 장치
    for a in held:
        if a not in weights:
            errs.append(f"weights: 든 종목 {a}가 없음 — 든 종목도 매일 비중을 다시 정한다(팔려면 target 0)")
    hand_by = {(h.get("asset"), h.get("horizon")): h for h in inp.get("hand", []) if isinstance(h, dict)}
    for a in targeted:
        t = weights[a]["target"]
        if t > C.STOCK_MAX_WEIGHT + 1e-9:
            errs.append(f"weights.{a}: 종목 하나의 비중 {t} > 상한 {C.STOCK_MAX_WEIGHT} (한 회사 주식은 흔들림이 2~3배)")
        if a not in held and a not in ctx.get("buyable", stocks):
            errs.append(f"weights.{a}: 더 사지 않기로 한 종목(stocks.toml buy = false)")
        wk = hand_by.get((a, C.ASSETS[a].horizons[1]))
        if wk is not None:
            p = wk.get("p_up")
            if not (isinstance(p, (int, float)) and p > 0.5):
                errs.append(f"weights.{a}: 종목을 들려면 1주 오를 확률이 0.5보다 커야 함(지금 {p}) — 모르면 종목 대신 묶음을 든다")
            if not wk.get("decisive"):
                errs.append(f"weights.{a}: 종목을 들려면 1주 예측에 결론 조각(decisive)이 있어야 함 — 조각에 묶인 판단만 채점·쪼개기가 된다")
    if len(targeted) > C.STOCK_MAX_COUNT:
        errs.append(f"weights: 종목 {len(targeted)}개 > 상한 {C.STOCK_MAX_COUNT}개")

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
    line = {"id": f"research:{day}:{i + 1}", "run": day, "kind": "research", "question": str(r.get("question", "")),
            "state": str(r.get("state", "")), "angle": str(r.get("angle", "")), "asked": str(r.get("asked", "")),
            "answer": str(r.get("answer", "")), "looked": list(r.get("looked", [])), "agent": str(r.get("agent", ""))}
    # 2026-11-04 (E6): 숫자와 정보 시각. 빈 as_of는 적지 않는다(2026-11-05 전 흉내 내기의 틀이 빈 칸을 가진다)
    if "numbers" in r:
        line["numbers"] = dict(r["numbers"]) if isinstance(r["numbers"], dict) else r["numbers"]
    if r.get("as_of") not in (None, ""):
        line["as_of"] = r["as_of"]
    return line


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
    readings, stock_readings, numbers, _, _ = resolve_numbers(
        inp, ctx["raw"], list(ctx["research_states"]), set(ctx.get("seed_states", [])), list(ctx.get("stock_states", [])))
    if readings or stock_readings:
        line = {
            "id": f"hand_reading:{day}", "run": day,
            "values": {k: v["value"] for k, v in readings.items()},
            "why": {k: v["why"] for k, v in readings.items()},
            "places": {k: list(v.get("places", [])) for k, v in readings.items()},
            "research": [r["id"] for r in out["research"]]}
        if stock_readings:  # 종목마다 대 본 값 (2026-10-29): 종목 → 상태 → {value, why, places[, numbers]}
            line["by_asset"] = {a: {st: {"value": r["value"], "why": str(r["why"]), "places": list(r.get("places", [])),
                                         **({"numbers": dict(r["numbers"])} if isinstance(r.get("numbers"), dict) else {})}
                                    for st, r in rs.items()} for a, rs in stock_readings.items()}
        if numbers:  # 숫자 상태가 쓴 숫자 (2026-11-04, E6): 상태 → {숫자 이름: 값}
            line["numbers"] = numbers
        out["hand_reading"].append(line)
    hand_values = {k: v["value"] for k, v in readings.items()}
    pieces = _piece_index(ctx["raw"])
    for h in inp.get("hand", []):
        a, hz = h["asset"], h["horizon"]
        out["prediction"].append({
            "id": f"{day}:hand:{a}:{hz}", "run": day, "predictor": "hand", "asset": a, "horizon": hz,
            **base[a], "p_up": h.get("p_up"),
            "p_range": None, "size_lo": h.get("size_lo"), "size_hi": h.get("size_hi"),
            "readings": {**ctx["calc_values"].get(a, {}), **hand_values,
                         **{st: r["value"] for st, r in (stock_readings.get(a) or {}).items()}},
            "used": list(h.get("used", [])), "decisive": list(h.get("decisive", [])),
            "reason": str(h["reason"]), "knowledge": digest, "confidence": h["confidence"],
            "layer": info_of(h, pieces) or "기타"})
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
