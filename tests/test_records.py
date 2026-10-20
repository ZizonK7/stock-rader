"""기록: 칸 검사, id·순서, git HEAD와 비교해 지난 줄이 바뀌거나 지워지면 실패."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from market import records as R

ORDER = {"id": "a:2026-07-01:069500", "run": "2026-07-01", "account": "a", "asset": "069500", "side": "buy",
         "qty_units": 3, "ref_price": 100.0, "reason": "r"}


class Fields(unittest.TestCase):
    def test_write_rejects_bad_lines_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            bad = dict(ORDER, side="hold")
            with self.assertRaises(ValueError):
                R.write(Path(d), "order", [ORDER, bad])
            self.assertFalse((Path(d) / "order.jsonl").exists())

    def test_missing_field_and_type(self):
        d = {"kind": "order", **ORDER}
        del d["qty_units"]
        self.assertTrue(any("qty_units" in e for e in R.line_errors("order", d, "x")))
        d = {"kind": "order", **ORDER, "qty_units": 1.5}
        self.assertTrue(any("타입" in e for e in R.line_errors("order", d, "x")))

    def test_negative_cash_is_an_error(self):
        acc = {"kind": "account", "id": "x", "run": "2026-07-01", "account": "a", "cash": -1, "positions": {},
               "prices": {}, "value": 0, "pending": []}
        self.assertTrue(any("음수" in e for e in R.line_errors("account", acc, "x")))

    def test_check_folder_finds_duplicates_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            R.write(Path(d), "order", [ORDER, dict(ORDER, run="2026-06-30", id="b")])
            p = Path(d) / "order.jsonl"
            p.write_text(p.read_text(encoding="utf-8") + json.dumps({"kind": "order", **ORDER}) + "\n", encoding="utf-8")
            errs = R.check_folder(Path(d))
            self.assertTrue(any("중복" in e for e in errs), errs)
            self.assertTrue(any("날짜 순서" in e for e in errs), errs)

    def test_every_kind_is_described(self):
        self.assertEqual(set(R.FIELDS), set(R.DESCRIBE))


@unittest.skipUnless(shutil.which("git"), "git 없음")
class AgainstHead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        run = lambda *a: subprocess.run(["git", *a], cwd=self.root, capture_output=True, check=True, timeout=60)
        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        R.write(self.root / "records", "order", [ORDER, dict(ORDER, id="b")])
        run("add", "-A")
        run("commit", "-q", "-m", "x")
        self.path = self.root / "records" / "order.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_appending_is_fine(self):
        R.write(self.root / "records", "order", [dict(ORDER, id="c", run="2026-07-02")])
        self.assertEqual(R.head_errors(self.root), [])

    def test_changed_line_fails(self):
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"qty_units": 3', '"qty_units": 4')
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        errs = R.head_errors(self.root)
        self.assertTrue(any("2번째 줄" in e for e in errs), errs)

    def test_removed_line_and_file_fail(self):
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text(lines[0] + "\n", encoding="utf-8")
        self.assertTrue(R.head_errors(self.root))
        self.path.unlink()
        self.assertTrue(any("지워짐" in e for e in R.head_errors(self.root)))


if __name__ == "__main__":
    unittest.main()
