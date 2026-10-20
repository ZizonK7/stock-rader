"""조사: 볼 곳 목록, 오늘의 조사 질문, 볼 곳 성적표 (조사 범위도 논리집에 — 사용자 제안 2026-10-20).

사용법 (저장소 루트):
  python -m market.research places              볼 곳마다 성적 (records/의 조사 기록에서 계산)
  python -m market.research questions [날짜]     그날 조사 질문 (살아 있는 조사 조각에서 나온다)

- 조사 질문은 논리집이 정한다: 살아 있는 조사 조각(read_by = "조사")의 조건 상태마다 질문 하나(상태의 ask),
  그리고 각도마다 열린 질문 하나("오늘 이 각도에서 판단을 바꿀 새 일이 있나" — 빠진 조각을 찾으려고).
- 볼 곳 성적(코드가 기록에서 계산): 다룬 질문 수, 본 횟수, 찾음/없음/늦음/막힘, 새로 시도한 횟수,
  그곳 정보로 한 대 보기를 나중에 다시 봤을 때 맞은 비율(reading_check), 판단 뒤에야 나와 늦었던 횟수(늦음 + reading_check.late).
- 새 곳 시도 몫: 각도마다 하루 한 곳 이상은 목록에 없거나 최근 14일 안 본 곳을 시도한다(쓰던 곳만 보면 그곳이 좋아서인지
  거기만 봐서인지 모른다 — PROVISO '사용 습관과 능력 구분'). 못 하면 경고로 남는다.
"""
from __future__ import annotations

import datetime as dt
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from . import config as C
from . import records as R

ANGLES = ("금리·환율·정책", "한국 시장", "미국 시장", "코인")
NEW_PER_ANGLE = 1        # 각도마다 하루에 새로 시도할 곳 수 (최소)
FRESH_DAYS = 14          # 이만큼 안 본 곳은 다시 봐도 '새 곳'으로 친다
SUGGEST = 3              # 질문마다 추천하는 볼 곳 수 (성적 순)
CHECK_AFTER_DAYS = 7     # 손 대 보기를 이만큼 지난 뒤 다시 본다(reading_check)


# ---------- 볼 곳 ----------
def load_places(path: Path | None = None) -> list[dict]:
    path = path or C.PLACES
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("place", [])


def check_places(places: list[dict], state_ids: set[str] | None = None) -> list[str]:
    errs, seen = [], set()
    for p in places:
        pid = p.get("id", "")
        if not pid.startswith("P_"):
            errs.append(f"[볼 곳] id '{pid}'는 P_로 시작한다")
        if pid in seen:
            errs.append(f"[볼 곳] id 중복 {pid}")
        seen.add(pid)
        for k in ("name", "url", "angle", "added"):
            if not p.get(k):
                errs.append(f"[볼 곳] {pid}: '{k}' 칸이 없음")
        if p.get("angle") and p["angle"] not in ANGLES:
            errs.append(f"[볼 곳] {pid}: angle은 {list(ANGLES)} 중 하나")
        if "official" in p and not isinstance(p["official"], bool):
            errs.append(f"[볼 곳] {pid}: official은 true/false")
        if state_ids is not None:
            for s in p.get("covers", []):
                if s not in state_ids:
                    errs.append(f"[볼 곳] {pid}: covers의 '{s}'는 논리집에 없는 상태")
    return errs


# ---------- 조사 조각 ----------
def research_pieces(raw: dict) -> list[tuple[dict, dict]]:
    """살아 있는 조사 조각과 그 조건 상태 (조각, 상태)."""
    states = {s["id"]: s for s in raw.get("state", [])}
    out = []
    for h in raw.get("hypothesis", []):
        if h.get("read_by") != "조사" or h.get("status", "hypothesis") == "retired":
            continue
        conds = h.get("conditions") or []
        if conds and conds[0] in states:
            out.append((h, states[conds[0]]))
    return out


