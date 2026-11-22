"""지식 파일(TOML)을 읽고 검사한다.

지식은 노드의 모음이다. 노드 종류: 목표, 상태, 사실, 관찰, 가설, 규칙.
노드끼리는 id로 서로를 가리키고, 그 참조가 그래프의 연결선이 된다.

규칙과 가설은 같은 것이다: 둘 다 "성적 붙은 가설"이고 상태는 지금 것(hypothesis)과 이전 버전(retired) 둘뿐이다.
규칙은 방법(method)이 붙은 가설이다. 표 이름 rule/hypothesis는 칸 모양만 가른다 (2026-10-19, Lean 방향).
'검증됨'이라는 상태는 없다: 성적은 자동 검사와 판정 기록(`[[judgment]]`)으로 엔진이 계산한다(score.py).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("goal", "state", "fact", "observation", "hypothesis", "rule")

KIND_LABEL = {
    "goal": "목표",
    "state": "상태",
    "fact": "사실",
    "observation": "관찰",
    "hypothesis": "가설",
    "rule": "규칙",
}

# 규칙·가설의 상태. '검증됨'은 없앴다(2026-10-19): 믿을 만한지는 성적표가 보여 준다.
STATUS = {"hypothesis": "가설", "retired": "이전 버전"}
RULE_STATUS = STATUS  # 옛 이름

CLAIMS = ("rule", "hypothesis")  # 성적을 받는 노드 (가설). 목표·상태·사실·관찰은 가설이 아니다

# 판정 출처. 순서 = 무게 (앞이 무겁다). 사용자 실사용 판정이 가장 무겁다.
SOURCES = {
    "user": "사용자 실사용",
    "replay": "실제 기록 재생",
    "measured": "실측",
    "auto": "자동 확인",
    "simulation": "시뮬레이션",
    "ai": "AI 판단",
}
VERDICTS = ("지지", "반박", "일부")

# 효과 칸(2026-10-19, 계산되는 논리): 가설·규칙이 기계가 읽는 효과를 가질 수 있다.
#   [[hypothesis.effect]] / [[rule.effect]] — on(무엇에) · op(어떻게) · range([하한, 상한]) · when(언제: 상태 id = 값) · reason(왜)
# 코어는 무엇에(on)의 이름을 모른다: 분야가 [domain] quantities로 밝힌다. 상황 값도 분야의 situation_values.
OPS = {"base": "기준값", "mul": "곱하기", "add": "더하기", "scale": "비율"}
# scale(비율, 2026-11-04): 빗댄 사례의 수 대비 이번 수(count 칸이 가리키는 기록 칸)로 곱한다. 범위 숫자가 없다 — 배수는 두 기록의 수에서 나온다
DIRS = {"up": "늘린다", "down": "줄인다"}
MAX_RATIO = 2.0  # 범위 폭 상한(상한/하한). 분야가 [domain] max_ratio로 바꿀 수 있다. 숫자만 넓혀 맞추는 것을 막는다

REQUIRED = {
    "goal": ("text",),
    "state": ("text",),
    "fact": ("text", "checked", "source"),
    "observation": ("text", "source"),
    "hypothesis": ("text", "source"),
    "rule": ("method", "reason", "status", "version"),
}

# (노드 종류, 필드) → 그 필드가 가리킬 수 있는 노드 종류
REFS = {
    ("goal", "parent"): {"goal"},
    ("state", "serves"): {"goal"},
    ("rule", "serves"): {"goal"},
    ("rule", "conditions"): {"state", "fact", "observation"},
    ("rule", "results"): {"state"},
    ("rule", "evidence"): {"observation", "fact"},
    ("rule", "against"): {"observation", "fact"},
    ("rule", "side_effects"): {"hypothesis", "state"},
    ("rule", "exceptions"): {"state"},
    ("rule", "replaced_by"): {"rule", "hypothesis"},
    ("hypothesis", "conditions"): {"state", "fact", "observation"},
    ("hypothesis", "evidence"): {"observation", "fact"},
    ("hypothesis", "against"): {"observation", "fact"},
    ("hypothesis", "harms"): {"goal"},
    ("hypothesis", "accepted_for"): {"goal"},
    ("hypothesis", "replaced_by"): {"rule", "hypothesis"},
    ("state", "harms"): {"goal"},
    ("state", "accepted_for"): {"goal"},
    ("fact", "harms"): {"goal"},
    ("fact", "accepted_for"): {"goal"},
}


@dataclass
class Node:
    kind: str
    id: str
    data: dict

    @property
    def text(self) -> str:
        return self.data.get("text") or self.data.get("method", "")

    def refs(self, field: str) -> list[str]:
        value = self.data.get(field, [])
        return [value] if isinstance(value, str) else list(value)


@dataclass
class Knowledge:
    domain: dict
    nodes: dict[str, Node]
    duplicates: list[str] = field(default_factory=list)
    judgments: list[dict] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)  # 읽은 파일 이름들 (knowledge.toml + 나눈 파일 knowledge-*.toml)
    load_errors: list[str] = field(default_factory=list)

    def of(self, kind: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def claims(self, alive_only: bool = False) -> list[Node]:
        """규칙과 가설(성적을 받는 노드). alive_only면 이전 버전은 뺀다."""
        return [n for n in self.nodes.values() if n.kind in CLAIMS and (alive(n) or not alive_only)]

    def judgments_of(self, node_id: str) -> list[dict]:
        return [j for j in self.judgments if j.get("of") == node_id]


def alive(node: Node) -> bool:
    """지금 쓰는 가설인가 (이전 버전이 아닌가)."""
    return node.kind in CLAIMS and node.data.get("status", "hypothesis") != "retired"


def check_names(node: Node) -> list[str]:
    """가설에 붙은 자동 검사 이름들 (check 칸: 문자열 하나 또는 목록)."""
    return node.refs("check")


PART_GLOB = "knowledge-*.toml"  # 나눈 파일 (2026-11-04): knowledge.toml 옆에 두면 같이 읽는다
MAIN_ONLY = ("domain", "goal", "state")  # 나눈 파일에는 두지 않는다 — 분야의 뼈대는 knowledge.toml 한 곳에


def load(path: Path) -> Knowledge:
    """분야 지식을 읽는다. path가 knowledge.toml이면 같은 폴더의 knowledge-*.toml(나눈 파일)도 이름 순서로 읽어 합친다.

    나눈 파일에는 사실·관찰·가설·규칙·판정만 둔다([domain]·목표·상태는 knowledge.toml에만). 어기면 validate 오류.
    id는 모든 파일을 통틀어 하나여야 하고(중복은 validate 오류), 참조도 파일을 넘어 이어진다.
    """
    files = [path]
    if path.name == "knowledge.toml":
        files += sorted(path.parent.glob(PART_GLOB))
    nodes: dict[str, Node] = {}
    duplicates, judgments, errors = [], [], []
    domain: dict = {}
    for i, f in enumerate(files):
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
        if i == 0:
            domain = raw.get("domain", {})
        else:
            for key in MAIN_ONLY:
                if key in raw:
                    errors.append(f"{f.name}: 나눈 파일에 [{key}]가 있음 — domain·goal·state는 {path.name}에만 둔다")
        for kind in KINDS:
            for item in raw.get(kind, []):
                node = Node(kind, item.get("id", ""), item)
                if node.id in nodes:
                    duplicates.append(node.id)
                nodes[node.id] = node
        judgments += list(raw.get("judgment", []))
    return Knowledge(domain, nodes, duplicates, judgments, [f.name for f in files], errors)


def validate(kn: Knowledge) -> list[str]:
    """문제 목록을 돌려준다. 비어 있으면 통과."""
    errors = list(kn.load_errors) + [f"id 중복: {d}" for d in kn.duplicates]
    for node in kn.nodes.values():
        if not node.id:
            errors.append(f"{KIND_LABEL[node.kind]}에 id가 없음: {node.data}")
        for field in REQUIRED[node.kind]:
            if field not in node.data:
                errors.append(f"{node.id}: '{field}' 칸이 비어 있음")
        if node.kind in CLAIMS:
            errors += _claim_errors(kn, node)
            errors += _effect_errors(kn, node)
        if node.kind == "state" and "known_before" in node.data and not isinstance(node.data["known_before"], bool):
            errors.append(f"{node.id}: known_before는 true/false여야 함")
        for (kind, field), allowed in REFS.items():
            if kind != node.kind:
                continue
            for ref in node.refs(field):
                target = kn.nodes.get(ref)
                if target is None:
                    errors.append(f"{node.id}.{field}: 없는 id '{ref}'")
                elif target.kind not in allowed:
                    errors.append(
                        f"{node.id}.{field}: '{ref}'는 {KIND_LABEL[target.kind]}인데 "
                        f"{'/'.join(KIND_LABEL[k] for k in allowed)}만 올 수 있음"
                    )
    errors += _goal_cycles(kn)
    errors += _judgment_errors(kn)
    return errors


def _claim_errors(kn: Knowledge, node: Node) -> list[str]:
    errors = []
    status = node.data.get("status", "hypothesis" if node.kind == "hypothesis" else None)
    if status not in STATUS:
        hint = " ('verified'는 2026-10-19에 없앴다: 성적표로 본다)" if status == "verified" else ""
        errors.append(f"{node.id}: status는 {list(STATUS)} 중 하나여야 함{hint}")
    if status == "retired" and not node.refs("replaced_by"):
        errors.append(f"{node.id}: 이전 버전(retired)인데 무엇이 대신하는지(replaced_by)가 없음")
    if "superseded_by" in node.data:
        errors.append(f"{node.id}: superseded_by는 없앴다 → status = \"retired\" + replaced_by")
    if "check" in node.data and not all(isinstance(c, str) and c for c in node.refs("check")):
        errors.append(f"{node.id}: check는 검사 이름(문자열) 하나나 목록이어야 함")
    # 쪼갤 때 더한 조건은 그래프에 이어지게 상태(또는 사실) 노드여야 한다
    for new_id in node.refs("replaced_by"):
        new = kn.nodes.get(new_id)
        if new is None:
            continue
        for cond in set(new.refs("conditions")) - set(node.refs("conditions")):
            target = kn.nodes.get(cond)
            if target is not None and target.kind not in ("state", "fact"):
                errors.append(f"{new_id}: 새 버전이 더한 조건 '{cond}'는 상태·사실 노드여야 함 ({KIND_LABEL[target.kind]})")
    return errors


def effects(node: Node) -> list[dict]:
    """가설·규칙의 효과 목록 (없으면 빈 목록)."""
    value = node.data.get("effect", [])
    return [value] if isinstance(value, dict) else list(value)


def when_items(effect: dict) -> list[tuple[str, list[str]]]:
    """효과의 조건: (상태 id, 맞는 값 목록). 값 앞의 '!'는 '그 값이 아님'."""
    out = []
    for sid, value in (effect.get("when") or {}).items():
        out.append((sid, [value] if isinstance(value, str) else list(value)))
    return out


def _effect_errors(kn: Knowledge, node: Node) -> list[str]:
    """효과 칸 검사. 지킬 장치: 이유 없는 숫자 금지(B), 보내기 전에 알 수 있는 조건만(C),
    방향이 있는 조건 조각(A), 범위 폭 상한(G)."""
    effs = effects(node)
    if not effs:
        return []
    errors = []
    quantities = kn.domain.get("quantities")
    values = kn.domain.get("situation_values")
    max_ratio = float(kn.domain.get("max_ratio", MAX_RATIO))
    reason = node.data.get("reason", "")
    if not (isinstance(reason, str) and reason.strip()):
        errors.append(f"{node.id}: 효과가 있는데 이유(reason)가 비어 있음 — 이유 없는 숫자는 안 된다")
    if not quantities:
        errors.append(f"{node.id}: 효과가 있는데 분야가 [domain] quantities(효과가 작용하는 것)를 밝히지 않음")
    conds = set(node.refs("conditions"))
    for i, e in enumerate(effs, 1):
        where = f"{node.id} 효과 {i}"
        if quantities and e.get("on") not in quantities:
            errors.append(f"{where}: on은 {quantities} 중 하나여야 함")
        op = e.get("op")
        if op not in OPS:
            errors.append(f"{where}: op는 {list(OPS)} 중 하나여야 함")
        rng = e.get("range")
        ok_range = (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng)
                    and 0 < rng[0] <= rng[1])
        if op == "scale":  # 비율 조각: 숫자가 없다. 어느 수(count)로 비를 내는지만 적는다
            if rng is not None:
                errors.append(f"{where}: 비율(scale) 조각은 range가 없다 — 배수는 빗댄 사례의 수 대비 이번 수에서 나온다")
            if not (isinstance(e.get("count"), str) and e["count"].strip()):
                errors.append(f"{where}: 비율(scale) 조각은 count(비를 낼 기록 칸 이름)가 있어야 함")
            if e.get("dir") is not None:
                errors.append(f"{where}: 비율(scale) 조각은 방향 주장(dir)이 없다 — 방향은 두 수의 비가 정한다")
        elif not ok_range:
            errors.append(f"{where}: range는 [하한, 상한] (0 < 하한 ≤ 상한)이어야 함")
        elif rng[1] / rng[0] > max_ratio + 1e-9:
            errors.append(f"{where}: 범위 폭 {rng[1] / rng[0]:.2f}배가 상한 {max_ratio:g}배를 넘음 — 넓혀서 맞추지 말고 조건을 찾는다")
        if not (isinstance(e.get("reason"), str) and e["reason"].strip()):
            errors.append(f"{where}: 이유(reason)가 비어 있음 — 왜 이 조건이 이 차이를 만드나")
        items = when_items(e)
        if op in ("mul", "add") and not e.get("analog"):
            if not items:
                errors.append(f"{where}: 곱하기·더하기 조각은 조건(when)이 있어야 함")
            direction = e.get("dir")
            if direction not in DIRS:
                errors.append(f"{where}: 조건 조각은 방향 주장(dir = up/down)이 있어야 함")
            elif ok_range:
                if op == "mul" and not ((direction == "up" and rng[0] >= 1 and rng[1] > 1)
                                        or (direction == "down" and rng[1] <= 1 and rng[0] < 1)):
                    errors.append(f"{where}: 방향 '{direction}'와 범위 {rng}가 어긋남 (곱하기 범위가 1을 넘나들면 방향이 없다)")
                if op == "add" and direction != "up":
                    errors.append(f"{where}: 더하기 조각의 방향은 up이어야 함")
        if e.get("analog") and op != "base":
            errors.append(f"{where}: 비유(analog) 조각은 기준값(base)이어야 함 — 빗댄 기록의 실측에 범위를 곱한다")
        for sid, vals in items:
            target = kn.nodes.get(sid)
            if target is None or target.kind != "state":
                errors.append(f"{where}: 조건 '{sid}'는 상태 노드여야 함")
                continue
            if target.data.get("known_before") is not True:
                errors.append(f"{where}: 조건 '{sid}'는 보내기 전에 알 수 있는 상태가 아님(known_before = true 없음)")
            if sid not in conds:
                errors.append(f"{where}: 조건 '{sid}'가 노드의 conditions에 없음 (그래프에 이어지게 적는다)")
            if values:
                for v in vals:
                    if v.lstrip("!") not in values:
                        errors.append(f"{where}: '{sid}'의 값 '{v}'는 {values} 중 하나여야 함 (앞에 !는 '아님')")
    return errors


def _judgment_errors(kn: Knowledge) -> list[str]:
    """판정 기록: 어떤 가설을(of), 누가(source), 어떻게 봤나(verdict)."""
    errors = []
    for i, j in enumerate(kn.judgments, 1):
        where = f"판정 {i}번째({j.get('of', '?')})"
        target = kn.nodes.get(j.get("of", ""))
        if target is None or target.kind not in CLAIMS:
            errors.append(f"{where}: of는 규칙·가설 id여야 함")
        if j.get("source") not in SOURCES:
            errors.append(f"{where}: source는 {list(SOURCES)} 중 하나여야 함")
        if j.get("verdict") not in VERDICTS:
            errors.append(f"{where}: verdict는 {list(VERDICTS)} 중 하나여야 함")
        if "date" not in j or not j.get("note"):
            errors.append(f"{where}: date와 note(무엇을 보고 그렇게 판정했나)가 있어야 함")
        for ref in ([j["evidence"]] if isinstance(j.get("evidence"), str) else j.get("evidence", [])):
            node = kn.nodes.get(ref)
            if node is None or node.kind not in ("observation", "fact"):
                errors.append(f"{where}: evidence '{ref}'는 관찰·사실 id여야 함")
    return errors


def _goal_cycles(kn: Knowledge) -> list[str]:
    errors = []
    for goal in kn.of("goal"):
        seen, cur = set(), goal
        while cur is not None:
            if cur.id in seen:
                errors.append(f"목표 위계가 돌고 돎: {goal.id}")
                break
            seen.add(cur.id)
            parent = cur.data.get("parent")
            cur = kn.nodes.get(parent) if parent else None
    return errors
