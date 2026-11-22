"""점검: 엔진이 LLM 없이 그래프만 보고 내리는 판단. 오류와 경고를 나눈다.

오류 (audit 실패):
- 목표를 해치는데 '대가로 받아들인다'는 기록이 없는 것.
  사실이 목표를 해치면 항상 문제다 (사실은 지금의 상태).
  상태·가설이 목표를 해치면, 그걸 부작용으로 낳는 규칙이 모두 이전 버전일 때는 지난 일로 보고 넘어간다.

경고 (실패는 아님, 다음 정리에서 처리):
- 쪼개지 않은 개정: 새 버전이 이전 버전에 없던 조건을 하나도 더하지 않았다.
  틀린 건 대개 논리가 아니라 흐리게 잡은 조건이다 → 틀리면 조건을 더해 쪼갠다(2026-10-19, Lean 방향).
  숫자만 바뀐 개정, 합친 개정, 조건을 뺀 개정을 따로 표시한다.
- 시험 수단 없음: 살아 있는 가설에 자동 검사도 판정 기록도 없다.

분야 점검 (2026-10-19): 그래프만으로는 못 보는 점검(예: 효과 조각의 방향이 기록과 맞나)은 분야가 낸다.
분야 checks.py의 AUDIT(kn) → (오류 목록, 경고 목록). 코어는 분야를 모르고, 부르는 쪽(CLI·MCP)이 넘긴다.
"""
from __future__ import annotations

import re

from .schema import KIND_LABEL, Knowledge, Node, alive, check_names


def _producers(kn: Knowledge, node: Node) -> list[Node]:
    return [r for r in kn.of("rule") if node.id in r.refs("side_effects")]


def unaccepted_harms(kn: Knowledge) -> list[str]:
    issues = []
    for node in kn.nodes.values():
        harmed = node.refs("harms")
        if not harmed or node.refs("accepted_for"):
            continue
        producers = _producers(kn, node)
        if node.kind != "fact" and producers and all(r.data.get("status") == "retired" for r in producers):
            continue
        for gid in harmed:
            goal = kn.nodes.get(gid)
            via = f" (부작용을 낳는 규칙: {', '.join(r.id for r in producers)})" if producers else ""
            issues.append(
                f"[{KIND_LABEL[node.kind]} {node.id}] {node.text}\n"
                f"  → 목표 {gid} '{goal.text if goal else '?'}'를 해치는데, 대가로 받아들인 기록이 없음{via}"
            )
    return issues


def _skeleton(text: str) -> str:
    """숫자·범위 기호를 지운 문장. 두 버전의 뼈대가 같으면 숫자만 바뀐 것."""
    return re.sub(r"[\d.,%~×–\-+·]+|만|배|분|턴|M\b", "", text).replace(" ", "")


def unsplit_revisions(kn: Knowledge) -> list[str]:
    """이전 버전 → 새 버전마다, 새 버전이 더한 조건이 없으면 경고 한 줄.

    조건 칸이 둘 다 없는 가설 표의 개정은 조건이 검사 코드에만 있어 비교하지 못한다 → 따로 센다(uncomparable).
    """
    warnings = []
    preds: dict[str, list[str]] = {}
    for node in kn.nodes.values():
        for new_id in node.refs("replaced_by"):
            preds.setdefault(new_id, []).append(node.id)
    for old in kn.nodes.values():
        for new_id in old.refs("replaced_by"):
            new = kn.nodes.get(new_id)
            if new is None or ("conditions" not in old.data and "conditions" not in new.data):
                continue
            before, after = set(old.refs("conditions")), set(new.refs("conditions"))
            if after - before:
                continue
            tags = []
            if old.kind == "rule" and new.kind == "rule" and _skeleton(old.data["method"]) == _skeleton(new.data["method"]):
                tags.append("숫자만 바뀜")
            if len(preds.get(new_id, [])) > 1:
                tags.append(f"합침: {', '.join(preds[new_id])} → {new_id}")
            if before - after:
                tags.append(f"조건을 뺌: {', '.join(sorted(before - after))}")
            tag = f" ({'; '.join(tags)})" if tags else ""
            warnings.append(f"쪼개지 않은 개정: {old.id} → {new_id} — 더한 조건 없음{tag}")
    return warnings


def uncomparable_revisions(kn: Knowledge) -> list[str]:
    """조건 칸이 양쪽 다 없어 쪼갰는지 볼 수 없는 개정 (가설 표: 조건이 문장과 검사 코드에만 있다)."""
    return [f"{old.id} → {new_id}" for old in kn.nodes.values() for new_id in old.refs("replaced_by")
            if new_id in kn.nodes and "conditions" not in old.data and "conditions" not in kn.nodes[new_id].data]


def untested(kn: Knowledge) -> list[str]:
    """살아 있는 가설 중 자동 검사도 판정 기록도 없는 것."""
    out = []
    for node in kn.claims(alive_only=True):
        if check_names(node) or kn.judgments_of(node.id):
            continue
        plan = node.data.get("test_plan")
        out.append(f"시험 수단 없음: [{KIND_LABEL[node.kind]} {node.id}] {node.text[:60]}"
                   + (f"\n  계획: {plan}" if plan else ""))
    return out


def warnings(kn: Knowledge) -> list[str]:
    return unsplit_revisions(kn) + untested(kn)


def report(kn: Knowledge, extra: tuple[list[str], list[str]] | None = None) -> str:
    """extra = 분야 점검 (오류, 경고). 분야 오류도 점검 실패다."""
    extra_errors, extra_warns = extra or ([], [])
    errors, warns = unaccepted_harms(kn) + list(extra_errors), warnings(kn) + list(extra_warns)
    lines = []
    if errors:
        lines.append(f"오류 {len(errors)}건 (점검 실패)")
        lines += ["  " + e.replace("\n", "\n  ") for e in errors]
    else:
        lines.append("오류 없음: 목표를 해치는 것은 모두 대가로 받아들인 기록이 있음")
    if warns:
        lines.append(f"경고 {len(warns)}건 (실패 아님: 쪼개지 않은 개정 {len(unsplit_revisions(kn))}, "
                     f"시험 수단 없음 {len(untested(kn))}, 분야 점검 {len(extra_warns)})")
        lines += ["  " + w.replace("\n", "\n  ") for w in warns]
    else:
        lines.append("경고 없음")
    blind = uncomparable_revisions(kn)
    if blind:
        lines.append(f"참고: 조건 칸이 없어 쪼갰는지 못 본 개정 {len(blind)}건 ({', '.join(blind)})")
    return "\n".join(lines)
