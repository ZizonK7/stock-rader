"""하위 조사 에이전트 정의(.claude/agents/stock-rader-research.md, 2026-11-22 ask:2026-11-22:2).

예약 실행의 하위 에이전트가 지시('원격 도구를 부르지 않는다')를 받고도 세션 보관·문서 읽기·PR 구독·저장소 신뢰 확인 같은
원격 도구(mcp__…)를 불러, 도구 목록을 정의로 묶었다. 이 시험은 그 목록이 넓어지지 않고 지시문이 그 종류로 보내는지를 지킨다.
"""
import re
import unittest

from market import config as C

AGENT = C.ROOT / ".claude" / "agents" / "stock-rader-research.md"
ALLOWED = {"Bash", "Read", "Write", "WebSearch", "WebFetch"}


def front(path) -> dict:
    """맨 위 --- 사이의 '키: 값' 줄."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in m.group(1).splitlines() if ":" in ln)}


class ResearchAgent(unittest.TestCase):
    def test_tools_are_exactly_the_five(self):
        fm = front(AGENT)
        self.assertEqual(fm.get("name"), "stock-rader-research")
        tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
        self.assertEqual(tools, ALLOWED)   # 비면 모든 도구(원격 도구 포함)를 물려받으므로 빈 목록도 실패
        self.assertFalse(any(t.startswith("mcp__") for t in tools))

    def test_morning_prompt_sends_every_kind_with_it(self):
        text = (C.ROOT / "docs" / "routine-prompt.md").read_text(encoding="utf-8")
        # 대상마다 아침 조사 · 원칙 줄 · 08:40 다시 보기 · 조사 넷 · 2차 조사
        self.assertGreaterEqual(text.count('subagent_type: "stock-rader-research"'), 5)


if __name__ == "__main__":
    unittest.main()
