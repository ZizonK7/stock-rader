"""대화 세션 몫 (2026-11-17): 매일 실행이 '코드·논리집 문법이 필요해 오늘은 못 한다'고 남긴 일을 묻히지 않게 모은다.

  python -m market.asks          열린 것 목록 (닫는 법과 함께)

까닭(사용자 2026-11-17, 지시서 p561): 11-06·07·10의 '묶음 대비 채점 칸이 필요', 11-17의 '범위에 빼기가 필요' 같은 메모가 실행
메모(run notes)에만 남아 아무도 읽지 않았다. 매일 실행은 기록(records/)만 쓰고 대화 세션은 기록을 쓰지 않으므로, 두 곳으로 나눈다.
- **여는 쪽(매일 실행)**: 입력의 `asks` = [{what, close}] → run 줄의 `asks` 칸(id `ask:<날짜>:<번호>`)에 덧붙인다(daily.record).
  what = 무엇이 필요한가(근거 날짜·건수), close = 무엇이 되면 닫히나(누가 봐도 확인할 수 있게).
- **닫는 쪽(대화 세션)**: 저장소 루트 `asks.toml`의 [[ask]]에 같은 id로 status = "닫힘"과 closed(날짜)·how(어떻게 닫았나 — 커밋·
  문서)를 적는다. run 줄보다 먼저 있던 일(옛 메모)이나 대화 세션이 스스로 찾은 일은 asks.toml에 status = "열림"으로 바로 연다.
- **보이는 곳**: 열린 것은 매일 prepare briefing 맨 위(`daily.briefing`)와 `docs/todo.md`(대화 세션이 고칠 때 맞춘다 — 2026-11-25 루트 CLAUDE.md에서 옮김).
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from . import config as C
from . import records as R

ASKS = C.ROOT / "asks.toml"
STATUS = ("열림", "닫힘")


def load(path: Path = ASKS) -> list[dict]:
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("ask", [])


def from_runs(runs: list[dict]) -> list[dict]:
    """run 줄의 asks 칸 → 항목(id, opened, from, what, close)."""
    out = []
    for r in runs:
        for a in r.get("asks") or []:
            out.append({"id": a["id"], "opened": r["run"], "from": r["id"], "what": a["what"], "close": a["close"],
                        "status": "열림"})
    return out


def open_items(path: Path = ASKS, records: Path = C.RECORDS) -> list[dict]:
    """열린 것: asks.toml에서 status '열림'인 것 + run 줄에서 연 것 중 asks.toml에 같은 id가 없는 것. 연 날짜 순."""
    listed = load(path)
    known = {a["id"] for a in listed}
    items = [a for a in listed if a.get("status") == "열림"]
    items += [a for a in from_runs(R.read(records, "run")) if a["id"] not in known]
    return sorted(items, key=lambda a: (str(a.get("opened", "")), a["id"]))


def check(items: list[dict]) -> list[str]:
    """asks.toml 모양: id·opened·what·close·status 필수, 닫힘이면 closed·how, id 겹침 없음."""
    errs, seen = [], set()
    for i, a in enumerate(items):
        w = f"asks.toml [{i}] {a.get('id', '')}"
        for k in ("id", "opened", "what", "close", "status"):
            if not str(a.get(k, "")).strip():
                errs.append(f"{w}: {k}가 없음")
        if a.get("status") not in STATUS:
            errs.append(f"{w}: status는 {list(STATUS)} 중 하나")
        if a.get("status") == "닫힘" and not (str(a.get("closed", "")).strip() and str(a.get("how", "")).strip()):
            errs.append(f"{w}: 닫힘이면 closed(날짜)와 how(어떻게 닫았나)를 적는다")
        if a.get("id") in seen:
            errs.append(f"{w}: id가 겹침")
        seen.add(a.get("id"))
    return errs


def input_errors(inp: dict) -> list[str]:
    """매일 실행 입력의 asks: [{what, close}] — 둘 다 빈 글이 아니어야 한다."""
    errs = []
    asks = inp.get("asks", [])
    if not isinstance(asks, list):
        return ["asks는 [{what, close}] 목록"]
    for i, a in enumerate(asks):
        if not isinstance(a, dict) or not str(a.get("what", "")).strip() or not str(a.get("close", "")).strip():
            errs.append(f"asks[{i}]: what(무엇이 필요한가 — 근거 날짜·건수)과 close(무엇이 되면 닫히나)를 둘 다 적는다")
    return errs


def run_field(day: str, inp: dict) -> list[dict]:
    """입력의 asks → run 줄의 asks 칸 (id를 붙인다)."""
    return [{"id": f"ask:{day}:{i + 1}", "what": str(a["what"]).strip(), "close": str(a["close"]).strip()}
            for i, a in enumerate(inp.get("asks") or [])]


def lines(items: list[dict]) -> list[str]:
    """briefing·명령 출력용 줄."""
    if not items:
        return ["열린 대화 세션 몫 없음."]
    out = []
    for a in items:
        out.append(f"- **{a['id']}** ({a.get('opened', '')} 염): {a['what']}\n  - 닫는 법: {a['close']}")
    return out


def main(argv: list[str]) -> int:
    errs = check(load())
    items = open_items()
    print(f"# 열린 대화 세션 몫 {len(items)}건\n")
    print("\n".join(lines(items)))
    for e in errs:
        print("오류:", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
