"""Slice 23: `[statics]` (associated fns without a receiver -> `Class.fn(..)`, `&mut self` methods; config, off = old skips, end to end on a
synthetic rdj, deny / allow-list, the client-needing refusal). Stdlib only."""
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
STR_REF = {"borrowed_ref": {"lifetime": None, "is_mutable": False, "type": {"primitive": "str"}}}
SELF_MUT = {"borrowed_ref": {"lifetime": None, "is_mutable": True, "type": {"generic": "Self"}}}
SELF = {"generic": "Self"}


def synth_statics() -> Synth:
    """azalea: Client { get(&self) -> Handle, make() -> Self };
    Handle (private field, Clone): new(n) -> Self, parse(&str) -> Handle, bump(&mut self, u32) -> u32, value(&self) -> u32, take(self) -> u32,
    boom<T>() -> u32, later() async -> u32, secret() private."""
    a = Synth("azalea", "0.0.0")
    clone = a.foreign("core", "clone", "Clone", kind="trait")
    client = a.struct("Client", a.root)
    a.struct("EntityRef", a.root)
    h = a.struct("Handle", a.root, fields=[("n", U32, "default")])
    ic = a.impl_(P("Client", client))
    a.fn("get", ic, [("self", S)], P("Handle", h))
    a.fn("make", ic, [], SELF)
    ih = a.impl_(P("Handle", h))
    a.fn("new", ih, [("n", U32)], SELF)
    a.fn("parse", ih, [("s", STR_REF)], P("Handle", h))
    a.fn("bump", ih, [("self", SELF_MUT), ("by", U32)], U32)
    a.fn("value", ih, [("self", S)], U32)
    a.fn("take", ih, [("self", SELF)], U32)
    boom = a.fn("boom", ih, [], U32)
    a.index[boom]["inner"]["function"]["generics"] = Synth.generics([Synth.ty_param("T")])
    a.fn("later", ih, [], U32, is_async=True)
    a.fn("secret", ih, [], U32, vis="default")
    a.fn("clone", a.impl_(P("Handle", h), trait=("Clone", clone)), [("self", S)], P("Handle", h), vis="default")
    return a


def project(tmp: Path):
    root = tmp / "root"
    (root / "azalea" / "src").mkdir(parents=True)
    (root / "azalea" / "src" / "lib.rs").write_text("pub struct Client;\n")
    (root / "azalea" / "Cargo.toml").write_text('[package]\nname = "azalea"\nversion = "0.0.0"\n')
    d = tmp / "rdj"
    d.mkdir()
    (d / "azalea.json").write_text(json.dumps(synth_statics().raw()))
    (d / "azalea.meta").write_text(f"rustc: hand-built\nformat_version: {rdj.FORMAT_TESTED}\nprivate_items: 1\nsource_sha1: {rdj.source_sha1(root / 'azalea')}\n")
    return root, d


def build(mode="auto", mut_self=True, **kw):
    g.STATICS = {"mode": mode, "classes": set(), "deny": set(), "mut_self": mut_self, **kw}
    for n in [n for n, row in g.CLASSES.items() if row.get("handle")]:
        del g.CLASSES[n]
    tmp = Path(tempfile.mkdtemp())
    root, d = project(tmp)
    ix = r.RdjIndex(root, d)
    ix.load()
    gen = g.Generator(ix)
    gen.build()
    return ix, gen


