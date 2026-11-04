"""Slice 28: the shapes fixture (tools/testdata/shapes, real rustdoc JSON, every mode on) generates exactly the committed golden files, and the
facts each slice 16-26 shape promises hold in them. Refresh after an intended change: run the command in `generate()`'s docstring with
--rust-out/--luau-out pointed at tools/testdata/shapes/golden and document the change. Stdlib only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tools" / "testdata" / "shapes"
GOLD = FIX / "golden"
FILES = ("gen_client.rs", "gen_events.rs", "gen_types.rs", "azalea.d.luau", "GEN_REPORT.json", "GEN_REPORT.md")


def generate(out: Path) -> None:
    """python3 tools/luaxite_gen.py --azalea tools/testdata/shapes --rustdoc tools/testdata/shapes/rustdoc --config tools/testdata/shapes/bindgen.toml
    --rust-out <dir> --luau-out <dir>"""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "luaxite_gen.py"), "--azalea", str(FIX), "--rustdoc", str(FIX / "rustdoc"),
                        "--config", str(FIX / "bindgen.toml"), "--rust-out", str(out), "--luau-out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr


class ShapesFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        generate(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_matches_golden(self):
        for f in FILES:
            self.assertEqual((self.out / f).read_text(), (GOLD / f).read_text(), f)

    def test_shapes_bound(self):
        d = (GOLD / "azalea.d.luau").read_text()
        for line in ("function pos(self): { number }", "rgb: { number }", "pair: { number }", "dist: number", "function sum(self, xs: { number }): number",
                     "function elapsed(self): number", "function home(self): string", "function initial(self): string",
                     "function apply(self, s: Spec): ()", "function draw(self, s: Shape): ()", "function blob(self): { [string]: any }",
                     "function door(self): Door", "new: (id: number) -> Door,", "level: number", "function bump(self, by: number): ()",
                     "function parse_u32(self, s: string): number?", "function component_hunger(self): number?",
                     "function each_tick(self, f: ((number) -> boolean)): ()", "function later(self, f: (() -> ())?): ()"):
            self.assertIn(line, d)

    def test_serde_value_not_handle(self):
        """The s28 finding: Blob (hand `impl Serialize/Deserialize`, private fields) is a serde value once `[serde]` is on, never a handle."""
        d = (GOLD / "azalea.d.luau").read_text()
        self.assertNotIn("extern type Blob", d)
        self.assertIn("LuaSerde<azalea::Blob>", (GOLD / "gen_client.rs").read_text() + (GOLD / "gen_types.rs").read_text())

    def test_coded_skips(self):
        rep = json.loads((GOLD / "GEN_REPORT.json").read_text())
        self.assertEqual(rep["skipped_by_code"], {"excluded": 1, "impl_trait": 1, "reference": 1})

    def test_serde_section_in_both_configs(self):
        for p in (ROOT / "bindgen.toml", FIX / "bindgen.toml"):
            self.assertIn("\n[serde]\n", p.read_text(), p)


if __name__ == "__main__":
    unittest.main()
