"""Slice 25: `[callbacks]` (closure parameters -> Luau functions): config, off = unchanged, front-end FnSig, exact Rust / Luau text, coded refusals,
classify agreement, report + helper, end to end on a synthetic rdj. Stdlib only."""
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
U32, BOOL, STR = {"primitive": "u32"}, {"primitive": "bool"}, {"primitive": "str"}


def ref(t):
    return {"borrowed_ref": {"lifetime": None, "is_mutable": False, "type": t}}


def refmut(t):
    return {"borrowed_ref": {"lifetime": None, "is_mutable": True, "type": t}}


def fnb(kind, ins, out=None, extra=()):
    """The bound list of `impl Fn..(ins) -> out + extra` (extra: 'Send', 'Sync', "'static")."""
    b = [{"trait_bound": {"trait": {"path": kind, "id": 1, "args": {"parenthesized": {"inputs": ins, "output": out}}}, "generic_params": [], "modifier": "none"}}]
    for e in extra:
        b.append({"outlives": e} if e.startswith("'") else {"trait_bound": {"trait": {"path": e, "id": 2, "args": None}, "generic_params": [], "modifier": "none"}})
    return b


def impl_fn(kind, ins, out=None, extra=()):
    return {"impl_trait": fnb(kind, ins, out, extra)}


def dyn_fn(kind, ins, out=None, extra=()):
    traits = [b["trait_bound"] for b in fnb(kind, ins, out, [e for e in extra if not e.startswith("'")])]
    return {"dyn_trait": {"traits": [{"trait": t["trait"], "generic_params": []} for t in traits], "lifetime": "'static" if "'static" in extra else None}}


def synth() -> Synth:
    a = Synth("azalea", "0.0.0")
    client = a.struct("Client", a.root)
    a.struct("EntityRef", a.root)
    plain = a.struct("Plain", a.root, fields=[("n", U32, "public")])
    box = a.foreign("alloc", "boxed", "Box")
    opt = a.foreign("core", "option", "Option")
    ic = a.impl_(P("Client", client))
    f = lambda name, t, out=None: a.fn(name, ic, [("self", S), ("f", t)], out)
    f("on_tick", impl_fn("Fn", [U32], BOOL))
    f("each", impl_fn("FnMut", [ref(STR), U32]))
    f("once", impl_fn("FnOnce", []))
    f("boxed", P("Box", box, [dyn_fn("Fn", [U32], P("String", 0), ("Send", "'static"))]))
    f("dynref", ref(dyn_fn("Fn", [U32], U32)))
    f("maybe", P("Option", opt, [P("Box", box, [dyn_fn("Fn", [])])]))
    f("two", impl_fn("Fn", [U32, BOOL], U32))
    # refusals
    f("ret_closure_arg", impl_fn("Fn", [U32], P("Plain", plain)))                 # result is not Default
    f("result_cb", impl_fn("Fn", [U32], P("Result", 0, [U32, P("String", 0)])))    # Result
    f("ref_out", impl_fn("Fn", [], ref(STR)))                                      # reference result
    f("mut_arg", impl_fn("Fn", [refmut(U32)]))                                      # &mut argument
    f("many", impl_fn("Fn", [U32] * 9))
    f("nested", impl_fn("Fn", [], impl_fn("Fn", [])))                               # closure result
    f("iter", {"impl_trait": [Synth.trait_bound("Iterator", 0, None, {"Item": U32})]})  # not a closure
    a.fn("make", ic, [("self", S)], impl_fn("Fn", [U32], U32))                     # closure return
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


def build(**kw):
    g.CALLBACKS = {**g._callbacks_cfg({}), **kw}
    tmp = Path(tempfile.mkdtemp())
    root, d = project(tmp)
    ix = r.RdjIndex(root, d)
    ix.load()
    gen = g.Generator(ix)
    gen.build()
    return gen