class StaticsTest(unittest.TestCase):
    def setUp(self):
        self._s, self._h, self._c = g.STATICS, g.HANDLES, dict(g.CLASSES)
        g.HANDLES = {"mode": "auto", "allow": set(), "deny": set(), "max": 64}

    def tearDown(self):
        g.STATICS, g.HANDLES = self._s, self._h
        g.CLASSES.clear()
        g.CLASSES.update(self._c)

    def skipped(self, gen, cls="Handle"):
        return {s.name: (s.code, s.reason) for s in gen.classes[cls].skipped}

    def test_config(self):
        self.assertEqual(g._statics_cfg({}), {"mode": "off", "classes": set(), "deny": set(), "mut_self": False})
        c = g._statics_cfg({"mode": "allow-list", "classes": ["A"], "deny": ["A.f"], "mut_self": True})
        self.assertEqual((c["mode"], c["classes"], c["deny"], c["mut_self"]), ("allow-list", {"A"}, {"A.f"}, True))
        for bad in ({"mode": "on"}, {"classes": "A"}, {"deny": ["f"]}, {"deny": ["A.b.c"]}, {"mut_self": 1}, {"nope": 1}):
            with self.assertRaises(g.ConfigError, msg=str(bad)):
                g._statics_cfg(bad)
        with self.assertRaises(g.ConfigError):
            g._statics_cfg([])
        self.assertEqual(g.load_config()["STATICS"]["mode"], "off")

    def test_off_keeps_the_old_skips(self):
        _, gen = build("off")
        sk = self.skipped(gen)
        self.assertEqual(sk["new"], ("receiver", "no `&self` receiver"))
        self.assertEqual(sk["bump"], ("receiver", "`&mut self` receiver"))
        self.assertEqual(sk["take"], ("receiver", "`self` receiver"))
        self.assertEqual([w.name for w in gen.classes["Handle"].wrappers], ["value"])
        self.assertEqual(gen.classes["Handle"].statics, [])
        self.assertEqual(self.skipped(gen, "Client")["make"], ("receiver", "no `&self` receiver"))
        self.assertNotIn("register_globals", g.emit_client(gen, "x"))
        self.assertNotIn('"statics"', json.dumps(g.report_model(gen, "x", gen.ix, {})))

    def test_auto_end_to_end(self):
        _, gen = build()
        cg = gen.classes["Handle"]
        self.assertEqual(sorted(w.name for w in cg.statics), ["new", "parse"])
        self.assertTrue(all(w.kind == "static" for w in cg.statics))
        self.assertEqual(sorted((w.name, w.kind) for w in cg.wrappers), [("bump", "mut"), ("value", "method")])
        sk = self.skipped(gen)
        self.assertEqual(sk["take"], ("receiver", "`self` receiver"))   # by-value receivers stay skipped
        self.assertEqual(sk["boom"][0], "generic")
        self.assertEqual(sk["later"][0], "receiver")
        self.assertIn("async associated fn", sk["later"][1])
        self.assertNotIn("secret", sk)   # not public: never collected
        rs = g.emit_client(gen, "x")
        self.assertIn("fn bump(&mut self, by: u32)", rs)
        self.assertIn("self.inner.bump(by)", rs)
        self.assertIn("fn value(&self)", rs)
        self.assertIn("pub fn register_statics_handle(lua: &mlua::Lua) -> mlua::Result<()> {", rs)
        self.assertIn('t.set("new", lua.create_function(|_, (n, ): (u32, )| -> mlua::Result<LuaHandle> {', rs)
        self.assertIn("LuaHandle { inner: azalea::Handle::new(n) }", rs)
        self.assertIn('t.set("parse", lua.create_function(|_, (s, ): (String, )|', rs)
        self.assertIn("azalea::Handle::parse(&s)", rs)
        self.assertIn('lua.globals().set("Handle", t)', rs)
        self.assertIn("    register_statics_handle(lua)?;", rs)
        d = g.emit_dluau(gen, "x", g.scan_extras(ROOT / "harness" / "src" / "luau"))
        self.assertIn("declare Handle: {\n    new: (n: number) -> Handle,\n    parse: (s: string) -> Handle,\n}", d)
        self.assertIn("function bump(self, by: number): number", d)
        rep = g.report_model(gen, "x", gen.ix, {})
        row = [c for c in rep["classes"] if c["name"] == "Handle"][0]
        self.assertEqual(row["statics"], ["new", "parse"])
        self.assertEqual(row["generated"], ["bump", "value"])

    def test_a_static_that_cannot_be_returned_is_refused(self):
        _, gen = build()
        self.assertEqual(self.skipped(gen, "Client")["make"][0], "private_fields")   # `Self` = Client resolves, then Client is no handle
        self.assertEqual(gen.classes["Client"].statics, [])

    def test_fake_statics_need_the_client(self):
        """On the fake azalea `Client::fake() -> Self` maps to the ECS `Client` value, whose conversion reads `self.client`: no `self` in a static."""
        g.STATICS = {"mode": "auto", "classes": set(), "deny": set(), "mut_self": True}
        ix = r.RdjIndex(ROOT / "harness" / "fake", ROOT / "harness" / "fake" / "rustdoc")
        ix.load()
        gen = g.Generator(ix)
        gen.build()
        self.assertEqual(self.skipped(gen, "Client")["fake"][0], "ecs_entity")
        self.assertEqual(self.skipped(gen, "EntityRef")["new"][0], "ecs_entity")
        self.assertEqual([cg.name for cg in gen.classes.values() if cg.statics], [])

    def test_mut_self_is_a_separate_switch(self):
        _, gen = build(mut_self=False)
        self.assertEqual(sorted(w.name for w in gen.classes["Handle"].statics), ["new", "parse"])
        self.assertEqual(self.skipped(gen)["bump"], ("receiver", "`&mut self` receiver"))
        self.assertNotIn("&mut self", g.emit_client(gen, "x"))

    def test_allow_list_and_deny(self):
        _, gen = build("allow-list", classes={"EntityRef"})
        self.assertEqual(gen.classes["Handle"].statics, [])
        self.assertEqual(self.skipped(gen)["new"], ("receiver", "no `&self` receiver"))
        _, gen = build("allow-list", classes={"Handle"})
        self.assertEqual(sorted(w.name for w in gen.classes["Handle"].statics), ["new", "parse"])
        _, gen = build(deny={"Handle.new", "Handle.bump"})
        self.assertEqual([w.name for w in gen.classes["Handle"].statics], ["parse"])
        self.assertEqual(self.skipped(gen)["new"], ("excluded", "`Handle.new` denied in [statics]"))
        self.assertEqual(self.skipped(gen)["bump"][0], "excluded")

    def test_self_resolves_only_when_statics_are_on(self):
        ix, gen = build("off")
        m = g.Mapper(ix)
        with self.assertRaises(g.Skip) as cm:
            m.map_ty(("other", "Self"), "ret")
        self.assertEqual(cm.exception.code, "type_unsupported")
        m.self_ty = "Handle"
        i = m.map_ty(("other", "Self"), "ret")
        self.assertEqual(i.rust, "LuaHandle")
        self.assertEqual(i.to_lua("x"), "LuaHandle { inner: x }")

    def test_two_runs_are_byte_equal(self):
        _, a = build()
        _, b = build()
        self.assertEqual(g.emit_client(a, "x"), g.emit_client(b, "x"))


if __name__ == "__main__":
    unittest.main()
