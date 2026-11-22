"""성적표: 모든 살아 있는 가설(규칙 포함)이 지금까지 받은 시험을 출처별로 모은다.

성적은 두 가지를 합친다.
  (i)  자동 검사 — 분야의 checks.py 함수(가설의 check 칸). 분야가 SOURCE로 출처를 밝힌다(실측·시뮬레이션 등).
       SOURCE는 문자열 하나(분야 전체) 또는 {검사 이름: 출처}(검사마다, "*" = 나머지)다.
       검사 함수가 출처별로 따로 판정하려면 (출처, 판정 문장, 자세히) 묶음의 목록을 돌려준다(2026-10-21,
       stock-rader: 같은 검사를 재생 기록과 실측 기록에 따로 돌려 따로 보인다). 예전처럼 (판정 문장, 자세히)를
       돌려주면 SOURCE의 출처 하나로 친다.
       가설은 세운 뒤에 나온 기록으로만 시험한다(분야 검사가 책임진다, 예: agent_usage의 SEEN_UNTIL).
  (ii) 판정 기록 — 지식 파일의 [[judgment]] (사용자 실사용·실제 기록 재생·시뮬레이션·AI 판단).
출처는 따로 보이고, 무거운 출처(schema.SOURCES 순서, 사용자 실사용이 가장 무겁다)부터 늘어선다.
'종합'은 판정이 난 가장 무거운 출처의 것이다. 다른 출처와 갈리면 갈렸다고 적는다.

코어는 분야를 모른다: 검사 함수 모음(checks)과 그 출처(source)는 부르는 쪽이 넘긴다.
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass

from .schema import CLAIMS, KIND_LABEL, SOURCES, STATUS, Knowledge, Node, alive, check_names

DECIDED = ("지지", "반박", "일부")


@dataclass
class Line:
    source: str     # schema.SOURCES의 키
    name: str       # 검사 이름 또는 판정 날짜
    verdict: str    # 지지 / 반박 / 일부 / 시험 전 / 불명
    text: str       # 사람이 읽을 한 줄 (검사의 판정 문장 또는 판정 메모)
    detail: str = ""


def normalize(verdict: str) -> str:
    """검사 함수의 판정 문장('지지됨 (시험 9건 중 8건 맞음)' 등)을 다섯 낱말로."""
    for word, norm in (("지지", "지지"), ("반박", "반박"), ("일부", "일부"), ("시험 전", "시험 전")):
        if verdict.startswith(word):
            return norm
    return "불명"


Source = str | dict[str, str]


def run_check(fn: Callable, node: Node):
    """검사 함수는 인자 없이(가설 하나의 검사) 또는 노드 id 하나를 받는다(여러 규칙에 같이 쓰는 검사).
    돌려주는 것: (판정 문장, 자세히) 또는 [(출처, 판정 문장, 자세히), ...] — results()가 하나로 맞춘다."""
    return fn(node.id) if inspect.signature(fn).parameters else fn()


def source_of(source: Source, name: str) -> str:
    """검사 이름의 기본 출처. source가 사전이면 검사마다, 없으면 "*", 그것도 없으면 'auto'."""
    if isinstance(source, dict):
        return source.get(name, source.get("*", "auto"))
    return source


def results(raw, default: str) -> list[tuple[str, str, str]]:
    """검사 결과를 (출처, 판정 문장, 자세히) 목록으로. 예전 모양 (판정, 자세히)는 기본 출처 하나."""
    if isinstance(raw, tuple):
        raw = [raw]
    out = []
    for item in raw:
        if len(item) == 2:
            out.append((default, item[0], item[1]))
        elif len(item) == 3:
            out.append(tuple(item))
        else:
            raise ValueError(f"검사 결과 모양이 틀림: {item!r} — (판정, 자세히) 또는 (출처, 판정, 자세히)")
    return out


def card(kn: Knowledge, node: Node, checks: dict[str, Callable], source: Source = "auto") -> list[Line]:
    """노드 하나의 성적 줄들. 무거운 출처부터. 한 검사가 출처 여럿으로 판정하면 이름에 출처를 붙인다."""
    lines = []
    for name in check_names(node):
        fn = checks.get(name)
        if fn is None:
            lines.append(Line(source_of(source, name), name, "불명", f"검사 '{name}'가 분야 checks.py에 없음"))
            continue
        got = results(run_check(fn, node), source_of(source, name))
        for src, verdict, detail in got:
            shown = f"{name}·{SOURCES.get(src, src)}" if len(got) > 1 else name
            lines.append(Line(src, shown, normalize(verdict), verdict, detail))
    for j in kn.judgments_of(node.id):
        lines.append(Line(j["source"], str(j.get("date", "")), j["verdict"], j["note"]))
    order = list(SOURCES)
    lines.sort(key=lambda l: order.index(l.source) if l.source in order else len(order))
    return lines


def summary(lines: list[Line]) -> str:
    """종합: 판정이 난 가장 무거운 출처의 판정. 시험이 없으면 '시험 전' 또는 '시험 수단 없음'."""
    if not lines:
        return "시험 수단 없음"
    decided = [l for l in lines if l.verdict in DECIDED]
    if not decided:
        return "시험 전"
    top = decided[0].source
    mine = [l.verdict for l in decided if l.source == top]
    counts = " · ".join(f"{v} {mine.count(v)}" for v in DECIDED if mine.count(v))
    others = sorted({l.verdict for l in decided if l.source != top} - set(mine))
    split = f" (다른 출처에는 {'/'.join(others)}도 있음)" if others else ""
    return f"{SOURCES.get(top, top)} 기준 {counts}{split}"


def has_means(node: Node, kn: Knowledge) -> bool:
    """시험 수단이 있나: 자동 검사가 붙었거나 판정 기록이 하나라도 있다."""
    return bool(check_names(node)) or bool(kn.judgments_of(node.id))


def head(node: Node) -> str:
    d = node.data
    status = STATUS.get(d.get("status", "hypothesis"), "?")
    if node.kind == "hypothesis":
        return f"{node.id} [가설{'' if status == '가설' else ' · ' + status}]"
    return f"{node.id} [규칙 v{d.get('version', '?')} · {status}]"


def tally(line: Line) -> str:
    """한눈 표에 넣을 짧은 성적. 검사는 '이름 맞음/시험'(검사 문장의 'N건 중 … M건'), 판정 기록은 '출처 판정'."""
    if line.name[:1].isdigit():  # 판정 기록 (이름 자리에 날짜)
        return f"{SOURCES.get(line.source, line.source)} {line.verdict}"
    m = re.search(r"(\d+)건 중 (?:\S+ )*?(\d+)건", line.text)
    return f"{line.name} {m.group(2)}/{m.group(1)}" if m else f"{line.name} {line.verdict}"


def report(kn: Knowledge, checks: dict[str, Callable], source: Source = "auto") -> str:
    """check 명령의 출력: 살아 있는 가설마다 성적표, 맨 끝에 한눈 표. 이전 버전은 무엇이 대신하는지만."""
    out, table, shown = [], [], {}
    for node in kn.nodes.values():
        if node.kind not in CLAIMS:
            continue
        if not alive(node):
            old = "; ".join(f"{l.name} {l.text}" for l in card(kn, node, checks, source))
            out.append(f"{head(node)} → 대신하는 것: {', '.join(node.refs('replaced_by'))}"
                       + (f" (옛 판정: {old})" if old else ""))
            continue
        lines = card(kn, node, checks, source)
        total = summary(lines)
        table.append((node.id, KIND_LABEL[node.kind], total, " · ".join(tally(l) for l in lines) or "–"))
        out.append(f"{head(node)} {_short(node.text)}")
        out.append(f"  종합: {total}")
        if not lines:
            plan = node.data.get("test_plan")
            out.append(f"  시험 수단 없음 (자동 검사도 판정도 없음){' — 계획: ' + plan if plan else ''}")
        for l in lines:
            star = "★ " if l.source == "user" else ""
            out.append(f"  {star}{SOURCES.get(l.source, l.source)} · {l.name}: {l.text}")
            if l.detail:
                if l.detail in shown:
                    out.append(f"    (기록 목록은 위 {shown[l.detail]}에)")
                else:
                    shown[l.detail] = node.id
                    out.append(l.detail)
    out.append("")
    out.append("한눈에 (살아 있는 가설)")
    out.append("| id | 표 | 종합 | 시험별 (맞음/시험) |\n|---|---|---|---|")
    out += [f"| {i} | {k} | {t} | {c} |" for i, k, t, c in table]
    return "\n".join(out)


def _short(text: str, width: int = 70) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
