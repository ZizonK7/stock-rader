"""아침 흐름의 문 (2026-11-17 사용자 결정, 잼민이 지시서 p564 — 지시문 docs/routine-prompt.md 4-0).

사용자: "8시쯤 시작해서 HAIKU 5.5 HIGH가 놓치는 거 없게 조사 종목별로 보내고 물량으로 OPUS 5.5 HIGH한테 내용 보내주고 피드백이랑
다 읽어보고 체결하는 걸로 하자. … 8시 40분에 다시 HAIKU 5.5 MEDIUM 몇 개 보내서 이슈 위주로 찾아보라고 하고 메인이 선택을
바꾸거나 하면 될 듯해. 그렇게 체결을 9시 이전에만 하면 되고, 그 코드가 9시 이후 체결은 막아버리면 되니까."

꼭 할 단계는 검사가 막는다(C.FLOW_SINCE 판단부터):
- `is_late`: 판단(record) 시각이 그날 09:00(C.RECORD_BY) 뒤면 주문·예측을 하나도 넣지 않는다(모든 대상) — record가 '못 넣음' run
  줄(`late_line`)만 남기고 끝난다. 버리지 않고 남기는 까닭: 왜 늦었는지 돌아볼 재료.
- `precheck_errors`: 판단 전에 뿌리 목록(전날까지 피드백 정리 실행이 정리한 것)과 최근 돌아보기 원문을 읽고 판단이 밟은
  뿌리(roots)와 고친 것(note), 읽은 것(read).
- `morning_errors`(p564 보완 1·3 — 사용자 "피드백 정리 … 글쓰기 … 9시 뒤"): 아침 입력에는 돌아보기·다시 보기·뿌리 묶기를
  넣지 않는다(9시 뒤 피드백 정리 실행 `daily feedback` 몫). 대신 글 쓰는 에이전트가 기록만 보고 쓰도록 판단의 요지(story)와
  단계 시각(timeline — 첫 며칠 extra 메인이 09:00에 얼마나 붙는지 보려고)을 남긴다.
- `recheck_errors`: 08:40(C.RECHECK_FROM) 뒤 다시 보기 — 보낸 시각·에이전트(둘 이상)·찾은 이슈·장전 값·바꾼 것. 바꾼 것은
  처음 값(from)과 바꾼 값(to)을 둘 다 남겨 '08:40에 바꾼 것이 나았나'를 나중에 채점할 수 있게 하고, to는 그날 입력의 최종 값과
  같아야 한다(어긋난 기록이 남지 않게).
"""
from __future__ import annotations

import datetime as dt

from . import config as C

RECHECK_MIN_AGENTS = 2
CHANGE_WHAT = ("weight", "p_up")


def active(day: dt.date) -> bool:
    return day.isoformat() >= C.FLOW_SINCE


def is_late(day: dt.date, at: dt.datetime) -> bool:
    """판단 시각이 판단 날의 09:00 뒤인가(그 날부터). 날짜가 넘어갔어도 늦은 것."""
    return active(day) and at.astimezone(C.KST) >= dt.datetime.combine(day, C.RECORD_BY, C.KST)


def late_line(day: dt.date, at: dt.datetime, inp: dict) -> dict:
    """'못 넣음' run 줄: 주문·예측 없이, 무엇을 하려 했는지(목표 비중·손 예측 수)만."""
    run = day.isoformat()
    weights = {a: w.get("target") for a, w in (inp.get("weights") or {}).items() if isinstance(w, dict)}
    hand = [h for h in inp.get("hand", []) if isinstance(h, dict)]
    return {"id": f"late:{run}", "run": run, "mode": "late",
            "steps": {"decided_at": at.isoformat(timespec="seconds"),
                      "rejected": {"weights": weights, "hand": len(hand),
                                   "p_up": {f"{h.get('asset')}:{h.get('horizon')}": h.get("p_up") for h in hand}},
                      **({"recheck": inp["recheck"]} if isinstance(inp.get("recheck"), dict) else {})},
            "notes": [f"못 넣음: 판단 시각 {at.astimezone(C.KST):%H:%M:%S}가 09:00 뒤라 주문·예측을 하나도 넣지 않았다"
                      f"(2026-11-17 사용자 결정 '9시 이후 체결은 막는다'). 지난 날짜로 다시 넣지 않는다"]
                     + [str(n) for n in inp.get("notes", [])]}


def _time(x) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(x))
    except ValueError:
        return None
    return t if t.tzinfo else None


def precheck_errors(inp: dict, day: dt.date, known: set[str]) -> list[str]:
    """입력 precheck = {roots: [밟은 뿌리 id…] 또는 [], note: 고친 것·안 고친 것, read: 판단 전에 읽은 것(뿌리 목록·최근 돌아보기)}."""
    if not active(day):
        return []
    pc = inp.get("precheck")
    if not isinstance(pc, dict):
        return ["precheck: 뿌리 목록과 최근 돌아보기를 읽고 판단이 밟은 뿌리·고친 것·읽은 것을 적는다(지시문 4-0)"]
    errs = []
    roots = pc.get("roots")
    if not isinstance(roots, list) or any(r not in known for r in roots):
        errs.append(f"precheck.roots: 오늘 판단이 밟은 뿌리 id 목록(없으면 []) — 모르는 id "
                    f"{[r for r in roots or [] if r not in known] if isinstance(roots, list) else roots}")
    for k in ("note", "read"):
        if not str(pc.get(k, "")).strip():
            errs.append(f"precheck.{k}가 비었다")
    return errs


