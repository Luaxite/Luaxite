"""Slice 24: `[monomorphise]` (generic methods bound once per substitution: config, substitute / mangle / turbofish, rows and auto mode end to end
on a synthetic rdj, ConfigErrors that name the key). Stdlib only."""
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
GEN = lambda n: {"generic": n}


def synth_mono() -> Synth:
    """azalea: Client { parse<T: Parse>(&self, &str) -> T; pair<K: Parse, V>(&self, K, V) -> (V, K); lonely<T: Parse>(&self) -> u32;
    wh<T>(&self, T) -> T where T: Parse; cn<const N: usize>(&self) -> u32 }; trait Parse implemented by Alpha, Beta, Gamma (pub-field structs)."""
    a = Synth("azalea", "0.0.0")
    parse = a.trait_("Parse", a.root)
    client = a.struct("Client", a.root)
    a.struct("EntityRef", a.root)
    for n, ft in (("Alpha", U32), ("Beta", {"primitive": "bool"}), ("Gamma", {"primitive": "f64"})):
        sid = a.struct(n, a.root, fields=[("v", ft, "public")])
        a.impl_(P(n, sid), trait=("Parse", parse))
    ic = a.impl_(P("Client", client))
    pb = lambda: Synth.trait_bound("Parse", parse)
    f = a.fn("parse", ic, [("self", S), ("s", STR_REF)], GEN("T"))
    a.index[f]["inner"]["function"]["generics"] = Synth.generics([Synth.ty_param("T", [pb()])])
    f = a.fn("pair", ic, [("self", S), ("k", GEN("K")), ("v", GEN("V"))], {"tuple": [GEN("V"), GEN("K")]})
    a.index[f]["inner"]["function"]["generics"] = Synth.generics([Synth.ty_param("K", [pb()]), Synth.ty_param("V")])
    f = a.fn("lonely", ic, [("self", S)], U32)
    a.index[f]["inner"]["function"]["generics"] = Synth.generics([Synth.ty_param("T", [pb()])])
    f = a.fn("wh", ic, [("self", S), ("x", GEN("T"))], GEN("T"))
    a.index[f]["inner"]["function"]["generics"] = Synth.generics(
        [Synth.ty_param("T")], [{"bound_predicate": {"type": GEN("T"), "bounds": [pb()], "generic_params": []}}])
    f = a.fn("cn", ic, [("self", S)], U32)
    a.index[f]["inner"]["function"]["generics"] = Synth.generics([Synth.const_param("N", {"primitive": "usize"})])
    return a


def project(tmp: Path):
    root = tmp / "root"
    (root / "azalea" / "src").mkdir(parents=True)
    (root / "azalea" / "src" / "lib.rs").write_text("pub struct Client;\n")
    (root / "azalea" / "Cargo.toml").write_text('[package]\nname = "azalea"\nversion = "0.0.0"\n')
    d = tmp / "rdj"
    d.mkdir()
    (d / "azalea.json").write_text(json.dumps(synth_mono().raw()))
    (d / "azalea.meta").write_text(f"rustc: hand-built\nformat_version: {rdj.FORMAT_TESTED}\nprivate_items: 1\nsource_sha1: {rdj.source_sha1(root / 'azalea')}\n")
    return root, d


def build(rows=(), auto=False, mx=4):
    cfg = {"rows": [{"item": it, **subs} for it, subs, _w in rows], "max": mx, **({"auto": True} if auto else {})}
    g.MONOMORPHISE = g._mono_cfg(cfg)
    tmp = Path(tempfile.mkdtemp())
    root, d = project(tmp)
    ix = r.RdjIndex(root, d)
    ix.load()
    gen = g.Generator(ix)
    gen.build()
    return ix, gen


