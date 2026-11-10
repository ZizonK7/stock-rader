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
        # 살아 있는 조각(C_vol_high)의 첫 효과 범위를 손으로 넓힌다 (숫자는 fit --write가 바꾸므로 자리로 찾는다)
        at = TEXT.index('id = "C_vol_high"')
        i = TEXT.index("range = [", at)
        j = TEXT.index("]", i)
        lo, hi = (float(x) for x in TEXT[i + len("range = ["):j].split(","))
        text = TEXT[:i] + f"range = [{lo!r}, {round(hi + 0.02, 3)!r}]" + TEXT[j + 1:]
        self.assertNotEqual(text, TEXT)
        errors, _ = CH.audit(self.kn(text), fitted_now())
        self.assertTrue(any("맞춘 값과 다름" in e for e in errors), errors)

    def test_fixed_value_cannot_be_changed_by_hand(self):
        """고정값(fixed, 2026-11-04 C_base_coin의 동전)은 맞추지 않지만, 주장에 적은 값과 다르면 audit 오류다."""
        text = TEXT.replace('range = [1.0, 1.0]\nasset = "069500"\nhorizon = 1\nfixed = true',
                            'range = [1.0, 1.05]\nasset = "069500"\nhorizon = 1\nfixed = true', 1)
        self.assertNotEqual(text, TEXT)
        errors, _ = CH.audit(self.kn(text), fitted_now())
        self.assertTrue(any("C_base_coin" in e and "맞춘 값과 다름" in e for e in errors), errors)
        bad = TEXT.replace('{ on = "up_odds", op = "base", fixed = true, range = [1.0, 1.0],',
                           '{ on = "up_odds", op = "base", fixed = true,', 1)
        self.assertNotEqual(bad, TEXT)
        errors, _ = CH.audit(self.kn(bad), fitted_now())
        self.assertTrue(any("고정값 주장" in e for e in errors), errors)

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


class FindCore(unittest.TestCase):
    """PROVISO-Nesy 찾기(2026-11-10: 비공개로 바뀌어 주소로 받아 올 수 없다 — 붙은 저장소의 폴더를 이름의 대소문자와 상관없이 찾는다)."""

    def test_sibling_folder_is_found_whatever_its_letter_case(self):
        import tempfile
        from pathlib import Path
        from market import logic as LG
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stock-rader").mkdir()
            (root / "other").mkdir()
            self.assertEqual(LG.siblings(root), [])
            (root / "proviso-nesy").mkdir()   # 세션에 따라 소문자로 받아진다
            self.assertEqual([p.name for p in LG.siblings(root)], ["proviso-nesy"])
        self.assertEqual(LG.siblings(Path("/없는/폴더")), [])
