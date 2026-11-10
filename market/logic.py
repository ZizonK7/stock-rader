"""PROVISO 코어로 이 저장소의 논리집(knowledge.toml)을 검사한다.

사용법 (저장소 루트):
  python -m market.logic validate   지식 파일 칸 검사 (PROVISO schema.validate)
  python -m market.logic check      성적표 (PROVISO score.report + market/checks.py)
  python -m market.logic audit      점검 (PROVISO audit.report + market/checks.py AUDIT). 오류가 있을 때만 1로 끝난다

PROVISO 코어 명령줄은 자기 저장소의 domains/만 보므로, 코어 함수를 직접 부른다.
PROVISO 경로: 환경변수 PROVISO_PATH → 형제 폴더 ../PROVISO-Nesy(폴더 이름의 대소문자는 가리지 않는다 — 세션에 따라 proviso-nesy로 받아진다).
PROVISO-Nesy는 비공개 저장소다(2026-11-10): 키 없이 받아 올 수 없으므로 세션·예약 실행에 저장소로 붙어 있어야 한다.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from . import config as C


def siblings(parent: Path) -> list[Path]:
    """형제 폴더 가운데 이름이 PROVISO-Nesy인 것(대소문자 무시, 이름 순)."""
    try:
        return sorted(p for p in parent.iterdir() if p.is_dir() and p.name.lower() == "proviso-nesy")
    except OSError:
        return []


def proviso_path() -> Path:
    env = os.environ.get("PROVISO_PATH")
    candidates = ([Path(env)] if env else []) + siblings(C.ROOT.parent)
    for p in candidates:
        if (p / "proviso" / "schema.py").exists():
            return p.resolve()
    raise SystemExit("PROVISO-Nesy를 찾지 못함: 환경변수 PROVISO_PATH를 주거나 형제 폴더 ../PROVISO-Nesy에 둔다 "
                     "(비공개 저장소 — 세션·예약 실행에 저장소로 붙어 있어야 받아진다)")


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
