"""Slice 21: `[serde]` (serde detection, mode / allow / deny / typed, LuaSerde text, precedence, direction). Stdlib only."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import luaxite_gen as g
import rdj_index as r

T = "ReachBlockPosGoal"  # private fields, no config row: a skip today
SER, DE = "serde::ser::Serialize", "serde::de::Deserialize"
ix = r.RdjIndex(ROOT / "harness" / "fake", ROOT / "harness" / "fake" / "rustdoc")
ix.load()


class SerdeTest(unittest.TestCase):
    def setUp(self):
        self._old = (g.SERDE, ix.impl_facts.get(T))
        self.M = g.Mapper(ix)

    def tearDown(self):
        g.SERDE = self._old[0]
        if self._old[1] is None:
            ix.impl_facts.pop(T, None)
        else:
            ix.impl_facts[T] = self._old[1]

    def cfg(self, traits=(SER, DE), **kw):
        g.SERDE = {"mode": "auto", "allow": set(), "deny": set(), "typed": set(), "max_depth": 8, **kw}
        ix.impl_facts[T] = [r.ImplFact(p, p.split("::")[-1], False) for p in traits]
        self.M = g.Mapper(ix)

    def skip_code(self, role):
        with self.assertRaises(g.Skip) as c:
            self.M.named(T, role)
        return c.exception.code

    def test_attr_helpers(self):
        self.assertEqual(r.serde_shape([]), "plain")
        self.assertEqual(r.serde_shape(r.serde_attr_bodies('#[serde(rename_all = "camelCase")]')), "any")
        self.assertEqual(r.serde_shape(["default, deny_unknown_fields"]), "optional-keys")
        self.assertEqual(r.serde_shape(["default, mystery"]), "any")
        self.assertEqual(r.serde_shape(['skip_serializing_if = "Option::is_none"']), "optional-keys")

    def test_off_is_unchanged(self):
        self.cfg(mode="off")
        self.assertEqual((self.skip_code("ret"), self.skip_code("param")), ("private_fields", "private_fields"))
        self.assertEqual(self.M.serde_kind(T, "ret"), None)

    def test_auto_binds_both_directions(self):
        self.cfg()
        for role in ("ret", "param"):
            i = self.M.named(T, role)
            self.assertTrue(i.rust.startswith("LuaSerde<azalea::") and i.rust.endswith(">"), i.rust)
            self.assertEqual(i.luau, "{ [string]: any }")
        self.assertEqual(self.M.named(T, "ret").to_lua("x"), "LuaSerde(x)")
        self.assertEqual(self.M.named(T, "param").from_lua("x"), "x.0")
        self.assertIn(("serde", "LuaSerde"), self.M.touched)
        self.assertEqual(self.M.serde_used, {T: "any"})
        self.assertEqual(self.M.classify(("path", T, []), "ret").kind, "value")

    def test_direction(self):
        self.cfg(traits=(SER,))
        self.assertEqual(self.M.named(T, "ret").luau, "{ [string]: any }")
        self.assertEqual(self.skip_code("param"), "direction")
        self.cfg(traits=(DE,))
        self.assertEqual(self.skip_code("ret"), "direction")
        self.assertEqual(self.M.classify(("path", T, []), "param").kind, "value")

    def test_user_trait_named_serialize_is_not_serde(self):
        self.cfg(traits=("my::Serialize", "my::Deserialize"))
        self.assertEqual(self.skip_code("ret"), "private_fields")

    def test_allow_list_and_deny(self):
        self.cfg(mode="allow-list", allow={"Other"})
        self.assertEqual(self.skip_code("ret"), "private_fields")
        self.cfg(mode="allow-list", allow={T})
        self.assertEqual(self.M.named(T, "ret").luau, "{ [string]: any }")
        self.cfg(deny={T})
        self.assertEqual(self.skip_code("ret"), "private_fields")
        with self.assertRaises(g.ConfigError):
            g._serde_cfg({"allow": ["A"], "deny": ["A"]})
        for bad in ({"mode": "x"}, {"allow": "A"}, {"max_depth": 0}, {"nope": 1}, "s"):
            with self.assertRaises(g.ConfigError):
                g._serde_cfg(bad)

    def test_typed_needs_no_shape_attr(self):
        self.cfg(typed={T})
        d = ix.unique_def(T)
        self.assertNotEqual(self.M.serde_luau(T, d, "ret"), "any")  # private fields: not typed, struct table
        self.assertEqual(self.M.serde_luau(T, d, "ret"), "{ [string]: any }")

    def test_helper_text(self):
        self.assertEqual(g.emit_serde_helpers(False), [])
        text = "\n".join(g.emit_serde_helpers(True))
        self.assertIn("to_value_with(lua, &self.0, mlua::serde::SerializeOptions::new()", text)
        self.assertIn("mlua::LuaSerdeExt::from_value::<T>(lua, value).map(LuaSerde)", text)


if __name__ == "__main__":
    unittest.main()