class CallbacksTest(unittest.TestCase):
    def setUp(self):
        self._c = g.CALLBACKS

    def tearDown(self):
        g.CALLBACKS = self._c

    def test_config(self):
        self.assertEqual(g._callbacks_cfg({}), {"mode": "off", "max_args": 8})
        self.assertEqual(g._callbacks_cfg({"mode": "auto", "max_args": 3}), {"mode": "auto", "max_args": 3})
        for bad in ({"mode": "on"}, {"max_args": 0}, {"max_args": 17}, {"max_args": True}, {"max_args": "3"}, {"nope": 1}):
            with self.assertRaises(g.ConfigError, msg=str(bad)):
                g._callbacks_cfg(bad)
        with self.assertRaises(g.ConfigError):
            g._callbacks_cfg([])
        self.assertEqual(g.load_config()["CALLBACKS"]["mode"], "off")

    def test_front_end(self):
        t = rdj.convert_type(impl_fn("FnMut", [U32, ref(STR)], BOOL, ("Send", "Sync", "'static")))
        self.assertEqual(t[0], "impl")
        self.assertEqual(t.fn, rdj.FnSig("FnMut", (("prim", "u32"), ("ref", ("prim", "str"))), ("prim", "bool"), False, True, True, True))
        d = rdj.convert_type(dyn_fn("Fn", []))
        self.assertEqual((d.fn.dyn, d.fn.inputs, d.fn.output), (True, (), None))
        self.assertIsNone(rdj.convert_type({"impl_trait": [Synth.trait_bound("Iterator", 0)]}).fn)
        two = rdj.convert_type({"impl_trait": fnb("Fn", []) + fnb("FnMut", [])})
        self.assertIsNone(two.fn)                                                     # two closure bounds: no signature
        self.assertEqual(rdj.convert_type(impl_fn("Fn", [])), ("impl", "impl Fn()"))   # still the plain 2-tuple

    def test_off_changes_nothing(self):
        gen = build(mode="off")
        sk = {s.name: (s.code, s.reason) for s in gen.classes["Client"].skipped}
        self.assertEqual(sk["on_tick"], ("closure", "`impl Fn(u32) -> bool` parameter"))
        self.assertEqual(sk["boxed"][0], "closure")
        names = {w.name for w in gen.classes["Client"].wrappers}
        self.assertFalse(names & {"on_tick", "each", "once", "boxed", "dynref", "maybe", "two"})
        self.assertNotIn("callbacks", g.report_model(gen, "x", gen.ix, {}))
        self.assertNotIn("lua_callback_error", g.emit_types(gen, "x"))

    def test_exact_text(self):
        gen = build(mode="auto")
        ws = {w.name: w for w in gen.classes["Client"].wrappers}
        for n in ("on_tick", "each", "once", "boxed", "dynref", "maybe", "two"):
            self.assertIn(n, ws, n)
        (_, ti), = ws["on_tick"].wparams
        self.assertEqual((ti.rust, ti.luau), ("mlua::Function", "((number) -> boolean)"))
        self.assertEqual(ws["on_tick"].call_args, [
            '{ let f = f; move |a0| { f.call::<LBool>((a0,)).and_then(|r| Ok(r.0)).unwrap_or_else(|e| { lua_callback_error("callback", &e); Default::default() }) } }'])
        self.assertEqual(ws["each"].wparams[0][1].luau, "((string, number) -> ())")
        self.assertEqual(ws["each"].call_args, [
            '{ let f = f; move |a0, a1| { if let Err(e) = f.call::<()>((a0.to_string(), a1)) { lua_callback_error("callback", &e); } } }'])
        self.assertEqual(ws["once"].wparams[0][1].luau, "(() -> ())")
        self.assertEqual(ws["once"].call_args, ['{ let f = f; move || { if let Err(e) = f.call::<()>(()) { lua_callback_error("callback", &e); } } }'])
        self.assertEqual(ws["two"].wparams[0][1].luau, "((number, boolean) -> number)")
        self.assertIn("f.call::<u32>((a0, a1))", ws["two"].call_args[0])
        self.assertTrue(ws["boxed"].call_args[0].startswith("Box::new({ let f = f; move |a0| { f.call::<String>((a0,))"))
        self.assertTrue(ws["boxed"].call_args[0].endswith(" as Box<dyn Fn(u32) -> String + Send + 'static>"))   # cast: Option::map can't coerce
        self.assertEqual(ws["boxed"].wparams[0][1].luau, "((number) -> string)")
        self.assertTrue(ws["dynref"].call_args[0].startswith("&{ let f = f; move |a0|"))
        self.assertEqual(ws["maybe"].wparams[0][1].luau, "(() -> ())?")
        self.assertTrue(ws["maybe"].call_args[0].startswith("f.map(|v| Box::new({ let f = v;"))
        self.assertTrue(ws["maybe"].call_args[0].endswith(" as Box<dyn Fn()>)"))
        self.assertFalse(" as Box" in ws["dynref"].call_args[0])

    def test_spell(self):
        m = build(mode="auto").m
        self.assertEqual(m.spell(("tuple", [("prim", "u8"), ("ref", ("prim", "str"))])), "(u8, &str)")
        self.assertEqual(m.spell(("path", "Vec", [("path", "String", [])])), "Vec<String>")
        self.assertIsNone(m.spell(("path", "NoSuchType", [])))
        self.assertIsNone(m.spell(("other", "T")))

    def test_refusals(self):
        gen = build(mode="auto")
        sk = {s.name: (s.code, s.reason) for s in gen.classes["Client"].skipped}
        self.assertEqual(sk["ret_closure_arg"][0], "trait_missing")
        self.assertIn("not known to be `Default`", sk["ret_closure_arg"][1])
        self.assertEqual(sk["result_cb"][0], "closure")
        self.assertIn("returns a `Result`", sk["result_cb"][1])
        self.assertEqual(sk["ref_out"][0], "closure")
        self.assertEqual(sk["mut_arg"][0], "reference")
        self.assertEqual(sk["many"][0], "closure")
        self.assertIn("9 arguments", sk["many"][1])
        self.assertEqual(sk["nested"][0], "closure")
        self.assertEqual(sk["make"][0], "impl_trait")   # build_method refuses an `impl` return first; the mapper's own refusal:
        with self.assertRaises(g.Skip) as cm:
            gen.m.map_ty(rdj.convert_type(impl_fn("Fn", [U32], U32)), "ret")
        self.assertEqual(cm.exception.code, "closure")
        self.assertIn("a callback is a parameter only", cm.exception.reason)
        self.assertEqual(sk["iter"][0], "closure")   # `impl Iterator` stays what it was
        self.assertEqual(sk["iter"][1], "`impl Iterator<Item = u32>` parameter")
        g.CALLBACKS = {**g.CALLBACKS, "max_args": 9}
        gen = build(mode="auto", max_args=9)
        self.assertIn("many", {w.name for w in gen.classes["Client"].wrappers})

    def test_classify_agrees(self):
        gen = build(mode="auto")
        m = gen.m
        ok = {w.name for w in gen.classes["Client"].wrappers}
        skipped = {s.name: s.code for s in gen.classes["Client"].skipped}
        for name, ty in (("on_tick", rdj.convert_type(impl_fn("Fn", [U32], BOOL))), ("each", rdj.convert_type(impl_fn("FnMut", [ref(STR), U32]))),
                         ("two", rdj.convert_type(impl_fn("Fn", [U32, BOOL], U32)))):
            c = m.classify(ty, "param")
            self.assertEqual((c.kind, c.mode), ("value", "value"), name)
            self.assertIn(name, ok)
        for ty, code in ((rdj.convert_type(impl_fn("Fn", [], ref(STR))), "closure"), (rdj.convert_type(impl_fn("Fn", [U32], P("Result", 0, [U32]))), "closure"),
                         (rdj.convert_type(impl_fn("Fn", [U32] * 9)), "closure"), (rdj.convert_type(impl_fn("Fn", [])), None)):
            c = m.classify(ty, "param")
            self.assertEqual(c.code, code or "")
        self.assertEqual(m.classify(rdj.convert_type(impl_fn("Fn", [])), "ret").code, "closure")   # as a return
        self.assertIn("ret_closure_arg", skipped)
        g.CALLBACKS = {**g.CALLBACKS, "mode": "off"}
        self.assertEqual(m.classify(rdj.convert_type(impl_fn("Fn", [])), "param").code, "closure")

    def test_outputs(self):
        gen = build(mode="auto")
        rep = g.report_model(gen, "x", gen.ix, {})
        self.assertEqual(rep["callbacks"], ["() -> ()", "(number) -> boolean", "(number) -> number", "(number) -> string", "(number, boolean) -> number",
                                            "(string, number) -> ()"])
        types = g.emit_types(gen, "x")
        self.assertEqual(types.count("fn lua_callback_error(what: &str, e: &mlua::Error) {"), 1)
        client = g.emit_client(gen, "x")
        self.assertIn("fn on_tick(&self, f: mlua::Function)", client)
        d = g.emit_dluau(gen, "x", {})
        self.assertIn("((number) -> boolean)", d)


if __name__ == "__main__":
    unittest.main()
