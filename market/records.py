"""덧붙이기만 하는 기록 (JSONL)과 그 검사기.

사용법 (저장소 루트):
  python -m market.records check            records/ 아래 모든 기록 + data/prices/*.csv 검사 (칸, id, 순서, git HEAD와 비교)
  python -m market.records check <폴더>     그 폴더만

기록 파일: 폴더마다 종류별 `<종류>.jsonl` 한 줄에 기록 하나. 칸 설명은 아래 FIELDS와 records/CLAUDE.md.
규칙: 지난 줄은 고치지도 지우지도 않는다. 정정은 correction 줄을 새로 덧붙인다.
검사기는 git HEAD에 있던 내용이 지금 파일의 앞부분과 글자 그대로 같은지 본다(달라졌거나 지워졌으면 실패).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import config as C

NUM = (int, float)
OPT_NUM = (int, float, type(None))
OPT_BOOL = (bool, type(None))

# 종류 → 칸 → 허용 타입. 모든 줄에 공통: id(문자열), kind(종류), run(판단 날짜 YYYY-MM-DD)
FIELDS: dict[str, dict[str, tuple]] = {
    "run": {"mode": (str,), "steps": (dict,), "notes": (list,)},
    "reading": {"asset": (str,), "known_through": (str,), "values": (dict,), "numbers": (dict,)},
    "prediction": {"predictor": (str,), "asset": (str,), "horizon": (int,), "base_date": (str,), "base_close": NUM,
                   "p_up": OPT_NUM, "p_range": (list, type(None)), "size_lo": OPT_NUM, "size_hi": OPT_NUM,
                   "readings": (dict,), "used": (list,), "decisive": (list,), "reason": (str,), "knowledge": (str,)},
    "score": {"prediction": (str,), "predictor": (str,), "asset": (str,), "horizon": (int,), "target_date": (str,),
              "target_close": NUM, "ret": NUM, "up": (bool,), "direction_hit": OPT_BOOL, "brier": OPT_NUM,
              "size_hit": OPT_BOOL},
    "order": {"account": (str,), "asset": (str,), "side": (str,), "qty_units": (int,), "ref_price": NUM, "reason": (str,)},
    "fill": {"account": (str,), "order": (str,), "asset": (str,), "side": (str,), "fill_date": (str,), "price": NUM,
             "qty_units": (int,), "value": (int,), "fee": (int,), "cash_after": (int,), "note": (str,)},
    "account": {"account": (str,), "cash": (int,), "positions": (dict,), "prices": (dict,), "value": NUM,
                "pending": (list,)},
    "review": {"prediction": (str,), "wrong": (list,), "why": (str,), "reason": (str,)},
    "correction": {"target": (str,), "note": (str,)},
    # 2단계 (매일 예약 실행)
    "research": {"question": (str,), "state": (str,), "angle": (str,), "asked": (str,), "answer": (str,),
                 "looked": (list,), "agent": (str,)},
    "hand_reading": {"values": (dict,), "why": (dict,), "places": (dict,), "research": (list,)},
    "reading_check": {"target": (str,), "state": (str,), "then": (str,), "now": (str,), "hit": OPT_BOOL,
                      "late": (list,), "reason": (str,)},
}
# 있을 때만 검사하는 칸 (1단계 재생 기록에는 없다)
OPTIONAL: dict[str, dict[str, tuple]] = {
    "score": {"counted": OPT_BOOL},
    "order": {"decided_at": (str,)},
    "prediction": {"confidence": (str,)},
    "hand_reading": {"by_asset": (dict,)},   # 종목마다 대 본 조사 상태 (2026-10-29): 종목 → 상태 → {value, why, places}
}
WHY = ("맞음", "잡음", "대 보기 틀림", "조건 흐림", "이유 틀림", "바깥 상황")
LOOK_RESULT = ("찾음", "없음", "늦음", "막힘")
SUMMARY_MAX = 300  # 조사 기록의 요약 칸 최대 글자 수 — 기사 본문을 옮기지 않는다(요약 + 링크)
DESCRIBE = {
    "run": "실행 한 번: mode(replay/live), steps(단계별 결과·실패), notes",
    "reading": "대 보기: 대상 하나의 조각 값(values: 상태 id → 예/아니오/모름)과 계산에 쓴 숫자(numbers), known_through = 본 마지막 일봉 날짜",
    "prediction": "예측: predictor(calc / hand / version:<id> / baseline:half / baseline:base_rate), 기간(봉 수), 기준 종가, 오를 확률과 범위, 움직임 크기 범위(%, 절댓값), 쓴 조각·결론을 바꾸는 조각, 이유, 논리집 지문",
    "score": "채점: 기한이 된 예측의 목표 종가와 수익률, 방향 적중(확률 0.5면 null), 확률 점수(Brier), 크기 적중",
    "order": "주문: 계좌, 대상(세 대상 또는 종목 코드), buy/sell, 수량(최소 단위 개수: ETF·종목 주, 코인 1e-8), 판단 때 가격",
    "fill": "체결: 주문 id, 체결일, 가격, 수량(현금이 모자라면 줄어듦 — note), 금액·수수료(원), 체결 뒤 현금",
    "account": "계좌 상태: 현금(원), 보유(최소 단위; 세 대상은 늘, 종목은 들고 있을 때만), 평가 가격(대상 → [날짜, 가격]; 세 대상 + 들거나 기다리는 종목), 평가액, 아직 체결 안 된 주문",
    "review": "돌아보기(2단계): 예측 id, 틀린 조각 id, 까닭(조건 흐림/이유 틀림/바깥 상황/잡음/대 보기 틀림), 이유",
    "correction": "정정: 고칠 기록 id(target)와 설명. 지난 줄은 고치지 않는다",
    "research": "조사(질문 하나): 대 볼 상태 id(열린 질문이면 ''), 각도, 질문, 답 요약, 본 곳마다 결과(찾음/없음/늦음/막힘)·새 곳 여부·링크·날짜, 맡은 에이전트",
    "hand_reading": "손 대 보기(하루 하나): 조사로 대 본 상태 값(예/아니오/일부/모름 또는 값), 상태마다 이유와 쓴 볼 곳, 근거 조사 id, (있으면) by_asset = 종목마다 대 본 상태",
    "reading_check": "대 보기 다시 보기: 지난 손 대 보기의 상태 값을 나중에 알게 된 것으로 다시 봄(맞음/틀림/모름), 판단 뒤에야 정보가 나온 볼 곳(late)",
}


def write(folder: Path, kind: str, items: list[dict]) -> None:
    """종류별 파일에 덧붙인다. 칸을 먼저 검사하고, 하나라도 틀리면 아무것도 쓰지 않는다."""
    errs = [e for i, it in enumerate(items) for e in line_errors(kind, {"kind": kind, **it}, f"{kind}+{i}")]
    if errs:
        raise ValueError("기록 칸 오류:\n" + "\n".join(errs))
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{kind}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps({"kind": kind, **it}, ensure_ascii=False, sort_keys=False) + "\n")


def read(folder: Path, kind: str) -> list[dict]:
    p = folder / f"{kind}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def line_errors(kind: str, d: dict, where: str) -> list[str]:
    errs = []
    if d.get("kind") != kind:
        errs.append(f"{where}: kind '{d.get('kind')}'가 파일 종류 '{kind}'와 다름")
    for k in ("id", "run"):
        if not isinstance(d.get(k), str) or not d.get(k):
            errs.append(f"{where}: '{k}' 칸이 비었거나 문자열이 아님")
    run = d.get("run", "")
    if isinstance(run, str) and not (len(run) == 10 and run[4] == "-" and run[7] == "-"):
        errs.append(f"{where}: run은 YYYY-MM-DD")
    for k, types in FIELDS.get(kind, {}).items():
        if k not in d:
            errs.append(f"{where}: '{k}' 칸이 없음")
        elif not isinstance(d[k], types) or (isinstance(d[k], bool) and bool not in types):
            errs.append(f"{where}: '{k}' 타입이 {[t.__name__ for t in types]}가 아님")
    if kind == "prediction" and isinstance(d.get("p_up"), NUM) and not 0 <= d["p_up"] <= 1:
        errs.append(f"{where}: p_up은 0~1")
    for k, types in OPTIONAL.get(kind, {}).items():
        if k in d and (not isinstance(d[k], types) or (isinstance(d[k], bool) and bool not in types)):
            errs.append(f"{where}: '{k}' 타입이 {[t.__name__ for t in types]}가 아님")
    if kind == "review" and d.get("why") not in WHY:
        errs.append(f"{where}: why는 {list(WHY)} 중 하나")
    if kind == "research":
        errs += _research_errors(d, where)
    if kind == "order" and d.get("side") not in ("buy", "sell"):
        errs.append(f"{where}: side는 buy/sell")
    if kind == "account" and isinstance(d.get("cash"), int) and d["cash"] < 0:
        errs.append(f"{where}: 현금이 음수")
    if kind == "fill" and isinstance(d.get("cash_after"), int) and d["cash_after"] < 0:
        errs.append(f"{where}: 체결 뒤 현금이 음수")
    return errs


def _research_errors(d: dict, where: str) -> list[str]:
    errs = []
    for k in ("asked", "answer"):
        if isinstance(d.get(k), str) and len(d[k]) > SUMMARY_MAX:
            errs.append(f"{where}: '{k}'가 {SUMMARY_MAX}자를 넘음 — 기사 본문을 옮기지 않는다(요약 + 링크)")
    for i, lk in enumerate(d.get("looked") or []):
        w = f"{where} looked[{i}]"
        if not isinstance(lk, dict):
            errs.append(f"{w}: 사전이 아님")
            continue
        if not isinstance(lk.get("place"), str) or not lk.get("place"):
            errs.append(f"{w}: place(볼 곳 id)가 없음")
        if lk.get("result") not in LOOK_RESULT:
            errs.append(f"{w}: result는 {list(LOOK_RESULT)} 중 하나")
        if not isinstance(lk.get("new"), bool):
            errs.append(f"{w}: new(새로 시도한 곳인가)는 true/false")
        if lk.get("result") in ("찾음", "늦음") and not str(lk.get("url", "")).startswith("http"):
            errs.append(f"{w}: 찾았으면 출처 링크(url)가 있어야 함")
        if isinstance(lk.get("note"), str) and len(lk["note"]) > SUMMARY_MAX:
            errs.append(f"{w}: note가 {SUMMARY_MAX}자를 넘음 — 요약만")
    return errs


def check_folder(folder: Path) -> list[str]:
    errs = []
    for p in sorted(folder.glob("*.jsonl")):
        kind = p.stem
        if kind not in FIELDS:
            errs.append(f"{p}: 모르는 기록 종류 '{kind}'")
            continue
        ids, last_run = set(), ""
        text = p.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            errs.append(f"{p}: 마지막 줄이 줄바꿈으로 끝나지 않음")
        for n, line in enumerate(text.splitlines(), 1):
            where = f"{p.name}:{n}"
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                errs.append(f"{where}: JSON이 아님 ({exc})")
                continue
            errs += line_errors(kind, d, where)
            if d.get("id") in ids:
                errs.append(f"{where}: id 중복 '{d.get('id')}'")
            ids.add(d.get("id"))
            run = d.get("run", "")
            if isinstance(run, str) and run < last_run:
                errs.append(f"{where}: run 날짜가 앞 줄({last_run})보다 이름 — 기록은 날짜 순서로 덧붙인다")
            last_run = max(last_run, run if isinstance(run, str) else "")
    return errs


# ---------- git HEAD와 비교 ----------
def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60)


def head_errors(root: Path, rel_dirs: tuple[str, ...] = ("records", "data/prices")) -> list[str]:
    """HEAD에 있던 기록 파일이 그대로 앞부분에 남아 있나 (지난 줄이 바뀌거나 지워졌으면 오류)."""
    errs = []
    try:
        listed = _git(["ls-tree", "-r", "--name-only", "HEAD", "--", *rel_dirs], root)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [f"git을 부를 수 없음({exc}) — HEAD 비교를 못 함"]
    if listed.returncode != 0:
        return []  # 커밋이 없거나 git 저장소가 아님: 비교할 것이 없다
    for name in listed.stdout.decode("utf-8").splitlines():
        if not (name.endswith(".jsonl") or name.endswith(".csv")):
            continue
        old = _git(["show", f"HEAD:{name}"], root).stdout
        p = root / name
        if not p.exists():
            errs.append(f"{name}: HEAD에 있던 기록 파일이 지워짐")
            continue
        now = p.read_bytes().replace(b"\r\n", b"\n")
        old = old.replace(b"\r\n", b"\n")
        if not now.startswith(old):
            # 처음 달라진 줄 찾기
            a, b = old.split(b"\n"), now.split(b"\n")
            k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            errs.append(f"{name}: {k + 1}번째 줄이 HEAD와 다름 — 지난 줄은 고치지 않는다(정정은 새 줄로)")
    return errs


def check(root: Path = C.ROOT, folders: list[Path] | None = None) -> list[str]:
    folders = folders or sorted({p.parent for p in (root / "records").rglob("*.jsonl")})
    errs = []
    for f in folders:
        errs += check_folder(f)
    errs += head_errors(root)
    return errs


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "check":
        print(__doc__)
        return 2
    folders = [Path(a) for a in argv[1:]] or None
    errs = check(C.ROOT, folders)
    if errs:
        print(f"기록 검사 실패 {len(errs)}건:")
        for e in errs[:200]:
            print("  -", e)
        return 1
    print("기록 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
