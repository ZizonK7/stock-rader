"""이전 버전 조각 모음: 떠 두기와 같은 날 다시 계산하기 (설계 '세 가지를 나란히 적는다').

사용법 (저장소 루트):
  python -m market.versions                 목록 (active, 지문 확인)
  python -m market.versions save <id> "<왜>"  지금 knowledge.toml을 versions/<id>.toml로 떠 두고 목록에 더한다 (active = true)

- 조각을 쪼개거나 고치기 **전에** 떠 둔다. 떠 둔 모음은 계산기와 같은 코드(calc.predict)로, 같은 날의 시세 대 보기 값으로
  다시 계산해 predictor "version:<id>"로 적고 같이 채점한다. 가상이라 공짜로 나란히 돌릴 수 있다.
- 동시에 돌리는 버전은 최대 3개(MAX_ACTIVE): 직전, 지금까지 성적이 가장 좋은 것 등. 늘 도는 '지금 것'은 calc다.
- 떠 둔 파일은 고치지 않는다. 지문(digest)이 다르면 check()가 오류를 낸다.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import calc as K
from . import config as C

MAX_ACTIVE = 3
REGISTRY = "versions.toml"


@dataclass
class Version:
    id: str
    file: Path
    digest: str
    active: bool
    why: str
    saved: str


def load(folder: Path | None = None) -> list[Version]:
    folder = folder or C.VERSIONS
    reg = folder / REGISTRY
    if not reg.exists():
        return []
    raw = tomllib.loads(reg.read_text(encoding="utf-8"))
    return [Version(v["id"], folder / v["file"], v["digest"], bool(v.get("active", False)), v.get("why", ""),
                    str(v.get("saved", ""))) for v in raw.get("version", [])]


def check(folder: Path | None = None) -> list[str]:
    errs = []
    vs = load(folder)
    ids = [v.id for v in vs]
    if len(ids) != len(set(ids)):
        errs.append("[버전] id 중복")
    act = [v for v in vs if v.active]
    if len(act) > MAX_ACTIVE:
        errs.append(f"[버전] active {len(act)}개가 상한 {MAX_ACTIVE}개를 넘음")
    for v in vs:
        if not v.file.exists():
            errs.append(f"[버전] {v.id}: 파일 {v.file.name}이 없음")
            continue
        got = K.digest(v.file.read_bytes())
        if got != v.digest:
            errs.append(f"[버전] {v.id}: 떠 둔 파일의 지문 {got}이 목록의 {v.digest}와 다름 — 떠 둔 파일은 고치지 않는다")
        if not v.why.strip():
            errs.append(f"[버전] {v.id}: 왜 떠 두었는지(why)가 없음")
    return errs


def active_pieces(folder: Path | None = None) -> list[tuple[str, list[K.Piece], str]]:
    """(버전 id, 조각들, 지문) — active인 것만. 목록에 문제가 있으면 멈춘다(엉뚱한 버전으로 채점하지 않게)."""
    errs = check(folder)
    if errs:
        raise SystemExit("버전 목록 오류:\n" + "\n".join(errs))
    return [(v.id, *K.load_pieces(v.file)) for v in load(folder) if v.active]


def save(vid: str, why: str, folder: Path | None = None, knowledge: Path | None = None) -> Version:
    folder = folder or C.VERSIONS
    knowledge = knowledge or C.KNOWLEDGE
    if any(v.id == vid for v in load(folder)):
        raise SystemExit(f"버전 {vid}가 이미 있다")
    if not why.strip():
        raise SystemExit("왜 떠 두는지 적는다")
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{vid}.toml"
    shutil.copyfile(knowledge, dst)
    digest = K.digest(dst.read_bytes())
    reg = folder / REGISTRY
    text = reg.read_text(encoding="utf-8") if reg.exists() else ""
    why_q = why.replace("\\", "\\\\").replace('"', '\\"')
    text += (f'\n[[version]]\nid = "{vid}"\nfile = "{dst.name}"\nsaved = {C.now_kst().date().isoformat()}\n'
             f'digest = "{digest}"\nactive = true\nwhy = "{why_q}"\n')
    reg.write_text(text, encoding="utf-8")
    errs = check(folder)
    if errs:
        print("주의:\n" + "\n".join(errs))
    return load(folder)[-1]


def main(argv: list[str]) -> int:
    if argv and argv[0] == "save" and len(argv) == 3:
        v = save(argv[1], argv[2])
        print(f"떠 둠: {v.id} → {v.file} (지문 {v.digest})")
        return 0
    if argv:
        print(__doc__)
        return 2
    for v in load():
        print(f"{v.id} {'active' if v.active else '쉼'} {v.file.name} 지문 {v.digest} ({v.saved}) — {v.why}")
    errs = check()
    for e in errs:
        print("오류:", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
