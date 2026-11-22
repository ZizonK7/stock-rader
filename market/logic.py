"""PROVISO 코어로 이 저장소의 논리집(knowledge.toml)을 검사한다.

사용법 (저장소 루트):
  python -m market.logic validate   지식 파일 칸 검사 (PROVISO schema.validate)
  python -m market.logic check      성적표 (PROVISO score.report + market/checks.py)
  python -m market.logic audit      점검 (PROVISO audit.report + market/checks.py AUDIT). 오류가 있을 때만 1로 끝난다

코어는 이 저장소의 proviso/에 있다(2026-11-22): PROVISO-Nesy main이 2026-11-21 0447979로 비워져,
바로 앞 커밋 08c6c93의 schema·score·audit을 글자 그대로 옮겼다(proviso/CLAUDE.md 머리). 형제 폴더·PROVISO_PATH는 더 보지 않는다.
"""
from __future__ import annotations

import importlib
import sys

from . import config as C

CORE = C.ROOT / "proviso"                 # 코어(schema·score·audit)와 메인 피드백의 자리
FEEDBACK = CORE / "feedback" / "main"     # 메인 피드백 common.md·stock_rader.md (briefing이 읽는다)


def core():
    """(schema, score, audit) 모듈 — 이 저장소의 proviso/."""
    root = str(C.ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return (importlib.import_module("proviso.schema"), importlib.import_module("proviso.score"),
            importlib.import_module("proviso.audit"))


def load():
    schema, _, _ = core()
    return schema.load(C.KNOWLEDGE)


def validate() -> list[str]:
    schema, _, _ = core()
    return schema.validate(load())


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("validate", "check", "audit"):
        print(__doc__)
        return 2
    schema, score, audit = core()
    kn = schema.load(C.KNOWLEDGE)
    from . import checks
    if argv[0] == "validate":
        errors = schema.validate(kn)
        counts = ", ".join(f"{schema.KIND_LABEL[k]} {len(kn.of(k))}" for k in schema.KINDS)
        print(f"[market] {counts}, 판정 기록 {len(kn.judgments)} (코어 {CORE.relative_to(C.ROOT).as_posix()}/)")
        if errors:
            print("문제 발견:")
            for e in errors:
                print("  -", e)
            return 1
        print("문제 없음")
        return 0
    if argv[0] == "check":
        missing = sorted({c for n in kn.claims() for c in schema.check_names(n)} - set(checks.CHECKS))
        if missing:
            print(f"checks.py에 없는 검사: {', '.join(missing)}")
            return 1
        print("[market] 성적표 (출처는 검사마다: 재생 기록 = '실제 기록 재생', 진짜 계좌 기록 = '실측')\n")
        print(score.report(kn, checks.CHECKS, checks.SOURCE))
        return 0
    extra = checks.AUDIT(kn)
    print(f"[market] {audit.report(kn, extra)}")
    return 1 if audit.unaccepted_harms(kn) or extra[0] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
