"""논문 폴더 형식 검사 (author.md 2판·reviewer.md).

    python -X utf8 papers/check.py papers/P-0004 [--run] [--review]

본다: theory.md 머리와 절, paper.md 절 이름과 차례, [E#] 표시,
theory.md 커밋이 evidence.py·paper.md 커밋보다 앞인가, ledger.md의 이번 판 줄.
--run: evidence.py를 돌려 종료 0인지, 논문의 [E#]가 출력에 다 있는지.
--review: review.md 절 이름과 차례.
숫자 하나하나가 출력과 같은지는 보지 않는다(심사자가 본다).
읽기만 한다. 통과면 종료 0, 걸리면 걸린 것을 찍고 종료 1.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "papers" / "ledger.md"

THEORY_HEAD = ("이론", "판", "교훈")
THEORY_SECTIONS = [
    "## 1. 출발점",
    "## 2. 효과",
    "## 3. 메커니즘",
    "## 4. 핵심과 보조",
    "## 5. 예측",
    "## 6. 경쟁 이론",
    "## 7. 공격 목록",
    "## 8. 사례와 재는 법",
]
PAPER_SECTIONS = [
    "## 0. 요약",
    "## 1. 출발점",
    "## 2. 이론",
    "## 3. 자료와 재는 법",
    "## 4. 시험",
    "## 5. 자기 비판",
    "## 6. 고친 것",
    "## 7. 사례 표",
    "## 8. 남은 이론",
    "## 9. 행동",
    "## 10. 다음 날들의 시험",
    "## 11. 곁가지",
    "## 12. 다시 계산하는 법",
    "## 13. 양식에 대한 말",
]
REPLY_SECTION = "## 받은 비판에 대한 답"
REVIEW_SECTIONS = [
    "## 0. 판정",
    "## 1. 다시 계산",
    "## 2. 판단 때 알 수 있었나",
    "## 3. 순서와 사례",
    "## 4. 반박",
    "## 5. 빠뜨린 공격",
    "## 6. 경쟁 이론",
    "## 7. 다음 판에 바라는 것",
    "## 8. 양식에 대한 말",
]
RESULTS = ("버팀", "좁혀짐", "무너짐", "모름")
MECH_RESULTS = RESULTS + ("열어 둠",)
VERDICTS = ("채택", "고쳐서 다시", "버림")  # "채택 — 효과만"은 "채택"으로 잡힌다
TAG = re.compile(r"\[E(\d+)\]")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def head(text: str) -> dict[str, str]:
    got = {}
    for line in text.split("\n"):
        for key in THEORY_HEAD:
            m = re.match(rf"^{key}:\s*(.+?)\s*$", line)
            if m and key not in got:
                got[key] = m.group(1)
    return got


def sections(text: str, wanted: list[str], name: str, bad: list[str]) -> list[str]:
    """wanted가 모두 그 차례로 있는가. 찾은 머리줄(## 로 시작)을 돌려준다."""
    found = [l.rstrip() for l in text.split("\n") if l.startswith("## ")]
    at = -1
    for s in wanted:
        if s not in found:
            bad.append(f"{name}: 절 없음 '{s}'")
            continue
        i = found.index(s)
        if i < at:
            bad.append(f"{name}: 절 차례가 다름 '{s}'")
        at = max(at, i)
    return found


def section_body(text: str, title: str) -> str:
    lines = text.split("\n")
    if title not in [l.rstrip() for l in lines]:
        return ""
    i = [l.rstrip() for l in lines].index(title) + 1
    body = []
    while i < len(lines) and not lines[i].startswith("## "):
        body.append(lines[i])
        i += 1
    return "\n".join(body)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() if r.returncode == 0 else ""


def added_in(path: Path) -> str:
    """그 파일을 처음 더한 커밋(없으면 빈 문자열)."""
    rel = path.relative_to(ROOT).as_posix()
    out = git("log", "--diff-filter=A", "--format=%H", "--", rel)
    return out.split("\n")[-1] if out else ""


def is_ancestor(a: str, b: str) -> bool:
    r = subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT, capture_output=True)
    return r.returncode == 0


def table(text: str, title: str) -> list[list[str]]:
    rows = []
    for line in section_body(text, title).split("\n"):
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows[1:]  # 머리줄 뺌