class MonoTest(unittest.TestCase):
    def setUp(self):
        self._m = g.MONOMORPHISE

    def tearDown(self):
        g.MONOMORPHISE = self._m

    def names(self, gen):
        return sorted(w.name for w in gen.classes["Client"].wrappers)

    def skipped(self, gen):
        return {s.name: (s.code, s.reason) for s in gen.classes["Client"].skipped}

    def test_config(self):
        self.assertEqual(g._mono_cfg({}), {"rows": [], "max": 4, "auto": False})
        c = g._mono_cfg({"rows": [{"item": "A.f", "T": "i64"}], "max": 2, "auto": True})
        self.assertEqual((c["rows"], c["max"], c["auto"]), ([("A.f", {"T": "i64"}, "monomorphise.rows[0]")], 2, True))
        bad = [{"nope": 1}, {"max": 0}, {"auto": 1}, {"rows": {}}, {"rows": [{"T": "i64"}]}, {"rows": [{"item": " "}]},
               {"rows": [{"item": "A.f"}]}, {"rows": [{"item": "A.f", "T": ""}]}, {"rows": [{"item": "A.f", "T": 3}]},
               {"rows": [{"item": "A.f", "T": "i64"}] * 2},
               {"max": 1, "rows": [{"item": "A.f", "T": "i64"}, {"item": "A.f", "T": "f64"}]}]
        for b in bad:
            with self.assertRaises(g.ConfigError, msg=str(b)):
                g._mono_cfg(b)
        with self.assertRaises(g.ConfigError) as cm:
            g._mono_cfg({"rows": [{"item": "A.f", "T": ""}]})
        self.assertIn("monomorphise.rows[0].T", str(cm.exception))
        with self.assertRaises(g.ConfigError):
            g._mono_cfg([])
        self.assertEqual(g.load_config()["MONOMORPHISE"]["auto"], False)

    def test_substitute_walks_every_shape(self):
        sub = {"T": ("prim", "i64")}
        T = ("other", "T")
        self.assertEqual(g.substitute(("ref", ("slice", T)), sub), ("ref", ("slice", ("prim", "i64"))))
        self.assertEqual(g.substitute(("tuple", [T, ("path", "Option", [T])]), sub),
                         ("tuple", [("prim", "i64"), ("path", "Option", [("prim", "i64")])]))
        self.assertEqual(g.substitute(("array", T, "4"), sub), ("array", ("prim", "i64"), "4"))
        keep = ("path", "Vec", [("prim", "u8")])
        self.assertIs(g.substitute(keep, sub), keep)
        self.assertEqual(g.substitute(("path", "T", []), sub), ("prim", "i64"))
        self.assertEqual(g.sub_node("Foo"), ("path", "Foo", []))
        for bad in ("Vec<i64>", "a::B", ""):
            with self.assertRaises(g.Skip) as cm:
                g.sub_node(bad)
            self.assertEqual(cm.exception.code, "type_unsupported")

    def test_mangle_and_turbofish_use_declaration_order(self):
        subs = {"K": "String", "V": "u8"}
        self.assertEqual(g.mangle("pair", ["V", "K"], subs), "pair_u8_string")
        nodes = {n: g.sub_node(t) for n, t in subs.items()}
        sp = lambda nd: nd[1] if nd[0] == "prim" else "x::" + nd[1]
        self.assertEqual(g.turbofish("pair", ["V", "K"], nodes, sp), "pair::<u8, x::String>")
        self.assertEqual(g.mangle("f", ["T"], {"T": "Vec3D"}), "f_vec3_d")

    def test_nothing_configured_keeps_the_old_skip(self):
        _, gen = build()
        self.assertEqual(self.names(gen), [])
        for n in ("parse", "pair", "lonely", "wh", "cn"):
            self.assertEqual(self.skipped(gen)[n], ("generic", "generic method"), n)

    def test_rows_end_to_end(self):
        _, gen = build([("Client.parse", {"T": "u32"}, "monomorphise.rows[0]"), ("Client.parse", {"T": "f64"}, "monomorphise.rows[1]"),
                        ("Client.pair", {"V": "u8", "K": "Alpha"}, "monomorphise.rows[2]"), ("Client.wh", {"T": "Beta"}, "monomorphise.rows[3]")])
        self.assertEqual(self.names(gen), ["pair_alpha_u8", "parse_f64", "parse_u32", "wh_beta"])
        rs = g.emit_client(gen, "x")
        self.assertIn("self.client.parse::<u32>(&s)", rs)
        self.assertIn("self.client.parse::<f64>(&s)", rs)
        self.assertIn("self.client.pair::<azalea::Alpha, u8>(", rs)   # declaration order K, V
        self.assertIn("self.client.wh::<azalea::Beta>(", rs)
        d = g.emit_dluau(gen, "x", g.scan_extras(ROOT / "harness" / "src" / "luau"))
        self.assertIn("function parse_u32(self, s: string): number", d)
        self.assertIn("function parse_f64(self, s: string): number", d)
        self.assertEqual(self.skipped(gen)["lonely"][0], "generic")   # no row: still skipped
        self.assertEqual(self.skipped(gen)["cn"][0], "generic")   # const parameter: never

    def test_row_errors_name_the_key(self):
        with self.assertRaises(g.ConfigError) as cm:
            build([("Client.parse", {"X": "u32"}, "monomorphise.rows[0]")])
        self.assertIn("monomorphise.rows[0].X", str(cm.exception))
        with self.assertRaises(g.ConfigError) as cm:
            build([("Client.pair", {"K": "u8"}, "monomorphise.rows[3]")])
        self.assertIn("no type for `V`", str(cm.exception))

    def test_a_refused_substitution_skips_the_method_with_its_code(self):
        _, gen = build([("Client.parse", {"T": "Vec<u8>"}, "monomorphise.rows[0]")])
        self.assertEqual(self.skipped(gen)["parse"][0], "type_unsupported")
        _, gen = build([("Client.parse", {"T": "Nope"}, "monomorphise.rows[0]")])
        self.assertEqual(self.skipped(gen)["parse"][0], "no_path")

    def test_auto_uses_the_implementors_and_the_cap(self):
        _, gen = build(auto=True)
        self.assertEqual(self.names(gen), ["parse_alpha", "parse_beta", "parse_gamma", "wh_alpha", "wh_beta", "wh_gamma"])
        self.assertEqual(self.skipped(gen)["pair"][0], "generic")   # two parameters: rows only
        self.assertEqual(self.skipped(gen)["lonely"][0], "generic")   # the parameter is not in the signature
        _, gen = build(auto=True, mx=2)
        self.assertEqual(self.names(gen), ["parse_alpha", "parse_beta", "wh_alpha", "wh_beta"])
        self.assertIn("self.client.parse::<azalea::Alpha>(&s)", g.emit_client(gen, "x"))

    def test_rows_beat_auto_and_runs_are_equal(self):
        _, a = build([("Client.parse", {"T": "f64"}, "monomorphise.rows[0]")], auto=True)
        self.assertEqual([n for n in self.names(a) if n.startswith("parse")], ["parse_f64"])
        _, b = build([("Client.parse", {"T": "f64"}, "monomorphise.rows[0]")], auto=True)
        self.assertEqual(g.emit_client(a, "x"), g.emit_client(b, "x"))

    def test_type_params_in_the_index(self):
        ix, _ = build()
        tp = {m.name: m.type_params for m in ix.methods if m.target == "Client"}
        self.assertEqual(tp["parse"], [("T", ("Parse",))])
        self.assertEqual(tp["pair"], [("K", ("Parse",)), ("V", ())])
        self.assertEqual(tp["wh"], [("T", ("Parse",))])   # from the where clause
        self.assertIsNone(tp["cn"])


if __name__ == "__main__":
    unittest.main()
