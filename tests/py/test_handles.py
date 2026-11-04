"""Slice 22: `[handles]` (auto opaque handle classes: eligibility, config, classify, end to end on a synthetic rdj). Stdlib only."""
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
U32 = {"primitive": "u32"}
CLONE = "core::clone::Clone"


def synth_handles() -> Synth:
    """azalea: Client { get(&self) -> Handle, use_it(&self, &Handle) -> u32, other(&self) -> Other, many(&self) -> Vec<Handle> };
    Handle (private field, Clone, pub `value` + `make` -> Other + a private fn + a trait impl fn); Other (private field, NOT Clone);
    Plain (all public fields: a value, never a handle); Gen<T> (generic)."""
    a = Synth("azalea", "0.0.0")
    vec = a.foreign("alloc", "vec", "Vec")
    clone = a.foreign("core", "clone", "Clone", kind="trait")
    client = a.struct("Client", a.root)
    a.struct("EntityRef", a.root)
    h = a.struct("Handle", a.root, fields=[("n", U32, "default")])
    o = a.struct("Other", a.root, fields=[("n", U32, "default")])
    pl = a.struct("Plain", a.root, fields=[("n", U32, "public")])
    gen = a.struct("Gen", a.root, fields=[("n", U32, "default")])
    a.index[gen]["inner"]["struct"]["generics"] = Synth.generics([Synth.ty_param("T")])
    ic = a.impl_(P("Client", client))
    a.fn("get", ic, [("self", S)], P("Handle", h))
    a.fn("use_it", ic, [("self", S), ("h", {"borrowed_ref": {"lifetime": None, "is_mutable": False, "type": P("Handle", h)}})], U32)
    a.fn("other", ic, [("self", S)], P("Other", o))
    a.fn("many", ic, [("self", S)], P("Vec", vec, [P("Handle", h)]))
    a.fn("plain", ic, [("self", S)], P("Plain", pl))
    a.fn("generic", ic, [("self", S)], P("Gen", gen, [U32]))
    ih = a.impl_(P("Handle", h))
    a.fn("value", ih, [("self", S)], U32)
    a.fn("make", ih, [("self", S)], P("Other", o))
    a.fn("secret", ih, [("self", S)], U32, vis="default")
    a.fn("clone", a.impl_(P("Handle", h), trait=("Clone", clone)), [("self", S)], P("Handle", h), vis="default")
    return a


def project(tmp: Path):
    root = tmp / "root"
    (root / "azalea" / "src").mkdir(parents=True)
    (root / "azalea" / "src" / "lib.rs").write_text("pub struct Client;\n")
    (root / "azalea" / "Cargo.toml").write_text('[package]\nname = "azalea"\nversion = "0.0.0"\n')
    d = tmp / "rdj"
    d.mkdir()
    syn = synth_handles()
    (d / "azalea.json").write_text(json.dumps(syn.raw()))
    (d / "azalea.meta").write_text(f"rustc: hand-built\nformat_version: {rdj.FORMAT_TESTED}\nprivate_items: 1\nsource_sha1: {rdj.source_sha1(root / 'azalea')}\n")
    return root, d


def build(mode="auto", **kw):
    """(ix, gen) with `[handles]` = mode; the generator's global tables are restored by `restore`."""
    g.HANDLES = {"mode": mode, "allow": set(), "deny": set(), "max": 64, **kw}
    for n in [n for n, row in g.CLASSES.items() if row.get("handle")]:   # one process, several builds: forget the rows an earlier build made
        del g.CLASSES[n]
    tmp = Path(tempfile.mkdtemp())
    root, d = project(tmp)
    ix = r.RdjIndex(root, d)
    ix.load()
    gen = g.Generator(ix)
    gen.build()
    return ix, gen


