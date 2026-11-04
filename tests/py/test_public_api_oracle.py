"""Slice 29 (optional part): tools/public_api_oracle.py parses `cargo public-api` text and diffs it against the index. Stdlib only."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "public_api_oracle.py"


def run(*a):
    return subprocess.run([sys.executable, str(TOOL), *a], capture_output=True, text=True, cwd=ROOT)


class Oracle(unittest.TestCase):
    def test_selftest(self):
        r = run("--selftest")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_input_is_2(self):
        self.assertEqual(run("--rustdoc", "x").returncode, 2)

    def test_gap_is_exit_1_and_clean_is_0(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "pa.txt"
            args = ("--azalea", "harness/fake", "--rustdoc", "harness/fake/rustdoc", "--public-api", str(f))
            f.write_text("pub struct azalea::Client\npub fn azalea::Client::chat(&self, c: impl Into<String>)\n")
            r = run(*args)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("public-api only types: 0", r.stdout)
            f.write_text("pub struct azalea::NotInTheIndex\n")
            r = run(*args)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("NotInTheIndex", r.stdout)


if __name__ == "__main__":
    unittest.main()
