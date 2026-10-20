"""PROVISO 코어로 이 저장소의 논리집(knowledge.toml)을 검사한다.

사용법 (저장소 루트):
  python -m market.logic validate   지식 파일 칸 검사 (PROVISO schema.validate)
  python -m market.logic check      성적표 (PROVISO score.report + market/checks.py)
  python -m market.logic audit      점검 (PROVISO audit.report + market/checks.py AUDIT). 오류가 있을 때만 1로 끝난다

PROVISO 코어 명령줄은 자기 저장소의 domains/만 보므로, 코어 함수를 직접 부른다.
PROVISO 경로: 환경변수 PROVISO_PATH → 형제 폴더 ../PROVISO-Nesy. 클라우드 예약 실행은 공개 저장소를 클론해 PROVISO_PATH로 준다.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from . import config as C


def proviso_path() -> Path:
    env = os.environ.get("PROVISO_PATH")
    candidates = ([Path(env)] if env else []) + [C.ROOT.parent / "PROVISO-Nesy"]
    for p in candidates:
        if (p / "proviso" / "schema.py").exists():
            return p.resolve()
    raise SystemExit("PROVISO-Nesy를 찾지 못함: 환경변수 PROVISO_PATH를 주거나 ../PROVISO-Nesy에 클론한다 "
                     "(git clone https://github.com/ZizonK7/PROVISO-Nesy)")


def core():
    """(schema, score, audit) 모듈."""
    root = str(proviso_path())
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
        print(f"[market] {counts}, 판정 기록 {len(kn.judgments)} (PROVISO {proviso_path()})")
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