class HandlesTest(unittest.TestCase):
    def setUp(self):
        self._h, self._c = g.HANDLES, dict(g.CLASSES)

    def tearDown(self):
        g.HANDLES = self._h
        g.CLASSES.clear()
        g.CLASSES.update(self._c)

    def skipped(self, gen, cls="Client"):
        return {s.name: (s.code, s.reason) for s in gen.classes[cls].skipped}

    def test_config(self):
        self.assertEqual(g._handles_cfg({}), {"mode": "off", "allow": set(), "deny": set(), "max": 64})
        c = g._handles_cfg({"mode": "allow-list", "allow": ["A"], "deny": ["B"], "max": 3})
        self.assertEqual((c["mode"], c["allow"], c["deny"], c["max"]), ("allow-list", {"A"}, {"B"}, 3))
        for bad in ({"mode": "on"}, {"allow": "A"}, {"max": -1}, {"max": True}, {"nope": 1}, {"allow": ["A"], "deny": ["A"]}):
            with self.assertRaises(g.ConfigError, msg=str(bad)):
                g._handles_cfg(bad)
        with self.assertRaises(g.ConfigError):
            g._handles_cfg([])
        self.assertEqual(g.load_config()["HANDLES"]["mode"], "off")

    def test_off_binds_nothing(self):
        ix, gen = build("off")
        self.assertEqual(sorted(gen.classes), ["Client", "EntityRef"])
        sk = self.skipped(gen)
        self.assertEqual(sk["get"][0], "private_fields")
        self.assertEqual(sorted(w.name for w in gen.classes["Client"].wrappers), ["plain"])
        self.assertTrue(all(m.target in g.CLASSES for m in ix.methods))   # no candidate methods collected when off

    def test_auto_end_to_end(self):
        ix, gen = build()
        self.assertEqual(sorted(gen.classes), ["Client", "EntityRef", "Handle"])
        row = g.CLASSES["Handle"]
        self.assertEqual((row["rust"], row["luau"], row["field"], row["emit"], row["handle"]), ("LuaHandle", "Handle", "inner", True, True))
        names = sorted(w.name for w in gen.classes["Client"].wrappers)
        self.assertEqual(names, ["get", "many", "plain", "use_it"])
        # Handle: public inherent methods only (no private fn, no trait impl fn); `make` returns Other, which is not Clone
        self.assertEqual([w.name for w in gen.classes["Handle"].wrappers], ["value"])
        sk = self.skipped(gen, "Handle")
        self.assertEqual(sorted(sk), ["make"])
        self.assertEqual(sk["make"][0], "private_fields")   # the native refusal stands, the hint says why `Other` is no handle
        hints = {s.name: s.hint for s in gen.classes["Client"].skipped}
        self.assertEqual(hints["other"], "not a handle: `Other` is not known to implement Clone (a handle is copied out of a borrow)")
        self.assertEqual(self.skipped(gen)["other"][0], "private_fields")
        self.assertEqual(self.skipped(gen)["generic"][0], "generic")
        self.assertNotIn("Other", gen.classes)
        self.assertNotIn("Plain", gen.classes)
        client = g.emit_client(gen, "x")
        self.assertIn("pub struct LuaHandle {", client)
        self.assertIn("pub inner: azalea::Handle,", client)
        self.assertIn("LuaHandle { inner:", client)
        self.assertIn("mlua::UserDataRef<LuaHandle>", client)
        self.assertIn("self.client.use_it(&h.inner)", client)   # a `&Handle` parameter is borrowed, not cloned
        d = g.emit_dluau(gen, "x", g.scan_extras(ROOT / "harness" / "src" / "luau"))
        self.assertIn("declare extern type Handle with\n    function value(self): number\nend", d)
        self.assertIn("function get(self): Handle", d)
        self.assertIn("function use_it(self, h: Handle): number", d)

    def test_deny_allow_cap(self):
        _, gen = build(deny={"Handle"})
        self.assertNotIn("Handle", gen.classes)
        self.assertEqual(self.skipped(gen)["get"][0], "private_fields")
        _, gen = build("allow-list", allow={"Other"})
        self.assertNotIn("Handle", gen.classes)
        _, gen = build("allow-list", allow={"Handle"})
        self.assertIn("Handle", gen.classes)
        _, gen = build(max=0)
        self.assertNotIn("Handle", gen.classes)

    def test_check_and_classify(self):
        ix, gen = build()
        m = g.Mapper(ix)
        g.CLASSES.pop("Handle", None)
        self.assertIsNone(m.handle_check("Handle"))
        self.assertEqual(m.handle_check("Other")[1], "trait_missing")
        self.assertEqual(m.handle_check("Plain")[1], "excluded")
        self.assertEqual(m.handle_check("Gen")[1], "generic")
        self.assertEqual(m.handle_check("Nope")[1], "no_definition")
        self.assertEqual(m.handle_check("Client")[1], "excluded")
        for role in ("ret", "param"):
            c = m.classify(("path", "Handle", []), role)
            self.assertEqual((c.kind, c.owned), ("handle", True), c)
        self.assertEqual(m.classify(("path", "Other", []), "ret").kind, "opaque")
        self.assertNotIn("Handle", g.CLASSES)   # classify is pure: no row registered
        i = m.named("Handle", "ret")   # first use registers the row, then it maps like any class
        self.assertIn("Handle", g.CLASSES)
        self.assertTrue(i.is_class)
        self.assertEqual(i.rust, "LuaHandle")
        self.assertEqual(i.to_lua("x"), "LuaHandle { inner: x }")
        self.assertEqual(m.named("Handle", "param").from_lua("x"), "x.inner.clone()")
        self.assertEqual(m.classify(("path", "Handle", []), "ret").kind, "class")

    def test_serde_wins(self):
        g.SERDE = {"mode": "auto", "allow": set(), "deny": set(), "typed": set(), "max_depth": 8}
        try:
            ix, gen = build()
            m = g.Mapper(ix)
            g.CLASSES.pop("Handle", None)
            ix.impl_facts["Handle"] = list(ix.impl_facts.get("Handle", [])) + [r.ImplFact(p, p.split("::")[-1], False) for p in ("serde::ser::Serialize", "serde::de::Deserialize")]
            i = m.named("Handle", "ret")
            self.assertTrue(i.rust.startswith("LuaSerde<"), i.rust)
            self.assertNotIn("Handle", g.CLASSES)
        finally:
            g.SERDE = {"mode": "off", "allow": set(), "deny": set(), "typed": set(), "max_depth": 8}


if __name__ == "__main__":
    unittest.main()
