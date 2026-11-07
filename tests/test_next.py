"""돌아보기의 '다음엔' 검사(2026-11-07 사용자: 피드백은 '이런 문제가 있었다 → 다음에는 이렇게 해 보겠다'의 연속).
hand.next_of·open_next·검사 함수와, 가짜 매일 실행에서 record가 '다음엔' 없는 돌아보기를 거절하는지 본다."""
import datetime as dt
import json
import tempfile
import unittest
from unittest import mock

from market import daily as D
from market import hand as H
from tests.test_daily import at, fill, make_root, TICK


def rv(rid: str, run: str, reason: str) -> dict:
    return {"id": rid, "run": run, "reason": reason}


class NextUnits(unittest.TestCase):
    def test_next_of(self):
        self.assertIsNone(H.next_of("이유만"))
        self.assertEqual(H.next_of("a. 다음엔: 하나. 더. 다음엔: 둘"), "둘")

    def test_open_next(self):
        day = dt.date(2026, 11, 20)
        reviews = [rv("review:a", "2026-11-18", "틀림. 다음엔: 큰 격차면 반대 근거도 본다"),
                   rv("review:b", "2026-11-18", "맞음. 다음엔: 그대로 — 문제 없음"),
                   rv("review:c", "2026-11-01", "오래됨. 다음엔: 무엇을 한다"),
                   rv("review:d", "2026-11-19", "틀림. 다음엔: 보상 확정 여부를 묻는다"),
                   rv("review:e", "2026-11-19", "지난 다음엔 review:a → 해 봄 — 0.5에 둠. 다음엔: 그대로 — 됐다"),
                   rv("review:f", "2026-11-19", "없는 표시. 이유만")]
        runs = [{"notes": ["지난 다음엔 review:d → 안 해 봄 — 상황이 짧았다"]}]
        without_e = [r for r in reviews if r["id"] != "review:e"]
        self.assertEqual(H.open_next(without_e, [], day), {"review:a": "큰 격차면 반대 근거도 본다",
                                                            "review:d": "보상 확정 여부를 묻는다"})
        self.assertEqual(H.open_next(reviews, [], day), {"review:d": "보상 확정 여부를 묻는다"})   # e가 a를 확인함
        self.assertEqual(H.open_next(reviews, runs, day), {})                                     # notes가 d를 확인함

    def test_next_errors(self):
        self.assertTrue(H._next_errors("w", "이유만"))
        self.assertTrue(H._next_errors("w", "이유. 다음엔: 지켜본다"))
        self.assertTrue(H._next_errors("w", "이유. 다음엔: "))
        self.assertEqual(H._next_errors("w", "이유. 다음엔: 같은 조각이 켜지면 조건을 같이 적는다"), [])
        self.assertEqual(H._next_errors("w", "맞음. 다음엔: 그대로 — 조각 이유와 같았다"), [])

    def test_past_errors(self):
        opened = {"review:a": "t"}
        self.assertEqual(H._past_errors("w", "지난 다음엔 review:a → 해 봄 — 됐다", opened), [])
        self.assertEqual(H._past_errors("w", "지난 다음엔 review:a → 안 해 봄 — 까닭", opened), [])
        self.assertTrue(H._past_errors("w", "지난 다음엔 review:z → 해 봄 — 됐다", opened))   # 열린 목록에 없음
        self.assertTrue(H._past_errors("w", "지난 다음엔 review:a 해 봄", opened))            # 형식
        self.assertEqual(H._past_errors("w", "언급 없음", opened), [])


class NextInDailyRun(unittest.TestCase):
    """NEXT_SINCE를 앞당겨 가짜 날들에도 검사가 걸리게 하고 prepare → record를 돌린다."""

    def run_day(self, root, day, edit):
        self.assertEqual(D.prepare(root, day, at(day, 7, 37), fetch=False, ticker=TICK, echo=False), 0)
        tpl = json.loads((root.day_dir(day) / "input.json").read_text(encoding="utf-8"))
        inp = fill(tpl)
        edit(inp)
        p = root.day_dir(day) / "filled.json"
        p.write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
        return D.record(root, day, at(day, 8, 30), p), inp

    def test_reject_then_chain(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(H, "NEXT_SINCE", "2000-01-01"):
            root = make_root(tmp)
            days = [dt.date(2025, 9, 2) + dt.timedelta(days=i) for i in range(6)]
            nxt = lambda inp: [r.update(reason=r["reason"] + ". 다음엔: 같은 조각이 켜지면 조건을 같이 적는다")
                               for r in inp["reviews"]]
            first = None
            for d in days:
                code, inp = self.run_day(root, d, lambda inp: None)
                if inp["reviews"]:                                  # '다음엔' 없는 돌아보기 → 거절
                    self.assertEqual(code, 1, d)
                    code, inp = self.run_day(root, d, nxt)
                    self.assertEqual(code, 0, d)
                    first = d
                    break
                self.assertEqual(code, 0, d)
            self.assertIsNotNone(first, "가짜 날들에 돌아볼 예측이 생기지 않았다")
            # 다음 날: briefing에 열린 '다음엔'이 보이고, 없는 id를 가리키면 거절, 열린 id를 이으면 통과
            d = first + dt.timedelta(days=1)
            self.assertEqual(D.prepare(root, d, at(d, 7, 37), fetch=False, ticker=TICK, echo=False), 0)
            brief = next(root.day_dir(d).glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("열린 '다음엔'", brief)
            opened = H.open_next([json.loads(x) for x in (root.records / "review.jsonl").read_text(encoding="utf-8").splitlines()],
                                 [], d)
            self.assertTrue(opened)
            rid = next(iter(opened))
            self.assertIn(rid, brief)
            bad = lambda inp: (nxt(inp), inp["notes"].append("지난 다음엔 review:없음 → 해 봄 — 가짜"))
            code, _ = self.run_day(root, d, bad)
            self.assertEqual(code, 1)
            good = lambda inp: (nxt(inp), inp["notes"].append(f"지난 다음엔 {rid} → 해 봄 — 가짜로 해 봄"))
            code, _ = self.run_day(root, d, good)
            self.assertEqual(code, 0)
            reviews = [json.loads(x) for x in (root.records / "review.jsonl").read_text(encoding="utf-8").splitlines()]
            runs = [json.loads(x) for x in (root.records / "run.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertNotIn(rid, H.open_next(reviews, runs, d + dt.timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
