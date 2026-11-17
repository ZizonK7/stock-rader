"""뿌리 목록 — '다음엔'을 같은 뿌리로 묶어 일반화하고, 서로 반대인 것은 가르는 조건으로 쪼갠다 (2026-11-17).

  python -m market.lessons           지금 뿌리 목록(씨앗 lessons.toml + 기록 lesson 줄)과 뿌리마다 묶인 수·해 본 결과

까닭(잼민이 지시서 p562, 사용자 "피드백을 하고 그거에 대해 일반화를 잘 해가지고 만약에 충돌되는 게 생긴다면 그때 논리를 쪼개고"):
돌아보기의 '다음엔'은 조각 숫자를 조금 고치는 좁은 처방이 대부분이고, 매일 실행은 지난 14일의 '다음엔'만 본다 — 조각으로 굳지
않은 교훈은 14일이 지나면 사라졌다. 그래서
- **일요일 주간 결산**(LESSONS_SINCE부터): 입력 `lessons`에 그 주(그날 포함 7일)의 '다음엔'(그대로 제외)을 **빠짐없이** 뿌리에
  나눠 넣는다(record가 거절). 항목 = {root, kind, reviews, tried, (새 뿌리) sentence, (충돌) against·split}.
  kind: 같음(있는 뿌리에 건수를 더함) / 새 뿌리(문장을 세움) / 충돌(있는 뿌리와 반대 말 — 어느 쪽도 버리지 않고 가르는 조건으로
  쪼갠 새 뿌리). tried = 그 묶음 가운데 '지난 다음엔 … → 해 봄'인 돌아보기의 결과 {나아짐, 모름, 나빠짐} — 합이 그 수와 같아야 한다.
- **매일**: briefing에 뿌리 목록(문장·묶인 수·해 본 결과·충돌)이 나온다 — 14일이 지나도 남는다.
- 기록: records/lesson.jsonl(매일 실행만 씀). 씨앗과 대화 세션의 정리는 저장소 루트 lessons.toml(지난 기록은 고치지 않는다).
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import tomllib
from pathlib import Path

from . import config as C
from . import records as R

LESSONS = C.ROOT / "lessons.toml"
LESSONS_SINCE = "2026-11-22"   # 첫 일요일(이 코드가 들어간 뒤) — 그 전 일요일 입력은 거절하지 않는다
KINDS = ("같음", "새 뿌리", "충돌")
TRIED = ("나아짐", "모름", "나빠짐")
ROOT_ID = re.compile(r"^L\d{2,}$")
NEXT_MARK, NEXT_KEEP = "다음엔:", "그대로"


def load(path: Path = LESSONS) -> dict:
    if not path.exists():
        return {"root": [], "conflict": []}
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return {"root": raw.get("root", []), "conflict": raw.get("conflict", [])}


def current(path: Path = LESSONS, records: Path = C.RECORDS) -> list[dict]:
    """뿌리마다 {id, sentence, source, opened, reviews(묶인 '다음엔' 수), tried{나아짐·모름·나빠짐}, splits[충돌 글]}."""
    seed = load(path)
    roots: dict[str, dict] = {}
    for r in seed["root"]:
        roots[r["id"]] = {"id": r["id"], "sentence": r["sentence"], "source": r.get("source", ""),
                          "opened": r.get("opened", ""), "reviews": 0, "tried": dict.fromkeys(TRIED, 0), "splits": []}
    for c in seed["conflict"]:
        for k in ("a", "b"):
            if c.get(k) in roots:
                roots[c[k]]["splits"].append(f"{c['id']}: {c['split']}")
    for ln in R.read(records, "lesson"):
        r = roots.setdefault(ln["root"], {"id": ln["root"], "sentence": ln.get("sentence", ""), "source": ln["id"],
                                          "opened": ln["run"], "reviews": 0, "tried": dict.fromkeys(TRIED, 0),
                                          "splits": []})
        r["reviews"] += len(ln.get("reviews", []))
        for k in TRIED:
            r["tried"][k] += int((ln.get("tried") or {}).get(k, 0))
        if ln.get("kind") == "충돌" and ln.get("against") in roots:
            text = f"{ln['root']}({ln['run']}): {ln.get('split', '')}"
            r["splits"].append(text)
            roots[ln["against"]]["splits"].append(text)
    return sorted(roots.values(), key=lambda r: r["id"])


def next_of(reason: str) -> str | None:
    reason = str(reason or "")
    return reason.rsplit(NEXT_MARK, 1)[1].strip() if NEXT_MARK in reason else None


def week_due(reviews: list[dict], day: dt.date, today: list[dict]) -> dict[str, str]:
    """그 주(day 포함 7일)의 '다음엔'(그대로 제외): review id → reason. today = 오늘 입력의 돌아보기(id 없는 꼴)."""
    lo = (day - dt.timedelta(days=6)).isoformat()
    out = {}
    for r in reviews:
        t = next_of(r.get("reason", ""))
        if lo <= str(r.get("run", "")) <= day.isoformat() and t and not t.startswith(NEXT_KEEP):
            out[r["id"]] = str(r.get("reason", ""))
    for rv in today:
        t = next_of(rv.get("reason", ""))
        if t and not t.startswith(NEXT_KEEP):
            out[f"review:{rv.get('prediction')}"] = str(rv.get("reason", ""))
    return out


def is_lesson_day(day: dt.date) -> bool:
    return day.weekday() == 6 and day.isoformat() >= LESSONS_SINCE


def input_errors(inp: dict, day: dt.date, due: dict[str, str], known: set[str]) -> list[str]:
    """일요일 입력 lessons 검사. due = week_due, known = 지금 있는 뿌리 id."""
    items = inp.get("lessons")
    if not is_lesson_day(day):
        return [] if not items else ["lessons는 일요일 주간 결산에만 적는다"]
    if not isinstance(items, list):
        return [f"lessons: 일요일 주간 결산은 그 주의 '다음엔' {len(due)}건을 뿌리에 묶은 목록을 적는다(지시문 '묶고 견주기')"]
    errs, seen, new_ids = [], {}, set()
    for i, e in enumerate(items):
        w = f"lessons[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{w}: {{root, kind, reviews, tried}} 꼴")
            continue
        root, kind = str(e.get("root", "")), e.get("kind")
        if kind not in KINDS:
            errs.append(f"{w}: kind는 {list(KINDS)} 중 하나")
        if not ROOT_ID.match(root):
            errs.append(f"{w}: root는 L 뒤 두 자리 이상 숫자(예: L12)")
        if kind == "같음" and root not in known:
            errs.append(f"{w}: '같음'인데 뿌리 {root}가 없다(python -m market.lessons)")
        if kind in ("새 뿌리", "충돌"):
            if root in known or root in new_ids:
                errs.append(f"{w}: 새 뿌리 id {root}가 이미 있다")
            new_ids.add(root)
            if not str(e.get("sentence", "")).strip():
                errs.append(f"{w}: 새 뿌리·충돌은 sentence(상황 → 하겠다, 한 문장)를 적는다")
        if kind == "충돌":
            if e.get("against") not in known:
                errs.append(f"{w}: 충돌은 against(반대 말인 있는 뿌리 id)를 적는다")
            if not str(e.get("split", "")).strip():
                errs.append(f"{w}: 충돌은 split(어느 쪽도 버리지 않고 가르는 조건)을 적는다")
        revs = e.get("reviews")
        if not isinstance(revs, list) or not revs:
            errs.append(f"{w}: reviews(묶은 돌아보기 id)가 비었다")
            revs = []
        for rid in revs:
            if rid not in due:
                errs.append(f"{w}: '{rid}'는 이 주의 '다음엔'이 아니다")
            elif rid in seen:
                errs.append(f"{w}: '{rid}'를 두 뿌리({seen[rid]}, {root})에 넣었다 — 하나에만")
            seen.setdefault(rid, root)
        tried = e.get("tried") or {}
        if not isinstance(tried, dict) or set(tried) - set(TRIED) or not all(
                isinstance(v, int) and v >= 0 for v in tried.values()):
            errs.append(f"{w}: tried는 {{나아짐, 모름, 나빠짐}} → 0 이상 정수")
        else:
            did = sum("→ 해 봄" in due.get(rid, "") for rid in revs)
            if sum(tried.values()) != did:
                errs.append(f"{w}: tried 합 {sum(tried.values())}가 묶은 것 중 '→ 해 봄' 돌아보기 수 {did}와 다르다")
    missing = sorted(set(due) - set(seen))
    if missing:
        errs.append(f"lessons: 이 주의 '다음엔' {len(missing)}건이 어느 뿌리에도 없다 — {', '.join(missing[:5])}"
                    f"{' …' if len(missing) > 5 else ''}")
    return errs


def check(seed: dict) -> list[str]:
    """lessons.toml 모양: 뿌리 id 꼴·겹침 없음·문장, 충돌의 a·b가 있는 뿌리, split."""
    errs, ids = [], set()
    for r in seed["root"]:
        if not ROOT_ID.match(str(r.get("id", ""))) or r.get("id") in ids:
            errs.append(f"[뿌리] '{r.get('id')}': id는 L+두 자리 이상 숫자, 겹치지 않게")
        if not str(r.get("sentence", "")).strip():
            errs.append(f"[뿌리] {r.get('id')}: sentence가 없음")
        ids.add(r.get("id"))
    for c in seed["conflict"]:
        if c.get("a") not in ids or c.get("b") not in ids or not str(c.get("split", "")).strip():
            errs.append(f"[뿌리 충돌] {c.get('id')}: a·b는 있는 뿌리, split(가르는 조건) 필수")
    return errs


PRECHECK_SINCE = "2026-11-18"   # 판단 전 확인(반대 검토 + 직전 확인)을 요구하는 첫 판단 날
PRECHECK_FROM = dt.time(8, 0)   # 직전 확인의 정보 시각은 이 뒤 — 장 전 대체거래소 거래(08:00–08:50)·동시호가 예상 체결가(08:30–)가 보이는 때


def precheck_errors(inp: dict, day: dt.date, at: dt.datetime, known: set[str]) -> list[str]:
    """판단 전 확인(2026-11-17, 지시문 4-0): 입력 precheck = {roots: [밟은 뿌리 id…] 또는 [], by: 반대 검토를 한 에이전트,
    note: 무엇을 고쳤나·안 고쳤나, late_as_of: 직전 확인 정보 시각(ISO, 그날 08:00 뒤·판단 시각 전), late: 직전 확인에서 본 것}."""
    if day.isoformat() < PRECHECK_SINCE:
        return []
    pc = inp.get("precheck")
    if not isinstance(pc, dict):
        return ["precheck: 판단 전 확인이 없다 — 반대 검토(뿌리 목록에 대 봄)와 08:00 뒤 직전 확인을 적는다(지시문 4-0)"]
    errs = []
    roots = pc.get("roots")
    if not isinstance(roots, list) or any(r not in known for r in roots):
        errs.append(f"precheck.roots: 오늘 판단이 밟은 뿌리 id 목록(없으면 []) — 모르는 id {[r for r in roots or [] if r not in known]}")
    for k in ("by", "note", "late"):
        if not str(pc.get(k, "")).strip():
            errs.append(f"precheck.{k}가 비었다")
    try:
        t = dt.datetime.fromisoformat(str(pc.get("late_as_of", "")))
        if t.tzinfo is None:
            raise ValueError
        lo = dt.datetime.combine(day, PRECHECK_FROM, C.KST)
        if not lo <= t <= at:
            errs.append(f"precheck.late_as_of {t.isoformat()}는 {lo:%H:%M}–판단 시각({at:%H:%M}) 사이여야 한다 — 직전 확인은 장 전 정보가 보일 때 한다")
    except ValueError:
        errs.append("precheck.late_as_of: 직전 확인 정보 시각(시간대 붙은 ISO, 예: 2026-11-18T08:35+09:00)")
    return errs


def record_lines(day: dt.date, inp: dict) -> list[dict]:
    out = []
    for i, e in enumerate(inp.get("lessons") or []):
        line = {"id": f"lesson:{day.isoformat()}:{i + 1}", "run": day.isoformat(), "root": str(e["root"]),
                "kind": e["kind"], "reviews": list(e["reviews"]),
                "tried": {k: int((e.get("tried") or {}).get(k, 0)) for k in TRIED}}
        for k in ("sentence", "against", "split"):
            if e.get(k):
                line[k] = str(e[k])
        out.append(line)
    return out


def brief_lines(roots: list[dict]) -> list[str]:
    out = []
    for r in roots:
        t = r["tried"]
        tried = f" · 해 봄 나아짐 {t['나아짐']}/모름 {t['모름']}/나빠짐 {t['나빠짐']}" if sum(t.values()) else ""
        out.append(f"- **{r['id']}** {r['sentence']} ({r['source']}; 묶인 '다음엔' {r['reviews']}건{tried})")
        for sp in r["splits"]:
            out.append(f"  - 충돌 → {sp}")
    return out


def feedback_lines() -> list[str]:
    """논리집(PROVISO-Nesy)의 메인 피드백 두 파일(common·stock_rader)의 줄. 못 찾으면 그 까닭 한 줄."""
    from . import logic
    try:
        base = logic.proviso_path()
    except SystemExit as exc:
        return [f"(논리집을 못 찾아 메인 피드백을 못 읽음: {str(exc)[:80]})"]
    out = []
    for name in ("common", "stock_rader"):
        p = base / "feedback" / "main" / f"{name}.md"
        if not p.exists():
            out.append(f"({p.name} 없음)")
            continue
        out += [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- **F")]
    return out


def main(argv: list[str]) -> int:
    roots = current()
    print(f"# 뿌리 {len(roots)}개 (씨앗 {LESSONS.name} + records/lesson.jsonl)\n")
    print("\n".join(brief_lines(roots)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
