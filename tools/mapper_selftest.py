#!/usr/bin/env python3
"""Selftest of the generator's type mapper (container types), stdlib only: `python3 tools/mapper_selftest.py`.

Uses the rustdoc index of harness/fake (harness/fake/rustdoc). Every container the
generator maps must turn into the Luau type and the Rust conversion written here; every one it must refuse must raise `Skip` with the reason.
"""
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import luaxite_gen as g
import rdj_index

ix = rdj_index.RdjIndex(HERE.parent / "harness" / "fake", HERE.parent / "harness" / "fake" / "rustdoc")
ix.load()
M = g.Mapper(ix)


def P(name, *args):
    return ("path", name, list(args))


def ok(t, role):
    i = M.map_ty(t, role)
    return i, (i.to_lua("x") if i.to_lua else None), (i.from_lua("x") if i.from_lua else None)


def skips(t, role, why):
    try:
        M.map_ty(t, role)
    except g.Skip as e:
        assert why in str(e), f"{g.type_text(t)} ({role}): wanted `{why}`, got `{e}`"
        return
    raise AssertionError(f"{g.type_text(t)} ({role}) should have been skipped (`{why}`)")


def main():
    n = 0
    for name in g.MAP_TYPES:
        i, to, frm = ok(P(name, P("String"), P("String")), "ret")
        assert i.luau == "{ [string]: string }" and i.rust == "std::collections::HashMap<String, String>", (name, i)
        assert to == "x.into_iter().map(|(k, v)| (k, v)).collect::<std::collections::HashMap<_, _>>()" and frm is None, (name, to, frm)
        n += 1
    i, to, _ = ok(P("HashMap", P("Uuid"), P("PlayerInfo")), "ret")
    assert i.luau == "{ [string]: PlayerInfo }" and i.rust == "std::collections::HashMap<String, LuaPlayerInfo>", i
    assert to == "x.into_iter().map(|(k, v)| (k.to_string(), LuaPlayerInfo(v))).collect::<std::collections::HashMap<_, _>>()", to
    i, to, _ = ok(P("BTreeMap", ("prim", "u32"), P("String")), "ret")
    assert i.luau == "{ [number]: string }", i
    n += 2
    i, _, frm = ok(P("BTreeMap", ("prim", "u32"), P("String")), "param")
    assert frm == "x.into_iter().map(|(k, v)| (k, v)).collect::<std::collections::BTreeMap<_, _>>()", frm
    i, _, frm = ok(P("HashMap", P("String"), ("prim", "f64")), "param")
    assert frm.endswith("collect::<std::collections::HashMap<_, _>>()"), frm
    n += 2
    for ty in ("i128", "u128"):
        for role in ("param", "ret"):
            i, to, frm = ok(("prim", ty), role)
            assert i.rust == ty and i.luau == "number" and to == "x" and frm == "x", (ty, role, i, to, frm)
    i, _, frm = ok(P("Vec", ("prim", "u128")), "param")
    assert i.rust == "Vec<u128>" and i.luau == "{ number }", i
    i, _, _ = ok(P("Option", ("prim", "i128")), "ret")
    assert i.rust == "Option<i128>" and i.luau == "number?", i
    skips(("prim", "f128"), "ret", "primitive `f128`")
    n += 7
    i, to, frm = ok(("prim", "bool"), "param")
    assert i.rust == "LBool" and i.luau == "boolean" and to is None and frm == "x.0", (i, to, frm)
    i, to, frm = ok(("prim", "bool"), "ret")
    assert i.rust == "bool" and to == "x" and frm == "x", (i, to, frm)
    i, _, frm = ok(P("Option", ("prim", "bool")), "param")
    assert i.rust == "Option<LBool>" and i.luau == "boolean?" and frm == "x.map(|v| v.0)", (i, frm)
    i, _, frm = ok(P("Vec", ("prim", "bool")), "param")
    assert i.rust == "Vec<LBool>" and ".0" in frm, (i, frm)
    n += 4
    i, to, frm = ok(P("Option", ("unit",)), "ret")
    assert i.luau == "boolean?" and i.rust == "Option<()>" and to == "x.map(|_| true)" and frm is None, (i, to, frm)
    skips(P("Option", ("unit",)), "param", "`Option<()>` parameter")
    n += 2
    skips(P("HashMap", P("Vec3"), P("String")), "ret", "map key type")
    skips(P("IndexMap", P("String"), P("String")), "param", "`IndexMap` parameter")
    skips(P("HashMap", P("String"), P("PlayerInfo")), "param", "can't be read from Luau")
    skips(P("HashMap", P("Uuid"), P("String")), "param", "nested fallible type in map")
    n += 4
    i, to, _ = ok(P("Vec", P("String")), "ret")
    assert i.luau == "{ string }", i
    n += 1
    i, to, _ = ok(P("LookDirection"), "ret")
    assert i.luau == "LookDirection" and i.rust == "LuaLookDirection" and to == "LuaLookDirection(x)", (i, to)
    assert M.records["LookDirection"]["fields"] == [("y_rot", "number", "v.y_rot()"), ("x_rot", "number", "v.x_rot()")]
    ok(P("Physics"), "ret")
    f = {a: (b, c) for a, b, c in M.records["Physics"]["fields"]}
    assert f["velocity"] == ("Vec3", "LVec3(v.velocity)") and f["on_ground"] == ("boolean", "v.on_ground()"), f
    assert list(f)[-1] == "on_ground" and "last_on_ground" not in f
    ok(P("Attributes"), "ret")
    f = {a: (b, c) for a, b, c in M.records["Attributes"]["fields"]}
    assert f["step_height"] == ("AttributeInstance", "LuaAttributeInstance(v.step_height)") and len(f) == 9, f
    assert M.records["AttributeInstance"]["fields"] == [("base", "number", "v.base"), ("value", "number", "v.calculate()")]
    ok(P("GameProfile"), "ret")
    assert [a for a, _b, _c in M.records["GameProfile"]["fields"]] == ["uuid", "name"]
    n += 7
    saved = dict(g.ACCESSORS)
    try:
        g.ACCESSORS["Physics"] = dict(fields=["nope"], getters=[])
        M2 = g.Mapper(ix)
        try:
            M2.map_ty(P("Physics"), "ret")
            raise AssertionError("missing field not refused")
        except g.Skip as e:
            assert "no public field `nope`" in str(e), e
        g.ACCESSORS["Physics"] = dict(fields=["velocity"], getters=[("velocity", "number", "1")])
        try:
            g.Mapper(ix).map_ty(P("Physics"), "ret")
            raise AssertionError("duplicate field not refused")
        except g.Skip as e:
            assert "listed twice" in str(e), e
        g.ACCESSORS["Physics"] = dict(fields=["on_ground"], getters=[])
        try:
            g.Mapper(ix).map_ty(P("Physics"), "ret")
            raise AssertionError("private field read")
        except g.Skip as e:
            assert "no public field `on_ground`" in str(e), e
    finally:
        g.ACCESSORS.clear()
        g.ACCESSORS.update(saved)
    n += 3
    def enum_def(name, variants):
        return g.Def("enum", name, "azalea", (), True, False, b"", variants=variants)

    def var(name, kind, types=()):
        return dict(name=name, kind=kind, types=list(types), attrs=[], doc="")

    M3 = g.Mapper(ix)
    defs = {
        "Shape": enum_def("Shape", [var("Empty", "unit"), var("Count", "tuple", [("prim", "u32")]),
                                    var("Pair", "tuple", [P("String"), ("prim", "bool")]), var("Who", "tuple", [P("PlayerInfo")])]),
        "Bad": enum_def("Bad", [var("Empty", "unit"), var("Named", "struct", [("prim", "u32")])]),
        "Clash": enum_def("Clash", [var("A", "unit"), var("a", "unit")]),
    }
    real_unique, real_resolve = ix.unique_def, ix.resolve_path
    ix.unique_def = lambda n: defs.get(n) or real_unique(n)
    ix.resolve_path = lambda n: f"azalea::x::{n}" if n in defs else real_resolve(n)
    try:
        i = M3.map_ty(P("Shape"), "ret")
        assert i.luau == "Shape" and i.rust == "LuaShape" and i.to_lua("x") == "LuaShape(x)" and not i.needs_client, i
        vs = {v["name"]: v for v in M3.tagged["Shape"]["variants"]}
        assert vs["Empty"]["kind"] == "empty" and vs["Empty"]["fields"] == [] and vs["Empty"]["pat"] == ""
        assert vs["Count"]["fields"] == [("value", "number", "v")] and vs["Count"]["pat"] == "(v)"
        assert vs["Pair"]["fields"] == [("_1", "string", "f0"), ("_2", "boolean", "f1")] and vs["Pair"]["pat"] == "(f0, f1)"
        assert [f[0] for f in vs["Who"]["fields"]] == ["name", "uuid", "latency", "gamemode"], vs["Who"]
        for bad, why in (("Bad", "struct-style variant"), ("Clash", "same snake_case name")):
            try:
                g.Mapper(ix).map_ty(P(bad), "ret")
                raise AssertionError(f"{bad} not refused")
            except g.Skip as e:
                assert why in str(e), e
        M4 = g.Mapper(ix)
        M4.cur_class = "Client"
        i = M4.map_ty(P("HitResult"), "ret")
        assert i.needs_client and i.to_lua("x") == "LuaHitResult(x, self.client.clone())", (i.needs_client, i.to_lua("x"))
        assert M4.tagged["HitResult"]["needs_client"] and M4.records["EntityHitResult"]["needs_client"]
        assert not M4.records["BlockHitResult"]["needs_client"]
        assert [v["kind"] for v in M4.tagged["HitResult"]["variants"]] == ["block", "entity"]
        M4.cur_class = "EntityRef"
        try:
            M4.map_ty(P("HitResult"), "ret")
            raise AssertionError("cached client type reached an Entity method")
        except g.Skip as e:
            assert "only `Client` methods" in str(e), e
        M5 = g.Mapper(ix)
        M5.cur_class = "EntityRef"
        try:
            M5.map_ty(P("HitResult"), "ret")
            raise AssertionError("HitResult built for an Entity method")
        except g.Skip:
            pass
        M5.cur_class = "Client"
        assert M5.map_ty(P("HitResult"), "ret").needs_client
    finally:
        ix.unique_def, ix.resolve_path = real_unique, real_resolve
    n += 11
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pk = Path(tmp) / "azalea-protocol" / "src" / "packets" / "game"
        pk.mkdir(parents=True)
        (pk / "mod.rs").write_text("declare_state_packets!(GamePacket,\n    Clientbound => [\n        foo, // a comment\n        bar_baz,\n        tup,\n    ],\n    Serverbound => [ zed ]\n);\n")
        mk = lambda name, fields, tuple_style=False, mod="game": g.Def(
            kind="struct", name=name, crate="azalea_protocol", modpath=("packets", mod, "c"), is_pub=True, generics=False,
            src=b"", tuple_style=tuple_style, fields=fields)
        added = [
            mk("ClientboundFoo", [("id", P("MinecraftEntityId"), True), ("blob", P("NoSuchType"), True), ("kind", ("prim", "u8"), True),
                                  ("hidden", ("prim", "u8"), False), ("count", ("prim", "u32"), True)]),
            mk("ClientboundTup", [("0", ("prim", "u8"), True), ("1", P("String"), True)], tuple_style=True),
            mk("ClientboundFoo", [("other", ("prim", "u8"), True)], mod="config"),
        ]
        for d in added:
            ix.defs[d.name].append(d)
        try:
            gp = g.Generator(ix)
            gp.build_packets_in(Path(tmp))
            by = {p["name"]: p for p in gp.packets_in}
            assert list(by) == ["foo", "bar_baz", "tup"], list(by)
            foo = by["foo"]
            assert foo["variant"] == "Foo" and foo["luau"] == "PacketFoo" and not foo["nodef"], foo
            assert [f[0] for f in foo["fields"]] == ["id", "count"], foo["fields"]
            assert [f[0] for f in foo["skipped"]] == ["blob", "kind", "hidden"], foo["skipped"]
            assert foo["fields"][0][2] == "LMinecraftEntityId(v.id)", foo["fields"][0]
            assert by["bar_baz"]["variant"] == "BarBaz" and by["bar_baz"]["nodef"] and not by["bar_baz"]["fields"], by["bar_baz"]
            assert [f[0] for f in by["tup"]["fields"]] == ["_1", "_2"] and by["tup"]["fields"][0][2] == "v.0", by["tup"]["fields"]
            out = g.emit_packets_in(gp, "test")
            assert "ClientboundGamePacket::BarBaz(v) => {" in out and 't.set("count", v.count)?;' in out, out
            gp2 = g.Generator(ix)
            gp2.build_packets_in(Path(tmp) / "nowhere")
            assert gp2.packets_in == []
        finally:
            for d in added:
                ix.defs[d.name].remove(d)
    n += 8
    with tempfile.TemporaryDirectory() as tmp:
        pk = Path(tmp) / "azalea-protocol" / "src" / "packets" / "game"
        pk.mkdir(parents=True)
        (pk / "mod.rs").write_text("declare_state_packets!(GamePacket,\n    Clientbound => [ foo ],\n    Serverbound => [\n        ok_one, // c\n        bad_one,\n        tup_out,\n        empty,\n    ]\n);\n")
        mk = lambda name, fields, tuple_style=False: g.Def(
            kind="struct", name=name, crate="azalea_protocol", modpath=("packets", "game", "s"), is_pub=True, generics=False,
            src=b"", tuple_style=tuple_style, fields=fields)
        added = [
            mk("ServerboundOkOne", [("id", P("MinecraftEntityId"), True), ("n", ("prim", "u32"), True)]),
            mk("ServerboundBadOne", [("id", P("MinecraftEntityId"), True), ("blob", P("NoSuchType"), True)]),
            mk("ServerboundTupOut", [("0", ("prim", "u8"), True), ("1", P("String"), True)], tuple_style=True),
            mk("ServerboundEmpty", []),
        ]
        for d in added:
            ix.defs[d.name].append(d)
        try:
            gp = g.Generator(ix)
            gp.build_packets_out(Path(tmp))
            by = {p["name"]: p for p in gp.packets_out}
            assert list(by) == ["ok_one", "bad_one", "tup_out", "empty"], list(by)
            assert by["ok_one"]["ok"] and by["ok_one"]["luau"] == "OutPacketOkOne" and [f[0] for f in by["ok_one"]["fields"]] == ["id", "n"]
            assert not by["bad_one"]["ok"] and not by["bad_one"]["fields"] and "`blob`" in by["bad_one"]["why"], by["bad_one"]
            assert by["tup_out"]["ok"] and by["tup_out"]["tuple_style"] and [f[0] for f in by["tup_out"]["fields"]] == ["_1", "_2"]
            assert by["empty"]["ok"] and by["empty"]["fields"] == []
            out = g.emit_packets_out(gp, "test")
            assert '"ok_one" => {' in out and "ServerboundOkOne { id: f0.0, n: f1 }" in out, out
            assert "ServerboundGamePacket::TupOut(azalea::protocol::packets::game::ServerboundTupOut(f0, f1))" in out, out
            assert "ServerboundEmpty {}" in out and '"bad_one"' not in out, out
            gp2 = g.Generator(ix)
            gp2.build_packets_out(Path(tmp) / "nowhere")
            assert gp2.packets_out == []
        finally:
            for d in added:
                ix.defs[d.name].remove(d)
    n += 9
    mk = lambda name, derive, fields, tuple_style=False: g.Def(
        kind="struct", name=name, crate="azalea_entity", modpath=("x",), is_pub=True, generics=False,
        src=b"", tuple_style=tuple_style, fields=fields, attrs=[derive])
    added = [
        mk("EcsMarker", "#[derive(Component)]", []),
        mk("EcsCount", "#[derive(Component, Clone)]", [("0", ("prim", "u32"), True)], tuple_style=True),
        mk("EcsHidden", "#[derive(Component, Clone)]", [("0", ("prim", "u32"), False)], tuple_style=True),
        mk("EcsNoClone", "#[derive(Component)]", [("0", ("prim", "u32"), True)], tuple_style=True),
        mk("EcsRes", "#[derive(Resource, Clone)]", [("0", ("prim", "bool"), True)], tuple_style=True),
        mk("EcsPlain", "#[derive(Debug, Clone)]", [("0", ("prim", "bool"), True)], tuple_style=True),
        mk("EcsResBoth", "#[derive(Clone, Component, Resource)]", [("0", ("prim", "bool"), True)], tuple_style=True),
    ]
    for d in added:
        ix.defs[d.name].append(d)
    dup_c = mk("EcsDup", "#[derive(Component, Clone)]", [("0", ("prim", "bool"), True)], tuple_style=True)
    dup_o = g.Def(kind="struct", name="EcsDup", crate="azalea_block", modpath=("y",), is_pub=True, generics=False, src=b"", fields=[])
    added += [dup_c, dup_o]
    for d in (dup_c, dup_o):
        ix.defs[d.name].append(d)
    saved_paths = g.PATH_OVERRIDES.copy()
    saved_ix = (ix.chain_public, ix.to_azalea)
    try:
        ix.chain_public = lambda crate, modpath: True
        ix.to_azalea = lambda crate, modpath, name: "::".join(["azalea", *modpath, name])
        for d in added:
            if d.name != "EcsDup":
                g.PATH_OVERRIDES[d.name] = "azalea::x::" + d.name
        ge = g.Generator(ix)
        ge.build_ecs()
        by = {e["name"]: e for e in ge.ecs}
        assert "EcsPlain" not in by, "a type without a Component/Resource derive is not in the table"
        assert by["EcsMarker"]["marker"] and by["EcsMarker"]["luau"] == "boolean" and by["EcsMarker"]["to_lua"] == "true", by["EcsMarker"]
        assert by["EcsCount"]["luau"] == "number" and by["EcsCount"]["to_lua"] == "v.0" and by["EcsCount"]["kind"] == "component", by["EcsCount"]
        assert by["EcsHidden"]["to_lua"] is None and "private" in by["EcsHidden"]["reason"], by["EcsHidden"]
        assert by["EcsNoClone"]["to_lua"] is None and "Clone" in by["EcsNoClone"]["reason"], by["EcsNoClone"]
        assert by["EcsRes"]["kind"] == "resource" and by["EcsRes"]["luau"] == "boolean", by["EcsRes"]
        assert by["EcsResBoth"]["kind"] == "resource", by["EcsResBoth"]
        assert by["EcsDup"]["path"] == "azalea::x::EcsDup" and by["EcsDup"]["to_lua"] == "v.0", by["EcsDup"]
        out = g.emit_ecs(ge, "test")
        assert 'EcsEntry { name: "EcsCount", kind: "component", rust: "azalea::x::EcsCount"' in out, out
        assert '"EcsMarker" => Ok(client.get_entity_component::<azalea::x::EcsMarker>(entity).map(|_| Value::Boolean(true))),' in out, out
        assert "pub fn read_ref_component(lua: &Lua, entity: &azalea::EntityRef" in out and "entity.get_component::<azalea::x::EcsCount>()" in out, out
        assert "map_get_resource::<azalea::x::EcsRes, _>" in out and '"EcsHidden" =>' not in out.split("read_component")[1].split("read_ref_component")[0], out
    finally:
        ix.chain_public, ix.to_azalea = saved_ix
        g.PATH_OVERRIDES.clear()
        g.PATH_OVERRIDES.update(saved_paths)
        for d in added:
            ix.defs[d.name].remove(d)
    n += 9
    assert "is a {}, not a {kind}" in out and " , not a" not in out, out
    gg = g.Generator(ix)
    gg.m.touched = set()
    gl = gg.goals()
    first = set(gg.m.touched)
    assert gl and first, "the fake azalea must have usable Goal types that touch mapper keys"
    gg.m.touched = {("type", "Other")}
    assert gg.goals() is gl
    assert gg.m.touched == first | {("type", "Other")}, (gg.m.touched, first)
    n += 3
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "extra_entity.rs").write_text(
            "impl LuaEntityRef {\n"
            "    pub fn outer(&self) -> u8 {\n"
            "        fn nested_helper(x: u8) -> u8 {\n"
            "            x + 1\n"
            "        }\n"
            "        let s = \"{ fn in_string() }\"; // fn in_comment() {\n"
            "        nested_helper(1)\n"
            "    }\n"
            "    pub async fn second(&self) {}\n"
            "}\n"
        )
        res = g.extras_reserved(Path(tmp))
        names = res["EntityRef"]
        assert names == {"outer", "second"}, names
    n += 1
    class DispIx:
        """The fake index plus a `Display` fact for one made-up error type."""
        def __init__(self, base, yes):
            self.base, self.yes = base, yes

        def __getattr__(self, k):
            return getattr(self.base, k)

        def is_display(self, name):
            return True if name in self.yes else self.base.is_display(name)

    assert ix.is_display("NoSuchType") is None
    MD = g.Mapper(DispIx(ix, {"Oops"}))
    i = MD.map_ty(P("Result", P("String"), P("Oops")), "ret")
    assert i.is_result and i.to_lua("x") == "x.map_err(lua_err)", i.to_lua("x")
    skips(P("Result", P("String"), P("Nope")), "ret", "not `MissingComponentError`")
    skips(P("Result", P("String"), P("Oops", P("T"))), "ret", "not `MissingComponentError`")
    n += 3
    # option structs: pub fields + Default + every field readable from Luau
    mkd = lambda name, fields, attrs=(), generics=False, tuple_style=False: g.Def(
        kind="struct", name=name, crate="azalea", modpath=(), is_pub=True, generics=generics, src=b"",
        tuple_style=tuple_style, fields=fields, attrs=list(attrs))
    good = [("language", P("String"), True), ("view_distance", ("prim", "u8"), True), ("chat_colors", ("prim", "bool"), True)]
    cases = {
        "OptGood": mkd("OptGood", good, ["#[derive(Clone, Default)]"]),
        "OptNoDefault": mkd("OptNoDefault", good, ["#[derive(Clone)]"]),
        "OptPrivate": mkd("OptPrivate", good + [("secret", ("prim", "u8"), False)], ["#[derive(Default)]"]),
        "OptGeneric": mkd("OptGeneric", good, ["#[derive(Default)]"], generics=True),
        "OptTuple": mkd("OptTuple", [("0", ("prim", "u8"), True)], ["#[derive(Default)]"], tuple_style=True),
        "OptBadField": mkd("OptBadField", good + [("blob", P("NoSuchType"), True)], ["#[derive(Default)]"]),
    }
    for name, d in cases.items():
        ix.defs[name] = [d]
    ix.default_impls.add("OptHand")
    ix.defs["OptHand"] = [mkd("OptHand", good)]
    saved_po = dict(g.PATH_OVERRIDES)
    g.PATH_OVERRIDES.update({k: f"azalea::{k}" for k in list(cases) + ["OptHand"]})
    try:
        Mo = g.Mapper(ix)
        for name in ("OptGood", "OptHand"):
            e = Mo.option_struct(name)
            assert e["path"] == f"azalea::{name}" and [f[0] for f in e["fields"]] == ["language", "view_distance", "chat_colors"], e
            assert [f[1].luau for f in e["fields"]] == ["string", "number", "boolean"], e
        for name, why in (("OptNoDefault", "not known to implement Default"), ("OptPrivate", "private fields"),
                          ("OptGeneric", "generic or a tuple"), ("OptTuple", "generic or a tuple"), ("OptBadField", "OptBadField`.blob")):
            try:
                Mo.option_struct(name)
                raise AssertionError(f"{name} not refused")
            except g.Skip as e:
                assert why in str(e), (name, str(e))
        try:
            Mo.option_struct("NoSuchStruct")
            raise AssertionError("unknown struct not refused")
        except g.Skip as e:
            assert "no unique public struct" in str(e), e
    finally:
        for name in list(cases) + ["OptHand"]:
            ix.defs.pop(name, None)
        ix.default_impls.discard("OptHand")
        g.PATH_OVERRIDES.clear()
        g.PATH_OVERRIDES.update(saved_po)
    assert ix.is_default("NoSuchType") is None
    n += 10
    # option struct emission (Rust FromLua + Luau type), exact text
    import types as _types
    ix.defs["OptEmit"] = [mkd("OptEmit", [("language", P("String"), True), ("chat_colors", ("prim", "bool"), True), ("type", ("prim", "f32"), True)],
                              ["#[derive(Default)]"])]
    g.PATH_OVERRIDES["OptEmit"] = "azalea::OptEmit"
    try:
        Me = g.Mapper(ix)
        e = Me.use_option_struct("OptEmit")
        assert Me.use_option_struct("OptEmit") is e and e["rust"] == "LuaOptEmitOpts" and ("type", "OptEmit") in Me.touched
        rs = g.emit_types(_types.SimpleNamespace(m=Me), "test")
        for want in (
            "pub struct LuaOptEmitOpts(pub azalea::OptEmit);",
            "impl FromLua for LuaOptEmitOpts {",
            "let mut out = azalea::OptEmit::default();",
            '"language" => {\n                    let v = <String as FromLua>::from_lua(v, lua)\n'
            '                        .map_err(|err| mlua::Error::runtime(format!("OptEmit.language: {err}")))?;\n'
            "                    out.language = v;\n                }",
            "out.chat_colors = v.0;",
            "out.r#type = v;",
            'format!("OptEmit: unknown field `{other}` (known: language, chat_colors, type)")',
            'format!("OptEmit: expected a table, got {}", other.type_name())',
        ):
            assert want in rs, want
        assert g.luau_option_struct("OptEmit", e) == "type OptEmit = { language: string?, chat_colors: boolean?, type: number? }"
        for bad in ("Bot", "OptNoDefault"):
            try:
                g.Mapper(ix).use_option_struct(bad)
                raise AssertionError(f"{bad} registered")
            except g.Skip:
                pass
        assert "Opts" not in g.emit_types(_types.SimpleNamespace(m=g.Mapper(ix)), "test")
    finally:
        ix.defs.pop("OptEmit", None)
        g.PATH_OVERRIDES.pop("OptEmit", None)
    n += 6
    # nested option struct: a field that is itself a Default struct is read from a nested table
    ix.defs["OptInner"] = [mkd("OptInner", [("hat", ("prim", "bool"), True)], ["#[derive(Default)]"])]
    ix.defs["OptOuter"] = [mkd("OptOuter", [("language", P("String"), True), ("skin", P("OptInner"), True)], ["#[derive(Default)]"])]
    ix.defs["OptLoop"] = [mkd("OptLoop", [("again", P("OptLoop"), True)], ["#[derive(Default)]"])]
    g.PATH_OVERRIDES.update({"OptInner": "azalea::OptInner", "OptOuter": "azalea::OptOuter", "OptLoop": "azalea::OptLoop"})
    try:
        Mn = g.Mapper(ix)
        e = Mn.use_option_struct("OptOuter")
        assert set(Mn.option_structs) == {"OptInner", "OptOuter"}, set(Mn.option_structs)
        assert [f[0] for f in e["fields"]] == ["language", "skin"] and e["fields"][1][1].rust == "LuaOptInnerOpts"
        rs = g.emit_types(_types.SimpleNamespace(m=Mn), "test")
        assert "let v = <LuaOptInnerOpts as FromLua>::from_lua(v, lua)" in rs and "out.skin = v.0;" in rs, rs
        assert g.luau_option_struct("OptOuter", e) == "type OptOuter = { language: string?, skin: OptInner? }"
        try:
            g.Mapper(ix).use_option_struct("OptLoop")
            raise AssertionError("self-referential option struct accepted")
        except g.Skip:
            pass
    finally:
        for k in ("OptInner", "OptOuter", "OptLoop"):
            ix.defs.pop(k, None)
            g.PATH_OVERRIDES.pop(k, None)
    n += 5
    # [option_structs] config: a listed name is read from a (partial) Luau table when it is a parameter
    ix.defs["OptCfg"] = [mkd("OptCfg", [("language", P("String"), True)], ["#[derive(Default)]"])]
    g.PATH_OVERRIDES["OptCfg"] = "azalea::OptCfg"
    try:
        assert "OPTION_STRUCTS" in vars(g) and g.OPTION_STRUCTS == g.load_config()["OPTION_STRUCTS"]
        g.OPTION_STRUCTS["OptCfg"] = "test"
        Mc = g.Mapper(ix)
        i = Mc.map_ty(P("OptCfg"), "param")
        assert i.rust == "LuaOptCfgOpts" and i.luau == "OptCfg" and i.from_lua("x") == "x.0", (i.rust, i.luau)
        assert "OptCfg" in Mc.option_structs and ("type", "OptCfg") in Mc.touched
        Mr = g.Mapper(ix)
        try:
            Mr.map_ty(P("OptCfg"), "ret")
        except g.Skip:
            pass
        assert "OptCfg" not in Mr.option_structs
    finally:
        g.OPTION_STRUCTS.pop("OptCfg", None)
        ix.defs.pop("OptCfg", None)
        g.PATH_OVERRIDES.pop("OptCfg", None)
    n += 5
    # structured skips (slice 13): closed CODES, every raise site coded, report counts
    try:
        g.Skip("x")
        raise AssertionError("Skip without a code must be a TypeError")
    except TypeError:
        pass
    for bad in ("nope", "", "other", None):
        try:
            g.Skip("x", code=bad)
            raise AssertionError(f"Skip code {bad!r} must be refused")
        except ValueError:
            pass
    e = g.Skip("why", "generic", hint="[records]")
    assert (e.reason, e.code, e.hint, str(e)) == ("why", "generic", "[records]", "why") and g.Skip("y", code="receiver").hint == ""
    assert len(g.CODES) == 22 and len(set(g.CODES)) == 22 and list(g.CODES) == list(dict.fromkeys(g.CODES))
    n += 4
    import ast
    tree = ast.parse(Path(g.__file__).read_text())
    used, sites = set(), 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and getattr(node.exc.func, "id", None) == "Skip":
            sites += 1
            call = node.exc
            if any(isinstance(a, ast.Starred) for a in call.args):   # re-raise of a stored Skip: (reason, code, hint)
                continue
            kw = {k.arg: k.value for k in call.keywords}
            assert "code" in kw, f"raise Skip without code= at line {node.lineno}"
            v = kw["code"]
            vals = [v] if not isinstance(v, ast.IfExp) else [v.body, v.orelse]
            for x in vals:
                if isinstance(x, ast.Constant):
                    assert x.value in g.CODES, f"line {node.lineno}: code {x.value!r} not in CODES"
                    used.add(x.value)
                else:
                    assert isinstance(x, ast.Attribute) and x.attr == "code", f"line {node.lineno}: code must be a literal or `e.code`"
    assert sites == 144, sites   # 93 at slice 13, +6 coded sites of slice 16 (tuple/array), +9 of slice 17 (references), +3 of slice 18 (tuple structs), +7 of slice 19 (wrappers), +1 s19 follow-up (a std name of a crate that is not loaded), +8 of slice 20 (value parameters), +3 of slice 21 (serde), +4 of slice 23 (statics: denied, async, shared bot, needs the client), +10 of slice 25 (callback_ty)
    unused = set(g.CODES) - used
    assert not unused, f"CODES with no raise site: {sorted(unused)}"
    n += 3
    # closedness: every rdj type kind, both roles: a TypeInfo or a coded Skip, never another exception
    kinds = [("unit",), ("prim", "bool"), ("prim", "u8"), ("prim", "f64"), ("prim", "char"), ("prim", "str"), ("prim", "never"),
             ("tuple", [P("String"), ("prim", "u8")]), ("slice", P("String")), ("ref", P("String")), ("ref", ("prim", "str")),
             ("impl", "impl Into<String>"), ("impl", "impl Fn(u8) -> u8"), ("other", "[u8; 4]"), ("other", "*const u8"), ("other", "dyn Fn()"),
             ("other", "fn(u8)"), ("other", "<T as Tr>::X"), ("other", "_"), P("T"), P("NoSuchType"), P("String"), P("Vec", P("String")),
             P("Vec", ("tuple", [P("String")])), P("Option", P("Option", P("String"))), P("Result", P("String"), P("String")),
             P("Arc", P("String")), P("Box", P("String")), P("HashMap", P("String")), P("Vec")]
    for t in kinds:
        for role in ("param", "ret"):
            try:
                assert isinstance(g.Mapper(ix).map_ty(t, role), g.TypeInfo)
            except g.Skip as e:
                assert e.code in g.CODES, (t, role, e.code)
    n += 1
    # the report: skipped_by_code adds up to the skipped methods of the fake azalea, and is sorted by code
    import json, subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "rust").mkdir()
        (Path(td) / "luau").mkdir()
        fake = HERE.parent / "harness" / "fake"
        run = subprocess.run([sys.executable, str(HERE / "luaxite_gen.py"), "--azalea", str(fake), "--rustdoc", str(fake / "rustdoc"),
                              "--rust-out", str(Path(td) / "rust"), "--luau-out", str(Path(td) / "luau")], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        rep = json.loads((Path(td) / "luau" / "GEN_REPORT.json").read_text())
        md = (Path(td) / "luau" / "GEN_REPORT.md").read_text()
        again = json.loads(json.dumps(rep))
    by = rep["skipped_by_code"]
    assert by == {"closure": 2, "excluded": 1, "generic": 4, "receiver": 2, "tuple_struct": 2}, by
    assert sum(by.values()) == sum(len(c["skipped"]) for c in rep["classes"]) == 11 and list(by) == sorted(by) and set(by) <= set(g.CODES)
    assert again == rep
    assert "## Skipped by code\n\n| code | methods |\n| --- | ---: |\n| `generic` | 4 |\n| `closure` | 2 |\n| `receiver` | 2 |\n| `tuple_struct` | 2 |\n| `excluded` | 1 |\n" in md
    n += 4
    # Mapper.classify (s14): pure, shape-only; kind / mode / owned / code per the rules R1-R4 of spec@s6
    C = g.Cls
    cm = lambda ty, role="ret", self_ty=None: g.Mapper(ix).classify(ty, role, self_ty)
    assert g.CLS_KINDS == ("scalar", "value", "handle", "class", "opaque", "unsupported") and g.CLS_MODES == ("value", "ref", "mut_ref")
    assert C._fields == ("kind", "mode", "owned", "reason", "code") and C("scalar", "value", False, "x").code == ""
    # (1) `&mut T` of a class: the class kind, mut_ref, not owned (CLASSES has EntityRef; the spec's `Context` is egui's)
    c = cm(("other", "&mut EntityRef"), "param")
    assert (c.kind, c.mode, c.owned) == ("class", "mut_ref", False), c
    c = cm(("ref", P("EntityRef")), "param")
    assert (c.kind, c.mode, c.owned) == ("class", "ref", False), c
    c = cm(P("EntityRef"), "ret")
    assert (c.kind, c.mode, c.owned) == ("class", "value", True), c
    # (2) Arc<T> of a class is an owned class value
    c = cm(P("Arc", P("EntityRef")), "param")
    assert (c.kind, c.mode, c.owned) == ("class", "value", True), c
    c = cm(P("Arc", P("String")), "ret")
    assert (c.kind, c.owned) == ("scalar", False), c
    # (5) String / &str / primitives / unit
    assert cm(P("String"), "param")[:3] == ("scalar", "value", False)
    assert cm(("ref", ("prim", "str")), "param")[:3] == ("scalar", "ref", False)
    assert cm(("ref", P("String")), "param")[:3] == ("scalar", "ref", False)
    assert cm(("prim", "u8"))[0] == "scalar" and cm(("unit",))[0] == "scalar"
    c = cm(("prim", "f128"))
    assert (c.kind, c.code) == ("unsupported", "type_unsupported"), c
    assert cm(("prim", "char"))[:3] == ("scalar", "value", False)
    # Self, Entity, built-ins, Result, Option<()>, impl, tuple, slice, generic type
    assert cm(("other", "Self"), "ret", "EntityRef")[:3] == ("class", "value", True)
    assert cm(("other", "&mut Self"), "param", "EntityRef")[:3] == ("class", "mut_ref", False)
    c = cm(("other", "Self"))
    assert (c.kind, c.code) == ("unsupported", "type_unsupported"), c
    assert cm(P("Entity"))[:3] == ("handle", "value", False)
    assert cm(P("BlockPos"), "param")[0] == "scalar"
    c = cm(P("ItemStack"), "param")
    assert (c.kind, c.code) == ("unsupported", "direction") and cm(P("ItemStack"), "ret")[0] == "scalar", c
    assert cm(P("Result", P("String"), P("MissingComponentError")), "ret")[0] == "scalar"
    assert cm(P("Result", P("String"), P("MissingComponentError")), "param").code == "result"
    assert cm(P("Option", ("unit",)), "ret")[0] == "value" and cm(P("Option", ("unit",)), "param").code == "type_unsupported"
    assert cm(("impl", "impl Into<String>"), "param")[0] == "scalar" and cm(("impl", "impl Fn(u8)"), "param").code == "closure"
    assert cm(("slice", P("String"))).code == "type_unsupported"   # a bare `[T]` is unsized (slice 16 maps tuples and arrays, not this)
    assert cm(("tuple", [P("String")]))[:3] == ("value", "value", False) and cm(("array", P("String"), "3"))[0] == "value"
    assert cm(P("Foo", P("String"))).code == "generic" and cm(("other", "&mut [u8]")).code == "reference"
    assert cm(("other", "*const u8")).code == "type_unsupported"
    # containers: values stay values; classes keep their kind (Vec<EntityRef> maps today); a map of a failing type fails
    assert cm(P("Vec", P("String")))[:3] == ("value", "value", False)
    assert cm(P("Option", ("prim", "u8")))[0] == "value" and cm(P("HashMap", P("String"), ("prim", "f64")))[0] == "value"
    assert cm(P("Vec", P("EntityRef")), "ret")[:3] == ("class", "value", True)
    assert cm(P("Vec", ("prim", "f128"))).code == "type_unsupported"
    assert cm(P("Box", ("slice", P("String"))))[0] == "value"
    n += 31
    # (4) structs from the shape: pub fields -> value, private/stripped -> opaque, tuple -> unsupported, generic -> unsupported
    shapes = {
        "ClsPub": mkd("ClsPub", [("a", ("prim", "u8"), True), ("b", P("String"), True)]),
        "ClsStripped": mkd("ClsStripped", [("a", ("prim", "u8"), True), ("..", ("other", "stripped"), False)]),
        "ClsPrivate": mkd("ClsPrivate", [("a", ("prim", "u8"), False)]),
        "ClsEmpty": mkd("ClsEmpty", []),
        "ClsTuple": mkd("ClsTuple", [("0", ("prim", "u8"), False)], tuple_style=True),
        "ClsTupleNew": mkd("ClsTupleNew", [("0", ("prim", "u8"), True)], tuple_style=True),
        "ClsGeneric": mkd("ClsGeneric", [("a", ("prim", "u8"), True)], generics=True),
        "ClsAcc": mkd("ClsAcc", [("a", ("prim", "u8"), False)]),
        "ClsRec": mkd("ClsRec", [("a", ("prim", "u8"), True)]),
    }
    for k, d in shapes.items():
        ix.defs[k] = [d]
    g.ACCESSORS["ClsAcc"] = dict(fields=[], getters=[])
    g.RECORDS["ClsRec"] = [("a", "number", "v.a")]
    try:
        assert cm(P("ClsPub"))[:3] == ("value", "value", False)
        for k in ("ClsStripped", "ClsPrivate", "ClsEmpty"):
            c = cm(P(k))
            assert (c.kind, c.code) == ("opaque", "private_fields"), (k, c)
        c = cm(P("ClsTuple"))
        assert (c.kind, c.code) == ("unsupported", "tuple_struct") and "tuple" in c.reason, c
        assert cm(P("ClsTupleNew"))[:3] == ("value", "value", False) and cm(P("ClsTupleNew"), "param")[:3] == ("value", "value", False)   # slice 18
        c = cm(P("ClsGeneric"))
        assert (c.kind, c.code) == ("unsupported", "generic"), c
        assert cm(P("ClsAcc"))[0] == "value" and cm(P("ClsRec"))[0] == "value"
        assert cm(P("ClsPub"), "param")[:3] == ("value", "value", False) and cm(P("ClsRec"), "param").code == "direction"   # slice 20: a plain struct is read from a table, a [records] row is write-only
        # (3) R3 narrowed: an opaque element inside a container is unsupported ("container" in the reason)
        for t in (P("Vec", P("ClsPrivate")), P("Option", P("ClsPrivate")), P("HashMap", P("String"), P("ClsPrivate"))):
            c = cm(t)
            assert c.kind == "unsupported" and "container" in c.reason, (t, c)
        assert cm(("ref", P("ClsPrivate")), "param")[:3] == ("opaque", "ref", False)
        # name clash (a Luau type name already taken) and a failed name
        c = cm(P("Client"))
        assert c.kind in ("unsupported", "value", "opaque"), c
        Mf = g.Mapper(ix)
        Mf.fail["ClsPub"] = ("boom", "not_convertible", "")
        c = Mf.classify(P("ClsPub"), "ret")
        assert (c.kind, c.reason, c.code) == ("unsupported", "boom", "not_convertible"), c
        Mf.building.add("ClsPub")
        Mf.fail.clear()
        assert Mf.classify(P("ClsPub"), "ret").code == "recursive"
    finally:
        for k in shapes:
            ix.defs.pop(k, None)
        g.ACCESSORS.pop("ClsAcc", None)
        g.RECORDS.pop("ClsRec", None)
    n += 17
    # (6) purity: nothing of the Mapper changes, whatever is classified
    Mp = g.Mapper(ix)
    snap = lambda m: (dict(m.cache), dict(m.fail), set(m.touched), dict(m.records), dict(m.enums), dict(m.tagged), dict(m.option_structs),
                      dict(m.paths), m.cur_class, set(m.building), list(m.dep_stack), list(m.client_flags))
    before = snap(Mp)
    names = sorted(ix.defs)
    for nm in names:
        for role in ("param", "ret"):
            Mp.classify(P(nm), role)
            Mp.classify(P("Vec", P(nm)), role)
            Mp.classify(("ref", P(nm)), role)
    assert snap(Mp) == before and not Mp.touched
    n += 1
    # (7) table-driven over every type of the fake azalea: the kind agrees with the branch `Mapper.named` takes
    checked = 0
    own_failure = re.compile(r"^(struct|enum) `\w+`[.:]")
    for nm in names:
        if nm == "Entity":
            continue
        for role in ("param", "ret"):
            c = g.Mapper(ix).classify(P(nm), role)
            assert c.kind in g.CLS_KINDS and c.mode == "value", (nm, role, c)
            info = None
            try:
                info = g.Mapper(ix).named(nm, role)
                got = None
            except g.Skip as e:
                got = e
            if got is None and c.kind == "unsupported":
                # `named` builds a record / tagged enum for a param too, but with no `from_lua`: classify says so up front (s20 adds it)
                assert role == "param" and c.code == "direction" and info.from_lua is None, (nm, role, c)
            elif got is None:
                assert c.kind in ("scalar", "value", "class", "handle"), (nm, role, c)
            elif got.code in ("generic", "tuple_struct", "private_fields", "no_definition", "name_clash", "shape_unsupported", "recursive") \
                    and not own_failure.match(got.reason):
                assert c.kind in ("unsupported", "opaque") and c.code == got.code, (nm, role, got.code, got.reason, c)
            elif got.code == "direction" and not own_failure.match(got.reason):
                assert c.code == "direction", (nm, role, got.reason, c)
            checked += 1
    assert checked >= 60, checked
    n += 1
    # [path_prefer] (s15): path_for hands the prefix to the index; [path_overrides] still wins; non-string -> ConfigError
    class PIx:
        calls = []
        def resolve_path(self, name, prefer=None):
            self.calls.append((name, prefer))
            return f"azalea::{prefer or 'any'}::{name}" if name != "Gone" else None
    Mq = g.Mapper(PIx())
    assert Mq.path_for("Chest") == "azalea::any::Chest" and PIx.calls[-1] == ("Chest", None)   # no entry: the one-argument call as before
    g.PATH_PREFER["Chest"] = "meta"
    g.PATH_OVERRIDES["Over"] = "azalea::forced::Over"
    g.PATH_PREFER["Over"] = "meta"
    g.PATH_PREFER["Gone"] = "meta"
    try:
        assert Mq.path_for("Chest") == "azalea::meta::Chest" and PIx.calls[-1] == ("Chest", "meta")
        n_calls = len(PIx.calls)
        assert Mq.path_for("Over") == "azalea::forced::Over" and len(PIx.calls) == n_calls          # override first, index not asked
        try:
            Mq.path_for("Gone")
            raise AssertionError("no path accepted")
        except g.Skip as e:
            assert e.code == "no_path" and e.hint == "[path_overrides]", e
    finally:
        for k in ("Chest", "Over", "Gone"):
            g.PATH_PREFER.pop(k, None)
        g.PATH_OVERRIDES.pop("Over", None)
    assert g.PATH_PREFER == {} == g.load_config()["PATH_PREFER"]
    import tempfile
    base = (HERE.parent / "bindgen.toml").read_text()
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "c.toml"
        f.write_text(base.replace("[path_prefer]\n", '[path_prefer]\nChest = "azalea::entity"\n'))
        assert g.load_config(f)["PATH_PREFER"] == {"Chest": "azalea::entity"}
        f.write_text(base.replace("[path_prefer]\n", "[path_prefer]\nChest = 3\n"))
        try:
            g.load_config(f)
            raise AssertionError("non-string path_prefer accepted")
        except g.ConfigError as e:
            assert "path_prefer.Chest" in str(e), e
    n += 6
    # slice 16: tuples and `[T; N]` <-> Luau arrays (exact text of the Luau type, the Rust type and both conversions)
    Mt = g.Mapper(ix)
    u8, u16, f64 = ("prim", "u8"), ("prim", "u16"), ("prim", "f64")

    def okt(t, role):
        i = Mt.map_ty(t, role)
        return i, (i.to_lua("x") if i.to_lua else None), (i.from_lua("x") if i.from_lua else None)

    def skt(t, role, why, code):
        try:
            Mt.map_ty(t, role)
        except g.Skip as e:
            assert why in str(e) and e.code == code, f"{g.type_text(t)} ({role}): wanted `{why}`/{code}, got `{e}`/{e.code}"
            return
        raise AssertionError(f"{g.type_text(t)} ({role}) should have been skipped (`{why}`)")

    assert ("seq", "LuaTuple") not in Mt.touched and ("seq", "LuaArray") not in Mt.touched
    i, to, frm = okt(("tuple", [u8, u16]), "ret")
    assert (i.rust, i.luau, to, frm) == ("LuaTuple<(u8, u16)>", "{ number }", "LuaTuple(x)", None), (i, to, frm)
    i, to, frm = okt(("tuple", [u8, u16]), "param")
    assert (i.rust, i.luau, to, frm) == ("LuaTuple<(u8, u16)>", "{ number }", None, "x.0"), (i, to, frm)
    i, to, _ = okt(("tuple", [P("String"), u8]), "ret")
    assert (i.rust, i.luau, to) == ("LuaTuple<(String, u8)>", "{ string | number }", "LuaTuple(x)"), (i, to)
    i, to, _ = okt(("tuple", [P("Option", u8), P("String")]), "ret")
    assert i.luau == "{ number? | string }" and i.rust == "LuaTuple<(Option<u8>, String)>", i
    i, to, _ = okt(("tuple", [u8]), "ret")
    assert (i.rust, i.luau) == ("LuaTuple<(u8,)>", "{ number }"), i
    i, to, _ = okt(("tuple", [P("Uuid"), P("PlayerInfo")]), "ret")
    assert i.rust == "LuaTuple<(String, LuaPlayerInfo)>" and i.luau == "{ string | PlayerInfo }", i
    assert to == "{ let (t0, t1) = x; LuaTuple((t0.to_string(), LuaPlayerInfo(t1))) }", to
    i, _, frm = okt(("tuple", [("prim", "bool"), u8]), "param")
    assert (i.rust, i.luau, frm) == ("LuaTuple<(LBool, u8)>", "{ boolean | number }", "{ let (t0, t1) = x.0; (t0.0, t1) }"), (i, frm)
    i, to, _ = okt(("tuple", [("tuple", [u8, u8]), P("String")]), "ret")
    assert i.rust == "LuaTuple<(LuaTuple<(u8, u8)>, String)>" and i.luau == "{ { number } | string }", i
    assert to == "{ let (t0, t1) = x; LuaTuple((LuaTuple(t0), t1)) }", to
    assert ("seq", "LuaTuple") in Mt.touched
    i, to, frm = okt(("array", f64, "3"), "ret")
    assert (i.rust, i.luau, to, frm) == ("LuaArray<f64, 3>", "{ number }", "LuaArray(x)", None), (i, to, frm)
    i, to, frm = okt(("array", f64, "3"), "param")
    assert (i.rust, to, frm) == ("LuaArray<f64, 3>", None, "x.0"), (i, to, frm)
    i, _, frm = okt(("array", ("prim", "bool"), "2"), "param")
    assert (i.rust, i.luau, frm) == ("LuaArray<LBool, 2>", "{ boolean }", "x.0.map(|v| v.0)"), (i, frm)
    i, to, _ = okt(("array", P("PlayerInfo"), "2"), "ret")
    assert (i.rust, i.luau, to) == ("LuaArray<LuaPlayerInfo, 2>", "{ PlayerInfo }", "LuaArray(x.map(|v| LuaPlayerInfo(v)))"), (i, to)
    i, _, _ = okt(("array", u8, "0"), "ret")
    assert i.rust == "LuaArray<u8, 0>" and okt(("array", u8, "007"), "ret")[0].rust == "LuaArray<u8, 7>"
    assert ("seq", "LuaArray") in Mt.touched
    n += 31
    # inside the existing containers
    i, to, _ = okt(P("Vec", ("tuple", [u8, u8])), "ret")
    assert (i.rust, i.luau, to) == ("Vec<LuaTuple<(u8, u8)>>", "{ { number } }", "x.into_iter().map(|v| LuaTuple(v)).collect::<Vec<_>>()"), (i, to)
    i, _, frm = okt(P("Vec", ("tuple", [u8, u8])), "param")
    assert frm == "x.into_iter().map(|v| v.0).collect::<Vec<_>>()", frm
    i, _, frm = okt(P("Vec", ("array", ("prim", "bool"), "2")), "param")
    assert frm == "x.into_iter().map(|v| v.0.map(|v| v.0)).collect::<Vec<_>>()", frm
    i, to, frm = okt(P("Option", ("array", u8, "4")), "ret")
    assert (i.rust, i.luau, to) == ("Option<LuaArray<u8, 4>>", "{ number }?", "x.map(|v| LuaArray(v))"), (i, to)
    i, _, frm = okt(P("Option", ("tuple", [u8, u8])), "param")
    assert frm == "x.map(|v| v.0)", frm
    i, to, _ = okt(P("HashMap", P("String"), ("tuple", [u8, u8])), "ret")
    assert i.luau == "{ [string]: { number } }" and to == "x.into_iter().map(|(k, v)| (k, LuaTuple(v))).collect::<std::collections::HashMap<_, _>>()", (i, to)
    n += 10
    # refusals, each with its code
    skt(("tuple", [u8, ("unit",)]), "ret", "tuple with a `()` element", "type_unsupported")
    skt(("array", ("unit",), "2"), "ret", "array with a `()` element", "type_unsupported")
    skt(("tuple", [("ref", P("String")), u8]), "param", "tuple with a reference element", "reference")
    skt(("array", ("ref", ("prim", "str")), "2"), "param", "array with a reference element", "reference")
    skt(("tuple", [u8] * 13), "ret", "tuple of 13 elements (supported: 1 to 12)", "type_unsupported")
    assert okt(("tuple", [u8] * 12), "ret")[0].rust == "LuaTuple<(" + ", ".join(["u8"] * 12) + ")>"
    skt(("array", u8, "N"), "ret", "array length `N` is not a decimal literal", "type_unsupported")
    skt(("array", u8, "N * 2"), "ret", "is not a decimal literal", "type_unsupported")
    skt(("array", u8, "4097"), "ret", "array of 4097 elements (supported: up to 4096)", "type_unsupported")
    assert okt(("array", u8, "4096"), "ret")[0].rust == "LuaArray<u8, 4096>"
    skt(("tuple", [P("Result", u8, P("MissingComponentError")), u8]), "ret", "nested fallible type in tuple", "nested_fallible")
    skt(("tuple", [("prim", "f128"), u8]), "ret", "primitive `f128`", "type_unsupported")
    skt(("array", P("NoSuchType"), "2"), "ret", "NoSuchType", "no_definition")
    n += 12
    # classify agrees with map_ty (same refusals, container rule over the elements)
    cs = lambda t, role="ret": M.classify(t, role)
    assert cs(("tuple", [u8, P("String")]))[:3] == ("value", "value", False) and cs(("array", f64, "3"), "param")[:3] == ("value", "value", False)
    assert cs(("tuple", [P("EntityRef"), u8]))[:3] == ("class", "value", True)
    assert cs(("tuple", [u8, ("unit",)])).code == "type_unsupported" and cs(("tuple", [u8] * 13)).code == "type_unsupported"
    assert cs(("tuple", [("ref", P("String"))]), "param").code == "reference" and cs(("array", u8, "N")).code == "type_unsupported"
    assert cs(("tuple", [("ref", P("String"))]))[:3] == ("value", "value", False)   # slice 17: a borrowed element of a RETURN is copied out
    assert cs(("array", u8, "4097")).code == "type_unsupported" and cs(("array", ("prim", "f128"), "2")).code == "type_unsupported"
    for t in (("tuple", [u8, u16]), ("array", f64, "3"), ("tuple", [P("Uuid"), P("PlayerInfo")]), ("tuple", [u8, ("unit",)]),
              ("array", u8, "N"), ("tuple", [("ref", P("String")), u8]), ("tuple", [u8] * 13), ("array", ("prim", "f128"), "2")):
        for role in ("param", "ret"):
            if role == "param" and "PlayerInfo" in g.type_text(t):
                continue   # a [records] row classifies unsupported(direction) as a parameter; map_ty still builds it, without from_lua (s20)
            c = cs(t, role)
            try:
                g.Mapper(ix).map_ty(t, role)
                mapped = True
            except g.Skip as e:
                mapped, mcode = False, e.code
            assert mapped == (c.kind != "unsupported") and (mapped or mcode == c.code), (g.type_text(t), role, c)
    n += 7
    # Rust helpers: nothing unless used; only what is used; one impl per arity
    assert g.emit_seq_helpers(set()) == []
    only_arr = "\n".join(g.emit_seq_helpers({"LuaArray"}))
    assert "pub struct LuaArray<T, const N: usize>(pub [T; N]);" in only_arr and "LuaTuple" not in only_arr and "fn lua_seq_table(" in only_arr
    both = "\n".join(g.emit_seq_helpers({"LuaTuple", "LuaArray"}))
    assert len(re.findall(r"(?m)^    \(\d+; ", both)) == 12 and "    (1; A 0)," in both and "    (3; A 0, B 1, C 2)," in both, both
    assert "    (12; A 0, B 1, C 2, D 3, E 4, F 5, G 6, H 7, I 8, J 9, K 10, L 11)," in both and "    (13;" not in both
    assert "pub struct LuaTuple<T>(pub T);" in both and "$( t.raw_set($i + 1, self.0.$i)?; )+" in both
    gt = g.Generator(ix)
    assert "LuaTuple" not in g.emit_types(gt, "fake") and "LuaArray" not in g.emit_types(gt, "fake")   # the fake azalea has no tuple/array signature
    gt.m.map_ty(("tuple", [u8, u8]), "ret")
    txt = g.emit_types(gt, "fake")
    assert "pub struct LuaTuple<T>(pub T);" in txt and "LuaArray" not in txt and "use super::convert::*;" in txt, txt[:400]
    n += 12
    # a method with tuple / array parameters and a tuple return goes through build_method (wrapper text, no hand glue)
    gm = g.Generator(ix)
    meth = g.Method("bounds", "", True, False, False, "&self", [("origin", ("array", f64, "3")), ("flags", ("tuple", [("prim", "bool"), u8]))],
                    ("tuple", [P("String"), u8]), "Client", "t.rs", "pub fn bounds(&self)")
    ws = gm.build_method(meth)
    assert len(ws) == 1 and ws[0].name == "bounds", ws
    w = ws[0]
    assert [(nm, ti.rust) for nm, ti in w.wparams] == [("origin", "LuaArray<f64, 3>"), ("flags", "LuaTuple<(LBool, u8)>")], w.wparams
    assert w.call_args == ["origin.0", "{ let (t0, t1) = flags.0; (t0.0, t1) }"], w.call_args
    assert w.ret.rust == "LuaTuple<(String, u8)>" and w.ret.to_lua("self.client.bounds()") == "LuaTuple(self.client.bounds())"
    assert g.rust_sig_ret(w)[:2] == ("#[lua(infallible)]", " -> LuaTuple<(String, u8)>")
    gm.m.touched.add(("type", "x"))
    meth2 = g.Method("bad", "", True, False, False, "&self", [("p", ("tuple", [u8, ("unit",)]))], None, "Client", "t.rs", "pub fn bad(&self)")
    try:
        gm.build_method(meth2)
        raise AssertionError("a tuple with `()` must skip the method")
    except g.Skip as e:
        assert e.code == "type_unsupported" and "tuple with a `()` element" in e.reason, e
    n += 6
    # slice 17: references. A parameter `&T` is read into an owned wrapper local and borrowed for the call; a returned `&T` is copied out at
    # once (`.to_string()`, `.clone()`, `*x`); `&&T` and `&mut T` are coded skips
    Mr = g.Mapper(ix)
    b = ("prim", "bool")
    S, STR = P("String"), ("prim", "str")

    def okr(t, role):
        i = Mr.map_ty(t, role)
        return i, (i.to_lua("x") if i.to_lua else None), (i.from_lua("x") if i.from_lua else None)

    def skr(t, role, why, code):
        try:
            Mr.map_ty(t, role)
        except g.Skip as e:
            assert why in str(e) and e.code == code, f"{g.type_text(t)} ({role}): wanted `{why}`/{code}, got `{e}`/{e.code}"
            return
        raise AssertionError(f"{g.type_text(t)} ({role}) should have been skipped (`{why}`)")

    # parameters
    i, _, frm = okr(("ref", STR), "param")
    assert (i.rust, i.luau, frm) == ("String", "string", "&x"), (i, frm)
    i, _, frm = okr(("ref", S), "param")
    assert (i.rust, i.luau, frm) == ("String", "string", "&x"), (i, frm)
    i, _, frm = okr(("ref", u8), "param")
    assert (i.rust, i.luau, frm) == ("u8", "number", "&x"), (i, frm)
    i, _, frm = okr(("ref", b), "param")
    assert (i.rust, i.luau, frm) == ("LBool", "boolean", "&x.0"), (i, frm)
    i, _, frm = okr(("ref", ("slice", u8)), "param")
    assert (i.rust, i.luau, frm) == ("Vec<u8>", "{ number }", "&x"), (i, frm)
    i, _, frm = okr(("ref", ("slice", b)), "param")
    assert (i.rust, i.luau, frm) == ("Vec<LBool>", "{ boolean }", "&x.into_iter().map(|v| v.0).collect::<Vec<_>>()"), (i, frm)
    i, _, frm = okr(("ref", ("slice", ("ref", STR))), "param")
    assert (i.rust, i.luau, frm) == ("Vec<String>", "{ string }", "&x.iter().map(|v| v.as_str()).collect::<Vec<_>>()"), (i, frm)
    i, _, frm = okr(P("Vec", ("ref", STR)), "param")
    assert (i.rust, i.luau, frm) == ("Vec<String>", "{ string }", "x.iter().map(|v| v.as_str()).collect::<Vec<_>>()"), (i, frm)
    i, _, frm = okr(("ref", P("Option", u8)), "param")
    assert (i.rust, i.luau, frm) == ("Option<u8>", "number?", "&x"), (i, frm)
    i, _, frm = okr(P("Option", ("ref", STR)), "param")
    assert (i.rust, i.luau, frm) == ("Option<String>", "string?", "x.as_deref()"), (i, frm)
    i, _, frm = okr(P("Option", ("ref", u8)), "param")
    assert (i.rust, i.luau, frm) == ("Option<u8>", "number?", "x.as_ref()"), (i, frm)
    i, _, frm = okr(P("Option", ("ref", b)), "param")
    assert (i.rust, i.luau, frm) == ("Option<LBool>", "boolean?", "x.map(|v| v.0).as_ref()"), (i, frm)
    i, _, frm = okr(("ref", ("tuple", [u8, u8])), "param")
    assert (i.rust, frm) == ("LuaTuple<(u8, u8)>", "&x.0"), (i, frm)
    i, _, frm = okr(("ref", ("array", u8, "3")), "param")
    assert (i.rust, frm) == ("LuaArray<u8, 3>", "&x.0"), (i, frm)
    i, _, frm = okr(("ref", P("Uuid")), "param")
    assert (i.rust, frm, i.fallible_from) == ("String", "&x.parse().map_err(lua_err)?", True), (i, frm)
    cfg = g.CLASSES["EntityRef"]
    i, _, frm = okr(("ref", P("EntityRef")), "param")
    assert (i.rust, i.is_class, frm) == (f"mlua::UserDataRef<{cfg['rust']}>", True, f"&x.{cfg['field']}"), (i, frm)   # borrowed from the userdata, no clone
    i, _, frm = okr(P("EntityRef"), "param")
    assert frm == f"x.{cfg['field']}.clone()", frm   # the by-value parameter still clones
    i, _, frm = okr(("ref", P("PlayerInfo")), "param")
    assert frm is None   # a [records] type has no parameter form (s20): build_method skips it as not convertible
    n += 20
    # returns
    for t, to in ((("ref", STR), "x.to_string()"), (("ref", S), "x.clone()"), (("ref", u8), "*x"), (("ref", b), "*x"),
                  (("ref", P("Uuid")), "x.to_string()"), (("ref", P("PlayerInfo")), "LuaPlayerInfo(x.clone())"),
                  (("ref", P("EntityRef")), f"LuaEntityRef {{ {cfg['field']}: x.clone() }}"), (("ref", P("Option", u8)), "x.clone()"),
                  (("ref", ("array", u8, "3")), "LuaArray(x.clone())")):
        assert okr(t, "ret")[1] == to, (g.type_text(t), okr(t, "ret")[1])
    i, to, _ = okr(("ref", ("slice", u8)), "ret")
    assert (i.rust, i.luau, to) == ("Vec<u8>", "{ number }", "x.to_vec()"), (i, to)
    assert okr(("ref", ("slice", S)), "ret")[1] == "x.to_vec()"
    assert okr(("ref", ("slice", ("ref", STR))), "ret")[1] == "x.iter().map(|v| (*v).to_string()).collect::<Vec<_>>()"
    assert okr(("ref", ("slice", P("Uuid"))), "ret")[1] == "x.iter().map(|v| v.to_string()).collect::<Vec<_>>()"
    assert okr(("ref", ("slice", ("ref", u8))), "ret")[1] == "x.iter().map(|v| *(*v)).collect::<Vec<_>>()"
    i, to, _ = okr(("ref", ("slice", P("PlayerInfo"))), "ret")
    assert (i.rust, i.luau, to) == ("Vec<LuaPlayerInfo>", "{ PlayerInfo }", "x.iter().map(|v| LuaPlayerInfo(v.clone())).collect::<Vec<_>>()"), (i, to)
    n += 15
    # inside the existing containers (a returned borrow is copied out, a parameter one is not allowed except `&str` / Option)
    i, to, _ = okr(P("Option", ("ref", STR)), "ret")
    assert (i.rust, i.luau, to) == ("Option<String>", "string?", "x.map(|v| v.to_string())"), (i, to)
    assert okr(P("Option", ("ref", u8)), "ret")[1] == "x.map(|v| *v)"
    assert okr(P("Vec", ("ref", STR)), "ret")[1] == "x.into_iter().map(|v| v.to_string()).collect::<Vec<_>>()"
    i, to, _ = okr(("tuple", [("ref", STR), u8]), "ret")
    assert (i.rust, i.luau, to) == ("LuaTuple<(String, u8)>", "{ string | number }", "{ let (t0, t1) = x; LuaTuple((t0.to_string(), t1)) }"), (i, to)
    assert okr(("array", ("ref", STR), "2"), "ret")[1] == "LuaArray(x.map(|v| v.to_string()))"
    assert okr(P("Result", ("ref", STR), P("MissingComponentError")), "ret")[1] == "x.map_err(lua_err).map(|v| v.to_string())"
    assert okr(P("HashMap", ("ref", STR), u8), "ret")[1] == "x.into_iter().map(|(k, v)| (k.to_string(), v)).collect::<std::collections::HashMap<_, _>>()"
    n += 7
    # refusals, each with its code
    skr(("ref", ("ref", STR)), "ret", "reference to a reference `&&str`", "reference")
    skr(("ref", ("ref", u8)), "param", "reference to a reference", "reference")
    skr(("other", "&mut Foo"), "param", "mutable reference type `&mut Foo`", "reference")
    skr(("other", "&'a mut Foo"), "ret", "mutable reference type `&'a mut Foo`", "reference")
    skr(("ref", ("other", "&mut Foo")), "ret", "reference to a reference", "reference")
    skr(("ref", ("slice", ("ref", u8))), "param", "slice with a reference element `&[&u8]`", "reference")
    skr(P("Vec", ("ref", u8)), "param", "list parameter with a reference element `&u8`", "reference")
    skr(P("Option", ("ref", ("slice", u8))), "param", "`Option<&[u8]>` parameter", "reference")
    skr(P("Option", ("ref", ("ref", u8))), "param", "parameter", "reference")
    skr(P("HashMap", ("ref", STR), u8), "param", "map parameter with a reference key or value", "reference")
    skr(P("Arc", ("ref", STR)), "ret", "an `Arc` of a reference", "reference")
    skr(("ref", P("Result", u8, P("MissingComponentError"))), "ret", "reference to a Result", "reference")
    skr(("tuple", [("ref", STR), u8]), "param", "tuple with a reference element", "reference")
    skr(("ref", P("BestToolResult")), "ret", "`BestToolResult` is not `Clone`", "trait_missing")
    skr(("ref", P("ContainerHandleRef")), "ret", "`ContainerHandleRef` is not `Clone`", "trait_missing")   # a CLASSES / BUILTIN row is not trusted for Clone
    skr(("ref", P("NoSuchType")), "ret", "NoSuchType", "no_definition")
    skr(("ref", ("impl", "dyn Fn()")), "param", "`dyn Fn()` parameter", "closure")
    n += 17
    # classify agrees with map_ty on every shape above, both roles (mapped <=> not unsupported, same code)
    for t in (("ref", STR), ("ref", S), ("ref", u8), ("ref", ("slice", u8)), ("ref", ("slice", ("ref", STR))), ("ref", ("slice", ("ref", u8))),
              P("Vec", ("ref", STR)), P("Vec", ("ref", u8)), P("Option", ("ref", STR)), P("Option", ("ref", u8)), P("Option", ("ref", ("slice", u8))),
              P("Option", ("ref", ("ref", u8))), ("ref", ("ref", STR)), ("ref", P("Option", u8)), ("ref", P("Uuid")), ("ref", P("EntityRef")),
              ("ref", P("Result", u8, P("MissingComponentError"))), P("HashMap", ("ref", STR), u8), P("Arc", ("ref", STR)),
              ("tuple", [("ref", STR), u8]), ("array", ("ref", STR), "2"), ("ref", ("tuple", [u8, u8])), ("ref", ("array", u8, "3")),
              ("ref", ("impl", "dyn Fn()")), ("ref", P("NoSuchType"))):
        for role in ("param", "ret"):
            c = M.classify(t, role)
            try:
                g.Mapper(ix).map_ty(t, role)
                mapped = True
            except g.Skip as e:
                mapped, mcode = False, e.code
            assert mapped == (c.kind != "unsupported"), (g.type_text(t), role, c, mapped)
            if not mapped and c.code in ("reference",):
                assert mcode == c.code, (g.type_text(t), role, c, mcode)
    assert M.classify(("ref", ("slice", u8)), "ret")[:3] == ("value", "ref", False)
    assert M.classify(("ref", ("ref", STR)), "ret").code == "reference" and M.classify(("ref", ("slice", ("ref", u8))), "param").code == "reference"
    n += 4
    # a method through build_method: wrapper parameter types, call arguments, return conversion
    gr = g.Generator(ix)
    meth = g.Method("tag", "", True, False, False, "&self", [("names", ("ref", ("slice", ("ref", STR)))), ("n", ("ref", P("Option", u8))), ("who", ("ref", S))],
                    ("ref", STR), "Client", "t.rs", "pub fn tag(&self)")
    ws = gr.build_method(meth)
    assert len(ws) == 1, ws
    w = ws[0]
    assert [(nm, ti.rust) for nm, ti in w.wparams] == [("names", "Vec<String>"), ("n", "Option<u8>"), ("who", "String")], w.wparams
    assert w.call_args == ["&names.iter().map(|v| v.as_str()).collect::<Vec<_>>()", "&n", "&who"], w.call_args
    assert w.ret.rust == "String" and w.ret.to_lua("self.client.tag(a, b, c)") == "self.client.tag(a, b, c).to_string()"
    assert g.rust_sig_ret(w)[:2] == ("#[lua(infallible)]", " -> String"), g.rust_sig_ret(w)
    for bad_meth, code in ((g.Method("edit", "", True, False, False, "&self", [("v", ("other", "&mut Vec<u8>"))], None, "Client", "t.rs", "pub fn edit(&self)"), "reference"),
                           (g.Method("peek", "", True, False, False, "&self", [], ("ref", P("BestToolResult")), "Client", "t.rs", "pub fn peek(&self)"), "trait_missing")):
        try:
            gr.build_method(bad_meth)
            raise AssertionError(f"{bad_meth.name} must be skipped")
        except g.Skip as e:
            assert e.code == code, (bad_meth.name, e.code, e.reason)
    n += 8
    # slice 18: tuple structs and newtypes (`struct W(pub A)` = the value of A, `struct W(pub A, pub B)` = the Luau array of (A, B))
    u8 = ("prim", "u8")
    ts = lambda name, fields, attrs=(), generics=False: g.Def(
        kind="struct", name=name, crate="azalea", modpath=(), is_pub=True, generics=generics, src=b"", tuple_style=True,
        fields=[(str(k), t, pub) for k, (t, pub) in enumerate(fields)], attrs=list(attrs))
    TAKEN = sorted(g.HAND_CLASSES)[0]   # a Luau name a hand-written class already owns
    tdefs = {
        "TsOne": ts("TsOne", [(u8, True)]),
        "TsBool": ts("TsBool", [(("prim", "bool"), True)]),
        "TsStr": ts("TsStr", [(P("String"), True)]),
        "TsTwo": ts("TsTwo", [(u8, True), (P("String"), True)]),
        "TsMixed": ts("TsMixed", [(("prim", "bool"), True), (u8, True)]),
        "TsUuid": ts("TsUuid", [(P("Uuid"), True), (u8, True)]),
        "TsNest": ts("TsNest", [(P("TsOne"), True), (u8, True)]),
        "TsRef": ts("TsRef", [(("ref", ("prim", "str")), True)]),
        "TsUnit": ts("TsUnit", [(("unit",), True)]),
        "TsRec": ts("TsRec", [(P("Vec", P("TsRec")), True)]),
        "TsNE": ts("TsNE", [(u8, True)], ["#[non_exhaustive]"]),
        "TsBig": ts("TsBig", [(u8, True)] * 13),
        "TsMax": ts("TsMax", [(u8, True)] * 12),
        "TsPriv": ts("TsPriv", [(u8, False)]),
        "TsHalf": ts("TsHalf", [(u8, True), (u8, False)]),
        "TsStripped": ts("TsStripped", [(u8, True), (("other", "_"), False)]),
        "TsEmpty": ts("TsEmpty", []),
        "TsGen": ts("TsGen", [(u8, True)], generics=True),
        "TsAcc": ts("TsAcc", [(u8, True)]),
        "TsNoPath": ts("TsNoPath", [(u8, True)]),
        TAKEN: ts(TAKEN, [(u8, True)]),
        "TsHolder": mkd("TsHolder", [("a", P("TsTwo"), True), ("b", P("TsOne"), True)]),
    }
    for k, d in tdefs.items():
        ix.defs[k] = [d]
        if k != "TsNoPath":
            g.PATH_OVERRIDES[k] = "azalea::ts::" + k
    g.ACCESSORS["TsAcc"] = dict(fields=[], getters=[])
    try:
        Mq = g.Mapper(ix)

        def okq(name, role, rust, luau, to=None, frm=None):
            i = Mq.map_ty(P(name), role)
            got = (i.rust, i.luau, i.to_lua("x") if i.to_lua else None, i.from_lua("x") if i.from_lua else None)
            assert got == (rust, luau, to, frm), (name, role, got)

        def skq(name, role, why, code, hint=""):
            try:
                Mq.map_ty(P(name), role)
            except g.Skip as e:
                assert why in e.reason and e.code == code and e.hint == hint, (name, role, e.reason, e.code, e.hint)
                return
            raise AssertionError(f"{name} ({role}) should have been skipped (`{why}`)")

        # one field: transparent, the Rust type is the field's (`bool` as a return, `LBool` as a parameter), nothing registered
        okq("TsOne", "ret", "u8", "number", "x.0")
        okq("TsOne", "param", "u8", "number", None, "azalea::ts::TsOne(x)")
        okq("TsBool", "ret", "bool", "boolean", "x.0")
        okq("TsBool", "param", "LBool", "boolean", None, "azalea::ts::TsBool(x.0)")
        okq("TsStr", "ret", "String", "string", "x.0")
        okq("TsStr", "param", "String", "string", None, "azalea::ts::TsStr(x)")
        okq("TsRef", "ret", "String", "string", "x.0.to_string()")
        assert Mq.touched == set() and not Mq.cache and not Mq.fail, (Mq.touched, Mq.cache, Mq.fail)
        # several fields: the Luau array of the tuple; a return reads `.0 .1` of an owned copy, a parameter rebuilds the struct
        okq("TsTwo", "ret", "LuaTuple<(u8, String)>", "{ number | string }", "LuaTuple({ let t = x; (t.0, t.1) })")
        okq("TsTwo", "param", "LuaTuple<(u8, String)>", "{ number | string }", None, "{ let (t0, t1) = x.0; azalea::ts::TsTwo(t0, t1) }")
        okq("TsMixed", "param", "LuaTuple<(LBool, u8)>", "{ boolean | number }", None,
            "{ let (t0, t1) = { let (t0, t1) = x.0; (t0.0, t1) }; azalea::ts::TsMixed(t0, t1) }")
        okq("TsUuid", "ret", "LuaTuple<(String, u8)>", "{ string | number }",
            "{ let (t0, t1) = { let t = x; (t.0, t.1) }; LuaTuple((t0.to_string(), t1)) }")
        okq("TsNest", "ret", "LuaTuple<(u8, u8)>", "{ number }", "{ let (t0, t1) = { let t = x; (t.0, t.1) }; LuaTuple((t0.0, t1)) }")
        okq("TsNest", "param", "LuaTuple<(u8, u8)>", "{ number }", None,
            "{ let (t0, t1) = { let (t0, t1) = x.0; (azalea::ts::TsOne(t0), t1) }; azalea::ts::TsNest(t0, t1) }")
        assert Mq.touched == {("seq", "LuaTuple")}, Mq.touched
        assert okq("TsMax", "ret", "LuaTuple<(" + ", ".join(["u8"] * 12) + ")>", "{ number }", "LuaTuple({ let t = x; (" + ", ".join(f"t.{k}" for k in range(12)) + ") })") is None
        # inside containers, an option, a record (the record keeps the converted field text), a method wrapper
        i = Mq.map_ty(P("Vec", P("TsOne")), "ret")
        assert (i.rust, i.luau, i.to_lua("x")) == ("Vec<u8>", "{ number }", "x.into_iter().map(|v| v.0).collect::<Vec<_>>()"), i
        i = Mq.map_ty(P("Option", P("TsTwo")), "param")
        assert (i.rust, i.luau, i.from_lua("x")) == (
            "Option<LuaTuple<(u8, String)>>", "{ number | string }?", "x.map(|v| { let (t0, t1) = v.0; azalea::ts::TsTwo(t0, t1) })"), (i.rust, i.luau, i.from_lua("x"))
        i = Mq.map_ty(("ref", P("TsOne")), "param")
        assert (i.rust, i.from_lua("x")) == ("u8", "&azalea::ts::TsOne(x)"), i
        Mq.named("TsHolder", "ret")
        assert Mq.records["TsHolder"]["fields"] == [("a", "{ number | string }", "LuaTuple({ let t = v.a; (t.0, t.1) })"), ("b", "number", "v.b.0")], Mq.records["TsHolder"]["fields"]
        gq = g.Generator(ix)
        mq = g.Method("go", "", True, False, False, "&self", [("p", P("TsTwo")), ("q", P("TsOne"))], P("TsOne"), "Client", "t.rs", "pub fn go(&self)")
        w = gq.build_method(mq)[0]
        assert [(nm, ti.rust) for nm, ti in w.wparams] == [("p", "LuaTuple<(u8, String)>"), ("q", "u8")], w.wparams
        assert w.call_args == ["{ let (t0, t1) = p.0; azalea::ts::TsTwo(t0, t1) }", "azalea::ts::TsOne(q)"], w.call_args
        assert w.ret.rust == "u8" and w.ret.to_lua("r") == "r.0", w.ret
        n += 31
        # refusals, each with its code (the old `tuple struct` skip text stays for a struct whose fields are not all public)
        skq("TsUuid", "param", "tuple struct `TsUuid`: nested fallible type in tuple", "nested_fallible")
        skq("TsRef", "param", "tuple struct `TsRef`: field with a reference element `&str`", "reference")
        skq("TsUnit", "ret", "tuple struct `TsUnit`: field with a `()` element", "type_unsupported")
        skq("TsRec", "ret", "tuple struct `TsRec`: `TsRec` contains itself", "recursive")
        skq("TsNE", "param", "`#[non_exhaustive]`", "shape_unsupported")
        okq("TsNE", "ret", "u8", "number", "x.0")
        skq("TsBig", "ret", "tuple struct `TsBig`: tuple of 13 elements (supported: 1 to 12)", "type_unsupported")
        for nm in ("TsPriv", "TsHalf", "TsStripped", "TsEmpty", "TsAcc"):
            for role in ("ret", "param"):
                skq(nm, role, f"tuple struct `{nm}`", "tuple_struct")
        skq("TsGen", "ret", "struct `TsGen` is generic", "generic")
        skq(TAKEN, "ret", f"`{TAKEN}` would redefine a Luau type that already exists", "name_clash")
        skq("TsNoPath", "param", "no public `azalea::` path", "no_path", "[path_overrides]")
        okq("TsNoPath", "ret", "u8", "number", "x.0")   # a return reads `.0`: no path is needed
        assert not Mq.tbuilding
        n += 21
        # classify agrees with map_ty on the shape (field-level failures such as TsUuid / TsRec / TsNoPath are not judged)
        shape_only = {("TsUuid", "param"), ("TsRec", "ret"), ("TsRec", "param"), ("TsNoPath", "param")}
        for nm in tdefs:
            if nm == "TsHolder":
                continue
            for role in ("ret", "param"):
                c = Mq.classify(P(nm), role)
                try:
                    Mq.map_ty(P(nm), role)
                    mapped = True
                    mcode = ""
                except g.Skip as e:
                    mapped, mcode = False, e.code
                if (nm, role) in shape_only:
                    assert c.kind == "value", (nm, role, c)
                    continue
                assert mapped == (c.kind != "unsupported"), (nm, role, c, mapped, mcode)
                if not mapped and nm != "TsGen":
                    assert c.code == mcode or (c.kind == "opaque" and mcode == "tuple_struct"), (nm, role, c, mcode)
        assert Mq.classify(P("TsOne"), "ret")[:3] == ("value", "value", False) and Mq.classify(P("TsTwo"), "param")[:3] == ("value", "value", False)
        assert Mq.classify(P("TsNE"), "param").code == "shape_unsupported" and Mq.classify(P("TsRef"), "param").code == "reference"
        assert Mq.classify(P("TsBig"), "ret").code == "type_unsupported" and Mq.classify(P("TsUnit"), "ret").code == "type_unsupported"
        n += 12
        # twice in a row gives the same text, and a second Mapper agrees (no cache, no state)
        a = Mq.map_ty(P("TsNest"), "param").from_lua("x")
        assert a == Mq.map_ty(P("TsNest"), "param").from_lua("x") == g.Mapper(ix).map_ty(P("TsNest"), "param").from_lua("x")
        n += 1
    finally:
        for k in tdefs:
            ix.defs.pop(k, None)
            g.PATH_OVERRIDES.pop(k, None)
        g.ACCESSORS.pop("TsAcc", None)
    # slice 19: std scalars (char, Duration, PathBuf / Path) and wrappers (Box, Rc, Arc of a string, Cow, Mutex, RwLock)
    u8, STR, S, PA = ("prim", "u8"), ("prim", "str"), P("String"), P("Path")
    CH = ("prim", "char")
    M19 = g.Mapper(ix)

    def t19(t, role, rust, luau, to=None, frm=None, mapper=None):
        i = (mapper or M19).map_ty(t, role)
        got = (i.rust, i.luau, i.to_lua("x") if i.to_lua else None, i.from_lua("x") if i.from_lua else None)
        assert got == (rust, luau, to, frm), (g.type_text(t), role, got)
        return i

    def s19(t, role, why, code, mapper=None):
        try:
            (mapper or M19).map_ty(t, role)
        except g.Skip as e:
            assert why in e.reason and e.code == code, (g.type_text(t), role, e.reason, e.code)
            return
        raise AssertionError(f"{g.type_text(t)} ({role}) should have been skipped (`{why}`)")

    # char: a one-character Luau string; a script string of another length is the FromStr error
    t19(CH, "ret", "String", "string", "x.to_string()", "x.parse::<char>().map_err(lua_err)?")
    assert M19.map_ty(CH, "param").fallible_from and not M19.map_ty(CH, "ret").is_result
    t19(("ref", CH), "ret", "String", "string", "x.to_string()")
    t19(("ref", CH), "param", "String", "string", None, "&x.parse::<char>().map_err(lua_err)?")
    t19(P("Option", CH), "ret", "Option<String>", "string?", "x.map(|v| v.to_string())", "x.map(|v| v.parse::<char>().map_err(lua_err)?)")
    t19(P("Vec", CH), "ret", "Vec<String>", "{ string }", "x.into_iter().map(|v| v.to_string()).collect::<Vec<_>>()")
    s19(P("Option", CH), "param", "nested fallible type in Option", "nested_fallible")
    s19(P("Vec", CH), "param", "nested fallible type in list", "nested_fallible")
    s19(("tuple", [CH, u8]), "param", "nested fallible type in tuple", "nested_fallible")
    t19(("tuple", [CH, u8]), "ret", "LuaTuple<(String, u8)>", "{ string | number }", "{ let (t0, t1) = x; LuaTuple((t0.to_string(), t1)) }")
    s19(("prim", "f128"), "ret", "primitive `f128`", "type_unsupported")
    n += 11
    # Duration = seconds (f64); a negative, NaN or overflowing number from a script is an error; PathBuf / Path = a string (lossy on return)
    DUR = "std::time::Duration::try_from_secs_f64(x).map_err(lua_err)?"
    t19(P("Duration"), "ret", "f64", "number", "x.as_secs_f64()", DUR)
    t19(P("Duration"), "param", "f64", "number", "x.as_secs_f64()", DUR)
    assert M19.map_ty(P("Duration"), "param").fallible_from
    t19(("ref", P("Duration")), "ret", "f64", "number", "x.as_secs_f64()")
    t19(("ref", P("Duration")), "param", "f64", "number", None, "&" + DUR)
    t19(P("Option", P("Duration")), "ret", "Option<f64>", "number?", "x.map(|v| v.as_secs_f64())", "x.map(|v| " + DUR.replace("(x)", "(v)") + ")")
    t19(P("Vec", P("Duration")), "ret", "Vec<f64>", "{ number }", "x.into_iter().map(|v| v.as_secs_f64()).collect::<Vec<_>>()")
    s19(P("Option", P("Duration")), "param", "nested fallible type in Option", "nested_fallible")
    s19(P("Vec", P("Duration")), "param", "nested fallible type in list", "nested_fallible")
    s19(P("HashMap", S, P("Duration")), "param", "nested fallible type in map", "nested_fallible")
    PB = "x.to_string_lossy().into_owned()"
    t19(P("PathBuf"), "ret", "String", "string", PB, "std::path::PathBuf::from(x)")
    t19(("ref", PA), "ret", "String", "string", PB)
    t19(("ref", PA), "param", "String", "string", None, "&std::path::PathBuf::from(x)")
    t19(("ref", P("PathBuf")), "param", "String", "string", None, "&std::path::PathBuf::from(x)")
    t19(P("Vec", P("PathBuf")), "param", "Vec<String>", "{ string }", None, "x.into_iter().map(|v| std::path::PathBuf::from(v)).collect::<Vec<_>>()")
    t19(P("Option", P("PathBuf")), "ret", "Option<String>", "string?", "x.map(|v| v.to_string_lossy().into_owned())", "x.map(|v| std::path::PathBuf::from(v))")
    n += 16
    # a type a loaded crate defines under the same name is not the std type (the index wins, like every other name)
    own = g.Def(kind="struct", name="Duration", crate="azalea", modpath=(), is_pub=True, generics=False, src=b"", tuple_style=False,
                fields=[("secs", ("prim", "u64"), True)], attrs=[])
    ix.defs["Duration"] = [own]
    g.PATH_OVERRIDES["Duration"] = "azalea::time::Duration"
    try:
        i = g.Mapper(ix).map_ty(P("Duration"), "ret")
        assert i.luau == "Duration" and i.rust != "f64", (i.rust, i.luau)
        assert g.Mapper(ix).classify(P("Duration"), "ret").kind == "value"
        assert not g.Mapper(ix).std_scalar("Duration", [])
    finally:
        ix.defs.pop("Duration", None)
        g.PATH_OVERRIDES.pop("Duration", None)
    assert g.Mapper(ix).std_scalar("Duration", []) and not g.Mapper(ix).std_scalar("Duration", [S])
    n += 4
    # Box / Rc / Arc: the Luau value is the inner one. A Box moves out (`*x`), an Rc / Arc is copied out (`Clone`); `str` / `Path` are unsized
    t19(P("Box", u8), "ret", "u8", "number", "(*x)")
    t19(P("Box", u8), "param", "u8", "number", None, "Box::new(x)")
    t19(P("Box", S), "ret", "String", "string", "(*x)")
    t19(P("Box", P("Vec", u8)), "ret", "Vec<u8>", "{ number }", "(*x)")
    t19(P("Box", STR), "ret", "String", "string", "x.to_string()")
    t19(P("Box", STR), "param", "String", "string", None, "x.into_boxed_str()")
    t19(P("Box", PA), "ret", "String", "string", PB)
    t19(P("Box", PA), "param", "String", "string", None, "std::path::PathBuf::from(x).into_boxed_path()")
    t19(P("Rc", STR), "ret", "String", "string", "x.to_string()")
    t19(P("Rc", STR), "param", "String", "string", None, "std::rc::Rc::from(x)")
    t19(P("Rc", S), "ret", "String", "string", "(*x).clone()")
    t19(P("Rc", S), "param", "String", "string", None, "std::rc::Rc::new(x)")
    t19(P("Rc", P("Vec", u8)), "ret", "Vec<u8>", "{ number }", "(*x).clone()")
    t19(P("Rc", PA), "param", "String", "string", None, "std::rc::Rc::from(std::path::PathBuf::from(x))")
    t19(P("Arc", STR), "ret", "String", "string", "x.to_string()")
    t19(P("Arc", STR), "param", "String", "string", None, "std::sync::Arc::from(x)")
    t19(P("Arc", PA), "ret", "String", "string", PB)
    t19(P("Arc", PA), "param", "String", "string", None, "std::sync::Arc::from(std::path::PathBuf::from(x))")
    t19(P("Box", P("Duration")), "ret", "f64", "number", "(*x).as_secs_f64()")
    t19(P("Box", P("PathBuf")), "param", "String", "string", None, "Box::new(std::path::PathBuf::from(x))")
    t19(P("Vec", P("Box", STR)), "ret", "Vec<String>", "{ string }", "x.into_iter().map(|v| v.to_string()).collect::<Vec<_>>()")
    t19(P("Option", P("Rc", S)), "ret", "Option<String>", "string?", "x.map(|v| (*v).clone())")
    t19(("ref", P("Box", STR)), "ret", "String", "string", "x.to_string()")
    t19(("ref", P("Arc", STR)), "ret", "String", "string", "x.to_string()")
    t19(P("Box", ("slice", u8)), "ret", "Vec<u8>", "{ number }", "Vec::from(x)")   # `Box<[T]>` stays with the list branch
    s19(P("Box", ("ref", STR)), "ret", "`Box<&str>`: a wrapper of a reference", "reference")
    s19(P("Rc", ("ref", S)), "param", "a wrapper of a reference", "reference")
    s19(P("Box", P("Result", u8, P("MissingComponentError"))), "ret", "nested fallible type in `Box<Result<u8, MissingComponentError>>`", "nested_fallible")
    s19(P("Rc", P("BestToolResult")), "ret", "`BestToolResult` is not `Clone`, so it can't be copied out of the `Rc`", "trait_missing")
    assert M19.map_ty(P("Box", P("BestToolResult")), "ret").to_lua("x") is not None   # a Box moves out: no Clone needed
    s19(P("Box", P("NoSuchType")), "ret", "NoSuchType", "no_definition")
    n += 31
    # Cow: `into_owned()` on return, `Cow::Owned(..)` on a parameter; `str` -> String, `[T]` -> Vec<T>, `Path` -> PathBuf
    t19(P("Cow", STR), "ret", "String", "string", "x.into_owned()")
    t19(P("Cow", STR), "param", "String", "string", None, "std::borrow::Cow::Owned(x)")
    t19(P("Cow", ("slice", u8)), "ret", "Vec<u8>", "{ number }", "x.into_owned()")
    t19(P("Cow", ("slice", u8)), "param", "Vec<u8>", "{ number }", None, "std::borrow::Cow::Owned(x)")
    t19(P("Cow", ("slice", S)), "ret", "Vec<String>", "{ string }", "x.into_owned()")
    t19(P("Cow", PA), "ret", "String", "string", "x.into_owned().to_string_lossy().into_owned()")
    t19(P("Cow", PA), "param", "String", "string", None, "std::borrow::Cow::Owned(std::path::PathBuf::from(x))")
    t19(P("Option", P("Cow", STR)), "ret", "Option<String>", "string?", "x.map(|v| v.into_owned())")
    t19(P("Vec", P("Cow", STR)), "param", "Vec<String>", "{ string }", None, "x.into_iter().map(|v| std::borrow::Cow::Owned(v)).collect::<Vec<_>>()")
    s19(P("Cow", ("ref", STR)), "ret", "a wrapper of a reference", "reference")
    s19(P("Cow", P("Result", u8, P("MissingComponentError"))), "ret", "nested fallible type in `Cow<Result", "nested_fallible")
    n += 11
    # Mutex / RwLock: only when [lock_types] says which lock it is (unset = the old `generic` skip, text unchanged); a lock is read under its guard
    # and the value copied out; a std lock is never poisoned for a script (`into_inner` of the poison error)
    s19(P("Mutex", u8), "ret", "generic type `Mutex<u8>`", "generic")
    s19(P("Arc", P("Mutex", S)), "ret", "generic type `Mutex<String>`", "generic")
    own_rw = ix.defs.pop("RwLock", None)   # the fake azalea defines its own RwLock: that one is not std's
    saved_locks = dict(g.LOCK_TYPES)
    try:
        g.LOCK_TYPES.update({"Mutex": "std", "RwLock": "std"})
        ML = g.Mapper(ix)
        GUARD = "x.lock().unwrap_or_else(|e| e.into_inner())"
        L19 = lambda t, role, rust, luau, to=None, frm=None, mapper=ML: t19(t, role, rust, luau, to, frm, mapper)
        L19(P("Mutex", u8), "ret", "u8", "number", "x.into_inner().unwrap_or_else(|e| e.into_inner())")
        L19(P("Mutex", u8), "param", "u8", "number", None, "std::sync::Mutex::new(x)")
        L19(P("RwLock", S), "ret", "String", "string", "x.into_inner().unwrap_or_else(|e| e.into_inner())")
        L19(P("RwLock", S), "param", "String", "string", None, "std::sync::RwLock::new(x)")
        L19(P("Arc", P("Mutex", S)), "ret", "String", "string", f"(*{GUARD}).clone()")
        L19(P("Arc", P("RwLock", u8)), "ret", "u8", "number", "(*x.read().unwrap_or_else(|e| e.into_inner())).clone()")
        L19(("ref", P("Mutex", u8)), "ret", "u8", "number", f"(*{GUARD}).clone()")
        L19(("ref", P("RwLock", S)), "ret", "String", "string", "(*x.read().unwrap_or_else(|e| e.into_inner())).clone()")
        L19(P("Arc", P("Mutex", P("Duration"))), "ret", "f64", "number", f"(*{GUARD}).as_secs_f64()")
        L19(P("Arc", P("Mutex", P("PathBuf"))), "ret", "String", "string", f"(*{GUARD}).to_string_lossy().into_owned()")
        L19(P("Mutex", P("Vec", P("PathBuf"))), "ret", "Vec<String>", "{ string }",
            "x.into_inner().unwrap_or_else(|e| e.into_inner()).into_iter().map(|v| v.to_string_lossy().into_owned()).collect::<Vec<_>>()")
        s19(P("Arc", P("Mutex", S)), "param", "Arc parameter", "direction", ML)
        s19(P("Arc", P("Mutex", P("BestToolResult"))), "ret", "`BestToolResult` is not `Clone`, so it can't be copied out of the lock", "trait_missing", ML)
        s19(P("Arc", P("Mutex", P("Result", u8, P("MissingComponentError")))), "ret", "the value inside the lock can't be converted", "not_convertible", ML)
        s19(P("Mutex", ("ref", S)), "ret", "a wrapper of a reference", "reference", ML)
        # parking_lot: `lock()` / `read()` return the guard at once; building such a lock from a script needs that crate, so a parameter is refused
        g.LOCK_TYPES.update({"Mutex": "parking_lot", "RwLock": "parking_lot"})
        MP = g.Mapper(ix)
        t19(P("Mutex", u8), "ret", "u8", "number", "x.into_inner()", mapper=MP)
        t19(P("Arc", P("Mutex", S)), "ret", "String", "string", "(*x.lock()).clone()", mapper=MP)
        t19(P("Arc", P("RwLock", S)), "ret", "String", "string", "(*x.read()).clone()", mapper=MP)
        s19(P("Mutex", u8), "param", "building a `parking_lot` lock needs that crate", "not_convertible", MP)
        # the fake's own `RwLock` (a loaded crate defines the name) stays the generic skip even when [lock_types] names it
        if own_rw is not None:
            ix.defs["RwLock"] = own_rw
            g.LOCK_TYPES.update({"Mutex": "std", "RwLock": "std"})
            s19(P("RwLock", S), "ret", "generic", "generic", g.Mapper(ix))
            ix.defs.pop("RwLock", None)
        # classify agrees with map_ty on every wrapper above, both roles, and the refusals carry the same code
        g.LOCK_TYPES.update({"Mutex": "std", "RwLock": "std"})
        for t in (P("Box", u8), P("Box", STR), P("Box", PA), P("Rc", S), P("Rc", STR), P("Arc", STR), P("Arc", PA), P("Cow", STR), P("Cow", ("slice", u8)),
                  P("Cow", PA), P("Mutex", u8), P("RwLock", S), P("Arc", P("Mutex", S)), ("ref", P("Mutex", u8)), P("Box", ("ref", STR)), P("Rc", ("ref", S)),
                  P("Cow", ("ref", STR)), P("Mutex", ("ref", S)), P("Box", P("EntityRef")), P("Rc", P("BestToolResult")), P("Duration"), P("PathBuf"),
                  ("ref", PA), ("ref", P("Duration")), CH, ("ref", CH), P("Vec", CH), P("Option", P("Duration")), ("tuple", [CH, u8])):
            for role in ("ret", "param"):
                if role == "param" and "BestToolResult" in g.type_text(t):
                    continue   # a [records]-like struct classifies unsupported(direction) as a parameter; map_ty still builds it, without from_lua (s20)
                c = g.Mapper(ix).classify(t, role)
                try:
                    g.Mapper(ix).map_ty(t, role)
                    mapped, mcode = True, ""
                except g.Skip as e:
                    mapped, mcode = False, e.code
                if (role == "param" and mapped is False and mcode in ("nested_fallible", "direction", "trait_missing")) or \
                        (role == "ret" and mapped is False and mcode == "trait_missing"):
                    continue   # classify judges shape only: fallible reads, Arc directions and Clone facts are decided by map_ty
                assert mapped == (c.kind != "unsupported"), (g.type_text(t), role, c, mapped, mcode)
                if not mapped:
                    assert c.code == mcode, (g.type_text(t), role, c, mcode)
        assert g.Mapper(ix).classify(P("Box", STR), "ret")[:3] == ("scalar", "value", False)
        assert g.Mapper(ix).classify(P("Arc", STR), "param")[:3] == ("scalar", "value", False)
        assert g.Mapper(ix).classify(P("Duration"), "ret")[:3] == ("scalar", "value", False)
        assert g.Mapper(ix).classify(P("Box", P("EntityRef")), "ret")[:3] == ("class", "value", True)
        assert g.Mapper(ix).classify(P("Mutex", u8), "ret")[:3] == ("scalar", "value", False)
        g.LOCK_TYPES.clear()
        assert g.Mapper(ix).classify(P("Mutex", u8), "ret").code == "generic"   # unset: the old answer
    finally:
        g.LOCK_TYPES.clear()
        g.LOCK_TYPES.update(saved_locks)
        if own_rw is not None:
            ix.defs["RwLock"] = own_rw
    n += 37
    # a method through build_method: the wrapper takes f64 / String / String / String, reads them (the fallible ones with `?`), and returns a String
    gw = g.Generator(ix)
    mw = g.Method("run", "", True, False, False, "&self",
                  [("wait", P("Duration")), ("dir", ("ref", PA)), ("key", CH), ("name", P("Box", STR)), ("tags", P("Cow", ("slice", S)))],
                  P("Cow", STR), "Client", "t.rs", "pub fn run(&self)")
    ww = gw.build_method(mw)[0]
    assert [(nm, ti.rust) for nm, ti in ww.wparams] == [("wait", "f64"), ("dir", "String"), ("key", "String"), ("name", "String"), ("tags", "Vec<String>")], ww.wparams
    assert ww.call_args == ["std::time::Duration::try_from_secs_f64(wait).map_err(lua_err)?", "&std::path::PathBuf::from(dir)",
                            "key.parse::<char>().map_err(lua_err)?", "name.into_boxed_str()", "std::borrow::Cow::Owned(tags)"], ww.call_args
    assert ww.ret.rust == "String" and ww.ret.to_lua("r") == "r.into_owned()"
    assert g.rust_sig_ret(ww)[:2] == ("", " -> mlua::Result<String>"), g.rust_sig_ret(ww)   # a fallible parameter makes the wrapper fallible
    mw2 = g.Method("peek", "", True, False, False, "&self", [("p", P("Rc", STR))], P("Duration"), "Client", "t.rs", "pub fn peek(&self)")
    w2 = gw.build_method(mw2)[0]
    assert w2.call_args == ["std::rc::Rc::from(p)"] and w2.ret.to_lua("r") == "r.as_secs_f64()"
    assert g.rust_sig_ret(w2)[:2] == ("#[lua(infallible)]", " -> f64"), g.rust_sig_ret(w2)   # nothing fallible: stays infallible
    # nothing is registered or cached by these types (no ('type', ..) touch, no Luau type emitted)
    Mz = g.Mapper(ix)
    for t in (CH, P("Duration"), P("PathBuf"), ("ref", PA), P("Box", STR), P("Box", u8), P("Rc", S), P("Arc", STR), P("Cow", STR), P("Cow", ("slice", u8)), P("Cow", PA)):
        for role in ("ret", "param"):
            Mz.map_ty(t, role)
    assert Mz.touched == set() and not Mz.cache and not Mz.fail, (Mz.touched, Mz.cache, Mz.fail)
    # twice in a row gives the same text, and a second Mapper agrees
    a = M19.map_ty(P("Cow", PA), "ret").to_lua("x")
    assert a == M19.map_ty(P("Cow", PA), "ret").to_lua("x") == g.Mapper(ix).map_ty(P("Cow", PA), "ret").to_lua("x")
    n += 12
    # --- the node's full path tells std's type from another crate's (open item of s19): `chrono::Duration` of a crate that is not loaded
    # was taken for std's `Duration` (and `triomphe::Arc`, `hashbrown::HashMap`, `tokio::sync::RwLock` likewise) because only the last path
    # segment reached the mapper. rdj.convert_type now keeps it (`PathTy.full`); a hand-built node without one behaves exactly as before.
    import rdj

    def PF(name, full, *args):
        t = rdj.PathTy(("path", name, list(args)))
        t.full = full
        return t

    nope = lambda name, full: f"`{full}` is not std's `{name}` and its crate is not loaded"

    def outcome(t, role, mapper=None):
        """('ok', rust, luau, to, from) or ('skip', reason, code)."""
        try:
            i = (mapper or g.Mapper(ix)).map_ty(t, role)
        except g.Skip as e:
            return ("skip", e.reason, e.code)
        return ("ok", i.rust, i.luau, i.to_lua("x") if i.to_lua else None, i.from_lua("x") if i.from_lua else None)

    CHR, STDD = PF("Duration", "chrono::oldtime::Duration"), PF("Duration", "core::time::Duration")
    for role in ("ret", "param"):
        assert outcome(CHR, role) == ("skip", nope("Duration", "chrono::oldtime::Duration"), "no_definition"), (role, outcome(CHR, role))
        c = g.Mapper(ix).classify(CHR, role)
        assert (c.kind, c.code, c.reason) == ("unsupported", "no_definition", nope("Duration", "chrono::oldtime::Duration")), c
        assert outcome(STDD, role)[0] == "ok" and outcome(STDD, role) == outcome(P("Duration"), role), role   # std's: the bare-name answer
        assert g.Mapper(ix).classify(STDD, role) == g.Mapper(ix).classify(P("Duration"), role)
    t19(STDD, "ret", "f64", "number", "x.as_secs_f64()", DUR)
    # a foreign type deep inside a shape stops the whole shape, with the same text and code, in both roles
    for shape in (lambda d: P("Vec", d), lambda d: P("Option", d), lambda d: P("HashMap", S, d), lambda d: ("tuple", [d, u8]), lambda d: ("array", d, "2"),
                  lambda d: ("ref", d), lambda d: P("Box", d), lambda d: P("Rc", d), lambda d: P("Cow", d), lambda d: P("Arc", d)):
        for role in ("ret", "param"):
            o = outcome(shape(CHR), role)
            assert o == ("skip", nope("Duration", "chrono::oldtime::Duration"), "no_definition"), (g.type_text(shape(CHR)), role, o)
            assert outcome(shape(STDD), role) == outcome(shape(P("Duration")), role), (g.type_text(shape(STDD)), role)
    # every name the mapper reads as std by its last segment: std's full path = the bare answer, another crate's = a coded skip
    STDFULL = {"String": "alloc::string::String", "Option": "core::option::Option", "Vec": "alloc::vec::Vec", "Result": "core::result::Result",
               "Arc": "alloc::sync::Arc", "HashMap": "std::collections::hash::map::HashMap", "BTreeMap": "alloc::collections::btree::map::BTreeMap",
               "Box": "alloc::boxed::Box", "Rc": "alloc::rc::Rc", "Cow": "alloc::borrow::Cow", "Duration": "core::time::Duration",
               "PathBuf": "std::path::PathBuf", "Path": "std::path::Path"}
    ARGS = {"String": [], "Option": [u8], "Vec": [u8], "Result": [u8, P("MissingComponentError")], "Arc": [S], "HashMap": [S, S], "BTreeMap": [S, S],
            "Box": [u8], "Rc": [u8], "Cow": [STR], "Duration": [], "PathBuf": [], "Path": []}
    assert set(STDFULL) | {"Mutex", "RwLock"} == set(g.STD_NAMES) == set(ARGS) | {"Mutex", "RwLock"}   # a new std name needs a row here
    for name in STDFULL:
        for role in ("ret", "param"):
            bare, std = outcome(P(name, *ARGS[name]), role), outcome(PF(name, STDFULL[name], *ARGS[name]), role)
            assert std == bare, (name, role, std, bare)
            assert outcome(PF(name, "zzz::inner::" + name, *ARGS[name]), role) == ("skip", nope(name, "zzz::inner::" + name), "no_definition"), (name, role)
            c = g.Mapper(ix).classify(PF(name, "zzz::inner::" + name, *ARGS[name]), role)
            assert (c.kind, c.code) == ("unsupported", "no_definition"), (name, role, c)
            assert g.Mapper(ix).classify(PF(name, STDFULL[name], *ARGS[name]), role) == g.Mapper(ix).classify(P(name, *ARGS[name]), role), (name, role)
    n += 2 * 13 * 3 + 2 * 10 * 2 + 12
    # a name that is not a std name is never judged (a [scalar] row, a hand class, a loaded crate's type keep their by-name rule)
    for name, full in (("Uuid", "uuid::Uuid"), ("BlockPos", "azalea_core::position::BlockPos"), ("Vec3", "nowhere::Vec3")):
        assert outcome(PF(name, full), "ret") == outcome(P(name), "ret"), name
    n += 3
    # a loaded crate's own `Duration` and std's `Duration` are told apart by the path, not by the name alone
    own = g.Def(kind="struct", name="Duration", crate="azalea", modpath=(), is_pub=True, generics=False, src=b"", tuple_style=False,
                fields=[("secs", ("prim", "u64"), True)], attrs=[])
    ix.defs["Duration"] = [own]
    g.PATH_OVERRIDES["Duration"] = "azalea::time::Duration"
    try:
        mine = outcome(PF("Duration", "azalea::time::Duration"), "ret")
        assert mine == outcome(P("Duration"), "ret") and mine[0] == "ok" and mine[2] == "Duration" and mine[1] != "f64", mine   # the crate's type
        assert outcome(STDD, "ret") == ("ok", "f64", "number", "x.as_secs_f64()", DUR), outcome(STDD, "ret")   # std's, although a loaded crate has one too
        assert outcome(STDD, "param")[1:3] == ("f64", "number")
        assert g.Mapper(ix).classify(STDD, "ret").kind == "scalar" and g.Mapper(ix).classify(PF("Duration", "azalea::time::Duration"), "ret").kind == "value"
        assert g.Mapper(ix).std_scalar("Duration", [], STDD) and not g.Mapper(ix).std_scalar("Duration", [], PF("Duration", "azalea::time::Duration"))
        # chrono's, with a loaded `Duration` around: still not std's, and not a loaded crate's: it keeps the by-name answer (the loaded one)
        assert outcome(CHR, "ret") == outcome(P("Duration"), "ret")
    finally:
        ix.defs.pop("Duration", None)
        g.PATH_OVERRIDES.pop("Duration", None)
    n += 7
    # Mutex / RwLock: the node's crate must be the one `[lock_types]` selects (std, or parking_lot / lock_api); tokio's is neither
    own_rw = ix.defs.pop("RwLock", None)
    saved_locks = dict(g.LOCK_TYPES)
    STDL, PLL = PF("Mutex", "std::sync::poison::mutex::Mutex", u8), PF("Mutex", "parking_lot::mutex::Mutex", u8)
    TOK = PF("Mutex", "tokio::sync::mutex::Mutex", u8)
    try:
        g.LOCK_TYPES.clear()   # unset: parking_lot's and std's both keep the old `generic` skip (the hint to configure stays), tokio's is a different type
        assert outcome(STDL, "ret") == outcome(PLL, "ret") == outcome(P("Mutex", u8), "ret") == ("skip", "generic type `Mutex<u8>`", "generic")
        assert outcome(TOK, "ret") == ("skip", nope("Mutex", "tokio::sync::mutex::Mutex"), "no_definition")
        g.LOCK_TYPES.update({"Mutex": "std", "RwLock": "std"})
        assert outcome(STDL, "ret") == outcome(P("Mutex", u8), "ret") and outcome(STDL, "ret")[0] == "ok"
        assert outcome(PLL, "ret") == ("skip", "`parking_lot::mutex::Mutex` is not the std `Mutex` that [lock_types] selects", "no_definition")
        assert outcome(TOK, "ret") == ("skip", nope("Mutex", "tokio::sync::mutex::Mutex").replace("not std's `Mutex` and its crate is not loaded", "not the std `Mutex` that [lock_types] selects"), "no_definition")
        STDRW = PF("RwLock", "std::sync::poison::rwlock::RwLock", S)
        assert outcome(P("Arc", PF("RwLock", "std::sync::poison::rwlock::RwLock", u8)), "ret")[3] == "(*x.read().unwrap_or_else(|e| e.into_inner())).clone()"
        assert outcome(STDRW, "param")[4] == "std::sync::RwLock::new(x)"
        g.LOCK_TYPES.update({"Mutex": "parking_lot", "RwLock": "parking_lot"})
        PLRW = PF("RwLock", "parking_lot::lock_api::RwLock", S)
        assert outcome(P("Arc", PLRW), "ret") == ("ok", "String", "string", "(*x.read()).clone()", None), outcome(P("Arc", PLRW), "ret")
        assert outcome(PLL, "ret") == outcome(P("Mutex", u8), "ret") and outcome(PLL, "ret")[0] == "ok"
        assert outcome(STDL, "ret") == ("skip", "`std::sync::poison::mutex::Mutex` is not the parking_lot `Mutex` that [lock_types] selects", "no_definition")
        assert outcome(TOK, "ret")[2] == "no_definition"
        assert g.Mapper(ix).classify(PLL, "ret").kind == "scalar" and g.Mapper(ix).classify(STDL, "ret").code == "no_definition"
        # the fake's own `RwLock` (azalea::RwLock) is a loaded crate's type: it is not judged, and std's of the same name is std's
        if own_rw is not None:
            ix.defs["RwLock"] = own_rw
            g.LOCK_TYPES.update({"Mutex": "std", "RwLock": "std"})
            assert outcome(PF("RwLock", "azalea::RwLock", S), "ret") == outcome(P("RwLock", S), "ret") == ("skip", "generic type `RwLock<String>`", "generic")
            assert outcome(STDRW, "ret")[0] == "ok", outcome(STDRW, "ret")
            ix.defs.pop("RwLock", None)
    finally:
        g.LOCK_TYPES.clear()
        g.LOCK_TYPES.update(saved_locks)
        if own_rw is not None:
            ix.defs["RwLock"] = own_rw
    n += 17
    # nothing is registered or cached by the guard (it raises before any table is touched)
    Mg = g.Mapper(ix)
    for t in (CHR, P("Vec", CHR), PF("Arc", "triomphe::Arc", S)):
        for role in ("ret", "param"):
            try:
                Mg.map_ty(t, role)
            except g.Skip:
                pass
    assert Mg.touched == set() and not Mg.cache and not Mg.fail
    # a method through build_method: the skip is the method's, with the code, and the std one next to it is generated
    gm = g.Generator(ix)
    bad = g.Method("a", "", True, False, False, "&self", [("d", CHR)], None, "Client", "t.rs", "pub fn a(&self)")
    good = g.Method("b", "", True, False, False, "&self", [("d", STDD)], None, "Client", "t.rs", "pub fn b(&self)")
    try:
        gm.build_method(bad)
        raise AssertionError("a chrono::Duration parameter should skip the method")
    except g.Skip as e:
        assert e.code == "no_definition" and e.reason == nope("Duration", "chrono::oldtime::Duration"), (e.code, e.reason)
    assert gm.build_method(good)[0].call_args == [DUR.replace("(x)", "(d)")], gm.build_method(good)[0].call_args
    n += 3

    # slice 20: value parameters (a struct with public fields / a data-carrying enum read from a Luau table)
    u8 = ("prim", "u8")
    f64 = ("prim", "f64")
    sd = lambda name, fields, attrs=(), generics=False: g.Def(
        kind="struct", name=name, crate="azalea", modpath=(), is_pub=True, generics=generics, src=b"", tuple_style=False,
        fields=[(f, t, pub) for f, t, pub in fields], attrs=list(attrs))
    ed = lambda name, variants, generics=False: g.Def("enum", name, "azalea", (), True, generics, b"", variants=variants)
    vr = lambda name, kind, types=(), attrs=(): dict(name=name, kind=kind, types=list(types), attrs=list(attrs), doc="")
    vdefs = {
        "VpPoint": sd("VpPoint", [("x", f64, True), ("y", f64, True)]),
        "VpOpt": sd("VpOpt", [("name", P("String"), True), ("tag", P("Option", P("String")), True), ("on", ("prim", "bool"), True)]),
        "VpOuter": sd("VpOuter", [("p", P("VpPoint"), True), ("n", u8, True), ("who", P("EntityRef"), True)]),
        "VpList": sd("VpList", [("ps", P("Vec", P("VpPoint")), True)]),
        "VpSelf": sd("VpSelf", [("again", P("Vec", P("VpSelf")), True)]),
        "VpPriv": sd("VpPriv", [("x", u8, False)]),
        "VpNE": sd("VpNE", [("x", u8, True)], attrs=["#[non_exhaustive]"]),
        "VpGen": sd("VpGen", [("x", u8, True)], generics=True),
        "VpBadName": sd("VpBadName", [("na\u00efve", u8, True)]),
        "VpUuid": sd("VpUuid", [("id", P("Option", P("Uuid")), True)]),
        "VpKw": sd("VpKw", [("type", u8, True), ("end", u8, True)]),
        "VpHandle": sd("VpHandle", [("h", P("VpPriv"), True)]),
        "VpEnum": ed("VpEnum", [vr("Stop", "unit"), vr("Move", "tuple", [u8]), vr("Pair", "tuple", [u8, P("String")]),
                                vr("Wrap", "tuple", [P("VpPoint")]), vr("Many", "tuple", [P("Vec", u8)])]),
        "VpUnits": ed("VpUnits", [vr("A", "unit"), vr("B", "unit")]),
        "VpSV": ed("VpSV", [vr("A", "unit"), vr("B", "struct")]),
        "VpVNE": ed("VpVNE", [vr("A", "unit"), vr("B", "tuple", [u8], attrs=["#[non_exhaustive]"])]),
        "VpDup": ed("VpDup", [vr("FooBar", "unit"), vr("Foo_bar", "tuple", [u8])]),
        "VpKind": ed("VpKind", [vr("A", "tuple", [P("VpKindIn")])]),
        "VpKindIn": sd("VpKindIn", [("kind", u8, True)]),
        "VpGenE": ed("VpGenE", [vr("A", "tuple", [u8])], generics=True),
        "VpEmptyE": ed("VpEmptyE", []),
        "VpShare": sd("VpShare", [("a", u8, True), ("b", ("prim", "bool"), True)]),
        "VpShareT": ed("VpShareT", [vr("A", "unit"), vr("B", "tuple", [u8])]),
    }
    for k, d in vdefs.items():
        ix.defs[k] = [d]
        g.PATH_OVERRIDES[k] = "azalea::vp::" + k
    try:
        Mv = g.Mapper(ix)

        def okv(name, role, rust, luau, to=None, frm=None):
            i = Mv.map_ty(P(name), role)
            got = (i.rust, i.luau, i.to_lua("x") if i.to_lua else None, i.from_lua("x") if i.from_lua else None)
            assert got == (rust, luau, to, frm), (name, role, got)

        def skv(name, role, why, code, mapper=None):
            try:
                (mapper or Mv).map_ty(P(name), role)
            except g.Skip as e:
                assert why in e.reason and e.code == code, (name, role, e.reason, e.code)
                return
            raise AssertionError(f"{name} ({role}) should have been skipped (`{why}`)")

        # struct: the wrapper type, the Luau type name, `.0` hands the struct over; a ret keeps its old (record) form
        okv("VpPoint", "param", "LuaVpPointIn", "VpPoint", None, "x.0")
        okv("VpOpt", "param", "LuaVpOptIn", "VpOpt", None, "x.0")
        okv("VpPoint", "ret", "LuaVpPoint", "VpPoint", "LuaVpPoint(x)", None)
        assert ("type", "VpPoint") in Mv.touched
        e = Mv.vparams["VpPoint"]
        assert (e["kind"], e["rust"], e["path"]) == ("struct", "LuaVpPointIn", "azalea::vp::VpPoint"), e
        assert [(f, fi.rust, fi.luau, r) for f, fi, r in e["fields"]] == [("x", "f64", "number", "x"), ("y", "f64", "number", "y")], e["fields"]
        e = Mv.vparams["VpOpt"]
        assert [(f, fi.rust, fi.luau) for f, fi, _r in e["fields"]] == [("name", "String", "string"), ("tag", "Option<String>", "string?"), ("on", "LBool", "boolean")]
        # nested struct, Vec of structs, a class field (the userdata is cloned out), keyword field names
        okv("VpOuter", "param", "LuaVpOuterIn", "VpOuter", None, "x.0")
        okv("VpList", "param", "LuaVpListIn", "VpList", None, "x.0")
        okv("VpKw", "param", "LuaVpKwIn", "VpKw", None, "x.0")
        assert [(f, r) for f, _fi, r in Mv.vparams["VpKw"]["fields"]] == [("type", "r#type"), ("end", "end")], Mv.vparams["VpKw"]["fields"]
        assert [(f, fi.rust) for f, fi, _r in Mv.vparams["VpOuter"]["fields"]] == [
            ("p", "LuaVpPointIn"), ("n", "u8"), ("who", f"mlua::UserDataRef<{g.CLASSES['EntityRef']['rust']}>")], Mv.vparams["VpOuter"]["fields"]
        assert Mv.vparams["VpList"]["fields"][0][1].rust == "Vec<LuaVpPointIn>" and Mv.vparams["VpList"]["fields"][0][1].luau == "{ VpPoint }"
        assert Mv.vparams["VpList"]["fields"][0][1].from_lua("v") == "v.into_iter().map(|v| v.0).collect::<Vec<_>>()", Mv.vparams["VpList"]["fields"][0][1].from_lua("v")
        # a second use is the cached TypeInfo and touches the same keys
        a = Mv.map_ty(P("VpPoint"), "param")
        assert a is Mv.map_ty(P("VpPoint"), "param")
        # enum: unit -> kind only, one field -> value, several -> _1 _2, a one-field variant holding a value-param struct is flattened
        okv("VpEnum", "param", "LuaVpEnumIn", "VpEnum", None, "x.0")
        ev = {v["kind"]: v for v in Mv.vparams["VpEnum"]["variants"]}
        assert list(ev) == ["stop", "move", "pair", "wrap", "many"], list(ev)
        assert (ev["stop"]["fields"], ev["stop"]["build"]) == ([], "azalea::vp::VpEnum::Stop")
        assert ([f for f, _ in ev["move"]["fields"]], ev["move"]["build"]) == (["value"], "azalea::vp::VpEnum::Move(f0)")
        assert ([f for f, _ in ev["pair"]["fields"]], ev["pair"]["build"]) == (["_1", "_2"], "azalea::vp::VpEnum::Pair(f0, f1)")
        assert ([f for f, _ in ev["wrap"]["fields"]], ev["wrap"]["build"]) == (["x", "y"], "azalea::vp::VpEnum::Wrap(azalea::vp::VpPoint { x: f0, y: f1 })")
        assert [(f, fi.luau) for f, fi in ev["many"]["fields"]] == [("value", "{ number }")]
        # a unit-only enum keeps its string form (old path), a tuple-struct / private struct / [records] row keep theirs
        okv("VpUnits", "param", "LuaVpUnits", "VpUnits", "LuaVpUnits(x)", "x.0")
        assert "VpUnits" not in Mv.vparams
        skv("VpPriv", "param", "has private fields", "private_fields")
        skv("VpHandle", "param", "has private fields", "private_fields")   # a field of a type that is not a value: the field's own refusal, named
        # refusals, each with its code and without a vparams row
        skv("VpNE", "param", "`#[non_exhaustive]`", "shape_unsupported")
        skv("VpGen", "param", "is generic", "generic")
        skv("VpBadName", "param", "not a plain ASCII field name", "name_invalid")
        skv("VpUuid", "param", "struct `VpUuid`.id", "nested_fallible")
        skv("VpSelf", "param", "contains itself", "recursive")
        skv("VpSV", "param", "struct-style variant", "shape_unsupported")
        skv("VpVNE", "param", "VpVNE", "shape_unsupported")
        skv("VpDup", "param", "same snake_case name", "name_clash")
        skv("VpKind", "param", "a field is called `kind`", "name_clash")
        skv("VpEmptyE", "param", "has no variants", "shape_unsupported")
        skv("VpGenE", "param", "is generic", "generic")
        for k in ("VpNE", "VpGen", "VpBadName", "VpSelf", "VpSV", "VpVNE", "VpDup", "VpKind", "VpPriv", "VpUnits"):
            assert k not in Mv.vparams, k
        # the failure is remembered (same reason, no second build) and is the PARAMETER's only: the return still maps
        skv("VpNE", "param", "`#[non_exhaustive]`", "shape_unsupported")
        okv("VpNE", "ret", "LuaVpNE", "VpNE", "LuaVpNE(x)", None)
        # the same Luau type both ways: equal text shares it, a different text is a coded name_clash (either order)
        Ms = g.Mapper(ix)
        Ms.map_ty(P("VpShare"), "ret")
        i = Ms.map_ty(P("VpShare"), "param")
        assert i.rust == "LuaVpShareIn" and "VpShare" in Ms.records and "VpShare" in Ms.vparams
        Ms2 = g.Mapper(ix)
        Ms2.map_ty(P("VpShareT"), "param")
        Ms2.map_ty(P("VpShareT"), "ret")
        assert "VpShareT" in Ms2.tagged and "VpShareT" in Ms2.vparams
        ix.defs["VpShare"] = [sd("VpShare", [("a", u8, True), ("b", ("prim", "bool"), True)])]
        Ms3 = g.Mapper(ix)
        Ms3.vparams["VpShare"] = dict(kind="struct", rust="LuaVpShareIn", path="azalea::vp::VpShare", fields=[("a", g.TypeInfo("u8", "string"), "a")])
        skv("VpShare", "ret", "different Luau type each way", "name_clash", Ms3)
        Ms4 = g.Mapper(ix)
        Ms4.records["VpShare"] = dict(rust="LuaVpShare", path="azalea::vp::VpShare", fields=[("a", "string", "v.a")], needs_client=False)
        skv("VpShare", "param", "different Luau type each way", "name_clash", Ms4)
        n += 50
        # exact Rust text of the generated readers
        S1 = "\n".join(g.emit_value_param("VpOpt", Mv.vparams["VpOpt"]))
        assert S1 == """/// `VpOpt` <- Luau table with the keys: name, tag?, on
pub struct LuaVpOptIn(pub azalea::vp::VpOpt);

impl FromLua for LuaVpOptIn {
    fn from_lua(value: Value, lua: &Lua) -> mlua::Result<Self> {
        let t = match value {
            Value::Table(t) => t,
            other => return Err(mlua::Error::runtime(format!("VpOpt: expected a table, got {}", other.type_name()))),
        };
        lua_check_keys(&t, "VpOpt", &["name", "tag", "on"])?;
        let f0 = {
            let v = lua_field(&t, "VpOpt", "name", false)?;
            let v = <String as FromLua>::from_lua(v, lua)
                .map_err(|err| mlua::Error::runtime(format!("VpOpt.name: {err}")))?;
            v
        };
        let f1 = {
            let v = lua_field(&t, "VpOpt", "tag", true)?;
            let v = <Option<String> as FromLua>::from_lua(v, lua)
                .map_err(|err| mlua::Error::runtime(format!("VpOpt.tag: {err}")))?;
            v
        };
        let f2 = {
            let v = lua_field(&t, "VpOpt", "on", false)?;
            let v = <LBool as FromLua>::from_lua(v, lua)
                .map_err(|err| mlua::Error::runtime(format!("VpOpt.on: {err}")))?;
            v.0
        };
        Ok(Self(azalea::vp::VpOpt { name: f0, tag: f1, on: f2 }))
    }
}
""", S1
        S2 = "\n".join(g.emit_value_param("VpEnum", Mv.vparams["VpEnum"]))
        for want in ('/// `VpEnum` <- Luau table `{ kind = "stop" | "move" | "pair" | "wrap" | "many", ... }`',
                     "pub struct LuaVpEnumIn(pub azalea::vp::VpEnum);",
                     '            Value::Nil => return Err(mlua::Error::runtime("VpEnum: missing field `kind`")),',
                     '            "stop" => {\n                lua_check_keys(&t, "VpEnum", &["kind"])?;\n                azalea::vp::VpEnum::Stop\n            }',
                     '                lua_check_keys(&t, "VpEnum", &["kind", "_1", "_2"])?;',
                     '                lua_check_keys(&t, "VpEnum", &["kind", "x", "y"])?;',
                     "                azalea::vp::VpEnum::Wrap(azalea::vp::VpPoint { x: f0, y: f1 })",
                     "invalid kind '{other}', expected one of: stop, move, pair, wrap, many"):
            assert want in S2, (want, S2)
        S3 = "\n".join(g.emit_value_helpers())
        assert "fn lua_check_keys(t: &mlua::Table, what: &str, known: &[&str]) -> mlua::Result<()> {" in S3
        assert "fn lua_field(t: &mlua::Table, what: &str, key: &str, optional: bool) -> mlua::Result<Value> {" in S3
        assert 'unknown field `{key}` (known: {})' in S3 and 'missing field `{key}`' in S3
        assert "if !known.contains(&key.as_str()) {" in S3 and "if !optional && matches!(v, Value::Nil) {" in S3
        n += 12
        # classify agrees with map_ty for every type above, both roles (shape only: kind and code)
        for k in vdefs:
            for role in ("param", "ret"):
                c = g.Mapper(ix).classify(P(k), role)
                try:
                    g.Mapper(ix).map_ty(P(k), role)
                    mapped, mcode = True, ""
                except g.Skip as e:
                    mapped, mcode = False, e.code
                if k in ("VpKind", "VpSelf", "VpUuid", "VpHandle", "VpPriv") or (k in ("VpBadName", "VpDup", "VpSV", "VpVNE") and role == "ret"):
                    continue   # decided by the fields / the private-field rule, which classify does not read for a parameter
                assert mapped == (c.kind != "unsupported"), (k, role, c, mapped)
                if not mapped:
                    assert mcode == c.code, (k, role, c, mcode)
        assert M.classify(P("VpPoint"), "param")[:3] == ("value", "value", False)
        assert g.Mapper(ix).classify(P("VpPoint"), "param").reason == "struct read from a table (required keys)"
        assert g.Mapper(ix).classify(P("VpEnum"), "param").reason.startswith("data-carrying enum")
        assert g.Mapper(ix).classify(P("VpNE"), "param").code == "shape_unsupported" and g.Mapper(ix).classify(P("VpNE"), "ret").kind == "value"
        Mb = g.Mapper(ix)
        Mb.building.add("VpPoint")
        assert Mb.classify(P("VpPoint"), "param").code == "recursive"
        n += 12
        # a method through build_method: the wrapper takes the reader type, the call passes `.0`, a Vec of them maps the elements
        gv = g.Generator(ix)
        meth = g.Method("go", "", True, False, False, "&self", [("p", P("VpPoint")), ("o", P("Option", P("VpOpt"))), ("e", P("VpEnum")), ("l", P("Vec", P("VpPoint")))],
                        None, "Client", "t.rs", "pub fn go(&self)")
        w = gv.build_method(meth)[0]
        assert [(nm, ti.rust) for nm, ti in w.wparams] == [("p", "LuaVpPointIn"), ("o", "Option<LuaVpOptIn>"), ("e", "LuaVpEnumIn"), ("l", "Vec<LuaVpPointIn>")], w.wparams
        assert w.call_args == ["p.0", "o.map(|v| v.0)", "e.0", "l.into_iter().map(|v| v.0).collect::<Vec<_>>()"], w.call_args
        assert {("type", "VpPoint"), ("type", "VpOpt"), ("type", "VpEnum")} <= gv.m.touched
        # emission: the Rust readers (helpers once, in name order) and the Luau types (optional keys `T?`, a keyword key quoted)
        rs = g.emit_types(gv, "x")
        assert rs.count("fn lua_check_keys(") == 1 and rs.count("fn lua_field(") == 1, rs
        assert [l for l in rs.split("\n") if l.startswith("pub struct")] == [
            "pub struct LuaVpEnumIn(pub azalea::vp::VpEnum);", "pub struct LuaVpOptIn(pub azalea::vp::VpOpt);", "pub struct LuaVpPointIn(pub azalea::vp::VpPoint);"]
        lu = g.emit_dluau(gv, "x", {})
        assert 'type VpPoint = { x: number, y: number }' in lu and 'type VpOpt = { name: string, tag: string?, on: boolean }' in lu, lu
        assert '{ kind: "stop" } | { kind: "move", value: number } | { kind: "pair", _1: number, _2: string } | { kind: "wrap", x: number, y: number }' in lu, lu
        assert '{ kind: "many", value: { number } }' in lu
        kw = g.Generator(ix)
        kw.build_method(g.Method("kw", "", True, False, False, "&self", [("k", P("VpKw"))], None, "Client", "t.rs", "pub fn kw(&self)"))
        assert 'type VpKw = { type: number, ["end"]: number }' in g.emit_dluau(kw, "x", {})
        # a struct both returned and passed: one Luau type line, both Rust wrappers
        sh = g.Generator(ix)
        sh.build_method(g.Method("s1", "", True, False, False, "&self", [("a", P("VpShare"))], P("VpShare"), "Client", "t.rs", "pub fn s1(&self)"))
        lu = g.emit_dluau(sh, "x", {})
        assert lu.count("type VpShare = ") == 1 and "type VpShare = { a: number, b: boolean }" in lu, lu
        rs = g.emit_types(sh, "x")
        assert "pub struct LuaVpShare(pub azalea::vp::VpShare);" in rs and "pub struct LuaVpShareIn(pub azalea::vp::VpShare);" in rs
        # nothing is emitted for a binding that has no value parameter
        assert "lua_check_keys" not in g.emit_types(g.Generator(ix), "x")
        n += 12
    finally:
        for k in vdefs:
            ix.defs.pop(k, None)
            g.PATH_OVERRIDES.pop(k, None)
    # the old shapes are kept: a [records] row is still write-only as a parameter
    skr2 = g.Mapper(ix)
    assert skr2.map_ty(P("PlayerInfo"), "param").from_lua is None
    n += 1

    print(f"mapper_selftest: OK ({n} checks)")


if __name__ == "__main__":
    main()