def research_states(raw: dict) -> list[dict]:
    """살아 있는 조사 조각이 쓰는 상태 (중복 없이, 처음 나온 순서)."""
    seen, out = set(), []
    for _, st in research_pieces(raw):
        if st["id"] not in seen:
            seen.add(st["id"])
            out.append(st)
    return out


# ---------- 성적 ----------
def place_cards(folder: Path, places: list[dict]) -> dict[str, dict]:
    """볼 곳 id → 성적. 목록에 없는 id도 기록에 있으면 나온다(listed = False)."""
    names = {p["id"]: p for p in places}
    cards: dict[str, dict] = defaultdict(lambda: {"questions": set(), "looks": 0, "찾음": 0, "없음": 0, "늦음": 0,
                                                  "막힘": 0, "new": 0, "checked": 0, "hits": 0, "late_after": 0,
                                                  "last": ""})
    for r in R.read(folder, "research"):
        for lk in r["looked"]:
            c = cards[lk["place"]]
            c["questions"].add(r["question"])
            c["looks"] += 1
            c[lk["result"]] += 1
            c["new"] += bool(lk.get("new"))
            c["last"] = max(c["last"], r["run"])
    readings = {h["id"]: h for h in R.read(folder, "hand_reading")}
    for chk in R.read(folder, "reading_check"):
        h = readings.get(chk["target"])
        for pid in set(h["places"].get(chk["state"], [])) if h else ():
            if chk["hit"] is not None:
                cards[pid]["checked"] += 1
                cards[pid]["hits"] += bool(chk["hit"])
        for pid in chk["late"]:
            cards[pid]["late_after"] += 1
    out = {}
    for pid in sorted(set(cards) | set(names)):
        c = cards[pid]
        out[pid] = {**c, "questions": len(c["questions"]), "listed": pid in names,
                    "name": names.get(pid, {}).get("name", "(목록에 없음)"),
                    "official": bool(names.get(pid, {}).get("official", False)),
                    "angle": names.get(pid, {}).get("angle", "")}
    return out


def usefulness(card: dict) -> tuple:
    """추천 순서: 찾은 비율(라플라스 보정) → 맞은 대 보기 → 덜 늦음. 처음 보는 곳은 가운데쯤(0.5)."""
    found = (card["찾음"] + 1) / (card["looks"] + 2)
    hit = (card["hits"] + 1) / (card["checked"] + 2)
    return (round(found, 3), round(hit, 3), -(card["늦음"] + card["late_after"]))


def table(cards: dict[str, dict]) -> str:
    lines = ["| 볼 곳 | 각도 | 공식 | 질문 | 본 횟수 | 찾음 | 없음 | 늦음(판단 뒤) | 막힘 | 새로 시도 | 대 보기 맞음 | 마지막 |",
             "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|"]
    for pid, c in sorted(cards.items(), key=lambda kv: usefulness(kv[1]), reverse=True):
        hit = f"{c['hits']}/{c['checked']}" if c["checked"] else "–"
        lines.append(f"| {pid} {c['name']} | {c['angle']} | {'예' if c['official'] else ''} | {c['questions']} | "
                     f"{c['looks']} | {c['찾음']} | {c['없음']} | {c['늦음'] + c['late_after']} | {c['막힘']} | "
                     f"{c['new']} | {hit} | {c['last'] or '–'} |")
    return "\n".join(lines)