def check(folder: Path, run: bool, review: bool) -> list[str]:
    bad: list[str] = []
    paper_id = folder.name
    for f in ("call.md", "theory.md", "evidence.py", "paper.md"):
        if not (folder / f).exists():
            bad.append(f"파일 없음: {paper_id}/{f}")
    if bad:
        return bad

    # theory.md
    theory = read(folder / "theory.md")
    h = head(theory)
    for key in THEORY_HEAD:
        if key not in h:
            bad.append(f"theory.md: 머리 '{key}:' 없음")
    tid = h.get("이론", "")
    if tid and not re.fullmatch(r"T-\d{4}", tid):
        bad.append(f"theory.md: 이론 id 꼴이 T-0000이 아님 ({tid})")
    m = re.fullmatch(r"v(\d+)", h.get("판", ""))
    version = int(m.group(1)) if m else 0
    if "판" in h and not m:
        bad.append(f"theory.md: 판 꼴이 v1이 아님 ({h['판']})")
    sections(theory, THEORY_SECTIONS, "theory.md", bad)

    # paper.md
    paper = read(folder / "paper.md")
    found = sections(paper, PAPER_SECTIONS, "paper.md", bad)
    if version >= 2:
        if REPLY_SECTION not in found:
            bad.append(f"paper.md: v{version}인데 절 없음 '{REPLY_SECTION}'")
        elif PAPER_SECTIONS[0] in found and found.index(REPLY_SECTION) > found.index(PAPER_SECTIONS[0]):
            bad.append(f"paper.md: '{REPLY_SECTION}'은 0절 앞에")
    summary = section_body(paper, PAPER_SECTIONS[0])
    if summary and not re.search(rf"효과\s*[:：]?\s*({'|'.join(RESULTS)})", summary):
        bad.append(f"paper.md 0절: '효과: {' / '.join(RESULTS)}'가 없음")
    if summary and not re.search(rf"메커니즘\s*(\([^)]*\))?\s*[:：]?\s*({'|'.join(MECH_RESULTS)})", summary):
        bad.append(f"paper.md 0절: '메커니즘: {' / '.join(MECH_RESULTS)}'가 없음")
    if summary and not any(v in summary for v in VERDICTS):
        bad.append(f"paper.md 0절: 저자 권고({' / '.join(VERDICTS)})가 없음")
    if tid and tid not in summary:
        bad.append(f"paper.md 0절: 이론 id {tid}가 없음")
    tags = set(TAG.findall(paper))
    if not tags:
        bad.append("paper.md: [E#] 표시가 하나도 없음")

    # 커밋 차례
    c_theory = added_in(folder / "theory.md")
    if not c_theory:
        bad.append("커밋: theory.md가 커밋되지 않음")
    for f in ("evidence.py", "paper.md"):
        c = added_in(folder / f)
        if not c:
            bad.append(f"커밋: {f}가 커밋되지 않음")
        elif c_theory and (c == c_theory or not is_ancestor(c_theory, c)):
            bad.append(f"커밋: theory.md 커밋이 {f} 커밋보다 앞이 아님")

    # ledger
    if not LEDGER.exists():
        bad.append("ledger.md 없음")
    else:
        ledger = read(LEDGER)
        trows = table(ledger, "## 이론 표")
        if tid and not any(r[:3] == [tid, f"v{version}", paper_id] for r in trows if len(r) >= 3):
            bad.append(f"ledger 이론 표: '{tid} | v{version} | {paper_id}' 줄 없음")

    # 돌려 보기
    if run:
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", str((folder / "evidence.py").relative_to(ROOT))],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=1800,
            )
        except subprocess.TimeoutExpired:
            bad.append("evidence.py: 30분 안에 끝나지 않음")
        else:
            if r.returncode != 0:
                tail = (r.stderr or r.stdout).strip().split("\n")[-1:]
                bad.append(f"evidence.py: 종료 {r.returncode} {tail}")
            else:
                missing = sorted(tags - set(TAG.findall(r.stdout)), key=int)
                if missing:
                    bad.append("paper.md의 [E#] 가운데 출력에 없는 것: " + ", ".join(f"E{t}" for t in missing))

    # 심사서
    if review:
        rv = folder / "review.md"
        if not rv.exists():
            bad.append(f"파일 없음: {paper_id}/review.md")
        else:
            text = read(rv)
            sections(text, REVIEW_SECTIONS, "review.md", bad)
            verdict = section_body(text, REVIEW_SECTIONS[0])
            if verdict and not any(v in verdict for v in VERDICTS):
                bad.append(f"review.md 0절: 권고({' / '.join(VERDICTS)})가 없음")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="논문 폴더 형식 검사")
    ap.add_argument("folder", help="papers/P-0004 같은 논문 폴더")
    ap.add_argument("--run", action="store_true", help="evidence.py를 돌려 [E#] 대조")
    ap.add_argument("--review", action="store_true", help="review.md도 검사")
    a = ap.parse_args()
    folder = Path(a.folder).resolve()
    if not folder.is_dir():
        print(f"폴더 없음: {a.folder}")
        return 1
    bad = check(folder, a.run, a.review)
    if bad:
        print(f"걸림 {len(bad)}건 — {folder.name}")
        for b in bad:
            print(f"- {b}")
        return 1
    print(f"통과 — {folder.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
