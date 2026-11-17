"""뿌리 목록 — '다음엔'을 같은 뿌리로 묶어 일반화하고, 서로 반대인 것은 가르는 조건으로 쪼갠다 (2026-11-17).

  python -m market.lessons           지금 뿌리 목록(씨앗 lessons.toml + 기록 lesson 줄)과 뿌리마다 묶인 수·해 본 결과, 내린 뿌리

까닭(잼민이 지시서 p562, 사용자 "피드백을 하고 그거에 대해 일반화를 잘 해가지고 만약에 충돌되는 게 생긴다면 그때 논리를 쪼개고"):
돌아보기의 '다음엔'은 조각 숫자를 조금 고치는 좁은 처방이 대부분이고, 매일 실행은 지난 14일의 '다음엔'만 본다 — 조각으로 굳지
않은 교훈은 14일이 지나면 사라졌다. 처음엔 일요일에 묶었고, 2026-11-17 p564(사용자 "어제나 이전의 선택들 읽어 보면서 자체
피드백하고 일반화시킬 거 하고 없앨 거 없애고 합칠 거 합치고 있는 거지", "피드백 정리하는 에이전트는 따로 opus 5.5 high")부터
**매일**(config.FLOW_SINCE) 따로 둔 피드백 정리 에이전트가 정리한다:
- 입력 `lessons`에 아직 어느 뿌리에도 묶이지 않은 '다음엔'(BIND_FROM 뒤, 그대로 제외 — `due`)을 **빠짐없이** 넣는다(record가 거절).
  항목 = {root, kind, reviews, tried, …}. kind:
  같음(있는 뿌리에 건수를 더함) / 새 뿌리(sentence) / 충돌(있는 뿌리와 반대 말 — 어느 쪽도 버리지 않고 가르는 조건으로 쪼갠
  새 뿌리: sentence·against·split) / 고침(있는 뿌리를 더 일반화한 문장으로: sentence·why) / 합침(같은 뿌리 둘을 하나로: into·why
  — root의 건수가 into로 옮겨 가고 root는 목록에서 내려간다) / 내림(더는 맞지 않는 뿌리를 목록에서 내림: why).
  '없앤다'는 지난 기록을 지우는 것이 아니라 목록에서 내리고 까닭을 남기는 것이다(기록은 덧붙이기만).
  tried = 그 묶음 가운데 '지난 다음엔 … → 해 봄'인 돌아보기의 결과 {나아짐, 모름, 나빠짐} — 합이 그 수와 같아야 한다.
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

BIND_FROM = "2026-11-17"     # 이 날 돌아보기의 '다음엔'부터 묶는다 — 그 전 '다음엔'은 씨앗 뿌리(잼민이가 95건을 묶음)에 들어 있다
KINDS = ("같음", "새 뿌리", "충돌", "고침", "합침", "내림")
NEEDS_REVIEWS = ("같음", "새 뿌리", "충돌")   # 묶을 '다음엔'이 있어야 하는 kind (고침·합침·내림은 없어도 된다)
TRIED = ("나아짐", "모름", "나빠짐")
ROOT_ID = re.compile(r"^L\d{2,}$")
NEXT_MARK, NEXT_KEEP = "다음엔:", "그대로"


def load(path: Path = LESSONS) -> dict:
    if not path.exists():
        return {"root": [], "conflict": []}
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return {"root": raw.get("root", []), "conflict": raw.get("conflict", [])}


def _all(path: Path, records: Path) -> dict[str, dict]:
    """뿌리마다 {id, sentence, source, opened, reviews, tried, splits, gone(내림·합침 글 또는 '')}."""
    seed = load(path)
    roots: dict[str, dict] = {}

    def new(rid, sentence, source, opened):
        return {"id": rid, "sentence": sentence, "source": source, "opened": opened, "reviews": 0,
                "tried": dict.fromkeys(TRIED, 0), "splits": [], "gone": ""}
    for r in seed["root"]:
        roots[r["id"]] = new(r["id"], r["sentence"], r.get("source", ""), r.get("opened", ""))
    for c in seed["conflict"]:
        for k in ("a", "b"):
            if c.get(k) in roots:
                roots[c[k]]["splits"].append(f"{c['id']}: {c['split']}")
    for ln in R.read(records, "lesson"):
        kind = ln.get("type")   # 기록 줄의 kind 칸은 기록 종류('lesson')라 묶는 방식은 type에 (입력에서는 kind)
        r = roots.setdefault(ln["root"], new(ln["root"], ln.get("sentence", ""), ln["id"], ln["run"]))
        r["reviews"] += len(ln.get("reviews", []))
        for k in TRIED:
            r["tried"][k] += int((ln.get("tried") or {}).get(k, 0))
        if kind == "충돌" and ln.get("against") in roots:
            text = f"{ln['root']}({ln['run']}): {ln.get('split', '')}"
            r["splits"].append(text)
            roots[ln["against"]]["splits"].append(text)
        elif kind == "고침" and ln.get("sentence"):
            r["sentence"] = ln["sentence"]
            r["source"] = f"{r['source']} → {ln['id']}"
        elif kind == "합침" and ln.get("into") in roots:
            into = roots[ln["into"]]
            into["reviews"] += r["reviews"]
            for k in TRIED:
                into["tried"][k] += r["tried"][k]
            into["splits"] += r["splits"]
            r["gone"] = f"{ln['run']} {ln['into']}에 합침 — {ln.get('why', '')}"
        elif kind == "내림":
            r["gone"] = f"{ln['run']} 내림 — {ln.get('why', '')}"
    return roots


def current(path: Path = LESSONS, records: Path = C.RECORDS) -> list[dict]:
    """지금 뿌리 목록(내리거나 합쳐진 뿌리는 뺌), id 순."""
    return sorted((r for r in _all(path, records).values() if not r["gone"]), key=lambda r: r["id"])


def gone(path: Path = LESSONS, records: Path = C.RECORDS) -> list[dict]:
    return sorted((r for r in _all(path, records).values() if r["gone"]), key=lambda r: r["id"])


def all_ids(path: Path = LESSONS, records: Path = C.RECORDS) -> set[str]:
    return set(_all(path, records))


def next_of(reason: str) -> str | None:
    reason = str(reason or "")
    return reason.rsplit(NEXT_MARK, 1)[1].strip() if NEXT_MARK in reason else None


def due(reviews: list[dict], lessons: list[dict], day: dt.date, today: list[dict]) -> dict[str, str]:
    """아직 어느 뿌리에도 묶이지 않은 '다음엔'(BIND_FROM~day, 그대로 제외): review id → reason.
    today = 오늘 입력의 돌아보기(id 없는 꼴 — 'review:<예측 id>'로)."""
    bound = {rid for ln in lessons for rid in ln.get("reviews", [])}
    out = {}
    for r in reviews:
        t = next_of(r.get("reason", ""))
        if BIND_FROM <= str(r.get("run", "")) <= day.isoformat() and t and not t.startswith(NEXT_KEEP) and r["id"] not in bound:
            out[r["id"]] = str(r.get("reason", ""))
    for rv in today:
        t = next_of(rv.get("reason", ""))
        if t and not t.startswith(NEXT_KEEP):
            out[f"review:{rv.get('prediction')}"] = str(rv.get("reason", ""))
    return out


def is_lesson_day(day: dt.date) -> bool:
    """매일 정리를 요구하는 날(C.FLOW_SINCE부터 — 그 전에는 일요일 결산도 아직 시작 전이었다)."""
    return day.isoformat() >= C.FLOW_SINCE


def input_errors(inp: dict, day: dt.date, due_: dict[str, str], known: set[str], ever: set[str] | None = None) -> list[str]:
    """입력 lessons 검사. due_ = due(…), known = 지금 있는 뿌리 id, ever = 내린 것까지 쓴 적 있는 id(새 id 겹침 막기)."""
    items = inp.get("lessons")
    ever = known | (ever or set())
    if not is_lesson_day(day):
        return [] if not items else [f"lessons는 {C.FLOW_SINCE}부터 적는다"]
    if not isinstance(items, list):
        return [f"lessons: 피드백 정리 결과 — 묶지 않은 '다음엔' {len(due_)}건을 뿌리에 묶은 목록(없으면 [])을 적는다(지시문 '피드백 정리')"]
    errs, seen, new_ids, touched = [], {}, set(), {}
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
        if kind in ("같음", "고침", "합침", "내림") and root not in known:
            errs.append(f"{w}: '{kind}'인데 뿌리 {root}가 지금 목록에 없다(python -m market.lessons)")
        if kind in ("고침", "합침", "내림"):
            if root in touched:
                errs.append(f"{w}: 뿌리 {root}를 한 날 두 번 '{touched[root]}'·'{kind}' — 하나로")
            touched[root] = kind
        if kind in ("새 뿌리", "충돌"):
            if root in ever or root in new_ids:
                errs.append(f"{w}: 새 뿌리 id {root}가 이미 있다(내린 뿌리 id도 다시 쓰지 않는다)")
            new_ids.add(root)
        if kind in ("새 뿌리", "충돌", "고침") and not str(e.get("sentence", "")).strip():
            errs.append(f"{w}: '{kind}'은 sentence(상황 → 하겠다, 한 문장)를 적는다")
        if kind == "충돌":
            if e.get("against") not in known:
                errs.append(f"{w}: 충돌은 against(반대 말인 있는 뿌리 id)를 적는다")
            if not str(e.get("split", "")).strip():
                errs.append(f"{w}: 충돌은 split(어느 쪽도 버리지 않고 가르는 조건)을 적는다")
        if kind == "합침" and (e.get("into") not in known or e.get("into") == root):
            errs.append(f"{w}: 합침은 into(합쳐 남길 다른 뿌리 id)를 적는다")
        if kind in ("고침", "합침", "내림") and not str(e.get("why", "")).strip():
            errs.append(f"{w}: '{kind}'은 why(까닭)를 적는다")
        revs = e.get("reviews", [] if kind not in NEEDS_REVIEWS else None)
        if not isinstance(revs, list) or (kind in NEEDS_REVIEWS and not revs):
            errs.append(f"{w}: reviews(묶은 돌아보기 id)가 비었다")
            revs = []
        for rid in revs:
            if rid not in due_:
                errs.append(f"{w}: '{rid}'는 아직 묶지 않은 '다음엔'이 아니다")
            elif rid in seen:
                errs.append(f"{w}: '{rid}'를 두 뿌리({seen[rid]}, {root})에 넣었다 — 하나에만")
            seen.setdefault(rid, root)
        tried = e.get("tried") or {}
        if not isinstance(tried, dict) or set(tried) - set(TRIED) or not all(
                isinstance(v, int) and v >= 0 for v in tried.values()):
            errs.append(f"{w}: tried는 {{나아짐, 모름, 나빠짐}} → 0 이상 정수")
        else:
            did = sum("→ 해 봄" in due_.get(rid, "") for rid in revs)
            if sum(tried.values()) != did:
                errs.append(f"{w}: tried 합 {sum(tried.values())}가 묶은 것 중 '→ 해 봄' 돌아보기 수 {did}와 다르다")
    missing = sorted(set(due_) - set(seen))
    if missing:
        errs.append(f"lessons: 묶지 않은 '다음엔' {len(missing)}건이 어느 뿌리에도 없다 — {', '.join(missing[:5])}"
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


def record_lines(day: dt.date, inp: dict) -> list[dict]:
    out = []
    for i, e in enumerate(inp.get("lessons") or []):
        line = {"id": f"lesson:{day.isoformat()}:{i + 1}", "run": day.isoformat(), "root": str(e["root"]),
                "type": e["kind"], "reviews": list(e.get("reviews") or []),
                "tried": {k: int((e.get("tried") or {}).get(k, 0)) for k in TRIED}}
        for k in ("sentence", "against", "split", "into", "why"):
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
    """python -m market.lessons [--root <폴더>] — --root면 그 폴더의 lessons.toml·records(흉내 폴더에서 새 뿌리가 보이게, 2026-11-17)."""
    if "--root" in argv:
        base = Path(argv[argv.index("--root") + 1]).resolve()
        roots, down = current(base / "lessons.toml", base / "records"), gone(base / "lessons.toml", base / "records")
    else:
        roots, down = current(), gone()
    print(f"# 뿌리 {len(roots)}개 (씨앗 {LESSONS.name} + records/lesson.jsonl)\n")
    print("\n".join(brief_lines(roots)))
    if down:
        print(f"\n## 내리거나 합친 뿌리 {len(down)}개")
        print("\n".join(f"- {r['id']} {r['sentence']} — {r['gone']}" for r in down))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