STORY_KEYS = ("conclusion", "why", "tomorrow")


def morning_errors(inp: dict, day: dt.date, at: dt.datetime) -> list[str]:
    """아침 입력(2026-11-17 p564 보완 1·3): 돌아보기·다시 보기·뿌리 묶기는 9시 뒤 피드백 정리 실행 몫 — 넣으면 거절.
    story = {conclusion: 오늘의 한 줄 결론, why: 왜 이렇게 샀나·팔았나(조각·사실), tomorrow: 내일 볼 것(숫자 조건)} —
    글 쓰는 에이전트는 판단한 본인이 아니라 기록만 본다. timeline = {started: 실행 시작 시각, …단계: 시각}(started 필수)."""
    if not active(day):
        return []
    errs = []
    for k in ("reviews", "reading_checks", "lessons"):
        if inp.get(k):
            errs.append(f"{k}: 아침 판단에는 넣지 않는다 — 9시 뒤 피드백 정리 실행(python -m market.daily feedback)이 한다")
    st = inp.get("story")
    if not isinstance(st, dict) or any(not str(st.get(k, "")).strip() for k in STORY_KEYS):
        errs.append(f"story: {{{', '.join(STORY_KEYS)}}} — 글 쓰는 에이전트가 기록만 보고 쓴다(판단의 까닭을 기록에 남긴다)")
    tl = inp.get("timeline")
    t = _time((tl or {}).get("started")) if isinstance(tl, dict) else None
    if t is None:
        errs.append("timeline.started: 이 실행을 시작한 시각(시간대 붙은 ISO) — 단계마다 시각을 더 적어도 된다")
    elif t > at:
        errs.append(f"timeline.started {t.isoformat()}가 판단 시각보다 늦다")
    if isinstance(tl, dict):
        bad = [k for k, v in tl.items() if _time(v) is None]
        if bad:
            errs.append(f"timeline: {bad}의 값은 시간대 붙은 ISO 시각")
    return errs


def recheck_errors(inp: dict, day: dt.date, at: dt.datetime) -> list[str]:
    """입력 recheck = {sent_at, agents: [{focus, model, effort, returned}], found: [{what, url?, asset?}], premarket: 본 것,
    changed: [{asset, what: weight|p_up, horizon(p_up만), from, to, why}]}."""
    if not active(day):
        return []
    rc = inp.get("recheck")
    if not isinstance(rc, dict):
        return [f"recheck: {C.RECHECK_FROM:%H:%M} 다시 보기가 없다 — 에이전트 {RECHECK_MIN_AGENTS}개 이상을 보내 이슈를 찾고 적는다(지시문 4-0)"]
    errs = []
    t, lo = _time(rc.get("sent_at")), dt.datetime.combine(day, C.RECHECK_FROM, C.KST)
    if t is None:
        errs.append("recheck.sent_at: 다시 보기 에이전트를 보낸 시각(시간대 붙은 ISO)")
    elif not lo <= t <= at:
        errs.append(f"recheck.sent_at {t.isoformat()}는 {lo:%H:%M}–판단 시각({at.astimezone(C.KST):%H:%M}) 사이여야 한다")
    agents = rc.get("agents")
    if not isinstance(agents, list) or len(agents) < RECHECK_MIN_AGENTS or not all(
            isinstance(a, dict) and str(a.get("focus", "")).strip() and str(a.get("model", "")).strip()
            and isinstance(a.get("returned"), bool) for a in agents):
        errs.append(f"recheck.agents: {RECHECK_MIN_AGENTS}개 이상, 저마다 {{focus, model, effort, returned(참/거짓)}}")
    found = rc.get("found")
    if not isinstance(found, list) or not all(isinstance(f, dict) and str(f.get("what", "")).strip() for f in found):
        errs.append("recheck.found: 찾은 이슈 목록 [{what, url, asset}] (없으면 [])")
    if not str(rc.get("premarket", "")).strip():
        errs.append("recheck.premarket: 장전 값에서 본 것(비었으면 '비어 있음'이라고) — python -m market.premarket")
    changed = rc.get("changed")
    if not isinstance(changed, list):
        return errs + ["recheck.changed: 바꾼 것 목록(없으면 [])"]
    weights = inp.get("weights") or {}
    hand = {(h.get("asset"), h.get("horizon")): h for h in inp.get("hand", []) if isinstance(h, dict)}
    for i, c in enumerate(changed):
        w = f"recheck.changed[{i}]"
        if not isinstance(c, dict) or c.get("what") not in CHANGE_WHAT:
            errs.append(f"{w}: {{asset, what: {'|'.join(CHANGE_WHAT)}, horizon(p_up만), from, to, why}}")
            continue
        a, frm, to = c.get("asset"), c.get("from"), c.get("to")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (frm, to)) or frm == to:
            errs.append(f"{w}: from·to는 서로 다른 수")
            continue
        if not str(c.get("why", "")).strip():
            errs.append(f"{w}: why가 비었다")
        if c["what"] == "weight":
            final = (weights.get(a) or {}).get("target") if isinstance(weights.get(a), dict) else None
        else:
            final = (hand.get((a, c.get("horizon"))) or {}).get("p_up")
        if not isinstance(final, (int, float)) or abs(float(final) - float(to)) > 1e-9:
            errs.append(f"{w}: to {to}가 입력의 최종 값({final})과 다르다 — 바꾼 값을 weights·hand에도 넣는다")
    return errs
