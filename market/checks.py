"""stock-rader 논리집의 자동 검사 (PROVISO 성적표·점검이 부른다: python -m market.logic check|audit).

PROVISO 분야 checks.py와 같은 모양: CHECKS(검사 이름 → 함수), SOURCE(판정 출처), SEEN_UNTIL, AUDIT(kn) → (오류, 경고).
판정은 시장 결과(채점 기록)로 한다. 가설은 **세운 뒤의 날들로만** 시험한다: 예측의 기준 날짜가 조각의 seen_until(맞출 때 본
마지막 시세 날짜) 뒤인 것만. 기록은 records/replay/(재생, 홀드아웃)와 records/(진짜 계좌, 클라우드 예약 실행)에서 읽는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config as C
from . import fit as F
from . import records as R

# 이 분야의 자동 검사 출처: 지금은 재생(홀드아웃 기간을 날짜 순서로 다시 돈 것)뿐이라 'replay'.
# 진짜 계좌 기록이 쌓이면 'measured'(시장 실측)로 바꾸고 재생과 따로 보여 준다.
SOURCE = "replay"
SEEN_UNTIL = {"piece_direction": "2026-06-30", "piece_size": "2026-06-30", "account_vs_equal": "2026-06-30"}
NEED = 0.8
MIN_SIDE = 5      # 방향 비교: 조건 있음·없음이 각각 이만큼은 있어야 그 칸을 센다
MAX_PIECES = 8    # 장치(조각 수 상한): 살아 있는 계산 조각 수


def _folders() -> list[tuple[str, Path]]:
    return [("재생", C.REPLAY), ("진짜 계좌", C.RECORDS)]


def _scored(cut: str) -> list[tuple[str, dict, dict]]:
    """(출처 이름, 예측, 채점) — 기준 날짜가 cut 뒤인 것만."""
    out = []
    for name, folder in _folders():
        preds = {p["id"]: p for p in R.read(folder, "prediction")}
        for s in R.read(folder, "score"):
            p = preds.get(s["prediction"])
            if p and p["base_date"] > cut:
                out.append((name, p, s))
    return out


def _node(node_id: str) -> dict | None:
    import tomllib
    raw = tomllib.loads(C.KNOWLEDGE.read_text(encoding="utf-8"))
    return next((h for h in raw.get("hypothesis", []) + raw.get("rule", []) if h.get("id") == node_id), None)


def piece_direction(node_id: str) -> tuple[str, str]:
    """조각의 방향 주장(곱하기 효과의 dir)이 세운 뒤의 날들에서 맞나: 대상·기간마다 조건 '예'일 때와 '아니오'일 때를 비교.
    오를 확률은 오른 비율, 움직임은 |수익률| 가운데 값. 겹치는 기간의 표본이라 거친 비교다."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", "9999")
    state = (node.get("conditions") or [None])[0]
    claims = [c for c in node.get("claims", []) if c.get("op", "mul") == "mul"]
    if not state or not claims:
        return "시험 전 (방향 주장이 없는 기준값 조각)", ""
    cells: dict = {}
    for _, p, s in _scored(cut):
        if p["predictor"] != "calc":
            continue
        v = p["readings"].get(state)
        if v not in ("예", "아니오"):
            continue
        cells.setdefault((p["asset"], p["horizon"]), {"예": [], "아니오": []})[v].append(s["ret"])
    lines, verdicts = [], []
    for c in claims:
        for (asset, h), cell in sorted(cells.items()):
            y, n = cell["예"], cell["아니오"]
            if len(y) < MIN_SIDE or len(n) < MIN_SIDE:
                lines.append(f"    {c['on']} {asset}:{h} 예 {len(y)} / 아니오 {len(n)} — 수가 모자라 안 셈")
                continue
            if c["on"] == "up_odds":
                a, b = sum(r > 0 for r in y) / len(y), sum(r > 0 for r in n) / len(n)
                shown = f"오른 비율 예 {a:.2f} vs 아니오 {b:.2f}"
            else:
                a = sorted(abs(r) for r in y)[len(y) // 2]
                b = sorted(abs(r) for r in n)[len(n) // 2]
                shown = f"움직임 가운데 값 예 {a * 100:.2f}% vs 아니오 {b * 100:.2f}%"
            ok = (a > b) if c.get("dir") == "up" else (a < b)
            verdicts.append(ok)
            lines.append(f"    {c['on']} {asset}:{h} {shown} → {'맞음' if ok else '틀림 ✗'} (예 {len(y)}, 아니오 {len(n)})")
    detail = f"  시험({cut} 뒤 기준 날짜의 계산기 예측):\n" + ("\n".join(lines) or "    없음")
    if not verdicts:
        return "시험 전 (세운 뒤 기록에 조건 있음·없음이 아직 모자람)", detail
    hits = sum(verdicts)
    word = "지지됨" if hits / len(verdicts) >= NEED else "반박됨"
    return f"{word} (방향 시험 {len(verdicts)}건 중 {hits}건 맞음)", detail


def piece_size(node_id: str) -> tuple[str, str]:
    """이 조각의 움직임 효과가 쓰인 계산기 예측의 크기 적중이, 같은 날·대상·기간의 기준선(최근 비율) 크기 적중보다 나은가."""
    node = _node(node_id)
    if not node:
        return "불명 (노드 없음)", ""
    cut = node.get("seen_until", "9999")
    rows = _scored(cut)
    base = {(p["run"], p["asset"], p["horizon"]): s for _, p, s in rows if p["predictor"] == "baseline:base_rate"}
    mine, theirs = [], []
    for _, p, s in rows:
        if p["predictor"] != "calc" or s["size_hit"] is None or f"{node_id}:move" not in p["used"]:
            continue
        b = base.get((p["run"], p["asset"], p["horizon"]))
        if b is None or b["size_hit"] is None:
            continue
        mine.append(s["size_hit"])
        theirs.append(b["size_hit"])
    if not mine:
        return "시험 전 (이 조각의 움직임 효과가 쓰인 채점이 없음)", ""
    detail = f"  같은 {len(mine)}건: 계산기 크기 적중 {sum(mine)} vs 기준선(최근 250봉 비율) {sum(theirs)}"
    word = "지지됨" if sum(mine) >= sum(theirs) else "반박됨"
    return f"{word} (크기 시험 {len(mine)}건 중 {sum(mine)}건 맞음, 기준선 {sum(theirs)}건)", detail


def account_vs_equal() -> tuple[str, str]:
    """R_weights: 계산기를 따른 계좌가 셋을 나눠 사 두기보다 나은가 (세운 뒤의 날들, 마지막 평가액)."""
    for name, folder in _folders():
        accs = [a for a in R.read(folder, "account") if a["run"] > SEEN_UNTIL["account_vs_equal"]]
        mine = [a for a in accs if a["account"] == "replay:calc"]
        eq = [a for a in accs if a["account"] == "baseline:equal_hold"]
        if mine and eq:
            m, e = mine[-1]["value"], eq[-1]["value"]
            detail = f"  {name} {mine[-1]['run']}: 계산기 계좌 {m:,.0f}원 vs 나눠 사 두기 {e:,.0f}원"
            return f"{'지지됨' if m >= e else '반박됨'} (시험 1건 중 {1 if m >= e else 0}건 맞음)", detail
    return "시험 전 (계좌 기록 없음)", ""


CHECKS = {"piece_direction": piece_direction, "piece_size": piece_size, "account_vs_equal": account_vs_equal}


# ---------- 점검 (audit의 분야 점검) ----------
def _names_in(text: str) -> list[str]:
    return [w for w in C.ASSET_WORDS if re.search(re.escape(w), text, re.IGNORECASE)]


def audit(kn, want: dict[str, list[dict]] | None = None) -> tuple[list[str], list[str]]:
    """오류: 조각 수 상한, 조각 이름·문장의 대상 이름, 기록에서 맞춘 값과 다른 효과(손으로 넣은 숫자), 효과 칸 모양.
    경고: 세운 효과가 하나도 없는 조각, 세운 뒤 기록에서 방향이 틀린 조각(돌아보기 대상).
    want = 맞추기를 다시 돌린 결과(조각 id → 효과 목록). 없으면 지금 시세로 다시 맞춘다(테스트는 직접 넘긴다)."""
    errors, warns = [], []
    live = [n for n in kn.claims(alive_only=True) if n.data.get("claims")]
    if len(live) > MAX_PIECES:
        errors.append(f"[계산 조각] 살아 있는 조각 {len(live)}개가 상한 {MAX_PIECES}개를 넘음 — 외워서 맞추는 쪽이다")
    for node in list(live) + kn.of("state"):
        text = " ".join(str(node.data.get(k, "")) for k in ("text", "reason")) + " " + node.id
        text += " ".join(c.get("reason", "") for c in node.data.get("claims", []))
        found = _names_in(text)
        if found:
            errors.append(f"[계산 조각] {node.id}: 이름·문장·이유에 대상 이름({', '.join(found)})이 있음 — 다른 대상에서 다시 쓰게 빼야 한다")
    for node in live:
        if not node.data.get("seen_until"):
            errors.append(f"[계산 조각] {node.id}: seen_until(맞출 때 본 마지막 날짜)이 없음")
        seen = set()
        for e in node.data.get("effect", []):
            key = (e.get("on"), e.get("asset"), e.get("horizon"))
            if e.get("asset") not in C.ASSETS or e.get("horizon") not in C.ASSETS.get(e.get("asset"), C.ASSETS["069500"]).horizons:
                errors.append(f"[계산 조각] {node.id}: 효과의 대상·기간 {key[1]}:{key[2]}이 config에 없음")
            if key in seen:
                errors.append(f"[계산 조각] {node.id}: 같은 대상·기간·값에 효과가 둘 {key}")
            seen.add(key)
        if not node.data.get("effect"):
            warns.append(f"[계산 조각] {node.id}: 세운 효과가 하나도 없음 — 모든 대상·기간에서 맞춘 시세가 방향을 받쳐 주지 않았다(not_set)")
    if want is None:
        try:
            want = F.expected()
        except Exception as exc:  # 시세 파일이 없는 등
            return errors + [f"[계산 조각] 맞추기를 다시 돌릴 수 없음: {exc}"], warns
    for node in live:
        have = node.data.get("effect", [])
        need = want.get(node.id, [])
        if _norm(have) != _norm(need):
            errors.append(f"[계산 조각] {node.id}: 효과 칸이 시세에서 맞춘 값과 다름 — 숫자는 손으로 넣거나 넓히지 않는다"
                          f"(python -m market.fit --write)")
    for node in live:
        if "piece_direction" in node.refs("check"):
            verdict, _ = piece_direction(node.id)
            if verdict.startswith("반박"):
                warns.append(f"[계산 조각] {node.id}: 세운 뒤 기록에서 방향이 틀림({verdict}) — 돌아보기에서 까닭(조건 흐림/이유 틀림/바깥 상황)을 가른다")
    return errors, warns


def _norm(effs: list[dict]) -> list[tuple]:
    return sorted((e.get("on"), e.get("op"), str(e.get("asset")), e.get("horizon"), tuple(float(x) for x in e.get("range", [])),
                   tuple(sorted((e.get("when") or {}).items())), e.get("dir")) for e in effs)


AUDIT = audit
