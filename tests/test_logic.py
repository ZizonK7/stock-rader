"""논리집 장치 (PROVISO 코어 + market/checks.py AUDIT): 이유 없는 숫자, 판단 전 값만 조건, 범위 폭 상한,
맞춘 값과 다른 범위, 조각 수 상한, 조각 문장의 대상 이름. PROVISO-Nesy가 없으면 건너뛴다."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from market import checks as CH
from market import config as C
from market import logic

try:
    SCHEMA, SCORE, AUDIT = logic.core()
except SystemExit:
    SCHEMA = None

TEXT = C.KNOWLEDGE.read_text(encoding="utf-8")


def fitted_now() -> dict:
    """지금 지식 파일의 효과 칸 = '맞춘 값'으로 친다(맞추기 자체는 test_calc_fit이 시험한다)."""
    raw = tomllib.loads(TEXT)
    return {h["id"]: h.get("effect", []) for h in raw.get("hypothesis", []) if h.get("claims")}


@unittest.skipIf(SCHEMA is None, "PROVISO-Nesy를 찾지 못함 (PROVISO_PATH)")
class Devices(unittest.TestCase):
    def kn(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.toml"
            p.write_text(text, encoding="utf-8")
            return SCHEMA.load(p)

    def test_real_knowledge_validates_and_audits_clean(self):
        kn = SCHEMA.load(C.KNOWLEDGE)
        self.assertEqual(SCHEMA.validate(kn), [])
        errors, _ = CH.audit(kn, fitted_now())
        self.assertEqual(errors, [])
        self.assertEqual(AUDIT.unaccepted_harms(kn), [])

    def test_hand_changed_range_is_an_audit_error(self):
        text = TEXT.replace("range = [1.16, 1.29]", "range = [1.16, 1.31]", 1)  # 살아 있는 조각(C_vol_high)의 효과
        self.assertNotEqual(text, TEXT)
        errors, _ = CH.audit(self.kn(text), fitted_now())
        self.assertTrue(any("맞춘 값과 다름" in e for e in errors), errors)

    def test_range_wider_than_cap_fails_validate(self):
        text = TEXT.replace("range = [1.03, 1.22]", "range = [1.03, 2.5]", 1)
        self.assertTrue(any("상한" in e for e in SCHEMA.validate(self.kn(text))))

    def test_number_without_reason_fails_validate(self):
        text = TEXT.replace('reason = "한 달 남짓 이어진 오름은 덜 반응한 몫이 남아 있다는 신호"\n[[hypothesis.effect]]',
                            'reason = ""\n[[hypothesis.effect]]', 1)
        self.assertNotEqual(text, TEXT)
        self.assertTrue(any("이유" in e for e in SCHEMA.validate(self.kn(text))))

    def test_condition_must_be_known_before_decision(self):
        text = TEXT.replace('id = "S_trend_20"\ntext = "최근 20봉 수익률이 양수다"\nknown_before = true',
                            'id = "S_trend_20"\ntext = "최근 20봉 수익률이 양수다"', 1)
        self.assertNotEqual(text, TEXT)
        self.assertTrue(any("known_before" in e for e in SCHEMA.validate(self.kn(text))))

    def test_asset_name_in_piece_text_is_an_error(self):
        text = TEXT.replace('text = "최근 20봉 동안 올랐고', 'text = "비트코인이 최근 20봉 동안 올랐고', 1)
        errors, _ = CH.audit(self.kn(text), fitted_now())
        self.assertTrue(any("대상 이름" in e for e in errors), errors)

    def test_too_many_pieces_is_an_error(self):
        extra = "".join(f'\n[[hypothesis]]\nid = "C_extra{i}"\ntext = "t"\nsource = "s"\nreason = "r"\n'
                        f'seen_until = "2026-06-30"\nclaims = [{{ on = "move", op = "mul", dir = "up", when = "예", reason = "r" }}]\n'
                        for i in range(CH.MAX_PIECES))
        errors, _ = CH.audit(self.kn(TEXT + extra), fitted_now())
        self.assertTrue(any("상한" in e for e in errors), errors)

    def test_every_check_name_exists(self):
        kn = SCHEMA.load(C.KNOWLEDGE)
        names = {c for n in kn.claims() for c in SCHEMA.check_names(n)}
        self.assertLessEqual(names, set(CH.CHECKS))
        self.assertEqual(set(CH.SEEN_UNTIL), set(CH.CHECKS))


if __name__ == "__main__":
    unittest.main()
