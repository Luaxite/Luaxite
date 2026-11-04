"""Slice 26: `[properties]` (getter/setter pairs -> Luau fields): config, off = unchanged, pairing rules of spec@s7 (tests 1-10), Rust / Luau / report
text, end to end on a synthetic rdj. Stdlib only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import luaxite_gen as g
import rdj
import rdj_index as r
from rdj_synth import Synth

P, S = Synth.path_type, Synth.SELF_REF
U32, F64 = {"primitive": "u32"}, {"primitive": "f64"}
SELF_MUT = {"borrowed_ref": {"lifetime": None, "is_mutable": True, "type": {"generic": "Self"}}}
MUT_U32 = {"borrowed_ref": {"lifetime": None, "is_mutable": True, "type": U32}}


def synth() -> Synth:
    """Handle (private field, Clone): x/set_x (f64 pair), set_y (no getter), z -> &mut u32 + set_z, take_x/take/mistake(+set_mistake), w(u32)/set_w(f64),
    r(u32)/set_r -> u32, get_k/set_k, ro (getter only)."""
    a = Synth("azalea", "0.0.0")
    a.foreign("core", "clone", "Clone", kind="trait")
    clone = a.foreign("core", "clone", "Clone", kind="trait")
    client = a.struct("Client", a.root)
    a.struct("EntityRef", a.root)
    h = a.struct("Handle", a.root, fields=[("n", U32, "default")])
    a.fn("get", a.impl_(P("Client", client)), [("self", S)], P("Handle", h))
    ih = a.impl_(P("Handle", h))
    for name, ret in (("x", F64), ("mistake", U32), ("take_x", U32), ("take", U32), ("w", U32), ("r", U32), ("get_k", U32), ("ro", U32)):
        a.fn(name, ih, [("self", S)], ret)
    a.fn("z", ih, [("self", S)], MUT_U32)
    for name, ty, ret in (("set_x", F64, None), ("set_y", F64, None), ("set_z", U32, None), ("set_mistake", U32, None), ("set_w", F64, None),
                          ("set_r", U32, U32), ("set_k", U32, None)):
        a.fn(name, ih, [("self", SELF_MUT), ("v", ty)], ret)
    a.fn("clone", a.impl_(P("Handle", h), trait=("Clone", clone)), [("self", S)], P("Handle", h), vis="default")
    return a


def project(tmp: Path):
    root = tmp / "root"
    (root / "azalea" / "src").mkdir(parents=True)
    (root / "azalea" / "src" / "lib.rs").write_text("pub struct Client;\n")
    (root / "azalea" / "Cargo.toml").write_text('[package]\nname = "azalea"\nversion = "0.0.0"\n')
    d = tmp / "rdj"
    d.mkdir()
    (d / "azalea.json").write_text(json.dumps(synth().raw()))
    (d / "azalea.meta").write_text(f"rustc: hand-built\nformat_version: {rdj.FORMAT_TESTED}\nprivate_items: 1\nsource_sha1: {rdj.source_sha1(root / 'azalea')}\n")
    return root, d


def props(**kw):
    return {**g._props_cfg({}), **kw}


def build(**kw):
    g.STATICS = {"mode": "auto", "classes": set(), "deny": set(), "mut_self": True}
    g.PROPERTIES = props(**kw)
    for n in [n for n, row in g.CLASSES.items() if row.get("handle")]:
        del g.CLASSES[n]
    tmp = Path(tempfile.mkdtemp())
    root, d = project(tmp)
    ix = r.RdjIndex(root, d)
    ix.load()
    gen = g.Generator(ix)
    gen.build()
    return gen


class PropertiesTest(unittest.TestCase):
    def setUp(self):
        self._s, self._h, self._p, self._c = g.STATICS, g.HANDLES, g.PROPERTIES, dict(g.CLASSES)
        g.HANDLES = {"mode": "auto", "allow": set(), "deny": set(), "max": 64}

    def tearDown(self):
        g.STATICS, g.HANDLES, g.PROPERTIES = self._s, self._h, self._p
        g.CLASSES.clear()
        g.CLASSES.update(self._c)

    def names(self, gen):
        cg = gen.classes["Handle"]
        return sorted(p.name for p in cg.props), sorted(w.name for w in cg.wrappers)

    def test_config(self):
        self.assertEqual(g._props_cfg({}), {"mode": "off", "classes": set(), "deny": set(), "getter_prefixes": [""], "result_getters": False, "readonly_getters": False})
        c = g._props_cfg({"mode": "allow-list", "classes": ["A"], "deny": ["A.x"], "getter_prefixes": ["get_"], "result_getters": True, "readonly_getters": True})
        self.assertEqual((c["classes"], c["deny"], c["getter_prefixes"], c["result_getters"], c["readonly_getters"]), ({"A"}, {"A.x"}, ["", "get_"], True, True))
        for bad in ({"mode": "on"}, {"classes": "A"}, {"deny": ["x"]}, {"getter_prefixes": ["Get"]}, {"result_getters": 1}, {"nope": 1}):
            with self.assertRaises(g.ConfigError, msg=str(bad)):
                g._props_cfg(bad)
        with self.assertRaises(g.ConfigError):
            g._props_cfg([])
        self.assertEqual(g.load_config()["PROPERTIES"]["mode"], "off")

    def test_off_changes_nothing(self):   # spec test 7
        gen = build(mode="off")
        pr, ms = self.names(gen)
        self.assertEqual(pr, [])
        self.assertIn("x", ms)
        self.assertIn("set_x", ms)
        self.assertNotIn('"properties"', json.dumps(g.report_model(gen, "x", gen.ix, {})))

    def test_pairs(self):   # spec tests 1, 2, 4, 5, 6, 8
        gen = build(mode="auto")
        pr, ms = self.names(gen)
        self.assertEqual(pr, ["mistake", "x"])   # (1) x/set_x; (4) `mistake` is not `take`
        self.assertNotIn("x", ms)
        self.assertNotIn("set_x", ms)
        self.assertIn("set_y", ms)                 # (2) no getter: stays a method
        for kept in ("take_x", "take", "w", "set_w", "r", "set_r", "ro", "set_z"):
            self.assertIn(kept, ms, kept)          # (4) take / take_x, (5) type mismatch, (8) setter returns u32, getter-only
        sk = {s.name: s.code for s in gen.classes["Handle"].skipped}
        self.assertEqual(sk["z"], "reference")     # (3)(6) `-> &mut T` is no getter: skip reason unchanged, set_z stays a method
        x = [p for p in gen.classes["Handle"].props if p.name == "x"][0]
        self.assertEqual((x.owner, x.getter, x.setter, x.luau), ("Handle", "x", "set_x", "number"))

    def test_deny_prefix_readonly(self):
        gen = build(mode="auto", deny={"Handle.x"})        # (9)
        pr, ms = self.names(gen)
        self.assertEqual(pr, ["mistake"])
        self.assertIn("x", ms)
        gen = build(mode="auto", getter_prefixes=["", "get_"])   # (10)
        self.assertEqual(self.names(gen)[0], ["k", "mistake", "x"])
        self.assertNotIn("get_k", self.names(gen)[1])
        gen = build(mode="auto", readonly_getters=True)
        pr, ms = self.names(gen)
        self.assertIn("ro", pr)
        self.assertNotIn("ro", ms)
        self.assertEqual([p.setter for p in gen.classes["Handle"].props if p.name == "ro"], [None])
        gen = build(mode="allow-list", classes={"EntityRef"})
        self.assertEqual(self.names(gen)[0], [])

    def test_mut_self_off_means_no_pair(self):
        g.STATICS = {"mode": "auto", "classes": set(), "deny": set(), "mut_self": False}
        g.PROPERTIES = props(mode="auto")
        for n in [n for n, row in g.CLASSES.items() if row.get("handle")]:
            del g.CLASSES[n]
        root, d = project(Path(tempfile.mkdtemp()))
        ix = r.RdjIndex(root, d)
        ix.load()
        gen = g.Generator(ix)
        gen.build()
        pr, ms = self.names(gen)
        self.assertEqual(pr, [])   # no setter is bound: both halves stay as they were (G3)
        self.assertIn("x", ms)

    def test_texts(self):
        gen = build(mode="auto")
        rs = g.emit_client(gen, "x")
        self.assertIn("#[lua(getter, infallible)]\n    fn x(&self) -> f64 {", rs)
        self.assertIn("#[lua(setter, infallible)]\n    fn set_x(&mut self, v: f64) {", rs)
        self.assertNotIn("#[lua(infallible)]\n    fn x(", rs)
        d = g.emit_dluau(gen, "x", g.scan_extras(ROOT / "harness" / "src" / "luau"))
        self.assertIn("    x: number\n", d)
        self.assertNotIn("function x(self", d)
        self.assertNotIn("function set_x(self", d)
        self.assertIn("function set_y(self, v: number): ()", d)
        row = [c for c in g.report_model(gen, "x", gen.ix, {})["classes"] if c["name"] == "Handle"][0]
        self.assertEqual(row["properties"], ["mistake", "x"])


if __name__ == "__main__":
    unittest.main()