# ---------- 오늘의 질문 ----------
def questions(day: dt.date, raw: dict, places: list[dict], cards: dict[str, dict]) -> list[dict]:
    """그날 조사 질문: 조사 상태마다 하나 + 각도마다 열린 질문 하나."""
    pieces = research_pieces(raw)
    out = []
    for st in research_states(raw):
        users = [h["id"] for h, s in pieces if s["id"] == st["id"]]
        cand = [p for p in places if st["id"] in p.get("covers", [])]
        cand.sort(key=lambda p: usefulness(cards.get(p["id"], _empty())), reverse=True)
        out.append({"question": f"q:{day.isoformat()}:{st['id']}", "state": st["id"], "angle": st.get("angle", ""),
                    "asked": st.get("ask", st["text"]), "pieces": users,
                    "suggest": [p["id"] for p in cand[:SUGGEST]]})
    for ang in ANGLES:
        cand = [p for p in places if p.get("angle") == ang]
        cand.sort(key=lambda p: usefulness(cards.get(p["id"], _empty())), reverse=True)
        out.append({"question": f"q:{day.isoformat()}:open:{ang}", "state": "", "angle": ang,
                    "asked": f"[열린 질문] 오늘 '{ang}' 쪽에서 앞으로 며칠~4주의 값에 영향을 줄 새 일이 있나? "
                             "지금 조각 목록에 없는 것이면 어떤 조각이 필요한지도 적는다",
                    "pieces": [], "suggest": [p["id"] for p in cand[:SUGGEST]]})
    return out


def _empty() -> dict:
    return {"looks": 0, "찾음": 0, "checked": 0, "hits": 0, "늦음": 0, "late_after": 0}


def stale_places(folder: Path, day: dt.date, places: list[dict]) -> set[str]:
    """목록에 있지만 최근 FRESH_DAYS일 안 본 곳 (다시 봐도 '새 곳'으로 칠 수 있다)."""
    last: dict[str, str] = {}
    for r in R.read(folder, "research"):
        for lk in r["looked"]:
            last[lk["place"]] = max(last.get(lk["place"], ""), r["run"])
    cut = (day - dt.timedelta(days=FRESH_DAYS)).isoformat()
    return {p["id"] for p in places if last.get(p["id"], "") < cut}


def due_checks(folder: Path, day: dt.date) -> list[dict]:
    """다시 볼 손 대 보기: CHECK_AFTER_DAYS일 넘게 지났고, 값이 모름이 아니고, 아직 다시 보지 않은 것."""
    done = {(c["target"], c["state"]) for c in R.read(folder, "reading_check")}
    cut = (day - dt.timedelta(days=CHECK_AFTER_DAYS)).isoformat()
    out = []
    for h in R.read(folder, "hand_reading"):
        if h["run"] > cut:
            continue
        for st, v in h["values"].items():
            if str(v) != "모름" and (h["id"], st) not in done:
                out.append({"target": h["id"], "state": st, "then": str(v), "run": h["run"],
                            "why_then": h["why"].get(st, ""), "places": h["places"].get(st, [])})
    return out


def new_share_warnings(research: list[dict], places: list[dict], stale: set[str]) -> list[str]:
    """각도마다 새 곳 시도가 NEW_PER_ANGLE 이상인가 (목록에 없는 곳, 또는 오래 안 본 곳)."""
    listed = {p["id"] for p in places}
    by_angle: dict[str, int] = defaultdict(int)
    for r in research:
        for lk in r["looked"]:
            if lk.get("new") and (lk["place"] not in listed or lk["place"] in stale):
                by_angle[r["angle"]] += 1
    return [f"새 곳 시도 몫: '{a}' 각도에서 새로 시도한 곳이 {by_angle[a]}곳 (몫 {NEW_PER_ANGLE})"
            for a in ANGLES if by_angle[a] < NEW_PER_ANGLE]


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("places", "questions"):
        print(__doc__)
        return 2
    places = load_places()
    cards = place_cards(C.RECORDS, places)
    if argv[0] == "places":
        print(table(cards))
        return 0
    raw = tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))
    day = dt.date.fromisoformat(argv[1]) if len(argv) > 1 else C.now_kst().date()
    for q in questions(day, raw, places, cards):
        print(f"- [{q['angle']}] {q['question']}: {q['asked']}\n    추천 볼 곳: {', '.join(q['suggest']) or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
