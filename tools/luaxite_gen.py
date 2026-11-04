#!/usr/bin/env python3
"""
luaxite_gen.py - generate the Luau <-> azalea bridge from azalea's own rustdoc JSON.

Reads the rustdoc JSON of the azalea workspace you vendor (--azalea DIR, --rustdoc DIR) and writes:

  rust_binds/luau/gen_client.rs   Luau -> azalea : `bot:method()` forwards to azalea::Client
  rust_binds/luau/gen_events.rs   azalea -> Luau : azalea::Event variants fire `bot:on(...)` handlers
  rust_binds/luau/gen_types.rs    value conversions (enums <-> strings, structs -> tables)
  luau/azalea.d.luau       Luau type definitions for luau-lsp (both directions)
  luau/GEN_REPORT.json     what was generated, what was skipped and why (the machine-readable report; tools read this)
  luau/GEN_REPORT.md       the same report as text for people (rendered from the JSON, never parsed)

Nothing is hand-listed: methods come from every `impl Client` / `XClientExt for Client`
in the rustdoc JSON, events come from `enum Event`, and enums/structs/goals are discovered
from it. Anything whose types can't be mapped is skipped and reported.

Usage (from your project root; stdlib only):
    python tools/luaxite_gen.py --azalea bind/azalea-src --rustdoc DIR --rust-out OUT --luau-out OUT
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import unicodedata
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional


SKIP_DIRS = {"examples", "benches", "tests", "target", ".git"}

BUILTIN = {
    "BlockPos": dict(rust="LBlockPos", luau_def="{ x: number, y: number, z: number }", param=True, ret=True),
    "Vec3": dict(rust="LVec3", luau_def="{ x: number, y: number, z: number }", param=True, ret=True),
    "MinecraftEntityId": dict(rust="LMinecraftEntityId", luau="number", luau_def=None, param=True, ret=True),
    "ItemStack": dict(
        rust="LItem", luau="Item?", alias="Item", luau_def="{ kind: string, count: number }", param=False, ret=True,
    ),
    "Menu": dict(
        rust="LMenu", luau="Menu", luau_def="{ kind: string, slots: { Item | false } }", param=False, ret=True,
        needs="ItemStack",
    ),
    "ContainerHandle": dict(
        rust="LuaContainer", luau="Container", luau_def=None, param=False, ret=True,
        to="LuaContainer { handle: Some(Held::Owned({x})) }", needs="ItemStack",
        class_doc="An open container (a chest, or the bot's own inventory). It closes on `close()` or when garbage collected.",
    ),
    "ContainerHandleRef": dict(
        rust="LuaContainer", luau="Container", luau_def=None, param=False, ret=True,
        to="LuaContainer { handle: Some(Held::Borrowed({x})) }", needs="ItemStack",
        class_doc="An open container (a chest, or the bot's own inventory). It closes on `close()` or when garbage collected.",
    ),
    "PathfinderOpts": dict(
        rust="LPathfinderOpts",
        luau_def="{ retry_on_no_path: boolean?, allow_mining: boolean? }",
        param=True,
        ret=False,
    ),
}


class ConfigError(Exception):
    pass


ROOT = Path(__file__).resolve().parent.parent
BUILTIN_NAME = "builtin"
CONFIG_PATH = ROOT / "bindgen.toml"
BUILTIN_CONFIG = {"deny_methods": [], "classes": {}, "scalar": {}, "records": {}, "accessors": {},
                  **{k: {} for k in ("path_overrides", "renames", "handwritten_goals", "event_arg_names", "hidden_events",
                                     "goal_param_names", "extra_events", "hand_classes", "hand_globals")}}
_STR_TABLES = {"path_overrides": "PATH_OVERRIDES", "renames": "RENAMES", "handwritten_goals": "HANDWRITTEN_GOALS",
               "event_arg_names": "EVENT_ARG_NAMES", "hidden_events": "HIDDEN_EVENTS", "goal_param_names": "GOAL_PARAM_NAMES",
               "extra_events": "EXTRA_EVENTS", "hand_classes": "HAND_CLASSES", "hand_globals": "HAND_GLOBALS"}
_CLASS_KEYS = {"rust": str, "luau": str, "field": str, "renames": (dict, str), "events": bool, "emit": bool, "deny": dict, "doc": str}
_SCALAR_KEYS = {"luau": str, "to": str, "frm": str, "fallible": bool}


def _type_name(t) -> str:
    return " or ".join(x.__name__ for x in t) if isinstance(t, tuple) else t.__name__


def _expect(path: str, v, t):
    if not isinstance(v, t) or (t is not bool and isinstance(v, bool) and bool not in (t if isinstance(t, tuple) else (t,))):
        raise ConfigError(f"key `{path}`: expected {_type_name(t)}, got {type(v).__name__}")
    return v


def _str_table(path: str, v) -> dict:
    _expect(path, v, dict)
    for k, x in v.items():
        _expect(f"{path}.{k}", x, str)
    return dict(v)


SERDE_MODES = ("off", "allow-list", "auto")


def _serde_cfg(t) -> dict:
    """`[serde]` (slice 21): mode off|allow-list|auto (default off), allow / deny / typed = type names, max_depth (typed field nesting)."""
    if not isinstance(t, dict):
        raise ConfigError("key `serde`: must be a table")
    out = {"mode": "off", "allow": set(), "deny": set(), "typed": set(), "max_depth": 8}
    for k, v in t.items():
        if k == "mode":
            if v not in SERDE_MODES:
                raise ConfigError(f"key `serde.mode`: must be one of {', '.join(SERDE_MODES)}, got {v!r}")
            out["mode"] = v
        elif k in ("allow", "deny", "typed"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"key `serde.{k}`: must be a list of strings")
            out[k] = set(v)
        elif k == "max_depth":
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ConfigError("key `serde.max_depth`: must be an integer >= 1")
            out[k] = v
        else:
            raise ConfigError(f"key `serde.{k}`: unknown (expected one of: mode, allow, deny, typed, max_depth)")
    both = sorted(out["allow"] & out["deny"])
    if both:
        raise ConfigError(f"key `serde`: `{both[0]}` is in both allow and deny")
    return out


HANDLE_MODES = ("off", "allow-list", "auto")


def _handles_cfg(t) -> dict:
    """`[handles]` (slice 22): mode off|allow-list|auto (default off), allow / deny = type names, max = cap on the number of classes
    made this way (default 64). A handle is a Luau userdata class for a type with private fields that a signature mentions."""
    if not isinstance(t, dict):
        raise ConfigError("key `handles`: must be a table")
    out = {"mode": "off", "allow": set(), "deny": set(), "max": 64}
    for k, v in t.items():
        if k == "mode":
            if v not in HANDLE_MODES:
                raise ConfigError(f"key `handles.mode`: must be one of {', '.join(HANDLE_MODES)}, got {v!r}")
            out["mode"] = v
        elif k in ("allow", "deny"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"key `handles.{k}`: must be a list of strings")
            out[k] = set(v)
        elif k == "max":
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ConfigError("key `handles.max`: must be an integer >= 0")
            out[k] = v
        else:
            raise ConfigError(f"key `handles.{k}`: unknown (expected one of: mode, allow, deny, max)")
    both = sorted(out["allow"] & out["deny"])
    if both:
        raise ConfigError(f"key `handles`: `{both[0]}` is in both allow and deny")
    return out


def _mono_cfg(t) -> dict:
    """`[monomorphise]` (slice 24): rows = [{ item = "Class.method", <TypeParam> = "Type", .. }], max = 4 (wrappers per fn), auto = false.
    One wrapper per row, named `<fn>_<type>`; a type is a primitive or a type name (no generic arguments)."""
    if not isinstance(t, dict):
        raise ConfigError("key `monomorphise`: must be a table")
    out = {"rows": [], "max": 4, "auto": False}
    for k, v in t.items():
        if k == "max":
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ConfigError("key `monomorphise.max`: must be an integer >= 1")
            out[k] = v
        elif k == "auto":
            if not isinstance(v, bool):
                raise ConfigError("key `monomorphise.auto`: must be true or false")
            out[k] = v
        elif k == "rows":
            if not isinstance(v, list):
                raise ConfigError("key `monomorphise.rows`: must be a list of tables")
            seen = set()
            for i, row in enumerate(v):
                w = f"monomorphise.rows[{i}]"
                if not isinstance(row, dict):
                    raise ConfigError(f"key `{w}`: must be a table")
                item = row.get("item")
                if not isinstance(item, str) or not item.strip():
                    raise ConfigError(f"key `{w}.item`: must be a non-empty string")
                subs = {}
                for pk, pv in row.items():
                    if pk == "item":
                        continue
                    if not isinstance(pv, str) or not pv.strip():
                        raise ConfigError(f"key `{w}.{pk}`: must be a non-empty type name")
                    if not pk.strip():
                        raise ConfigError(f"key `{w}`: empty type parameter name")
                    subs[pk] = pv.strip()
                if not subs:
                    raise ConfigError(f"key `{w}`: needs at least one type argument besides `item`")
                key = (item, tuple(sorted(subs.items())))
                if key in seen:
                    raise ConfigError(f"key `{w}`: duplicate row for `{item}`")
                seen.add(key)
                out["rows"].append((item, subs, w))
        else:
            raise ConfigError(f"key `monomorphise.{k}`: unknown (expected one of: rows, max, auto)")
    per = {}
    for item, _s, w in out["rows"]:
        per[item] = per.get(item, 0) + 1
        if per[item] > out["max"]:
            raise ConfigError(f"key `{w}`: more than `monomorphise.max` ({out['max']}) rows for `{item}`")
    return out


STATICS_MODES = ("off", "allow-list", "auto")
PROPERTIES_MODES = ("off", "allow-list", "auto")
CALLBACKS_MODES = ("off", "auto")


def _statics_cfg(t) -> dict:
    """`[statics]` (slice 23): mode off|allow-list|auto (default off), classes = class names (allow-list), deny = `Class.fn` rows,
    mut_self = also bind `&mut self` methods (default false). An associated fn without a receiver becomes `Class.fn(..)` (a global table
    named like the Luau class); a `&mut self` method becomes a userdata method taking `&mut self` (mlua's runtime borrow check is the borrow model)."""
    if not isinstance(t, dict):
        raise ConfigError("key `statics`: must be a table")
    out = {"mode": "off", "classes": set(), "deny": set(), "mut_self": False}
    for k, v in t.items():
        if k == "mode":
            if v not in STATICS_MODES:
                raise ConfigError(f"key `statics.mode`: must be one of {', '.join(STATICS_MODES)}, got {v!r}")
            out["mode"] = v
        elif k in ("classes", "deny"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"key `statics.{k}`: must be a list of strings")
            if k == "deny":
                for x in v:
                    if x.count(".") != 1 or not all(x.split(".")):
                        raise ConfigError(f"key `statics.deny`: `{x}` must look like `Class.fn`")
            out[k] = set(v)
        elif k == "mut_self":
            if not isinstance(v, bool):
                raise ConfigError("key `statics.mut_self`: must be true or false")
            out[k] = v
        else:
            raise ConfigError(f"key `statics.{k}`: unknown (expected one of: mode, classes, deny, mut_self)")
    return out


def _props_cfg(t) -> dict:
    """`[properties]` (slice 26): mode off|allow-list|auto (default off), classes = class names (allow-list), deny = `Class.name` rows (the property
    name), getter_prefixes = name prefixes a getter may carry besides none (e.g. `get_`), result_getters = a getter returning `Result` may be a field,
    readonly_getters = a getter without a setter becomes a read-only field. A getter `x(&self) -> T` plus a setter `set_x(.., T)` of the same
    type become the Luau field `x: T` instead of two methods."""
    if not isinstance(t, dict):
        raise ConfigError("key `properties`: must be a table")
    out = {"mode": "off", "classes": set(), "deny": set(), "getter_prefixes": [""], "result_getters": False, "readonly_getters": False}
    for k, v in t.items():
        if k == "mode":
            if v not in PROPERTIES_MODES:
                raise ConfigError(f"key `properties.mode`: must be one of {', '.join(PROPERTIES_MODES)}, got {v!r}")
            out[k] = v
        elif k in ("classes", "deny", "getter_prefixes"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"key `properties.{k}`: must be a list of strings")
            if k == "deny":
                for x in v:
                    if x.count(".") != 1 or not all(x.split(".")):
                        raise ConfigError(f"key `properties.deny`: `{x}` must look like `Class.name`")
                out[k] = set(v)
            elif k == "getter_prefixes":
                for x in v:
                    if x and not re.fullmatch(r"[a-z][a-z0-9]*_", x):
                        raise ConfigError(f"key `properties.getter_prefixes`: `{x}` must be empty or like `get_`")
                out[k] = [""] + [x for x in v if x]
            else:
                out[k] = set(v)
        elif k in ("result_getters", "readonly_getters"):
            if not isinstance(v, bool):
                raise ConfigError(f"key `properties.{k}`: must be true or false")
            out[k] = v
        else:
            raise ConfigError(f"key `properties.{k}`: unknown (expected one of: mode, classes, deny, getter_prefixes, result_getters, readonly_getters)")
    return out


def _callbacks_cfg(t) -> dict:
    """`[callbacks]` (slice 25): mode off|auto (default off), max_args = most closure arguments a callback may take (1..16, default 8). A parameter
    `impl Fn(A, B) -> C` / `Box<dyn FnMut(..)>` / `&dyn Fn(..)` becomes a Luau function `(A, B) -> C` (a `mlua::Function` called synchronously)."""
    if not isinstance(t, dict):
        raise ConfigError("key `callbacks`: must be a table")
    out = {"mode": "off", "max_args": 8}
    for k, v in t.items():
        if k == "mode":
            if v not in CALLBACKS_MODES:
                raise ConfigError(f"key `callbacks.mode`: must be one of {', '.join(CALLBACKS_MODES)}, got {v!r}")
            out[k] = v
        elif k == "max_args":
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16:
                raise ConfigError("key `callbacks.max_args`: must be an integer from 1 to 16")
            out[k] = v
        else:
            raise ConfigError(f"key `callbacks.{k}`: unknown (expected one of: mode, max_args)")
    return out


def load_config(path=None) -> dict:
    """Read and validate the TOML tables: {PYTHON_NAME: value}. Every problem is a ConfigError naming the key.
    path=None: `<root>/bindgen.toml`, or the built-in (empty) tables when that file does not exist; an explicit path must exist."""
    import tomllib
    if str(path) == BUILTIN_NAME or (path is None and not CONFIG_PATH.exists()):
        raw = BUILTIN_CONFIG
        path = CONFIG_PATH
    else:
        path = Path(CONFIG_PATH if path is None else path)
        raw = None
    try:
        if raw is None:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror or e}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} is not valid TOML: {e}") from None
    known = {"deny_methods", "classes", "scalar", "records", "accessors", *_STR_TABLES}
    optional = {"extra_event_payloads", "option_structs", "path_prefer", "lock_types", "serde", "handles", "statics", "monomorphise", "properties", "callbacks"}
    for k in raw:
        if k not in known and k not in optional:
            raise ConfigError(f"unknown key `{k}` (expected one of: {', '.join(sorted(known | optional))})")
    for k in sorted(known - set(raw)):
        raise ConfigError(f"missing key `{k}`")
    out: dict = {"DENY_METHODS": set(_expect("deny_methods", raw["deny_methods"], list))}
    for i, x in enumerate(raw["deny_methods"]):
        _expect(f"deny_methods[{i}]", x, str)
    for k, name in _STR_TABLES.items():
        out[name] = _str_table(k, raw[k])
    classes = {}
    for cname, c in _expect("classes", raw["classes"], dict).items():
        _expect(f"classes.{cname}", c, dict)
        for k in c:
            if k not in _CLASS_KEYS:
                raise ConfigError(f"key `classes.{cname}.{k}`: unknown (expected one of: {', '.join(_CLASS_KEYS)})")
        for k in ("rust", "luau", "field", "renames", "deny"):
            if k not in c:
                raise ConfigError(f"key `classes.{cname}.{k}`: missing")
        for k, t in _CLASS_KEYS.items():
            if k in c:
                _expect(f"classes.{cname}.{k}", c[k], t)
        d = dict(c)
        d["deny"] = _str_table(f"classes.{cname}.deny", c["deny"])
        if isinstance(c["renames"], str):
            if c["renames"] != "@renames":
                raise ConfigError(f"key `classes.{cname}.renames`: a string must be \"@renames\", got {c['renames']!r}")
            d["renames"] = out["RENAMES"]
        else:
            d["renames"] = _str_table(f"classes.{cname}.renames", c["renames"])
        classes[cname] = d
    out["CLASSES"] = classes
    scalar = {}
    for tname, c in _expect("scalar", raw["scalar"], dict).items():
        _expect(f"scalar.{tname}", c, dict)
        for k, v in c.items():
            if k not in _SCALAR_KEYS:
                raise ConfigError(f"key `scalar.{tname}.{k}`: unknown (expected one of: {', '.join(_SCALAR_KEYS)})")
            _expect(f"scalar.{tname}.{k}", v, _SCALAR_KEYS[k])
        if "luau" not in c:
            raise ConfigError(f"key `scalar.{tname}.luau`: missing")
        scalar[tname] = dict(c)
    out["SCALAR"] = scalar
    records = {}
    for tname, rows in _expect("records", raw["records"], dict).items():
        _expect(f"records.{tname}", rows, list)
        for i, row in enumerate(rows):
            _expect(f"records.{tname}[{i}]", row, list)
            if len(row) != 3:
                raise ConfigError(f"key `records.{tname}[{i}]`: expected 3 strings (luau field, luau type, rust expr), got {len(row)} items")
            for j, x in enumerate(row):
                _expect(f"records.{tname}[{i}][{j}]", x, str)
        records[tname] = [tuple(r) for r in rows]
    out["RECORDS"] = records
    acc = {}
    for tname, c in _expect("accessors", raw["accessors"], dict).items():
        _expect(f"accessors.{tname}", c, dict)
        for k in c:
            if k not in ("fields", "getters"):
                raise ConfigError(f"key `accessors.{tname}.{k}`: unknown (expected one of: fields, getters)")
        fields = _expect(f"accessors.{tname}.fields", c.get("fields", []), list)
        for i, x in enumerate(fields):
            _expect(f"accessors.{tname}.fields[{i}]", x, str)
        getters = _expect(f"accessors.{tname}.getters", c.get("getters", []), list)
        for i, row in enumerate(getters):
            _expect(f"accessors.{tname}.getters[{i}]", row, list)
            if len(row) != 3:
                raise ConfigError(f"key `accessors.{tname}.getters[{i}]`: expected 3 strings (luau field, luau type, rust expr), got {len(row)} items")
            for j, x in enumerate(row):
                _expect(f"accessors.{tname}.getters[{i}][{j}]", x, str)
        if not fields and not getters:
            raise ConfigError(f"key `accessors.{tname}`: needs `fields` or `getters`")
        if tname in records:
            raise ConfigError(f"key `accessors.{tname}`: also listed in [records] (use one)")
        acc[tname] = dict(fields=list(fields), getters=[tuple(r) for r in getters])
    out["ACCESSORS"] = acc
    payloads = _str_table("extra_event_payloads", raw.get("extra_event_payloads", {}))
    for n, t in payloads.items():
        if n not in out["EXTRA_EVENTS"]:
            raise ConfigError(f"key `extra_event_payloads.{n}`: not listed in [extra_events]")
        if t != "ClientboundPacket":
            raise ConfigError(f"key `extra_event_payloads.{n}`: unknown payload type `{t}` (only `ClientboundPacket`, the generated union of clientbound game packets)")
    out["EXTRA_EVENT_PAYLOADS"] = payloads
    out["OPTION_STRUCTS"] = _str_table("option_structs", raw.get("option_structs", {}))
    out["PATH_PREFER"] = _str_table("path_prefer", raw.get("path_prefer", {}))
    locks = _str_table("lock_types", raw.get("lock_types", {}))
    for n, v in locks.items():
        if n not in ("Mutex", "RwLock"):
            raise ConfigError(f"key `lock_types.{n}`: unknown (expected one of: Mutex, RwLock)")
        if v not in ("std", "parking_lot"):
            raise ConfigError(f"key `lock_types.{n}`: must be \"std\" or \"parking_lot\", got {v!r}")
    out["LOCK_TYPES"] = locks
    out["SERDE"] = _serde_cfg(raw.get("serde", {}))
    out["HANDLES"] = _handles_cfg(raw.get("handles", {}))
    out["STATICS"] = _statics_cfg(raw.get("statics", {}))
    out["MONOMORPHISE"] = _mono_cfg(raw.get("monomorphise", {}))
    out["PROPERTIES"] = _props_cfg(raw.get("properties", {}))
    out["CALLBACKS"] = _callbacks_cfg(raw.get("callbacks", {}))
    return out


try:
    globals().update(load_config())
except ConfigError as e:
    sys.exit(f"error: {CONFIG_PATH.name}: {e}")

# Every integer reaches Luau as a plain `number` (a double). `i128` / `u128` too: mlua implements IntoLua / FromLua for both (`lua_convert_int!`),
# so no conversion is written. Like `i64` / `u64` they are exact only up to 2^53 (lossy above); a value beyond the i64 range does not fit a Luau integer at all.
INT_TYPES = {"i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize"}
FLOAT_TYPES = {"f32", "f64"}
# std types with a fixed Luau form (slice 19): `Duration` = seconds (a number), `PathBuf` / `Path` = a string. A name a loaded crate defines itself,
# or a [scalar] / [builtin] / [classes] row, is not the std type. Wrappers that are unwrapped: Box Rc Cow (always), Mutex RwLock ([lock_types]).
STD_SCALARS = ("Duration", "PathBuf", "Path")
STD_WRAPPERS = ("Box", "Rc", "Cow", "Mutex", "RwLock")
# Every name the mapper turns into a std shape by its LAST path segment. The rdj type AST keeps the full path of a node (`PathTy.full`), so
# `chrono::Duration` / `triomphe::Arc` / `tokio::sync::RwLock` of a crate that is not loaded are told from std's (`std_origin`). Mutex /
# RwLock of `parking_lot` / `lock_api` count as the type `[lock_types]` names.
STD_ROOTS = ("std", "core", "alloc")
LOCK_ROOTS = ("parking_lot", "lock_api")
STD_NAMES = ("String", "Option", "Vec", "Result", "Arc", "HashMap", "BTreeMap") + STD_SCALARS + STD_WRAPPERS
MAP_TYPES = ("HashMap", "BTreeMap", "IndexMap")
# Slice 16: a Rust tuple / `[T; N]` is a Luau array. `LuaTuple` has an impl per arity up to TUPLE_MAX (the macro in emit_seq_helpers);
# ARRAY_MAX keeps a `[u8; 1 << 20]` from becoming a stack array in a generated wrapper.
TUPLE_MAX = 12
ARRAY_MAX = 4096
RUST_KEYWORDS = {
    "as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if",
    "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "Self",
    "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while", "async",
    "await", "dyn", "abstract", "become", "box", "do", "final", "macro", "override", "priv", "typeof",
    "unsized", "virtual", "yield", "try", "gen",
}
LUAU_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
    "nil", "not", "or", "repeat", "return", "then", "true", "until", "while", "continue", "export", "type",
}

LUAU_HARD_KEYWORDS = LUAU_KEYWORDS - {"continue", "export", "type"}
LUAU_TYPE_NAMES = {"string", "number", "boolean", "any", "nil", "table", "thread", "buffer", "vector", "never", "unknown", "function"}
ASCII_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

HEADER = "// @generated by tools/luaxite_gen.py from {src} - DO NOT EDIT, re-run the generator."



# Why something was skipped, as a closed token (slice 13; the taxonomy of slice 10A). `Skip.reason` stays the free text people read;
# `code` is what GEN_REPORT counts and what later slices turn into bindings, one code at a time.
CODES: tuple = (
    "type_unsupported",
    "reference",
    "closure",
    "generic",
    "tuple_struct",
    "private_fields",
    "trait_missing",
    "not_convertible",
    "nested_fallible",
    "result",
    "map_key",
    "direction",
    "ecs_entity",
    "recursive",
    "name_clash",
    "name_invalid",
    "no_path",
    "no_definition",
    "shape_unsupported",
    "impl_trait",
    "receiver",
    "excluded",
)


class Skip(Exception):
    """A type, field or method that gets no binding. `code` is required and must be one of CODES (no default, no \"other\"); `hint` =
    the bindgen.toml section that would fix it, when one applies."""
    def __init__(self, reason: str, code: str, hint: str = ""):
        if code not in CODES:
            raise ValueError(f"unknown skip code {code!r} (reason: {reason})")
        super().__init__(reason)
        self.reason, self.code, self.hint = reason, code, hint


def snake(s: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


_BIDI = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_doc(doc: str) -> str:
    """Control characters and bidi overrides would be copied verbatim into azalea.d.luau and the generated Rust
    (the emitters call this on every doc string)."""
    return "".join(" " if ch == "\t" else ch for ch in doc
                   if ch == "\t" or not (unicodedata.category(ch) == "Cc" or ch in _BIDI))



def type_text(t) -> str:
    k = t[0]
    if k == "prim":
        return t[1]
    if k == "unit":
        return "()"
    if k == "path":
        return t[1] + (f"<{', '.join(type_text(a) for a in t[2])}>" if t[2] else "")
    if k == "ref":
        return "&" + type_text(t[1])
    if k == "slice":
        return f"[{type_text(t[1])}]"
    if k == "array":
        return f"[{type_text(t[1])}; {t[2]}]"
    if k == "tuple":
        return f"({', '.join(type_text(a) for a in t[1])})"
    return t[1]



@dataclass
class Def:
    kind: str
    name: str
    crate: str
    modpath: tuple
    is_pub: bool
    generics: bool
    src: bytes
    tuple_style: bool = False
    fields: list = field(default_factory=list)
    variants: list = field(default_factory=list)
    attrs: list = field(default_factory=list)
    dropped_variants: list = field(default_factory=list)


@dataclass
class Method:
    name: str
    doc: str
    is_pub: bool
    is_async: bool
    generics: bool
    receiver: Optional[str]
    params: list
    ret: Optional[tuple]
    owner: str
    file: str
    sig: str
    target: str = "Client"
    type_params: Optional[list] = None   # slice 24: [(name, (bound trait names,..))]; None = unknown (hand-built) or a const parameter



@dataclass
class TypeInfo:
    rust: str
    luau: str
    to_lua: Optional[Callable[[str], str]] = None
    from_lua: Optional[Callable[[str], str]] = None
    fallible_from: bool = False
    is_result: bool = False
    is_class: bool = False
    needs_client: bool = False
    by_ref: bool = False


def is_ident(f: Callable[[str], str]) -> bool:
    return f("v") == "v"


ID = lambda x: x


MUT_REF = re.compile(r"&\s*(?:'\w+\s+)?mut\b")
STR_TYPES = (("prim", "str"), ("path", "str", []))
BORROWED_STR = (("ref", ("prim", "str")), ("ref", ("path", "str", [])))


CLS_KINDS = ("scalar", "value", "handle", "class", "opaque", "unsupported")
CLS_MODES = ("value", "ref", "mut_ref")


class Cls(NamedTuple):
    """What a type is, decided from its shape (`Mapper.classify`). kind: scalar (String, numbers, SCALAR/BUILTIN rows) | value (a copied
    Luau value: enum, record, option struct, list/map of values) | handle (ECS `Entity`, becomes an `EntityRef` class value) | class (a
    CLASSES row, userdata) | opaque (a struct with private fields and no [records]/[accessors] row: no binding yet, s22) | unsupported (no
    rule; `code` is a CODES token). mode: value | ref (`&T`) | mut_ref (`&mut T`). owned: a by-value / `Arc<T>` handle or class."""
    kind: str
    mode: str
    owned: bool
    reason: str
    code: str = ""


PRIM_NAMES = INT_TYPES | FLOAT_TYPES | {"bool", "char", "str"}


def sub_node(text: str):
    """The type node of one `[monomorphise]` substitution: a primitive or a plain type name; generic arguments are not parsed (slice 24)."""
    t = text.strip()
    if t in PRIM_NAMES:
        return ("prim", t)
    if not re.fullmatch(r"[A-Za-z_]\w*", t):
        raise Skip(f"nested generic substitution `{t}` (a primitive or a plain type name only)", code="type_unsupported")
    return ("path", t, [])


def substitute(ty, subs: dict):
    """`ty` with every type parameter in `subs` ({name: node}) replaced; walks tuple, array, slice, `&T` and path arguments (slice 24).
    A node nothing changed in comes back as the same object (so a path keeps its `.full`)."""
    k = ty[0]
    if k in ("other", "path") and not (k == "path" and ty[2]) and ty[1] in subs:
        return subs[ty[1]]
    if k == "path":
        args = [substitute(a, subs) for a in ty[2]]
        if all(a is b for a, b in zip(args, ty[2])):
            return ty
        node = type(ty)(("path", ty[1], args)) if type(ty) is not tuple else ("path", ty[1], args)
        if hasattr(ty, "full"):
            node.full = ty.full
        return node
    if k == "tuple":
        els = [substitute(a, subs) for a in ty[1]]
        return ty if all(a is b for a, b in zip(els, ty[1])) else ("tuple", els)
    if k in ("slice", "ref", "array"):
        inner = substitute(ty[1], subs)
        return ty if inner is ty[1] else (k, inner) + tuple(ty[2:])
    return ty


def mentions_param(ty, name: str) -> bool:
    return substitute(ty, {name: ("prim", "()")}) is not ty


def turbofish(item: str, params: list, subs: dict, spell) -> str:
    """`item::<A, B>` in DECLARATION order of `params`; `spell(type text)` writes a non-primitive's path."""
    return f"{item}::<" + ", ".join(spell(subs[n]) for n in params) + ">"


def mangle(name: str, params: list, subs: dict) -> str:
    """`name_<type>_<type>` in declaration order, lower snake, `[^a-z0-9_]` -> `_`, runs collapsed."""
    parts = [re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", snake(subs[n]).lower())).strip("_") for n in params]
    return name + "_" + "_".join(parts)


class Mapper:
    def __init__(self, ix: Any):
        self.ix = ix
        self.cache: dict[str, TypeInfo] = {}
        self.fail: dict[str, tuple] = {}   # name -> (reason, code, hint) of the Skip that failed it
        self.deps: dict[str, set] = {}
        self.dep_stack: list[set] = []
        self.touched: set = set()
        self.enums: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.tagged: dict[str, dict] = {}
        self.option_structs: dict[str, dict] = {}
        self.vparams: dict[str, dict] = {}      # slice 20: structs / tagged enums read from a Luau table (parameters)
        self.vp_cache: dict[str, tuple] = {}
        self.serde_used: dict[str, str] = {}
        self.callbacks: set[str] = set()   # slice 25: the Luau text of every callback parameter mapped
        self.vp_fail: dict[str, tuple] = {}
        self.opt_stack: list = []
        self.enum_notes: list[str] = []
        self.cexpr = "self.client"
        self.client_flags: list[bool] = []
        self.paths: dict[str, str] = {}
        self.cur_class: Optional[str] = None
        self.self_ty: Optional[str] = None   # slice 23: what `Self` means while a method of that type is built (set only when [statics] is on)
        self.building: set[str] = set()
        self.tbuilding: set[str] = set()   # tuple structs being converted (slice 18): the recursion guard, they have no cache entry

    def touch(self, key):
        self.touched.add(key)
        for s in self.dep_stack:
            s.add(key)

    def map_ty(self, t, role: str) -> TypeInfo:
        k = t[0]
        if self.self_ty and ((k == "other" and t[1] == "Self") or (k == "path" and t[1] == "Self" and not t[2])):
            return self.path_ty(self.self_ty, [], role, None)
        if k == "unit":
            return TypeInfo("()", "()", to_lua=ID)
        if k == "prim":
            n = t[1]
            if n == "bool":
                if role == "param":
                    return TypeInfo("LBool", "boolean", from_lua=lambda x: f"{x}.0")
                return TypeInfo("bool", "boolean", ID, ID)
            if n in INT_TYPES or n in FLOAT_TYPES:
                return TypeInfo(n, "number", ID, ID)
            if n == "char":   # a Luau string of exactly one character; a script that passes another length gets the `FromStr` error
                return TypeInfo("String", "string", to_lua=lambda x: f"{x}.to_string()", by_ref=True,
                                from_lua=lambda x: f"{x}.parse::<char>().map_err(lua_err)?", fallible_from=True)
            raise Skip(f"primitive `{n}`", code="type_unsupported")
        if k == "ref":
            return self.ref_ty(t, role)
        if k == "other" and MUT_REF.match(t[1]):
            raise Skip(f"mutable reference type `{t[1]}` (a `&mut` borrow has no Luau form; handle `&mut self` is s23)", code="reference")
        if k == "impl":
            if re.fullmatch(r"impl Into<String>", t[1]) and role == "param":
                return TypeInfo("String", "string", from_lua=ID)
            sig = getattr(t, "fn", None)
            if sig is not None and CALLBACKS["mode"] != "off":
                return self.callback_ty(t, sig, role)
            raise Skip(f"`{t[1]}` parameter", code="closure")
        if k == "tuple":
            return self.tuple_ty(t[1], role)
        if k == "array":
            return self.array_ty(t, role)
        if k == "path":
            return self.path_ty(t[1], t[2], role, t)
        raise Skip(f"type `{type_text(t)}`", code="type_unsupported")

    def spell(self, t) -> Optional[str]:
        """Rust source text of a type the generated code can write without help (primitives, `()`, `&T`, slices, arrays, tuples, std `String` /
        `Vec` / `Option` / `Box`, and a type with a public `azalea::` path); None when it can't (slice 25: the `dyn Fn(..)` of a boxed callback)."""
        k = t[0]
        if k == "prim":
            return t[1]
        if k == "unit":
            return "()"
        if k == "ref":
            i = self.spell(t[1])
            return f"&{i}" if i else None
        if k == "slice":
            i = self.spell(t[1])
            return f"[{i}]" if i else None
        if k == "array":
            i = self.spell(t[1])
            return f"[{i}; {t[2]}]" if i and re.fullmatch(r"[0-9]+", t[2]) else None
        if k == "tuple":
            parts = [self.spell(e) for e in t[1]]
            return "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")" if all(parts) else None
        if k != "path":
            return None
        args = [self.spell(a) for a in t[2]]
        if not all(args):
            return None
        tail = f"<{', '.join(args)}>" if args else ""
        if t[1] in ("String", "Vec", "Option", "Box") and self.is_std(t[1], t):
            return t[1] + tail
        try:
            return self.path_for(t[1]) + tail
        except Skip:
            return None

    def dyn_fn_text(self, node) -> Optional[str]:
        """`dyn Fn(A, B) -> C + Send + Sync + 'static` spelled from the signature (not the written text, whose paths may not resolve here)."""
        sig = node.fn
        ins = [self.spell(a) for a in sig.inputs]
        out = self.spell(sig.output) if sig.output is not None else ""
        if not all(ins) or out is None:
            return None
        return (f"dyn {sig.kind}({', '.join(ins)})" + (f" -> {out}" if out else "") + (" + Send" if sig.send else "") + (" + Sync" if sig.sync else "")
                + (" + 'static" if sig.static else ""))

    def has_default(self, t) -> bool:
        """Does `t` implement `Default` for sure (primitives but `str`, `String`, `Vec`, `Option`, maps, `()`, tuples of those, a type the index says
        derives / implements it)? Unknown is no."""
        k = t[0]
        if k == "unit":
            return True
        if k == "prim":
            return t[1] != "str"
        if k == "tuple":
            return all(self.has_default(e) for e in t[1])
        if k == "path":
            if t[1] in ("String", "Vec", "Option", "HashMap", "BTreeMap", "HashSet", "BTreeSet") and self.is_std(t[1], t):
                return True
            return self.ix.is_default(t[1]) is True
        return False

    def callback_ty(self, t, sig, role: str) -> TypeInfo:
        """A closure parameter (slice 25): `impl Fn(A, B) -> C` (also `FnMut` / `FnOnce`, `Box<dyn ..>` and `&dyn ..` through the wrappers) is a Luau
        function `((A, B) -> C)`, read as an `mlua::Function` and handed to Rust as a closure that calls it SYNCHRONOUSLY (`Function::call`). Arguments
        go Rust -> Luau (return conversions, no `()`, nothing fallible, nothing that reads the connected client); the result goes Luau -> Rust (parameter
        conversion). A closure cannot raise a Luau error into its Rust caller: a failed call, or a result that can't be read, goes to
        `lua_callback_error` (stderr) and the closure returns `Default::default()` (`()` for none), so `C` must be `Default` (or `()`). A callback that
        yields fails the same way (mlua can't yield across the Rust frame). Refused with code `closure`: a callback as a return, `Result` / reference /
        callback results, more than `[callbacks] max_args` arguments."""
        if role != "param":
            raise Skip(f"returns the closure `{t[1]}` (a callback is a parameter only)", code="closure")
        what = f"`{t[1]}`"
        if len(sig.inputs) > CALLBACKS["max_args"]:
            raise Skip(f"{what}: {len(sig.inputs)} arguments (`[callbacks] max_args` = {CALLBACKS['max_args']})", code="closure")
        ins = []
        for k, a in enumerate(sig.inputs):
            if a == ("unit",):
                raise Skip(f"{what}: a `()` argument", code="type_unsupported")
            i = self.map_ty(a, "ret")
            if i.is_result:
                raise Skip(f"{what}: nested fallible argument", code="nested_fallible")
            if not i.to_lua:
                raise Skip(f"{what}: argument {k + 1} not convertible", code="not_convertible")
            if i.needs_client or "self." in i.to_lua(f"a{k}"):
                raise Skip(f"{what}: argument {k + 1} needs the connected client (a `'static` closure has no `self`)", code="ecs_entity")
            ins.append(i)
        out = sig.output
        conv = None
        if out is not None:
            if out[0] in ("ref", "impl") or (out[0] == "other" and MUT_REF.match(out[1])):
                raise Skip(f"{what}: the closure returns `{type_text(out)}` (a reference or a closure can't come back from Luau)", code="closure")
            if out[0] == "path" and out[1] in ("Result", "AzaleaResult"):
                raise Skip(f"{what}: the closure returns a `Result` (its error type can't be built from a Luau error)", code="closure")
            oi = self.map_ty(out, "param")
            if not oi.from_lua:
                raise Skip(f"{what}: result not convertible", code="not_convertible")
            if not self.has_default(out):
                raise Skip(f"{what}: result `{type_text(out)}` is not known to be `Default` (the value returned when the callback fails)", code="trait_missing")
            conv = oi
        self.touch(("callback", "lua_callback_error"))
        luau = "(" + ", ".join(i.luau for i in ins) + ") -> " + (conv.luau if conv else "()")
        self.callbacks.add(luau)

        def frm(x, ins=ins, conv=conv):
            names = [f"a{k}" for k in range(len(ins))]
            args = "(" + ", ".join(i.to_lua(n) for i, n in zip(ins, names)) + ("," if len(ins) == 1 else "") + ")"
            head = f"{{ let f = {x}; move |{', '.join(names)}| "
            if conv is None:
                return head + f'{{ if let Err(e) = f.call::<()>({args}) {{ lua_callback_error("callback", &e); }} }} }}'
            return (head + f'{{ f.call::<{conv.rust}>({args}).and_then(|r| Ok({conv.from_lua("r")})).unwrap_or_else(|e| {{ lua_callback_error("callback", &e); '
                    "Default::default() }) } }")
        return TypeInfo("mlua::Function", f"({luau})", from_lua=frm)

    def seq_elem(self, e, role: str, what: str) -> TypeInfo:
        """One element of a tuple / array (slice 16): no `()` (a nil slot), nothing fallible; a `&T` element only in a return (copied out at
        once; as a parameter it would borrow a local of the wrapper that the tuple outlives)."""
        if e == ("unit",):
            raise Skip(f"{what} with a `()` element", code="type_unsupported")
        if e[0] == "ref" and role == "param":
            raise Skip(f"{what} with a reference element `{type_text(e)}`", code="reference")
        i = self.map_ty(e, role)
        if i.is_result or (role == "param" and i.fallible_from):
            raise Skip(f"nested fallible type in {what}", code="nested_fallible")
        return i

    @staticmethod
    def borrow(i: TypeInfo, x: str) -> str:
        """`&<from_lua(x)>` for a parameter of type `&T`. A class parameter (`UserDataRef<R>`) is borrowed straight from the userdata
        instead of cloned out of it."""
        f = i.from_lua(x)
        if i.is_class and i.rust.startswith("mlua::UserDataRef<") and f.endswith(".clone()"):
            return "&" + f[:-len(".clone()")]
        return "&" + f

    def ref_ty(self, t, role: str) -> TypeInfo:
        """`&T` (slice 17). Parameter: T is read from Luau into an owned wrapper local and borrowed for the call (`&x`); a temporary
        (`&x.0.map(..)`) lives to the end of the call statement. Return: the borrow is converted at once into the owned Luau value
        (`.to_string()` for `&str`, `.clone()` for a `Clone` T, `*x` for a primitive), so nothing outlives the `&self` borrow. A `&&T`
        is a coded skip; `&mut T` is one in `map_ty` (an `other` node)."""
        inner = t[1]
        if inner[0] == "ref" or (inner[0] == "other" and MUT_REF.match(inner[1])):
            raise Skip(f"reference to a reference `{type_text(t)}`", code="reference")
        if inner in STR_TYPES:
            if role == "param":
                return TypeInfo("String", "string", from_lua=lambda x: f"&{x}")
            return TypeInfo("String", "string", to_lua=lambda x: f"{x}.to_string()")
        if inner[0] == "slice":
            return self.slice_ref_ty(inner[1], role)
        i = self.map_ty(inner, role)
        if i.is_result:
            raise Skip(f"reference to a Result `{type_text(t)}`", code="reference")
        if role == "param":
            frm = (lambda x, i=i: self.borrow(i, x)) if i.from_lua else None
            return TypeInfo(i.rust, i.luau, from_lua=frm, fallible_from=i.fallible_from, is_class=i.is_class)
        if not i.to_lua:
            return TypeInfo(i.rust, i.luau, is_class=i.is_class, needs_client=i.needs_client)
        lk = self.lock_parts(inner)
        if i.by_ref:
            to = i.to_lua   # a SCALAR row converts from a reference already (`x.to_string()`); so do char, Duration, Path
        elif inner[0] == "prim" and inner[1] != "str":
            to = lambda x, i=i: i.to_lua(f"*{x}")
        elif lk:
            return self.lock_copy(inner, lk)
        else:
            bad = self.not_clone(inner)
            if not bad and inner[0] == "path" and not inner[2] and self.ix.is_clone(inner[1]) is False:
                bad = inner[1]   # `not_clone` trusts every CLASSES / BUILTIN row; they wrap their value, so a copy out of a borrow needs `Clone` for real
            if bad:
                raise Skip(f"`{type_text(t)}`: `{bad}` is not `Clone`, so it can't be copied out of the borrow", code="trait_missing")
            to = lambda x, i=i: i.to_lua(f"{x}.clone()")
        return TypeInfo(i.rust, i.luau, to_lua=to, is_class=i.is_class, needs_client=i.needs_client)

    def slice_ref_ty(self, elem, role: str) -> TypeInfo:
        """`&[T]` <-> Luau list `{ T }`. Parameter: a `Vec<T'>` local, borrowed (`&[&str]` is `&x.iter().map(|v| v.as_str()).collect()`);
        return: each element copied out of the borrow (`x.to_vec()` when that is all it takes)."""
        if role == "param":
            if elem in BORROWED_STR:
                return TypeInfo("Vec<String>", "{ string }", from_lua=lambda x: f"&{x}.iter().map(|v| v.as_str()).collect::<Vec<_>>()")
            if elem[0] == "ref":
                raise Skip(f"slice with a reference element `{type_text(('ref', ('slice', elem)))}`", code="reference")
            i = self.path_ty("Vec", [elem], "param")
            return TypeInfo(i.rust, i.luau, from_lua=(lambda x, i=i: "&" + i.from_lua(x)) if i.from_lua else None)
        ei = self.ref_ty(elem if elem[0] == "ref" else ("ref", elem), "ret")
        if not ei.to_lua:
            return TypeInfo(f"Vec<{ei.rust}>", f"{{ {ei.luau} }}")
        if elem[0] == "ref":
            conv = ei.to_lua("(*v)")   # the iterator yields `&&T`
        else:
            conv = ei.to_lua("v")
        if conv in ("v.clone()", "*v"):
            to = lambda x: f"{x}.to_vec()"
        else:
            to = lambda x, conv=conv: f"{x}.iter().map(|v| {conv}).collect::<Vec<_>>()"
        return TypeInfo(f"Vec<{ei.rust}>", f"{{ {ei.luau} }}", to_lua=to)

    def tuple_ty(self, elems: list, role: str) -> TypeInfo:
        """`(A, B, ..)` <-> Luau array `{ a, b }` (element i at index i + 1; a `None` element leaves a nil slot). Luau has no positional
        table type, so the Luau text is `{ T }` for one distinct element type and `{ T | U }` otherwise. Rust side: `LuaTuple<(A', B')>` (gen_types.rs)."""
        if not 1 <= len(elems) <= TUPLE_MAX:
            raise Skip(f"tuple of {len(elems)} elements (supported: 1 to {TUPLE_MAX})", code="type_unsupported")
        infos = [self.seq_elem(e, role, "tuple") for e in elems]
        self.touch(("seq", "LuaTuple"))
        tup = lambda parts: "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
        names = [f"t{n}" for n in range(len(infos))]
        rust = f"LuaTuple<{tup([i.rust for i in infos])}>"
        luau = "{ " + " | ".join(dict.fromkeys(i.luau for i in infos)) + " }"
        to = frm = None
        if role == "ret":
            if all(i.to_lua is None or is_ident(i.to_lua) for i in infos):
                to = lambda x: f"LuaTuple({x})"
            else:
                to = lambda x, infos=infos: (f"{{ let {tup(names)} = {x}; LuaTuple({tup([i.to_lua(n) if i.to_lua else n for i, n in zip(infos, names)])}) }}")
        elif all(i.from_lua for i in infos):
            if all(is_ident(i.from_lua) for i in infos):
                frm = lambda x: f"{x}.0"
            else:
                frm = lambda x, infos=infos: f"{{ let {tup(names)} = {x}.0; {tup([i.from_lua(n) for i, n in zip(infos, names)])} }}"
        return TypeInfo(rust, luau, to, frm)

    def array_ty(self, t, role: str) -> TypeInfo:
        """`[T; N]` <-> Luau array `{ T }` of exactly N elements (checked when a script passes one). Rust side: `LuaArray<T', N>`."""
        inner, n = t[1], t[2]
        if not re.fullmatch(r"[0-9]+", n):
            raise Skip(f"array length `{n}` is not a decimal literal", code="type_unsupported")
        if int(n) > ARRAY_MAX:
            raise Skip(f"array of {n} elements (supported: up to {ARRAY_MAX})", code="type_unsupported")
        i = self.seq_elem(inner, role, "array")
        self.touch(("seq", "LuaArray"))
        n = str(int(n))
        to = frm = None
        if role == "ret":
            to = (lambda x: f"LuaArray({x})") if i.to_lua is None or is_ident(i.to_lua) else (lambda x, i=i: f"LuaArray({x}.map(|v| {i.to_lua('v')}))")
        elif i.from_lua:
            frm = (lambda x: f"{x}.0") if is_ident(i.from_lua) else (lambda x, i=i: f"{x}.0.map(|v| {i.from_lua('v')})")
        return TypeInfo(f"LuaArray<{i.rust}, {n}>", f"{{ {i.luau} }}", to, frm)

    def own_def(self, name: str) -> bool:
        """A loaded crate (or a config row) defines a type of this name, so it is not the std type of the same name."""
        defs = getattr(self.ix, "defs", None)
        return name in SCALAR or name in BUILTIN or name in CLASSES or bool(defs and defs.get(name))

    def std_roots(self, name: str) -> tuple:
        """Crates a path may start in for `name` to be the type the mapper converts: std / core / alloc; for Mutex / RwLock the crate the
        `[lock_types]` flavour names (std, or parking_lot / lock_api), both while the flavour is unset (the old `generic` skip decides)."""
        if name in ("Mutex", "RwLock"):
            flavour = LOCK_TYPES.get(name)
            return STD_ROOTS if flavour == "std" else LOCK_ROOTS if flavour == "parking_lot" else STD_ROOTS + LOCK_ROOTS
        return STD_ROOTS

    def std_origin(self, name: str, node) -> Optional[bool]:
        """Is the path node `node` (a `PathTy`, see rdj.py) the type the name stands for in this mapper (std's)? True: its full path starts
        in one of `std_roots(name)`; False: it names another crate's type; None: the node does not know its path (a hand-built AST, or a
        bare written path without a `paths` entry), the caller falls back to the by-name rule (`own_def`)."""
        full = getattr(node, "full", None)
        if not full:
            return None
        return full.split("::", 1)[0] in self.std_roots(name)

    def is_std(self, name: str, node=None) -> bool:
        """The std type of this name: decided by the node's full path when it has one (so std's `Duration` stays std even when a loaded crate
        defines its own, and another crate's is never taken for it), else by whether a loaded crate / config row defines the name."""
        o = self.std_origin(name, node)
        return (not self.own_def(name)) if o is None else o

    def foreign_std(self, name: str, node) -> Optional[str]:
        """Reason text when `node` carries a std name (STD_NAMES) but its full path is a crate that is NOT loaded (`chrono::Duration`,
        `tokio::sync::RwLock`, or a std lock where `[lock_types]` says parking_lot): that type has no Luau form here, and the std
        conversion would not compile for it. None otherwise. A name a loaded crate defines is not judged here (it is that crate's type:
        `named` handles it)."""
        if name not in STD_NAMES or self.std_origin(name, node) is not False or self.own_def(name):
            return None
        flavour = LOCK_TYPES.get(name) if name in ("Mutex", "RwLock") else None
        if flavour:
            return f"`{node.full}` is not the {flavour} `{name}` that [lock_types] selects"
        return f"`{node.full}` is not std's `{name}` and its crate is not loaded"

    def std_scalar(self, name: str, args: list, node=None) -> bool:
        return not args and name in STD_SCALARS and self.is_std(name, node)

    def std_path(self, t) -> bool:
        """The unsized std `Path` node (`Box<Path>`, `Cow<Path>`)."""
        return t == ("path", "Path", []) and self.is_std("Path", t)

    def unsized_str(self, name: str, t) -> bool:
        """`Box<str>` / `Rc<str>` / `Arc<str>` and the same of `Path`: no sized inner type, converted through the deref."""
        return name in ("Box", "Rc", "Arc") and (t in STR_TYPES or self.std_path(t))

    def std_wrapper(self, name: str, args: list, node=None) -> bool:
        return len(args) == 1 and name in STD_WRAPPERS and self.is_std(name, node)

    def std_scalar_ty(self, name: str) -> TypeInfo:
        """`Duration` <-> seconds (`f64`; a script's negative, NaN or overflowing number is an error), `PathBuf` / `Path` <-> string (lossy on
        return). `by_ref`: the return conversion also works on a `&Duration` / `&Path`."""
        if name == "Duration":
            return TypeInfo("f64", "number", to_lua=lambda x: f"{x}.as_secs_f64()", by_ref=True, fallible_from=True,
                            from_lua=lambda x: f"std::time::Duration::try_from_secs_f64({x}).map_err(lua_err)?")
        return TypeInfo("String", "string", to_lua=lambda x: f"{x}.to_string_lossy().into_owned()", by_ref=True,
                        from_lua=lambda x: f"std::path::PathBuf::from({x})")

    # lock helpers (slice 19): `[lock_types]` says which lock `Mutex` / `RwLock` is; unset = the type stays a skip
    def lock_parts(self, t):
        """(name, inner type) when `t` is a configured `Mutex<T>` / `RwLock<T>`, else None."""
        if t[0] == "path" and t[1] in ("Mutex", "RwLock") and self.std_wrapper(t[1], t[2], t) and LOCK_TYPES.get(t[1]):
            return t[1], t[2][0]
        return None

    def lock_guard(self, name: str, x: str) -> str:
        meth = "lock" if name == "Mutex" else "read"
        return f"{x}.{meth}().unwrap_or_else(|e| e.into_inner())" if LOCK_TYPES[name] == "std" else f"{x}.{meth}()"

    def lock_copy(self, t, parts) -> TypeInfo:
        """A `&Mutex<T>` / `Arc<RwLock<T>>` return: the value is copied out under the lock (`T: Clone`); the guard never leaves the call."""
        name, inner = parts
        i = self.map_ty(inner, "ret")
        if i.is_result or not i.to_lua:
            raise Skip(f"`{type_text(t)}`: the value inside the lock can't be converted", code="not_convertible")
        if i.by_ref:
            to = lambda x, i=i, n=name: i.to_lua(f"(*{self.lock_guard(n, x)})")
        else:
            bad = self.not_clone(inner)
            if bad:
                raise Skip(f"`{type_text(t)}`: `{bad}` is not `Clone`, so it can't be copied out of the lock", code="trait_missing")
            to = lambda x, i=i, n=name: i.to_lua(f"(*{self.lock_guard(n, x)}).clone()")
        return TypeInfo(i.rust, i.luau, to_lua=to, is_class=i.is_class, needs_client=i.needs_client)

    def wrapper_ty(self, name: str, args: list, role: str) -> TypeInfo:
        """`Box<T>`, `Rc<T>`, `Cow<'_, T>`, configured `Mutex<T>` / `RwLock<T>` (slice 19): the wrapper is unwrapped, the Luau value is the one of T.
        Return: `*x` of a Box, a clone out of an Rc (like `Arc`), `into_owned()` of a Cow (`str` -> String, `[T]` -> Vec<T>, `Path` -> PathBuf),
        `into_inner()` of a lock. Parameter: the wrapper is built from the converted value. `Box<str>` / `Box<Path>` / `Rc<str>` / `Rc<Path>` are
        unsized: converted through the deref, built with `From`."""
        a = args[0]
        what = f"`{name}<{type_text(a)}>`"
        if a[0] == "ref" or (a[0] == "other" and MUT_REF.match(a[1])):
            raise Skip(f"{what}: a wrapper of a reference", code="reference")
        unsized = self.unsized_str(name, a)
        if name == "Cow":
            owned = ("path", "String", []) if a in STR_TYPES else ("path", "Vec", [a[1]]) if a[0] == "slice" else ("path", "PathBuf", []) if self.std_path(a) else a
            i = self.map_ty(owned, role)
            if i.is_result:
                raise Skip(f"nested fallible type in {what}", code="nested_fallible")
            to = (lambda x, i=i: i.to_lua(f"{x}.into_owned()")) if i.to_lua and role == "ret" else None
            frm = (lambda x, i=i: f"std::borrow::Cow::Owned({i.from_lua(x)})") if i.from_lua and role == "param" else None
            return TypeInfo(i.rust, i.luau, to, frm, fallible_from=i.fallible_from, is_class=i.is_class)
        i = self.map_ty(("path", "String", []) if a in STR_TYPES else ("path", "PathBuf", []) if unsized else a, role)
        if i.is_result:
            raise Skip(f"nested fallible type in {what}", code="nested_fallible")
        to = frm = None
        if role == "ret" and i.to_lua:
            if unsized:
                # `str`: `x.to_string()` (the `String` row is the identity, and a `Box<str>` is no `String`); `Path`: the `PathBuf` row
                # (`x.to_string_lossy().into_owned()`) already works through the deref
                to = (lambda x: f"{x}.to_string()") if a in STR_TYPES else i.to_lua
            elif name == "Box":
                to = lambda x, i=i: i.to_lua(f"(*{x})")
            elif name == "Rc":
                if i.by_ref:
                    to = i.to_lua
                else:
                    bad = self.not_clone(a)
                    if bad:
                        raise Skip(f"{what}: `{bad}` is not `Clone`, so it can't be copied out of the `Rc`", code="trait_missing")
                    to = lambda x, i=i: i.to_lua(f"(*{x}).clone()")
            elif name in ("Mutex", "RwLock"):
                if LOCK_TYPES[name] == "std":
                    to = lambda x, i=i: i.to_lua(f"{x}.into_inner().unwrap_or_else(|e| e.into_inner())")
                else:
                    to = lambda x, i=i: i.to_lua(f"{x}.into_inner()")
        elif role == "param" and i.from_lua:
            if unsized:
                ctor = ("{x}.into_boxed_str()" if a in STR_TYPES else "{x}.into_boxed_path()") if name == "Box" \
                    else f"std::{'rc::Rc' if name == 'Rc' else 'sync::Arc'}::from({{x}})"
                frm = lambda x, i=i, c=ctor: c.format(x=i.from_lua(x))
            elif name in ("Mutex", "RwLock"):
                if LOCK_TYPES[name] != "std":
                    raise Skip(f"{what} parameter: building a `parking_lot` lock needs that crate in the generated code", code="not_convertible")
                frm = lambda x, i=i, n=name: f"std::sync::{n}::new({i.from_lua(x)})"
            elif name == "Box" and getattr(a, "fn", None) is not None and self.dyn_fn_text(a):
                # slice 25: `Box::new(closure)` coerces to `Box<dyn Fn..>` only where the target is known; inside `Option::map` / `Vec::map` it
                # is not, so the box is cast to the spelled-out type
                frm = lambda x, i=i, d=self.dyn_fn_text(a): f"Box::new({i.from_lua(x)}) as Box<{d}>"
            else:
                frm = lambda x, i=i, n=name: f"{'Box' if n == 'Box' else 'std::rc::Rc'}::new({i.from_lua(x)})"
        return TypeInfo(i.rust, i.luau, to, frm, by_ref=unsized and role == "ret", fallible_from=i.fallible_from, is_class=i.is_class,
                        needs_client=i.needs_client)

    def path_ty(self, name: str, args: list, role: str, node=None) -> TypeInfo:
        bad = self.foreign_std(name, node)
        if bad:
            raise Skip(bad, code="no_definition")
        if name == "String" and not args:
            return TypeInfo("String", "string", ID, ID)
        if self.std_scalar(name, args, node):
            return self.std_scalar_ty(name)
        if name == "Option" and len(args) == 1:
            if args[0] == ("unit",):
                if role != "ret":
                    raise Skip("`Option<()>` parameter", code="type_unsupported")
                return TypeInfo("Option<()>", "boolean?", to_lua=lambda x: f"{x}.map(|_| true)")
            if role == "param" and args[0][0] == "ref":
                return self.option_ref_param(args[0])
            i = self.map_ty(args[0], role)
            if i.is_result or (role == "param" and i.fallible_from):
                raise Skip("nested fallible type in Option", code="nested_fallible")
            to = frm = None
            if i.to_lua:
                to = (lambda x, i=i: x) if is_ident(i.to_lua) else (lambda x, i=i: f"{x}.map(|v| {i.to_lua('v')})")
            if i.from_lua:
                frm = (lambda x, i=i: x) if is_ident(i.from_lua) else (lambda x, i=i: f"{x}.map(|v| {i.from_lua('v')})")
            return TypeInfo(f"Option<{i.rust}>", f"{i.luau}?", to, frm)
        if (name == "Vec" and len(args) == 1) or (name == "Box" and len(args) == 1 and args[0][0] == "slice"):
            elem = args[0] if name == "Vec" else args[0][1]
            if role == "param" and elem[0] == "ref":
                if name == "Vec" and elem in BORROWED_STR:   # `Vec<&str>`: the strings stay owned by the wrapper local, the Vec borrows them
                    return TypeInfo("Vec<String>", "{ string }", from_lua=lambda x: f"{x}.iter().map(|v| v.as_str()).collect::<Vec<_>>()")
                raise Skip(f"list parameter with a reference element `{type_text(elem)}`", code="reference")
            i = self.map_ty(elem, role)
            if i.is_result or (role == "param" and i.fallible_from):   # `fallible_from` is about reading a parameter; a returned `Vec<Duration>` is fine
                raise Skip("nested fallible type in list", code="nested_fallible")
            to = frm = None
            if role == "ret" and i.to_lua:
                head = "{x}" if name == "Vec" else "Vec::from({x})"
                if is_ident(i.to_lua):
                    to = (lambda x, h=head: h.format(x=x))
                else:
                    to = (lambda x, h=head, i=i: h.format(x=x) + f".into_iter().map(|v| {i.to_lua('v')}).collect::<Vec<_>>()")
            if role == "param" and name == "Vec" and i.from_lua and is_ident(i.from_lua):
                frm = ID
            elif role == "param" and i.from_lua and not is_ident(i.from_lua):
                coll = "Vec<_>" if name == "Vec" else "Box<[_]>"
                frm = (lambda x, i=i, c=coll: f"{x}.into_iter().map(|v| {i.from_lua('v')}).collect::<{c}>()")
            return TypeInfo(f"Vec<{i.rust}>", f"{{ {i.luau} }}", to, frm)
        if name in MAP_TYPES and len(args) == 2:
            if role == "param" and any(a[0] == "ref" for a in args):
                raise Skip(f"map parameter with a reference key or value `{type_text(('path', name, args))}`", code="reference")
            k, v = self.map_ty(args[0], role), self.map_ty(args[1], role)
            if any(x.is_result or (role == "param" and x.fallible_from) for x in (k, v)):
                raise Skip("nested fallible type in map", code="nested_fallible")
            if k.luau not in ("string", "number"):
                raise Skip(f"map key type `{type_text(args[0])}` (Luau table keys must be strings or numbers)", code="map_key")
            to = frm = None
            if role == "ret" and k.to_lua and v.to_lua:
                to = (lambda x, k=k, v=v: f"{x}.into_iter().map(|(k, v)| ({k.to_lua('k')}, {v.to_lua('v')}))"
                                          ".collect::<std::collections::HashMap<_, _>>()")
            if role == "param":
                if name == "IndexMap":
                    raise Skip("`IndexMap` parameter (the crate has no `indexmap` dependency to build one)", code="map_key")
                if not (k.from_lua and v.from_lua):
                    raise Skip("map parameter with a key or value that can't be read from Luau", code="not_convertible")
                coll = "std::collections::HashMap<_, _>" if name == "HashMap" else "std::collections::BTreeMap<_, _>"
                frm = (lambda x, k=k, v=v, c=coll: f"{x}.into_iter().map(|(k, v)| ({k.from_lua('k')}, {v.from_lua('v')})).collect::<{c}>()")
            return TypeInfo(f"std::collections::HashMap<{k.rust}, {v.rust}>", f"{{ [{k.luau}]: {v.luau} }}", to, frm)
        if name in ("Result", "AzaleaResult"):
            if role != "ret":
                raise Skip("Result as parameter", code="result")
            if name == "Result" and not (len(args) == 2 and self.error_ok(args[1])):
                raise Skip("Result with an error type that is not `MissingComponentError` or a type known to implement Display", code="trait_missing")
            i = self.map_ty(args[0] if args else ("unit",), "ret")
            if i.is_result:
                raise Skip("nested Result", code="nested_fallible")
            tail = "" if is_ident(i.to_lua) else f".map(|v| {i.to_lua('v')})"
            return TypeInfo(i.rust, i.luau, to_lua=lambda x, tail=tail: f"{x}.map_err(lua_err){tail}", is_result=True)
        if self.std_wrapper(name, args, node) and (name in ("Box", "Rc", "Cow") or LOCK_TYPES.get(name)):
            return self.wrapper_ty(name, args, role)
        if name == "Arc" and len(args) == 1:
            if args[0][0] == "ref":
                raise Skip(f"`{type_text(('path', name, args))}`: an `Arc` of a reference", code="reference")
            if self.unsized_str("Arc", args[0]):
                return self.wrapper_ty("Arc", args, role)
            lk = self.lock_parts(args[0])
            if lk and role == "ret":
                return self.lock_copy(args[0], lk)
            i = self.map_ty(args[0], role)
            if role == "ret" and i.to_lua:
                if i.by_ref:
                    to = i.to_lua
                else:
                    bad = self.not_clone(args[0])
                    if bad:
                        raise Skip(f"`Arc<{type_text(args[0])}>`: `{bad}` is not `Clone`, so it can't be copied out of the `Arc`", code="trait_missing")
                    to = lambda x, i=i: i.to_lua(f"(*{x}).clone()")
                return TypeInfo(i.rust, i.luau, to_lua=to)
            raise Skip("Arc parameter", code="direction")
        if args:
            raise Skip(f"generic type `{type_text(('path', name, args))}`", code="generic")
        return self.named(name, role)

    def option_ref_param(self, a) -> TypeInfo:
        """`Option<&T>` parameter: `Option<T'>` local, handed over with `as_deref()` (`&str`) or `as_ref()`."""
        inner = a[1]
        if inner in STR_TYPES:
            return TypeInfo("Option<String>", "string?", from_lua=lambda x: f"{x}.as_deref()")
        if inner[0] in ("ref", "slice") or (inner[0] == "other" and MUT_REF.match(inner[1])):
            raise Skip(f"`Option<{type_text(a)}>` parameter", code="reference")
        i = self.map_ty(inner, "param")
        if i.is_result or i.fallible_from:
            raise Skip("nested fallible type in Option", code="nested_fallible")
        frm = None
        if i.from_lua:
            frm = (lambda x: f"{x}.as_ref()") if is_ident(i.from_lua) else (lambda x, i=i: f"{x}.map(|v| {i.from_lua('v')}).as_ref()")
        return TypeInfo(f"Option<{i.rust}>", f"{i.luau}?", from_lua=frm)

    def error_ok(self, e) -> bool:
        """`lua_err` takes any `Display`: MissingComponentError, or a plain path type the index knows implements Display."""
        if e == ("path", "MissingComponentError", []):
            return True
        is_display = getattr(self.ix, "is_display", None)
        return bool(e[0] == "path" and not e[2] and is_display is not None and is_display(e[1]) is True)

    def option_struct(self, name: str) -> dict:
        """An options struct a script may pass as a table with only some keys: not generic, named pub fields (ASCII names), `Default`,
        every field readable from Luau. Returns dict(name, path, fields=[(field, TypeInfo)]); raises Skip(reason) otherwise."""
        d = self.ix.unique_def(name)
        if d is None or d.kind != "struct":
            raise Skip(f"`{name}`: no unique public struct definition", code="no_definition")
        if d.generics or d.tuple_style:
            raise Skip(f"struct `{name}` is generic or a tuple struct", code="generic" if d.generics else "tuple_struct")
        if not d.fields or any(not f[2] for f in d.fields):
            raise Skip(f"struct `{name}` has private fields", code="private_fields")
        is_default = getattr(self.ix, "is_default", None)
        if is_default is None or is_default(name) is not True:
            raise Skip(f"struct `{name}` is not known to implement Default (missing keys need a default)", code="trait_missing")
        fields = []
        for fname, ft, _ in d.fields:
            if not ASCII_IDENT.fullmatch(bare(fname)):
                raise Skip(f"struct `{name}`.{fname}: not a plain ASCII field name", code="name_invalid")
            fi = self.nested_option_struct(ft)
            if fi is None:
                try:
                    fi = self.map_ty(ft, "param")
                except Skip as e:
                    raise Skip(f"struct `{name}`.{fname}: {e.reason}", code=e.code)
            if fi.is_result or fi.fallible_from or not fi.from_lua:
                raise Skip(f"struct `{name}`.{fname}: cannot be read from Luau", code="not_convertible")
            fields.append((bare(fname), fi))
        return dict(name=name, path=self.path_for(name), fields=fields)

    def nested_option_struct(self, ft) -> Optional[TypeInfo]:
        """A field whose type is itself an option struct (plain path to a struct that is `Default`): read as a nested table. None: not one."""
        if ft[0] != "path" or ft[2]:
            return None
        d = self.ix.unique_def(ft[1])
        is_default = getattr(self.ix, "is_default", None)
        if d is None or d.kind != "struct" or is_default is None or is_default(ft[1]) is not True or ft[1] in self.opt_stack:
            return None
        self.opt_stack.append(ft[1])
        try:
            e = self.use_option_struct(ft[1])
        except Skip:
            return None
        finally:
            self.opt_stack.pop()
        return TypeInfo(e["rust"], ft[1], from_lua=lambda x: f"{x}.0")

    def use_option_struct(self, name: str) -> dict:
        """`option_struct(name)` registered for emission (`emit_types`: `Lua<Name>Opts`, `emit_dluau`: the table type) and marked used."""
        if name not in self.option_structs:
            if name in self.taken_luau_names() or name in self.enums or name in self.records or name in self.tagged:
                raise Skip(f"`{name}` would redefine a Luau type that already exists", code="name_clash")
            self.option_structs[name] = dict(self.option_struct(name), rust=f"Lua{name}Opts")
        self.touch(("type", name))
        return self.option_structs[name]

    def not_clone(self, t) -> Optional[str]:
        """The name of a type inside `t` that is known not to implement Clone (None: Clone, or can't tell)."""
        k = t[0]
        if k == "tuple":
            return next((b for x in t[1] if (b := self.not_clone(x))), None)
        if k == "array":
            return self.not_clone(t[1])
        if k == "path":
            name, args = t[1], t[2]
            if name in ("Arc", "Rc"):
                return None
            for a in args:
                if (b := self.not_clone(a)):
                    return b
            if name in SCALAR or name in BUILTIN or name in CLASSES or name in ("Vec", "Option", "Box", "String", "Result") + MAP_TYPES:
                if name in SCALAR and self.ix.is_clone(name) is False:
                    return name
                return None
            return name if self.ix.is_clone(name) is False else None
        return None

    def taken_luau_names(self) -> set:
        """Luau type/class names already in use: built-ins, hand-written classes, Luau's own."""
        out = set(LUAU_TYPE_NAMES)
        for n, b in BUILTIN.items():
            if b.get("luau_def"):
                out.add(b.get("alias", n))
            if "class_doc" in b:
                out.add(b["luau"])
        out |= {c["luau"] for c in CLASSES.values()}
        out |= set(HAND_CLASSES)
        return out

    def named(self, name: str, role: str) -> TypeInfo:
        """The native shapes first (records .. value params), then serde (slice 21, `[serde]`), else the native refusal."""
        try:
            return self._named(name, role)
        except Skip as e:
            if e.code in ("recursive", "no_path"):
                raise
            alt = self.serde_info(name, role, e)
            if alt is None:
                alt = self.handle_info(name, role, e)
            if alt is None:
                raise
            return alt

    def handle_check(self, name: str) -> Optional[tuple]:
        """Pure (slice 22, `[handles]`): None when `name` may become a handle class (a non-generic struct of a loaded crate with a private
        field or no field, one definition, a public path, `Clone`, no config row of its own, allowed by the mode, under the cap), else
        (reason, code). The cap counts the classes already made this way."""
        c = HANDLES
        if c["mode"] == "off":
            return ("handles are off", "excluded")
        if name in c["deny"]:
            return (f"`{name}` is in [handles] deny", "excluded")
        if c["mode"] == "allow-list" and name not in c["allow"]:
            return (f"`{name}` is not in [handles] allow", "excluded")
        if (name in RECORDS or name in ACCESSORS or name in OPTION_STRUCTS or name in CLASSES or name in BUILTIN or name in SCALAR
                or name == "Entity"):
            return ("it has a config row of its own", "excluded")
        d = self.ix.unique_def(name)
        if d is None or d.kind != "struct":
            return (f"`{name}`: no unique public struct definition", "no_definition")
        if d.generics:
            return (f"struct `{name}` is generic", "generic")
        if d.fields and all(f[2] for f in d.fields):
            return (f"struct `{name}` has only public fields (a value, not a handle)", "excluded")
        if self.ix.resolve_path(name) is None:
            return (f"`{name}` has no public path", "no_path")
        if self.ix.is_clone(name) is not True:
            return (f"`{name}` is not known to implement Clone (a handle is copied out of a borrow)", "trait_missing")
        made = sum(1 for n, r in CLASSES.items() if r.get("handle"))
        if made >= c["max"]:
            return (f"[handles] max = {c['max']} classes reached", "excluded")
        return None

    def handle_info(self, name: str, role: str, why: Skip) -> Optional[TypeInfo]:
        """The handle fallback of `named` (after the native shapes and serde): registers a class row for `name` and maps it like any
        CLASSES row. None = not a handle (the native refusal stands). Only a `private_fields` refusal can become a handle."""
        if why.code != "private_fields" or HANDLES["mode"] == "off":
            return None
        bad = self.handle_check(name)
        if bad is not None:   # the native refusal stands, with the reason this type is not a handle as its hint
            if not why.hint:
                why.hint = f"not a handle: {bad[0]}"
                self.fail[name] = (why.reason, why.code, why.hint)
            return None
        CLASSES[name] = dict(rust=f"Lua{name}", luau=name, field="inner", renames={}, events=False, emit=True, deny={}, handle=True,
                             doc=f"Handle to a `{name}` (opaque: private fields).")
        self.fail.pop(name, None)
        return self._named(name, role)

    def serde_kind(self, name: str, role: str) -> Optional[Cls]:
        """Pure: a Cls(value) when `[serde]` takes `name` in this role (Serialize for a return, Deserialize for a parameter; a
        type of a loaded crate, not generic, no config row of its own, not denied), else None."""
        c = SERDE
        if c["mode"] == "off" or name in c["deny"] or (c["mode"] == "allow-list" and name not in c["allow"]):
            return None
        if (name in RECORDS or name in ACCESSORS or name in OPTION_STRUCTS or name in CLASSES or name in BUILTIN or name in SCALAR
                or name == "Entity"):
            return None
        d = self.ix.unique_def(name)
        if d is None or d.generics:
            return None
        ok = self.ix.is_serialize(name) if role == "ret" else self.ix.is_deserialize(name)
        if ok is not True:
            return None
        return Cls("value", "value", False, "serde type (copied through mlua's serde support)")

    def serde_luau(self, name: str, d, role: str) -> str:
        """The Luau text: field-typed only for a `[serde] typed` struct with named fields and no shape-changing attribute, else `any`
        (`{ [string]: any }` for a struct). A wrong text is worse than `any`, so a visible shape key always wins over `typed`."""
        if d.kind != "struct":
            return "any"
        plain = d.fields and not d.tuple_style and all(f[2] for f in d.fields)
        import rdj_index
        shape = rdj_index.serde_shape(self.ix.serde_attrs(name))
        if name not in SERDE["typed"] or shape == "any" or not plain:
            return "{ [string]: any }" if not d.tuple_style else "any"
        if len(self.building) >= SERDE["max_depth"]:
            raise Skip(f"`{name}`: serde field types nest deeper than max_depth", code="recursive")
        self.building.add(name)
        try:
            parts = []
            for fname, ft, _pub in d.fields:
                try:
                    fi = self.map_ty(ft, role)
                except Skip as e:
                    raise Skip(f"serde type `{name}`.{fname}: {e.reason}", code="not_convertible")
                parts.append(f"{bare(fname)}{'?' if shape == 'optional-keys' else ''}: {fi.luau}")
        finally:
            self.building.discard(name)
        return "{ " + ", ".join(parts) + " }"

    def serde_info(self, name: str, role: str, why: Skip) -> Optional[TypeInfo]:
        if SERDE["mode"] == "off":
            return None
        if self.serde_kind(name, role) is None:
            other = "param" if role == "ret" else "ret"
            if self.serde_kind(name, other) is not None and why.code != "direction":
                what = "Serialize only: it can't be passed" if role == "param" else "Deserialize only: it can't be returned"
                raise Skip(f"`{name}` is {what} (serde)", code="direction")
            return None
        d = self.ix.unique_def(name)
        luau = self.serde_luau(name, d, role)
        path = self.path_for(name)
        self.touch(("serde", "LuaSerde"))
        self.serde_used[name] = "typed" if luau.startswith("{ ") and "[string]" not in luau else "any"
        return TypeInfo(f"LuaSerde<{path}>", luau, to_lua=lambda x: f"LuaSerde({x})", from_lua=lambda x: f"{x}.0")

    def _named(self, name: str, role: str) -> TypeInfo:
        if role == "param" and name in OPTION_STRUCTS:
            e = self.use_option_struct(name)
            return TypeInfo(e["rust"], name, from_lua=lambda x: f"{x}.0")
        if name == "Entity":
            cls = CLASSES["EntityRef"]
            nested = bool(self.building)
            if not cls["emit"] or (not nested and self.cur_class != "Client") or (nested and role == "param"):
                raise Skip("ECS `Entity` handle: only `Client` methods can turn it into an `Entity` class value", code="ecs_entity")
            self.touch(("class", "EntityRef"))
            r, f = cls["rust"], cls["field"]
            if role == "param":
                return TypeInfo(f"mlua::UserDataRef<{r}>", cls["luau"], from_lua=lambda x, f=f: f"{x}.{f}.id()", is_class=True)
            if nested:
                self.client_flags[-1] = True
            return TypeInfo(r, cls["luau"], to_lua=lambda x, r=r, f=f: f"{r} {{ {f}: {self.cexpr}.entity_ref_for({x}) }}", is_class=True,
                            needs_client=nested)
        cfg = CLASSES.get(name)
        if cfg is not None and cfg["emit"] and name != "Client":
            self.touch(("class", name))
            r, f = cfg["rust"], cfg["field"]
            if role == "param":
                return TypeInfo(f"mlua::UserDataRef<{r}>", cfg["luau"], from_lua=lambda x, f=f: f"{x}.{f}.clone()", is_class=True)
            return TypeInfo(r, cfg["luau"], to_lua=lambda x, r=r, f=f: f"{r} {{ {f}: {x} }}", is_class=True)
        if name in BUILTIN:
            b = BUILTIN[name]
            if not b[role]:
                raise Skip(f"`{name}` can't be used as a {role}", code="direction")
            self.touch(("builtin", name))
            r = b["rust"]
            return TypeInfo(
                r, b.get("luau", name),
                to_lua=(lambda x, b=b, r=r: b["to"].replace("{x}", x) if "to" in b else f"{r}({x})") if b["ret"] else None,
                from_lua=(lambda x: f"{x}.0") if b["param"] else None,
            )
        if name in SCALAR:
            s = SCALAR[name]
            frm = s.get("frm")
            return TypeInfo(
                "String", s["luau"],
                to_lua=lambda x, s=s: s["to"].format(x=x),
                from_lua=(lambda x, frm=frm: frm.format(x=x)) if frm else None,
                fallible_from=bool(s.get("fallible")),
                by_ref=True,
            )
        td = self.tuple_struct_def(name)
        if td is not None:
            return self.client_gate(name, self.tuple_struct_ty(name, td, role))
        if role == "param":
            vp = self.value_param(name)
            if vp is not None:
                return vp
        if name in self.fail:
            raise Skip(*self.fail[name])
        if name in self.building:
            raise Skip(f"`{name}` contains itself (recursive types are not converted)", code="recursive")
        if name in self.cache:
            self.touch(("type", name))
            for dep in self.deps.get(name, ()):
                self.touch(dep)
            return self.client_gate(name, self.cache[name])
        self.dep_stack.append(set())
        self.client_flags.append(False)
        self.building.add(name)
        saved_class, self.cur_class = self.cur_class, None
        saved_c, self.cexpr = self.cexpr, "client"
        try:
            info = self.build_named(name)
        except Skip as e:
            self.fail[name] = (e.reason, e.code, e.hint)
            raise
        finally:
            self.building.discard(name)
            deps = self.dep_stack.pop()
            info_needs = self.client_flags.pop()
            self.cur_class = saved_class
            self.cexpr = saved_c
        info.needs_client = info_needs
        self.cache[name] = info
        self.deps[name] = deps
        self.touch(("type", name))
        for dep in deps:
            self.touch(dep)
        return self.client_gate(name, info)

    def vparam_def(self, name: str):
        """The `Def` of a type a script passes as a Luau table (slice 20): a unique, non-generic struct with named public fields only, or an
        enum with a data-carrying variant. None: not one (a [records] / [accessors] / [option_structs] / class / built-in / scalar row, a
        taken Luau name, a tuple struct, a struct with a private field, a unit-only enum): `named` then goes the old way. Pure."""
        if (name in RECORDS or name in ACCESSORS or name in OPTION_STRUCTS or name in CLASSES or name in BUILTIN or name in SCALAR
                or name == "Entity" or name in self.taken_luau_names()):
            return None
        d = self.ix.unique_def(name)
        if d is None or d.generics:
            return None
        if d.kind == "struct":
            return d if (not d.tuple_style and d.fields and all(f[2] for f in d.fields)) else None
        if d.kind == "enum" and d.variants and any(v["kind"] != "unit" for v in d.variants):
            return d
        return None

    def vparam_shape(self, name: str, d) -> Optional[tuple]:
        """(reason, code) when the shape of `d` can't be read from a table at all (shape only: field types are `map_ty`'s business), else None."""
        if d.kind == "struct":
            if "#[non_exhaustive]" in (d.attrs or ()):
                return (f"struct `{name}` is `#[non_exhaustive]`: it can't be built outside its crate", "shape_unsupported")
            for fname, _ft, _ in d.fields:
                if not ASCII_IDENT.fullmatch(bare(fname)):
                    return (f"struct `{name}`.{fname}: not a plain ASCII field name", "name_invalid")
            return None
        kinds = [snake(bare(v["name"])) for v in d.variants]
        if len(set(kinds)) != len(kinds):
            return (f"enum `{name}`: two variants have the same snake_case name", "name_clash")
        for v in d.variants:
            if v["kind"] == "struct":
                return (f"enum `{name}`::{bare(v['name'])} is a struct-style variant", "shape_unsupported")
            if "#[non_exhaustive]" in (v.get("attrs") or ()):
                return (f"enum `{name}`::{bare(v['name'])} is `#[non_exhaustive]`: it can't be built outside its crate", "shape_unsupported")
        return None

    def value_param(self, name: str) -> Optional[TypeInfo]:
        """A struct with public fields or a data-carrying enum as a PARAMETER (slice 20): read from a Luau table by a generated
        `Lua<Name>In` (`FromLua`, `emit_types`). Struct: one key per field, every key required except an `Option<T>` field (nil = None); an unknown
        key is an error. Enum: `{ kind = "<variant, snake_case>", ... }` with the same field rules as the tagged table a return gives
        (`value` / `_1`, `_2`, ..., or the flattened fields of a one-field variant holding such a struct). None: `vparam_def` says it is not one."""
        d = self.vparam_def(name)
        if d is None:
            return None
        if name in self.vp_cache:
            info, deps = self.vp_cache[name]
            self.touch(("type", name))
            for dep in deps:
                self.touch(dep)
            return info
        if name in self.vp_fail:
            raise Skip(*self.vp_fail[name])
        if name in self.building:
            raise Skip(f"`{name}` contains itself (recursive types are not converted)", code="recursive")
        self.dep_stack.append(set())
        self.client_flags.append(False)
        self.building.add(name)
        saved_class, self.cur_class = self.cur_class, None
        saved_c, self.cexpr = self.cexpr, "client"
        try:
            info = self.build_value_param(name, d)
        except Skip as e:
            self.vp_fail[name] = (e.reason, e.code, e.hint)
            raise
        finally:
            self.building.discard(name)
            deps = self.dep_stack.pop()
            self.client_flags.pop()
            self.cur_class = saved_class
            self.cexpr = saved_c
        self.vp_cache[name] = (info, deps)
        self.touch(("type", name))
        for dep in deps:
            self.touch(dep)
        return info

    def vp_field(self, what: str, ft) -> TypeInfo:
        try:
            fi = self.map_ty(ft, "param")
        except Skip as e:
            raise Skip(f"{what}: {e.reason}", code=e.code, hint=e.hint)
        if fi.is_result or not fi.from_lua:
            raise Skip(f"{what}: cannot be read from Luau", code="not_convertible")
        return fi

    def check_share(self, name: str, sig: tuple) -> None:
        """A struct / tagged enum that is both returned and passed has ONE Luau type `Name`: the two sides must agree on its text."""
        other = None
        if name in self.records:
            other = tuple((f, t) for f, t, _e in self.records[name]["fields"])
        elif name in self.tagged:
            other = tuple((v["kind"], tuple((f, t) for f, t, _e in v["fields"])) for v in self.tagged[name]["variants"])
        if other is not None and other != sig:
            raise Skip(f"`{name}` is returned and passed as a table with a different Luau type each way", code="name_clash")

    def check_share_ret(self, name: str, sig: tuple) -> None:
        """The other direction of `check_share`: a record / tagged enum built after a value parameter of the same name."""
        e = self.vparams.get(name)
        if e is None:
            return
        mine = (tuple((f, fi.luau) for f, fi, _r in e["fields"]) if e["kind"] == "struct"
                else tuple((v["kind"], tuple((f, fi.luau) for f, fi in v["fields"])) for v in e["variants"]))
        if mine != sig:
            raise Skip(f"`{name}` is returned and passed as a table with a different Luau type each way", code="name_clash")

    def build_value_param(self, name: str, d) -> TypeInfo:
        bad = self.vparam_shape(name, d)
        if bad:
            raise Skip(*bad)
        rust = f"Lua{name}In"
        path = self.path_for(name)
        if d.kind == "struct":
            fields = []
            for fname, ft, _ in d.fields:
                fi = self.vp_field(f"struct `{name}`.{fname}", ft)
                fields.append((bare(fname), fi, rust_ident(fname)))
            self.check_share(name, tuple((f, fi.luau) for f, fi, _r in fields))
            self.vparams[name] = dict(kind="struct", rust=rust, path=path, fields=fields)
            return TypeInfo(rust, name, from_lua=lambda x: f"{x}.0")
        variants = []
        for v in d.variants:
            vn = bare(v["name"])
            kname = snake(vn)
            fields, build = [], f"{path}::{v['name']}"
            if v["kind"] == "tuple":
                ts = v["types"]
                infos = [self.vp_field(f"enum `{name}`::{vn}", t) for t in ts]
                inner = ts[0][1] if len(ts) == 1 and ts[0][0] == "path" and not ts[0][2] else None
                if inner in self.vparams and self.vparams[inner]["kind"] == "struct" and infos[0].luau == inner:
                    ie = self.vparams[inner]
                    fields = [(f, fi) for f, fi, _r in ie["fields"]]
                    build += f"({ie['path']} {{ " + ", ".join(f"{r}: f{i}" for i, (_f, _fi, r) in enumerate(ie["fields"])) + " })"
                elif len(ts) == 1:
                    fields = [("value", infos[0])]
                    build += "(f0)"
                else:
                    fields = [(f"_{i + 1}", fi) for i, fi in enumerate(infos)]
                    build += "(" + ", ".join(f"f{i}" for i in range(len(infos))) + ")"
            if any(f[0] == "kind" for f in fields):
                raise Skip(f"enum `{name}`::{vn}: a field is called `kind`", code="name_clash")
            variants.append(dict(name=vn, kind=kname, fields=fields, build=build))
        self.check_share(name, tuple((v["kind"], tuple((f, fi.luau) for f, fi in v["fields"])) for v in variants))
        self.vparams[name] = dict(kind="enum", rust=rust, path=path, variants=variants)
        self.note_dropped(name, d, "a script cannot pass such a variant")
        return TypeInfo(rust, name, from_lua=lambda x: f"{x}.0")

    def tuple_struct_def(self, name: str):
        """The `Def` of a tuple struct this mapper turns into a Luau value (slice 18): a unique, non-generic `struct Name(pub A, pub B)` with at
        least one field, every field public and none stripped, and no [records] / [accessors] row (those keep their own rules). None: not
        one, `build_named` then reports the old coded skip (`tuple struct`) for a tuple struct with private fields."""
        if name in RECORDS or name in ACCESSORS or name in self.taken_luau_names():   # a taken Luau name keeps its `name_clash` skip
            return None
        d = self.ix.unique_def(name)
        if d is None or d.kind != "struct" or not d.tuple_style or d.generics or not d.fields or any(not f[2] for f in d.fields):
            return None
        return d

    def tuple_struct_ty(self, name: str, d, role: str) -> TypeInfo:
        """`struct Name(pub A)` -> the Luau value of `A` (a transparent newtype); `struct Name(pub A, pub B)` -> the Luau array of the
        tuple `(A, B)` (`LuaTuple`, see `tuple_ty`). Nothing is emitted for the struct itself: it has no Luau type name, the fields are
        converted where it is used, so there is no cache (the role decides the Rust type: `bool` / `LBool`). Return: fields are read
        with `.0 .1` of the owned value. Parameter: the struct is built with its path, so `#[non_exhaustive]` and a reference field
        are refused. A struct that holds itself is a coded skip (`recursive`)."""
        what = f"tuple struct `{name}`"
        if name in self.tbuilding:
            raise Skip(f"`{name}` contains itself (recursive types are not converted)", code="recursive")
        if role == "param" and "#[non_exhaustive]" in (d.attrs or ()):
            raise Skip(f"{what} is `#[non_exhaustive]`: it can't be built outside its crate", code="shape_unsupported")
        self.tbuilding.add(name)
        try:
            try:
                if len(d.fields) == 1:
                    i = self.seq_elem(d.fields[0][1], role, "field")
                    if role == "ret":
                        return TypeInfo(i.rust, i.luau, to_lua=(lambda x, i=i: i.to_lua(f"{x}.0")) if i.to_lua else None)
                    path = self.path_for(name)
                    return TypeInfo(i.rust, i.luau, from_lua=(lambda x, i=i, p=path: f"{p}({i.from_lua(x)})") if i.from_lua else None)
                t = self.tuple_ty([f[1] for f in d.fields], role)
            except Skip as e:
                raise Skip(f"{what}: {e.reason}", code=e.code, hint=e.hint)
            names = [f"t{n}" for n in range(len(d.fields))]
            tup = "(" + ", ".join(names) + ")"
            if role == "ret":
                fields = "(" + ", ".join(f"t.{n}" for n in range(len(names))) + ")"
                return TypeInfo(t.rust, t.luau, to_lua=(lambda x, t=t, fields=fields: t.to_lua(f"{{ let t = {x}; {fields} }}")) if t.to_lua else None)
            path = self.path_for(name)
            return TypeInfo(t.rust, t.luau, from_lua=(lambda x, t=t, p=path: f"{{ let {tup} = {t.from_lua(x)}; {p}({', '.join(names)}) }}") if t.from_lua else None)
        finally:
            self.tbuilding.discard(name)

    def _classify_callback(self, ty, sig, role: str, self_ty) -> "Cls":
        """Pure mirror of `callback_ty` (slice 25): a callback is a `value` (a Luau function) when it is a parameter, has at most `max_args`
        arguments, every argument classifies (as a return) and the result (as a parameter) is not a reference / closure / Result. Field-level
        failures (`()` argument, fallible argument, a client-reading argument, a non-`Default` result) are decided by `map_ty`."""
        what = f"`{ty[1]}`"
        if role != "param":
            return Cls("unsupported", "value", False, f"returns the closure {what} (a callback is a parameter only)", "closure")
        if len(sig.inputs) > CALLBACKS["max_args"]:
            return Cls("unsupported", "value", False, f"{what}: {len(sig.inputs)} arguments", "closure")
        parts = [(a, "ret") for a in sig.inputs] + ([(sig.output, "param")] if sig.output is not None else [])
        if sig.output is not None and (sig.output[0] in ("ref", "impl") or (sig.output[0] == "other" and MUT_REF.match(sig.output[1]))
                                       or (sig.output[0] == "path" and sig.output[1] in ("Result", "AzaleaResult"))):
            return Cls("unsupported", "value", False, f"{what}: the closure result is a reference / closure / Result", "closure")
        for a, r in parts:
            c = self.classify(a, r, self_ty)
            if c.kind == "unsupported":
                return Cls("unsupported", "value", False, f"{what}: {c.reason}", c.code)
        return Cls("value", "value", False, what)

    def classify(self, ty, role: str, self_ty: Optional[str] = None) -> Cls:
        """What kind of thing `ty` is for `role` ("param" | "ret"), decided from its shape and the config tables. Pure: no `touch`, no
        cache or `fail` write, no `Skip` (an unsupported type comes back as kind "unsupported" with a CODES `code`). It answers
        "which rule would own this type", not "does `map_ty` succeed": reference shapes (s17), tuples/arrays (s16) and field-level
        failures are not judged here. `self_ty` is what `Self` stands for."""
        k = ty[0]
        if k == "unit":
            return Cls("scalar", "value", False, "unit")
        if k == "prim":
            n = ty[1]
            if n == "bool" or n == "char" or n in INT_TYPES or n in FLOAT_TYPES:
                return Cls("scalar", "value", False, f"primitive `{n}`")
            return Cls("unsupported", "value", False, f"primitive `{n}`", "type_unsupported")
        if k == "ref":
            inner = ty[1]
            if inner[0] == "ref" or (inner[0] == "other" and MUT_REF.match(inner[1])):
                return Cls("unsupported", "ref", False, f"reference to a reference `{type_text(ty)}`", "reference")
            if inner in STR_TYPES:
                return Cls("scalar", "ref", False, "`&str`")
            if role == "ret" and inner[0] == "path" and inner[1] in ("Result", "AzaleaResult"):
                return Cls("unsupported", "ref", False, f"reference to a Result `{type_text(ty)}`", "reference")
            if inner[0] == "slice":   # `&[T]`: a list of what the element is; a parameter's element may only borrow as `&str`
                if role == "param" and inner[1][0] == "ref" and inner[1] not in BORROWED_STR:
                    return Cls("unsupported", "ref", False, f"slice with a reference element `{type_text(ty)}`", "reference")
                return self._borrowed(self._container("slice", [self.classify(inner[1], role, self_ty)]), "ref")
            return self._borrowed(self.classify(inner, role, self_ty), "ref")
        if k == "impl":
            if re.fullmatch(r"impl Into<String>", ty[1]) and role == "param":
                return Cls("scalar", "value", False, "`impl Into<String>` parameter")
            sig = getattr(ty, "fn", None)
            if sig is not None and CALLBACKS["mode"] != "off":
                return self._classify_callback(ty, sig, role, self_ty)
            return Cls("unsupported", "value", False, f"`{ty[1]}` parameter", "closure")
        if k in ("tuple", "array"):
            return self._classify_seq(ty, role, self_ty)
        if k == "slice":
            return Cls("unsupported", "value", False, f"slice type `{type_text(ty)}` (unsized: only behind `Box` or `&`)", "type_unsupported")
        if k == "other":
            text = ty[1]
            if text == "Self":
                return self.classify(("path", self_ty, []), role, None) if self_ty else \
                    Cls("unsupported", "value", False, "`Self` outside an impl", "type_unsupported")
            m = re.fullmatch(r"&mut\s+(?:'\w+\s+)?([A-Za-z_]\w*)", text)
            if m:
                inner = ("prim", "str") if m.group(1) == "str" else ("path", m.group(1), [])
                if inner == ("prim", "str"):
                    return Cls("unsupported", "mut_ref", False, "`&mut str`", "reference")
                return self._borrowed(self.classify(inner, role, self_ty), "mut_ref")
            if text.startswith("&mut"):
                return Cls("unsupported", "mut_ref", False, f"reference type `{text}`", "reference")
            return Cls("unsupported", "value", False, f"type `{text}`", "type_unsupported")
        if k == "path":
            return self._classify_path(ty[1], ty[2], role, self_ty, ty)
        return Cls("unsupported", "value", False, f"type `{type_text(ty)}`", "type_unsupported")

    def _classify_seq(self, ty, role: str, self_ty: Optional[str]) -> Cls:
        """Tuple / `[T; N]` (slice 16), the same refusals as `tuple_ty` / `array_ty`, then the container rule over the elements."""
        what = "tuple" if ty[0] == "tuple" else "array"
        elems = ty[1] if ty[0] == "tuple" else [ty[1]]
        if ty[0] == "tuple" and not 1 <= len(elems) <= TUPLE_MAX:
            return Cls("unsupported", "value", False, f"tuple of {len(elems)} elements (supported: 1 to {TUPLE_MAX})", "type_unsupported")
        if ty[0] == "array":
            if not re.fullmatch(r"[0-9]+", ty[2]):
                return Cls("unsupported", "value", False, f"array length `{ty[2]}` is not a decimal literal", "type_unsupported")
            if int(ty[2]) > ARRAY_MAX:
                return Cls("unsupported", "value", False, f"array of {ty[2]} elements (supported: up to {ARRAY_MAX})", "type_unsupported")
        for e in elems:
            if e == ("unit",):
                return Cls("unsupported", "value", False, f"{what} with a `()` element", "type_unsupported")
            if e[0] == "ref" and role == "param":
                return Cls("unsupported", "value", False, f"{what} with a reference element `{type_text(e)}`", "reference")
        return self._container(what, [self.classify(e, role, self_ty) for e in elems])

    @staticmethod
    def _borrowed(inner: Cls, mode: str) -> Cls:
        """R1: a borrow keeps the kind of what it points at; a handle or class borrowed is not owned."""
        if inner.kind == "unsupported":
            return inner._replace(mode=mode)
        return inner._replace(mode=mode, owned=False)

    def _classify_path(self, name: str, args: list, role: str, self_ty: Optional[str], node=None) -> Cls:
        if name == "Self" and not args and self_ty:
            name, node = self_ty, None
        bad = self.foreign_std(name, node)
        if bad:
            return Cls("unsupported", "value", False, bad, "no_definition")
        if name == "String" and not args:
            return Cls("scalar", "value", False, "`String`")
        if name == "Option" and len(args) == 1 and args[0] == ("unit",):
            if role != "ret":
                return Cls("unsupported", "value", False, "`Option<()>` parameter", "type_unsupported")
            return Cls("value", "value", False, "`Option<()>` -> boolean?")
        if (name in ("Option", "Vec") and len(args) == 1) or (name == "Box" and len(args) == 1 and args[0][0] == "slice"):
            elem = args[0] if name != "Box" else args[0][1]
            if role == "param" and elem[0] == "ref":   # same rules as path_ty / option_ref_param
                bad = (elem[1][0] in ("ref", "slice") or (elem[1][0] == "other" and MUT_REF.match(elem[1][1]))) if name == "Option" \
                    else not (name == "Vec" and elem in BORROWED_STR)
                if bad:
                    return Cls("unsupported", "value", False, f"`{name}` parameter with a reference element `{type_text(elem)}`", "reference")
            return self._container(name, [self.classify(elem, role, self_ty)])
        if self.std_scalar(name, args, node):
            return Cls("scalar", "value", False, f"std `{name}`")
        if self.std_wrapper(name, args, node) and (name in ("Box", "Rc", "Cow") or LOCK_TYPES.get(name)):
            a = args[0]
            if a[0] == "ref" or (a[0] == "other" and MUT_REF.match(a[1])):
                return Cls("unsupported", "value", False, f"`{name}<{type_text(a)}>`: a wrapper of a reference", "reference")
            if a in STR_TYPES or self.std_path(a):
                return Cls("scalar", "value", False, f"`{name}` of a string-like value")
            if name == "Cow" and a[0] == "slice":
                return self._container("Cow", [self.classify(a[1], role, self_ty)])
            if name == "Mutex" or name == "RwLock":
                if role == "param" and LOCK_TYPES[name] != "std":
                    return Cls("unsupported", "value", False, f"`{name}` parameter: building a `parking_lot` lock needs that crate", "not_convertible")
            inner = self.classify(a, role, self_ty)
            if name in ("Box", "Rc") and inner.kind in ("handle", "class"):
                return Cls(inner.kind, "value", True, f"`{name}` of a {inner.kind}: owned", inner.code)
            return inner._replace(mode="value", owned=False) if inner.kind == "unsupported" else inner
        if name in MAP_TYPES and len(args) == 2:
            if role == "param" and any(a[0] == "ref" for a in args):
                return Cls("unsupported", "value", False, f"map parameter with a reference key or value `{type_text(('path', name, args))}`", "reference")
            return self._container(name, [self.classify(a, role, self_ty) for a in args])
        if name in ("Result", "AzaleaResult"):
            if role != "ret":
                return Cls("unsupported", "value", False, "Result as parameter", "result")
            return self.classify(args[0] if args else ("unit",), "ret", self_ty)
        if name == "Arc" and len(args) == 1:
            if args[0][0] == "ref":
                return Cls("unsupported", "value", False, f"`{type_text(('path', name, args))}`: an `Arc` of a reference", "reference")
            if self.unsized_str("Arc", args[0]):
                return Cls("scalar", "value", False, "`Arc` of a string-like value")
            inner = self.classify(args[0], role, self_ty)
            if inner.kind in ("handle", "class"):
                return Cls("class", "value", True, f"`Arc<{name}>` of a {inner.kind}: owned", inner.code)
            return inner
        if args:
            return Cls("unsupported", "value", False, f"generic type `{type_text(('path', name, args))}`", "generic")
        return self._classify_named(name, role)

    @staticmethod
    def _container(name: str, elems: list) -> Cls:
        """A list/map/option of values is a value; of classes it keeps the class kind (EntityRef lists map today). R3, narrowed to what
        the mapper really does: an element that is `opaque` (no by-value Luau form) makes the container unsupported."""
        for e in elems:
            if e.kind == "unsupported":
                return e._replace(mode="value", owned=False)
            if e.kind == "opaque":
                return Cls("unsupported", "value", False, f"opaque type by value inside a container (`{name}`): {e.reason}", "private_fields")
        for kind in ("class", "handle"):
            if any(e.kind == kind for e in elems):
                return Cls(kind, "value", True, f"`{name}` of {kind} values")
        return Cls("value", "value", False, f"`{name}` of values")

    def _classify_named(self, name: str, role: str) -> Cls:
        c = self._classify_native(name, role)
        if c.kind in ("unsupported", "opaque") and c.code not in ("recursive", "no_path"):
            sk = self.serde_kind(name, role)
            if sk is not None:
                return sk
        if c.kind == "opaque" and self.handle_check(name) is None:   # slice 22: map_ty registers a class row on first use
            return Cls("handle", "value", True, f"handle class `{name}` (private fields, `[handles]`)")
        return c

    def _classify_native(self, name: str, role: str) -> Cls:
        """The order of `named` / `build_named`, as a pure function of the config tables and the index."""
        if role == "param" and name in OPTION_STRUCTS:
            return Cls("value", "value", False, "option struct (table with optional keys)")
        if name == "Entity":
            if not CLASSES["EntityRef"]["emit"]:
                return Cls("unsupported", "value", False, "ECS `Entity` handle: the EntityRef class is not emitted", "ecs_entity")
            return Cls("handle", "value", False, "ECS `Entity` -> `EntityRef` class value (Client methods only)")
        cfg = CLASSES.get(name)
        if cfg is not None and cfg["emit"] and name != "Client":
            return Cls("class", "value", True, f"class `{cfg['luau']}`")
        if name in BUILTIN:
            if not BUILTIN[name][role]:
                return Cls("unsupported", "value", False, f"`{name}` can't be used as a {role}", "direction")
            return Cls("scalar", "value", False, f"built-in `{name}`")
        if name in SCALAR:
            return Cls("scalar", "value", False, f"string-like `{name}`")
        if role == "param":
            vd = self.vparam_def(name)
            if vd is not None:
                if name in self.building:
                    return Cls("unsupported", "value", False, f"`{name}` contains itself (recursive types are not converted)", "recursive")
                bad = self.vparam_shape(name, vd)
                if bad:
                    return Cls("unsupported", "value", False, bad[0], bad[1])
                return Cls("value", "value", False, "struct read from a table (required keys)" if vd.kind == "struct" else "data-carrying enum read from a `{ kind = .. }` table")
        if name in self.fail:
            reason, code, _hint = self.fail[name]
            return Cls("unsupported", "value", False, reason, code)
        if name in self.building:
            return Cls("unsupported", "value", False, f"`{name}` contains itself (recursive types are not converted)", "recursive")
        if name in self.taken_luau_names() and name not in RECORDS and name not in ACCESSORS:
            return Cls("unsupported", "value", False, f"`{name}` would redefine a Luau type that already exists", "name_clash")
        if name in RECORDS:
            return self._value_ret_only(role, "[records] row")
        d = self.ix.unique_def(name)
        if d is None:
            return Cls("unsupported", "value", False, f"`{name}`: no unique public definition in the azalea source", "no_definition")
        if d.kind == "enum":
            if not d.variants:
                return Cls("unsupported", "value", False, f"enum `{name}` has no variants", "shape_unsupported")
            if any(v["kind"] != "unit" for v in d.variants):
                if d.generics:
                    return Cls("unsupported", "value", False, f"enum `{name}` is generic", "generic")
                return self._value_ret_only(role, "data-carrying enum (tagged table)")
            return Cls("value", "value", False, "unit-only enum (string)")
        if d.generics:
            return Cls("unsupported", "value", False, f"struct `{name}` is generic", "generic")
        if d.tuple_style:
            if name in ACCESSORS or not d.fields or any(not f[2] for f in d.fields):
                return Cls("unsupported", "value", False, f"tuple struct `{name}` has private fields, no fields or an [accessors] row", "tuple_struct")
            if len(d.fields) > TUPLE_MAX:
                return Cls("unsupported", "value", False, f"tuple struct `{name}` has {len(d.fields)} fields (supported: 1 to {TUPLE_MAX})", "type_unsupported")
            if role == "param" and "#[non_exhaustive]" in (d.attrs or ()):
                return Cls("unsupported", "value", False, f"tuple struct `{name}` is `#[non_exhaustive]`: it can't be built outside its crate", "shape_unsupported")
            for f in d.fields:
                if f[1] == ("unit",):
                    return Cls("unsupported", "value", False, f"tuple struct `{name}` with a `()` field", "type_unsupported")
                if f[1][0] == "ref" and role == "param":
                    return Cls("unsupported", "value", False, f"tuple struct `{name}` with a reference field `{type_text(f[1])}`", "reference")
            return Cls("value", "value", False, f"tuple struct `{name}` (newtype: its field; several fields: a Luau array)")
        if name in ACCESSORS:
            return self._value_ret_only(role, "[accessors] row (record of getters)")
        if not d.fields or any(not f[2] for f in d.fields):
            return Cls("opaque", "value", False, f"struct `{name}` has private fields", "private_fields")
        return self._value_ret_only(role, "struct with all public fields (record)")

    @staticmethod
    def _value_ret_only(role: str, what: str) -> Cls:
        """A [records] / [accessors] row is copied to a Luau table and has no Luau -> Rust reading (slice 20 reads plain structs and enums instead)."""
        if role == "param":
            return Cls("unsupported", "value", False, f"{what} is written to Luau only (no reading back)", "direction")
        return Cls("value", "value", False, what)

    def client_gate(self, name: str, info: TypeInfo) -> TypeInfo:
        """A type that carries the `Client` (it holds an ECS `Entity`) can only be returned by `Client` methods, or sit inside another such type."""
        if info.needs_client:
            if self.building:
                self.client_flags[-1] = True
            elif self.cur_class != "Client":
                raise Skip(f"`{name}` holds an ECS `Entity`: only `Client` methods can turn it into an `Entity` class value", code="ecs_entity")
        return info

    def path_for(self, name: str) -> str:
        prefer = PATH_PREFER.get(name)
        p = PATH_OVERRIDES.get(name) or (self.ix.resolve_path(name, prefer) if prefer else self.ix.resolve_path(name))
        if not p:
            raise Skip(f"`{name}`: no public `azalea::` path found (add it to PATH_OVERRIDES)", code="no_path", hint="[path_overrides]")
        self.paths[name] = p
        return p

    def build_named(self, name: str) -> TypeInfo:
        rust = f"Lua{name}"
        if name in self.taken_luau_names() and name not in RECORDS and name not in ACCESSORS:
            raise Skip(f"`{name}` would redefine a Luau type that already exists (built-in, hand-written class, or Luau's own)", code="name_clash")
        if name in RECORDS:
            self.path_for(name)
            self.records[name] = dict(rust=rust, path=self.paths[name], fields=RECORDS[name], needs_client=False)
            return TypeInfo(rust, name, to_lua=lambda x, r=rust: f"{r}({x})")
        d = self.ix.unique_def(name)
        if d is None:
            raise Skip(f"`{name}`: no unique public definition in the azalea source", code="no_definition")
        if d.kind == "enum":
            if not d.variants:
                raise Skip(f"enum `{name}` has no variants" + (f" ({len(d.dropped_variants)} not in the JSON index)" if d.dropped_variants else ""), code="shape_unsupported")
            if any(v["kind"] != "unit" for v in d.variants):
                return self.build_tagged(name, rust, d)
            strs = [snake(bare(v["name"])) for v in d.variants]
            if len(set(strs)) != len(strs):
                raise Skip(f"enum `{name}`: two variants have the same snake_case name", code="name_clash")
            self.path_for(name)
            self.enums[name] = dict(rust=rust, path=self.paths[name], variants=[v["name"] for v in d.variants])
            self.note_dropped(name, d, "a value of such a variant has no Luau string")
            return TypeInfo(rust, name, to_lua=lambda x, r=rust: f"{r}({x})", from_lua=lambda x: f"{x}.0")
        if d.generics:
            raise Skip(f"struct `{name}` is generic", code="generic")
        if d.tuple_style:
            raise Skip(f"tuple struct `{name}`", code="tuple_struct")
        if name in ACCESSORS:
            return self.build_accessor(name, rust, d)
        if not d.fields or any(not f[2] for f in d.fields):
            raise Skip(f"struct `{name}` has private fields", code="private_fields")
        fields = []
        for fname, ft, _ in d.fields:
            if not ASCII_IDENT.fullmatch(bare(fname)):
                raise Skip(f"struct `{name}`.{fname}: not a plain ASCII field name", code="name_invalid")
            try:
                fi = self.map_ty(ft, "ret")
            except Skip as e:
                raise Skip(f"struct `{name}`.{fname}: {e.reason}", code=e.code)
            if fi.is_result or not fi.to_lua:
                raise Skip(f"struct `{name}`.{fname}: not convertible", code="not_convertible")
            fields.append((bare(fname), fi.luau, fi.to_lua(f"v.{rust_ident(fname)}")))
        self.path_for(name)
        self.check_share_ret(name, tuple((f, t) for f, t, _e in fields))
        nc = self.client_flags[-1]
        self.records[name] = dict(rust=rust, path=self.paths[name], fields=fields, needs_client=nc)
        return TypeInfo(rust, name, to_lua=self.wrap_to_lua(rust, nc))


    def wrap_to_lua(self, rust: str, needs_client: bool):
        """`to_lua` of a generated record/tagged enum: `LuaX(x)`, or `LuaX(x, <client>.clone())` when it holds an ECS `Entity`."""
        if needs_client:
            return lambda x, r=rust: f"{r}({x}, {self.cexpr}.clone())"
        return lambda x, r=rust: f"{r}({x})"

    def build_tagged(self, name: str, rust: str, d) -> TypeInfo:
        """A data-carrying enum -> a Luau table `{ kind = "<variant, snake_case>", ... }` (return values only).
        Unit variant: just `kind`. One-field tuple variant holding a record struct: that record's fields, flattened next to `kind`;
        holding anything else: `value`. Several fields: `_1`, `_2`, ... Struct-style variants are refused (the rustdoc JSON index keeps no field names)."""
        if d.generics:
            raise Skip(f"enum `{name}` is generic", code="generic")
        kinds = [snake(bare(v["name"])) for v in d.variants]
        if len(set(kinds)) != len(kinds):
            raise Skip(f"enum `{name}`: two variants have the same snake_case name", code="name_clash")
        variants = []
        for v, kname in zip(d.variants, kinds):
            vn = bare(v["name"])
            if v["kind"] == "struct":
                raise Skip(f"enum `{name}`::{vn} is a struct-style variant", code="shape_unsupported")
            fields, pat = [], ""
            if v["kind"] == "tuple":
                ts = v["types"]
                infos = []
                for i, t in enumerate(ts):
                    try:
                        infos.append(self.map_ty(t, "ret"))
                    except Skip as e:
                        raise Skip(f"enum `{name}`::{vn}: {e.reason}", code=e.code)
                    if infos[-1].is_result or not infos[-1].to_lua:
                        raise Skip(f"enum `{name}`::{vn}: not convertible", code="not_convertible")
                if len(ts) == 1:
                    inner = ts[0][1] if ts[0][0] == "path" and not ts[0][2] else None
                    if infos[0].rust == "()":
                        pat = "(_)"
                    elif inner in self.records and infos[0].luau == inner:
                        pat, fields = "(v)", list(self.records[inner]["fields"])
                    else:
                        pat, fields = "(v)", [("value", infos[0].luau, infos[0].to_lua("v"))]
                else:
                    pat = "(" + ", ".join("_" if inf.rust == "()" else f"f{i}" for i, inf in enumerate(infos)) + ")"
                    fields = [(f"_{i + 1}", inf.luau, inf.to_lua(f"f{i}")) for i, inf in enumerate(infos) if inf.rust != "()"]
            if any(f[0] == "kind" for f in fields):
                raise Skip(f"enum `{name}`::{vn}: a field is called `kind`", code="name_clash")
            variants.append(dict(name=vn, kind=kname, pat=pat, fields=fields))
        self.path_for(name)
        self.check_share_ret(name, tuple((v["kind"], tuple((f, t) for f, t, _e in v["fields"])) for v in variants))
        nc = self.client_flags[-1]
        self.tagged[name] = dict(rust=rust, path=self.paths[name], variants=variants, needs_client=nc)
        self.note_dropped(name, d, "a value of such a variant reaches Luau as an empty table (`_ => {}`)")
        return TypeInfo(rust, name, to_lua=self.wrap_to_lua(rust, nc))

    def note_dropped(self, name: str, d, effect: str) -> None:
        """Front end lost variants of an enum that is being emitted (rustdoc JSON lacks #[doc(hidden)] variants unless it was made
        with --document-hidden-items): one report line, so the emitted enum is never silently narrower than the source."""
        if d.dropped_variants:
            kept = len(d.variants)
            self.enum_notes.append(f"`{name}`: {kept} of {kept + len(d.dropped_variants)} variants emitted, missing "
                                   + ", ".join(d.dropped_variants) + f" (not in the JSON index); {effect}")

    def build_accessor(self, name: str, rust: str, d) -> TypeInfo:
        """A struct with private fields, read through [accessors.X]: the listed public fields (mapped like any record field)
        plus the getter rows (luau field, luau type, rust expr over `v`)."""
        acc = ACCESSORS[name]
        if d.generics or d.tuple_style:
            raise Skip(f"accessor struct `{name}` is generic or a tuple struct", code="generic" if d.generics else "tuple_struct")
        public = {bare(f[0]): f for f in (d.fields or []) if f[2]}
        fields = []
        for fname in acc["fields"]:
            f = public.get(fname)
            if f is None:
                raise Skip(f"accessor struct `{name}`: no public field `{fname}`", code="private_fields")
            try:
                fi = self.map_ty(f[1], "ret")
            except Skip as e:
                raise Skip(f"struct `{name}`.{fname}: {e.reason}", code=e.code)
            if fi.is_result or not fi.to_lua:
                raise Skip(f"struct `{name}`.{fname}: not convertible", code="not_convertible")
            fields.append((fname, fi.luau, fi.to_lua(f"v.{rust_ident(fname)}")))
        fields += list(acc["getters"])
        names = [f[0] for f in fields]
        if len(set(names)) != len(names):
            raise Skip(f"accessor struct `{name}`: a Luau field is listed twice", code="name_clash")
        self.path_for(name)
        nc = self.client_flags[-1]
        self.records[name] = dict(rust=rust, path=self.paths[name], fields=fields, needs_client=nc)
        return TypeInfo(rust, name, to_lua=self.wrap_to_lua(rust, nc))



@dataclass
class Wrapper:
    name: str
    doc: str
    wparams: list
    call_args: list
    ret: Optional[TypeInfo]
    owner: str
    origin: str
    origin_name: str = ""
    is_async: bool = False
    kind: str = "method"   # "method" (`&self`) | "mut" (`&mut self`, slice 23) | "static" (no receiver, slice 23)


class PropertyPair(NamedTuple):
    """Slice 26: one Luau field made from a getter (and a setter). `setter` None = read-only; gw / sw are the bound Wrappers."""
    owner: str
    name: str
    getter: str
    setter: Optional[str]
    luau: str
    gw: Any = None
    sw: Any = None


class Skipped(NamedTuple):
    """One azalea method no binding was made for. `name` is the Rust fn name, so nothing has to cut it out of `sig` again."""
    origin: str
    sig: str
    reason: str
    name: str
    code: str = ""   # one of CODES (slice 13); "" only for a Skipped built by hand
    hint: str = ""   # the bindgen.toml section that would fix it, when one applies


def properties_on(cname: str) -> bool:
    pr = PROPERTIES
    return pr["mode"] == "auto" or (pr["mode"] == "allow-list" and cname in pr["classes"])


def _prop_value_ok(w) -> bool:
    return w.ret is None or (w.ret.is_result and w.ret.rust == "()")


def pair_properties(owner: str, wrappers: list, pr: dict) -> list:
    """Getter = `&self` method, no parameters, a value result (a `Result` only with result_getters), not async, name not `take` / `take_*` / `set_*`.
    Setter = `set_<x>`, one parameter whose Rust and Luau type equal the getter's result, result `()` or `Result<(), E>`, not async, `&self` or
    `&mut self`. A name clash with another remaining wrapper, a keyword, a denied `Class.name` or a getter whose Luau name is already a field = no
    pair (both stay methods). A getter without a setter is a read-only field only with readonly_getters."""
    by = {w.name: w for w in wrappers}
    out: list = []
    used: set = set()
    for gname in sorted(by):
        g = by[gname]
        if g.kind != "method" or g.is_async or g.wparams or g.ret is None:
            continue
        if g.ret.is_result and not pr["result_getters"]:
            continue
        if gname == "take" or gname.startswith("take_") or gname.startswith("set_"):
            continue
        base = gname
        for pre in pr["getter_prefixes"]:
            if pre and gname.startswith(pre) and len(gname) > len(pre):
                base = gname[len(pre):]
                break
        if base in RUST_KEYWORDS or base in LUAU_KEYWORDS or base.startswith("__") or f"{owner}.{base}" in pr["deny"]:
            continue
        if base != gname and base in by:
            continue   # the prefix-stripped name is another method
        if base in used:
            continue
        s = by.get(f"set_{base}")
        pair_set = None
        if s is not None and s.kind in ("method", "mut") and not s.is_async and len(s.wparams) == 1 and _prop_value_ok(s):
            sti = s.wparams[0][1]
            gti = g.ret
            # the getter's result type vs the setter's parameter type: Luau text and Rust text must agree
            if sti.luau == gti.luau and sti.rust == gti.rust:
                pair_set = s
        if pair_set is None and not pr["readonly_getters"]:
            continue
        out.append(PropertyPair(owner, base, gname, pair_set.name if pair_set else None, g.ret.luau, g, pair_set))
        used.add(base)
    return out


@dataclass
class ClassGen:
    """Everything collected for one target type (see CLASSES)."""
    name: str
    cfg: dict
    wrappers: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    touched: set = field(default_factory=set)
    statics: list = field(default_factory=list)   # Wrapper(kind="static"): `Class.fn(..)` (slice 23)
    props: list = field(default_factory=list)     # PropertyPair: getter/setter pairs bound as Luau fields (slice 26)

    @property
    def rust(self) -> str:
        return self.cfg["rust"]

    @property
    def luau(self) -> str:
        return self.cfg["luau"]

    @property
    def emit(self) -> bool:
        return self.cfg["emit"]


def bare(n: str) -> str:
    """`r#type` -> `type`: the Luau side never sees raw-identifier syntax (the Rust call sites keep it)."""
    return n[2:] if n.startswith("r#") else n


def safe_name(n: str) -> str:
    n = bare(n)
    return n + "_" if n in RUST_KEYWORDS or n in LUAU_KEYWORDS else n


RAW_NEVER = {"self", "Self", "super", "crate"}


def rust_ident(n: str) -> str:
    """A field/variant name as Rust source: `type` -> `r#type` (the rustdoc JSON has no `r#`)."""
    return f"r#{n}" if n in RUST_KEYWORDS and n not in RAW_NEVER else n


def luau_field(f: str) -> str:
    """A property name inside a Luau table type."""
    return f'["{f}"]' if f in LUAU_HARD_KEYWORDS else f


class Generator:
    def __init__(self, ix: Any):
        self.ix = ix
        self.m = Mapper(ix)
        self.classes: dict[str, ClassGen] = {n: ClassGen(n, c) for n, c in CLASSES.items()}
        self.goal_notes: list[str] = []
        self.goal_rows: list[tuple] = []
        self._goals: Optional[list] = None
        self._goals_touched: set = set()
        self.packets_in: list = []
        self.packets_out: list = []
        self.ecs: list = []
        self.has_events = any(c["events"] for c in CLASSES.values())
        self.events: list = []
        self.event_notes: list = []
        self.event_rows: list[tuple] = []

    @property
    def wrappers(self) -> list:
        return self.classes["Client"].wrappers if "Client" in self.classes else []

    @property
    def skipped(self) -> list:
        return self.classes["Client"].skipped if "Client" in self.classes else []

    def event_note(self, variant: str, why: str) -> None:
        self.event_notes.append(f"`{variant}`: {why}")
        self.event_rows.append((variant, why))

    def goals(self) -> list:
        if self._goals is not None:
            self.m.touched |= self._goals_touched
            return self._goals
        before = self.m.touched
        self.m.touched = set()
        out = []
        suffixes: dict[str, str] = {}
        for gname in sorted(self.ix.goal_impls):
            d = self.ix.unique_def(gname)
            if not d or d.kind != "struct":
                continue
            try:
                if d.generics:
                    raise Skip("generic", code="generic")
                if not d.fields or any(not f[2] for f in d.fields):
                    raise Skip("private fields", code="private_fields")
                path = self.m.path_for(gname)
                params = []
                for i, (fname, ft, _) in enumerate(d.fields):
                    ti = self.m.map_ty(ft, "param")
                    if not ti.from_lua:
                        raise Skip(f"field `{fname}` is not convertible", code="not_convertible")
                    if d.tuple_style:
                        pname = GOAL_PARAM_NAMES.get(ft[1] if ft[0] == "path" else "", f"v{i}")
                    else:
                        pname = fname
                        if not ASCII_IDENT.fullmatch(bare(pname)):
                            raise Skip(f"field `{fname}`: not a plain ASCII name", code="name_invalid")
                    params.append((fname, safe_name(pname), ti))
                suffix = snake(gname[:-4] if gname.endswith("Goal") else gname)
                if suffix in suffixes:
                    raise Skip(f"its method suffix `{suffix}` is already taken by `{suffixes[suffix]}`", code="name_clash")
                suffixes[suffix] = gname
                out.append(dict(name=gname, path=path, tuple_style=d.tuple_style, params=params))
            except Skip as e:
                extra = f"; {HANDWRITTEN_GOALS[gname]}" if gname in HANDWRITTEN_GOALS else ""
                self.goal_notes.append(f"`{gname}`: {e.reason}{extra}")
                self.goal_rows.append((gname, f"{e.reason}{extra}"))
        self._goals = out
        self._goals_touched = self.m.touched
        self.m.touched = before | self._goals_touched
        return out

    def build(self):
        """Build wrappers for every class in CLASSES. Each class tracks the mapper keys it touched in
        its own set; only classes with `emit` feed the shared set that decides which type
        conversions get generated, so collecting a class never changes another class's output."""
        shared = self.m.touched
        built: set = set()
        while True:
            for n, row in CLASSES.items():   # slice 22: a handle row the Mapper registered while building another class
                if n not in self.classes:
                    self.classes[n] = ClassGen(n, row)
            todo = [cg for n, cg in self.classes.items() if n not in built]
            if not todo:
                break
            for cg in todo:
                built.add(cg.name)
                self.m.touched = set()
                self.build_class(cg)
                cg.touched = self.m.touched
                if cg.emit:
                    shared |= cg.touched
        self.m.touched = shared

    def build_class(self, cg: ClassGen):
        seen = set()
        seen_static = set()
        self.m.cur_class = cg.name
        renames = cg.cfg["renames"]
        reserved = set(getattr(self, "reserved", {}).get(cg.name, ()))
        if cg.cfg["events"]:
            reserved |= {"on", "wait_for"}
        for m in sorted((m for m in self.ix.methods if m.target == cg.name), key=lambda m: (m.file, m.name)):
            snap = set(self.m.touched)
            try:
                ws = self.build_method(m)
                for w in ws:
                    w.name = renames.get(w.name, w.name)
                for w in ws:
                    if w.name in reserved:
                        raise Skip(f"`{w.name}` is hand-written (extra_*.rs) or generated elsewhere", code="excluded")
                    if w.name.startswith("__"):
                        raise Skip("metamethod-like name", code="name_invalid")
                names = [w.name for w in ws]
                if len(set(names)) != len(names):
                    raise Skip("its expansions share a Luau method name", code="name_clash")
                is_static = bool(ws) and ws[0].kind == "static"
                pool = seen_static if is_static else seen   # statics live in their own table: a method of the same name does not clash
                for w in ws:
                    if w.name in pool:
                        raise Skip(f"duplicate Luau {'static' if is_static else 'method'} name `{w.name}`", code="name_clash")
                for w in ws:
                    pool.add(w.name)
                (cg.statics if is_static else cg.wrappers).extend(ws)
            except Skip as e:
                self.m.touched = snap
                cg.skipped.append(Skipped(f"{m.owner} ({m.file})", m.sig, e.reason, m.name, e.code, e.hint))
        if properties_on(cg.name) and not cg.cfg.get("free"):
            cg.props = self.pair_properties(cg)
            gone = {id(w) for p in cg.props for w in (p.gw, p.sw) if w is not None}
            cg.wrappers = [w for w in cg.wrappers if id(w) not in gone]

    def pair_properties(self, cg: ClassGen) -> list:
        """Slice 26 (spec@s7, rules G1-G3 / R1-R2): pair the BOUND wrappers of a class, so both halves are bindable by construction (G3). Pure:
        reads cg.wrappers and PROPERTIES only. Deviation from spec@s7: it takes Wrappers, not RdMethods (a skipped half cannot pair)."""
        return pair_properties(cg.name, cg.wrappers, PROPERTIES)

    def receiver_kind(self, m: Method) -> str:
        """Slice 23: "static" for an associated fn without a receiver, "mut" for `&mut self`, when `[statics]` allows it; else the old receiver Skip
        (text unchanged, so `mode = "off"` leaves every report byte-equal)."""
        old = Skip("no `&self` receiver" if m.receiver is None else f"`{m.receiver}` receiver", code="receiver")
        st = STATICS
        if st["mode"] == "off" or (st["mode"] == "allow-list" and m.target not in st["classes"]):
            raise old
        if f"{m.target}.{m.name}" in st["deny"]:
            raise Skip(f"`{m.target}.{m.name}` denied in [statics]", code="excluded")
        if m.receiver is None:
            if m.is_async:
                raise Skip("async associated fn (a static is a plain function)", code="receiver")
            return "static"
        if m.receiver == "&mut self" and st["mut_self"]:
            if m.target == "Client" or CLASSES[m.target].get("events"):
                raise Skip("`&mut self` on the shared bot object (handlers and the connection hold it)", code="receiver")
            return "mut"
        raise old

    def build_method(self, m: Method) -> list[Wrapper]:
        self.m.self_ty = m.target if STATICS["mode"] != "off" and m.target in CLASSES and not CLASSES[m.target].get("free") else None
        try:
            return self._build_method(m)
        finally:
            self.m.self_ty = None

    def mono_sets(self, m: Method) -> list:
        """The substitutions to bind a generic method with: [{param: type text}] from `[monomorphise] rows` (config errors name the key), else []."""
        item = f"{m.target}.{m.name}"
        declared = [n for n, _b in (m.type_params or [])]
        out = []
        for it, subs, w in MONOMORPHISE["rows"]:
            if it != item:
                continue
            for k in subs:
                if k not in declared:
                    raise ConfigError(f"key `{w}.{k}`: `{k}` is not a type parameter of `{item}` (declared: {', '.join(declared) or 'none'})")
            for k in declared:
                if k not in subs:
                    raise ConfigError(f"key `{w}`: no type for `{k}` of `{item}`")
            out.append(subs)
        return out

    def auto_sets(self, m: Method) -> list:
        """Auto mode: one type parameter with one trait bound that the signature mentions -> a candidate per implementor of the trait (slice 12),
        in source order; the caller keeps one per distinct result and stops at `max`."""
        tp = m.type_params or []
        if len(tp) != 1 or len(tp[0][1]) != 1:
            return []
        name, (trait,) = tp[0][0], tp[0][1]
        if not (any(mentions_param(t, name) for _n, t in m.params) or (m.ret and mentions_param(m.ret, name))):
            return []
        out = []
        for n in self.ix.implementors(trait):
            if self.m.classify(("path", n, []), "ret").kind in ("unsupported", "opaque"):
                continue
            out.append({name: n})
        return out

    def build_generic(self, m: Method, kind: str) -> list[Wrapper]:
        """Slice 24: a generic method is bound once per substitution: `fn_<type>` calling `fn::<Type>`. Without `[monomorphise]` rows (and
        auto off) it stays the old coded skip."""
        old = Skip("generic method", code="generic")
        free = CLASSES[m.target].get("free", False)
        if free or not m.type_params or (not MONOMORPHISE["rows"] and not MONOMORPHISE["auto"]):
            raise old
        sets = self.mono_sets(m)
        auto = not sets
        if auto:
            if not MONOMORPHISE["auto"]:
                raise old
            sets = self.auto_sets(m)
        params = [n for n, _b in m.type_params]
        out, seen, last = [], set(), None
        for subs in sets:
            if len(out) >= MONOMORPHISE["max"] and auto:
                break
            try:
                nodes = {n: sub_node(subs[n]) for n in params}
                spell = lambda nd: nd[1] if nd[0] == "prim" else self.m.path_for(nd[1])
                tf = turbofish(m.name, params, nodes, spell)
                m2 = dataclasses.replace(
                    m, generics=False, type_params=None,
                    params=[(pn, substitute(pt, nodes)) for pn, pt in m.params],
                    ret=substitute(m.ret, nodes) if m.ret else None)
                ws = self._build_method(m2)
                for w in ws:
                    w.name = mangle(w.name, params, subs)
                    w.origin_name = tf
                key = tuple((w.ret.luau if w.ret else "()") for w in ws)
                if auto and key in seen:
                    continue
                seen.add(key)
                out += ws
            except Skip as e:
                if not auto:
                    raise
                last = e
        if not out:
            raise last or old
        return out

    def _build_method(self, m: Method) -> list[Wrapper]:
        if not m.is_pub:
            raise Skip("not public", code="excluded")
        if m.name in DENY_METHODS:
            raise Skip("denied in config", code="excluded")
        if m.name in CLASSES[m.target]["deny"]:
            raise Skip(f"denied: {CLASSES[m.target]['deny'][m.name]}", code="excluded")
        free = CLASSES[m.target].get("free", False)
        if free and (m.receiver is not None or m.is_async):
            raise Skip("async free fn" if m.is_async else "free fn with a receiver", code="receiver")
        kind = "method"
        if not free and m.receiver != "&self":
            kind = self.receiver_kind(m)
        if m.generics:
            return self.build_generic(m, kind)
        bn = bare(m.name)
        if bn in RUST_KEYWORDS or bn in LUAU_KEYWORDS:
            raise Skip("name is a keyword", code="name_invalid")
        if not ASCII_IDENT.fullmatch(bn):
            raise Skip("name is not a plain ASCII identifier", code="name_invalid")
        if m.ret and m.ret[0] == "impl":
            raise Skip(f"returns `{m.ret[1]}`", code="impl_trait")

        ret = None
        if m.ret is not None and m.ret != ("unit",):
            ret = self.m.map_ty(m.ret, "ret")
            if not ret.to_lua:
                raise Skip("return type not convertible", code="not_convertible")

        base_params: list = []
        for pname, ptype in m.params:
            if pname is None:
                raise Skip("destructured parameter", code="shape_unsupported")
            if not ASCII_IDENT.fullmatch(bare(pname)):
                raise Skip(f"parameter `{pname}`: not a plain ASCII name", code="name_invalid")
            if ptype[0] == "impl" and re.fullmatch(r"impl Goal( \+ '\w+)?", ptype[1]):
                base_params.append(("GOAL", pname))
                continue
            ti = self.m.map_ty(ptype, "param")
            if not ti.from_lua:
                raise Skip(f"parameter `{pname}`: not convertible", code="not_convertible")
            base_params.append((safe_name(pname), ti))

        goal_idx = [i for i, p in enumerate(base_params) if p[0] == "GOAL"]
        if len(goal_idx) > 1:
            raise Skip("more than one goal parameter", code="shape_unsupported")
        origin = m.owner
        if kind == "static":   # a static has no `self`: a conversion that reads the connected client (`self.client`) cannot be written
            texts = ([ret.to_lua("x")] if ret and ret.to_lua else []) + [ti.from_lua("x") for n, ti in base_params if n != "GOAL" and ti.from_lua]
            if ret is not None and ret.needs_client or any(getattr(ti, "needs_client", False) for n, ti in base_params if n != "GOAL") \
                    or any("self." in t for t in texts):
                raise Skip("associated fn whose conversion needs the connected client (no `self` to read it from)", code="ecs_entity")
        if kind != "method" and goal_idx:
            raise Skip("`impl Goal` parameter on a static / `&mut self` fn", code="impl_trait")
        if not goal_idx:
            w = self.finish(bn, m.doc, base_params, None, ret, m, origin)
            w.kind = kind
            return [w]
        gs = self.goals()
        if not gs:
            raise Skip("`impl Goal` parameter and no usable Goal types were found", code="impl_trait")
        out = []
        for g in gs:
            suffix = snake(g["name"][:-4] if g["name"].endswith("Goal") else g["name"])
            wp = []
            for p in base_params:
                if p[0] == "GOAL":
                    wp += [(pn, ti) for _f, pn, ti in g["params"]]
                else:
                    wp.append(p)
            out.append(self.finish(f"{bn}_{suffix}", m.doc, wp, g, ret, m, origin, goal_at=goal_idx[0]))
        return out

    def finish(self, name, doc, wparams, goal, ret, m: Method, origin, goal_at=None) -> Wrapper:
        args: list[str] = []
        if goal is None:
            args = [ti.from_lua(n) for n, ti in wparams]
        else:
            pos = 0
            gcount = len(goal["params"])
            before = wparams[:goal_at]
            gparams = wparams[goal_at:goal_at + gcount]
            after = wparams[goal_at + gcount:]
            args = [ti.from_lua(n) for n, ti in before]
            conv = [(rust_ident(f), ti.from_lua(pn)) for (f, pn, ti) in goal["params"]]
            if goal["tuple_style"]:
                args.append(f"{goal['path']}({', '.join(e for _f, e in conv)})")
            else:
                args.append(f"{goal['path']} {{ {', '.join(f'{f}: {e}' for f, e in conv)} }}")
            args += [ti.from_lua(n) for n, ti in after]
        return Wrapper(name, doc, wparams, args, ret, m.owner, origin, m.name, m.is_async)

    def build_packets_in(self, root: Path):
        """Every `ClientboundGamePacket` variant -> `{ kind = "<snake variant>", <fields> }` (gen_packets_in.rs, real azalea only).
        The variants come from the `declare_state_packets!` list in azalea-protocol/src/packets/game/mod.rs (a macro, so the front
        ends see no enum); the payload struct of variant `Foo` is `ClientboundFoo` in `packets::game`. A field the mapper cannot
        convert (NBT, registry holders, ECS entities, ...) is left out and listed in the report; the packet itself is never dropped."""
        self.packets_in = []
        mod_rs = Path(root) / "azalea-protocol" / "src" / "packets" / "game" / "mod.rs"
        if not mod_rs.is_file():
            return
        text = re.sub(r"//[^\n]*", "", mod_rs.read_text())
        mt = re.search(r"Clientbound\s*=>\s*\[(.*?)\]", text, re.S)
        if not mt:
            return
        names = [n.strip() for n in mt.group(1).split(",") if n.strip()]
        camel = lambda n: "".join(p[:1].upper() + p[1:] for p in n.split("_"))
        taken = self.m.taken_luau_names()
        saved_class, self.m.cur_class = self.m.cur_class, "EntityRef"
        for n in names:
            variant = camel(n)
            sname = "Clientbound" + variant
            cands = [d for d in self.ix.defs.get(sname, []) if d.is_pub and d.kind == "struct" and "game" in d.modpath]
            d = cands[0] if len(cands) == 1 else self.ix.unique_def(sname)
            entry = dict(name=n, variant=variant, struct=sname, luau=f"Packet{variant}", fields=[], skipped=[], nodef=None)
            if f"Packet{variant}" in taken:
                entry["nodef"] = f"`Packet{variant}` is already a Luau type"
            elif d is None or d.kind != "struct":
                entry["nodef"] = "no unique `packets::game` struct found"
            elif d.generics:
                entry["nodef"] = "generic struct"
            else:
                seen = set()
                for i, (fname, ft, is_pub) in enumerate(d.fields):
                    lname = f"_{i + 1}" if d.tuple_style else bare(fname)
                    try:
                        if not is_pub:
                            raise Skip("private field", code="private_fields")
                        if not ASCII_IDENT.fullmatch(lname):
                            raise Skip("not a plain ASCII field name", code="name_invalid")
                        if lname == "kind" or lname in seen:
                            raise Skip("name clashes with `kind` or another field", code="name_clash")
                        self.m.client_flags.append(False)
                        try:
                            fi = self.m.map_ty(ft, "ret")
                            holds_entity = self.m.client_flags[-1]
                        finally:
                            self.m.client_flags.pop()
                        if holds_entity:
                            raise Skip("holds an ECS `Entity` (needs the client)", code="ecs_entity")
                        if fi.is_result or not fi.to_lua:
                            raise Skip("not convertible", code="not_convertible")
                        access = f"v.{i}" if d.tuple_style else f"v.{rust_ident(fname)}"
                        entry["fields"].append((lname, fi.luau, fi.to_lua(access)))
                        seen.add(lname)
                    except Skip as e:
                        entry["skipped"].append((lname, e.reason))
            self.packets_in.append(entry)
        self.m.cur_class = saved_class

    def build_ecs(self):
        """Every public, non-generic azalea type that derives `Component` or `Resource` -> one table entry (gen_ecs.rs, real azalea only).
        Entry = Luau-visible name (the Rust type name), kind, Rust path, Luau type and a `v -> Luau` conversion. A type whose value the
        mapper cannot turn into Luau (private fields, ECS handles, ...) stays in the table with `readable` False and a reason: scripts get
        a clear error instead of a silent nil. Marker types (no fields) read as `true`; a one-field public tuple struct reads as its field.
        A name defined twice is left out (ambiguous). Only `Clone` types can be read (the read clones out of the ECS lock)."""
        self.ecs = []
        derive = re.compile(r"derive\(.*\b(Component|Resource)\b")
        seen = {}
        for name, defs in sorted(self.ix.defs.items()):
            for d in defs:
                if not d.is_pub or d.generics:
                    continue
                kinds = {m.group(1) for a in d.attrs for m in [derive.search(a)] if m}
                if "Resource" in kinds:
                    kinds = {"Resource"}
                if not kinds:
                    continue
                seen.setdefault(name, []).append((d, kinds))
        saved_class, self.m.cur_class = self.m.cur_class, "EntityRef"
        for name, found in sorted(seen.items()):
            d, kinds = found[0]
            entry = dict(name=name, kind="resource" if kinds == {"Resource"} else "component", path=None, luau=None, to_lua=None,
                         marker=False, reason=None)
            try:
                if len(found) > 1:
                    raise Skip("defined more than once in the azalea source", code="no_definition")
                if not ASCII_IDENT.fullmatch(name):
                    raise Skip("not a plain ASCII name", code="name_invalid")
                try:
                    entry["path"] = self.m.path_for(name)
                except Skip:
                    p = self.ix.to_azalea(d.crate, d.modpath, d.name) if d.is_pub and self.ix.chain_public(d.crate, d.modpath) else None
                    if not p:
                        raise
                    entry["path"] = self.m.paths[name] = p
                if d.kind == "struct" and not d.fields:
                    entry.update(marker=True, luau="boolean", to_lua="true")
                    self.ecs.append(entry)
                    continue
                if self.ix.is_clone(name) is not True:
                    raise Skip("not `Clone` (cannot be copied out of the ECS)", code="trait_missing")
                if False:
                    pass
                elif d.kind == "struct" and d.tuple_style and len(d.fields) == 1:
                    _fname, ft, is_pub = d.fields[0]
                    if not is_pub:
                        raise Skip("private field", code="private_fields")
                    self.m.client_flags.append(False)
                    try:
                        fi = self.m.map_ty(ft, "ret")
                        holds_entity = self.m.client_flags[-1]
                    finally:
                        self.m.client_flags.pop()
                    if holds_entity:
                        raise Skip("holds an ECS `Entity` (needs the client)", code="ecs_entity")
                    if fi.is_result or not fi.to_lua:
                        raise Skip("field not convertible", code="not_convertible")
                    entry.update(luau=fi.luau, to_lua=fi.to_lua("v.0"))
                else:
                    self.m.client_flags.append(False)
                    try:
                        fi = self.m.build_named(name)
                        holds_entity = self.m.client_flags[-1]
                    finally:
                        self.m.client_flags.pop()
                    if fi.is_result or not fi.to_lua or fi.needs_client or holds_entity:
                        raise Skip("not convertible without the client", code="not_convertible")
                    entry.update(luau=fi.luau, to_lua=fi.to_lua("v"))
                    self.m.touch(("type", name))
            except Skip as e:
                entry.update(reason=e.reason, luau=None, to_lua=None)
            self.ecs.append(entry)
        self.m.cur_class = saved_class

    def build_packets_out(self, root: Path):
        """Every `ServerboundGamePacket` variant -> a constructor from a Luau table of its fields (gen_packets_out.rs, real azalea only).
        Same sources as `build_packets_in` (the `Serverbound => [..]` list; struct `Serverbound<Camel>` in `packets::game`). Unlike the
        clientbound side a field cannot be left out when building a struct: a packet with one unconvertible field gets no constructor
        (`ok` False, reasons listed in the report). Class values (`Entity`, ...) are not accepted as packet fields."""
        self.packets_out = []
        mod_rs = Path(root) / "azalea-protocol" / "src" / "packets" / "game" / "mod.rs"
        if not mod_rs.is_file():
            return
        text = re.sub(r"//[^\n]*", "", mod_rs.read_text())
        mt = re.search(r"Serverbound\s*=>\s*\[(.*?)\]", text, re.S)
        if not mt:
            return
        names = [n.strip() for n in mt.group(1).split(",") if n.strip()]
        camel = lambda n: "".join(p[:1].upper() + p[1:] for p in n.split("_"))
        taken = self.m.taken_luau_names()
        saved_class, self.m.cur_class = self.m.cur_class, "EntityRef"
        for n in names:
            variant = camel(n)
            sname = "Serverbound" + variant
            cands = [d for d in self.ix.defs.get(sname, []) if d.is_pub and d.kind == "struct" and "game" in d.modpath]
            d = cands[0] if len(cands) == 1 else self.ix.unique_def(sname)
            entry = dict(name=n, variant=variant, struct=sname, luau=f"OutPacket{variant}", fields=[], skipped=[], ok=False, why=None,
                         tuple_style=False)
            snap = set(self.m.touched)
            if f"OutPacket{variant}" in taken:
                entry["why"] = f"`OutPacket{variant}` is already a Luau type"
            elif d is None or d.kind != "struct":
                entry["why"] = "no unique `packets::game` struct found"
            elif d.generics:
                entry["why"] = "generic struct"
            else:
                entry["tuple_style"] = d.tuple_style
                seen = set()
                for i, (fname, ft, is_pub) in enumerate(d.fields):
                    lname = f"_{i + 1}" if d.tuple_style else bare(fname)
                    try:
                        if not is_pub:
                            raise Skip("private field", code="private_fields")
                        if not ASCII_IDENT.fullmatch(lname):
                            raise Skip("not a plain ASCII field name", code="name_invalid")
                        if lname in seen:
                            raise Skip("name clashes with another field", code="name_clash")
                        fi = self.m.map_ty(ft, "param")
                        if not fi.from_lua or fi.is_class:
                            raise Skip("not convertible from Luau", code="not_convertible")
                        entry["fields"].append((lname, fi.luau, fi.rust, fi.from_lua, None if d.tuple_style else rust_ident(fname)))
                        seen.add(lname)
                    except Skip as e:
                        entry["skipped"].append((lname, e.reason))
                if entry["skipped"]:
                    entry["why"] = "; ".join(f"`{f}`: {r}" for f, r in entry["skipped"])
                else:
                    entry["ok"] = True
            if not entry["ok"]:
                entry["fields"] = []
                self.m.touched = snap
            self.packets_out.append(entry)
        self.m.cur_class = saved_class

    def build_events(self):
        cands = [x for x in self.ix.defs.get("Event", []) if x.is_pub and x.kind == "enum"]
        if len(cands) > 1:
            cands = [x for x in cands if x.crate == "azalea"] or cands
        if len(cands) > 1:
            at_root = [x for x in cands if x.modpath == () or any(
                c == "azalea" and mp == () and ex == "Event" and full.split("::")[:-1][-1:] == list(x.modpath[-1:])
                for c, mp, ex, full in self.ix.reexports)]
            cands = at_root if len(at_root) == 1 else cands
        if len(cands) > 1:
            sys.exit("error: `enum Event` is defined more than once: " + ", ".join(
                "::".join([x.crate, *x.modpath]) for x in cands))
        d = cands[0] if cands else None
        if not d:
            sys.exit("error: could not find `enum Event` in the azalea source")
        self.event_def = d
        self.events = []
        lnames: dict[str, str] = {}
        self.event_notes = []
        self.event_rows = []
        self.m.cur_class = None
        for v in d.variants:
            if v["name"] in HIDDEN_EVENTS:
                self.event_note(v["name"], f"{HIDDEN_EVENTS[v['name']]}, not exposed")
                continue
            if any(a.startswith("#[cfg(") for a in v["attrs"]):
                self.event_note(v["name"], f"cfg-gated ({', '.join(a for a in v['attrs'] if 'cfg' in a)}), not exposed")
                continue
            lname = snake(bare(v["name"]))
            if lname in EXTRA_EVENT_PAYLOADS:
                self.event_note(v["name"], f"handled by the hand-written extra event `{lname}`, not exposed")
                continue
            if lname in lnames:
                self.event_note(v["name"], f"Luau name `{lname}` is already taken by `{lnames[lname]}`, not exposed")
                continue
            lnames[lname] = v["name"]
            payload = None
            if v["kind"] == "tuple" and len(v["types"]) == 1:
                snap = set(self.m.touched)
                try:
                    ti = self.m.map_ty(v["types"][0], "ret")
                    if ti.is_result or not ti.to_lua:
                        raise Skip("not convertible", code="not_convertible")
                    payload = ti
                except Skip as e:
                    self.m.touched = snap
                    self.event_note(v["name"], f"payload `{type_text(v['types'][0])}` not exposed ({e.reason}); handlers get no arguments")
            elif v["kind"] != "unit":
                self.event_note(v["name"], f"{v['kind']} variant payload not exposed; handlers get no arguments")
            self.events.append(dict(
                variant=v["name"], lname=lname, payload=payload, doc=v["doc"],
                arg=EVENT_ARG_NAMES.get(v["name"], "value"),
            ))
        for n in EXTRA_EVENTS:
            if n in lnames:
                sys.exit(f"error: {CONFIG_PATH.name}: extra_events.{n} clashes with `Event::{lnames[n]}`")



EXTRA_RE = re.compile(r"^\s*//\s*luau\[(\w+)\]:\s?(.*)$")
EXTRA_FN_RE = re.compile(r"(?:function (\w+)|(\w+): \()")


def extra_fn_name(text: str):
    m = EXTRA_FN_RE.match(text)
    return (m.group(1) or m.group(2)) if m else None


IMPL_BLOCK_RE = re.compile(r"^impl\s+(\w+)\s*\{\n(.*?)^\}", re.S | re.M)
IMPL_FN_RE = re.compile(r"^\s+(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)", re.M)


def impl_top_fns(body: str) -> set:
    """Names of the `fn`s declared at the top brace depth of an impl body: a nested `fn` inside a method body, and
    anything inside a string, char literal or comment, is not a method. Simple scan (no macro/raw-string edge cases
    beyond `r"..."` / `r#"..."#`)."""
    n, i, depth = len(body), 0, 0
    masked = []
    depths = []

    def put(ch: str):
        masked.append(ch)
        depths.append(depth)

    while i < n:
        c = body[i]
        if body.startswith("//", i):
            j = body.find("\n", i)
            j = n if j < 0 else j
            for _ in range(i, j):
                put(" ")
            i = j
        elif body.startswith("/*", i):
            j = body.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                put("\n" if body[k] == "\n" else " ")
            i = j
        elif c == "r" and (m := re.match(r'r(#*)"', body[i:i + 40])) and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_")):
            end = '"' + m.group(1)
            j = body.find(end, i + m.end())
            j = n if j < 0 else j + len(end)
            for k in range(i, j):
                put("\n" if body[k] == "\n" else " ")
            i = j
        elif c == '"':
            j = i + 1
            while j < n and body[j] != '"':
                j += 2 if body[j] == "\\" else 1
            j = min(j + 1, n)
            for k in range(i, j):
                put("\n" if body[k] == "\n" else " ")
            i = j
        elif c == "'" and (m := re.match(r"'(?:\\.[^']*|[^\\'])'", body[i:i + 12])):
            for _ in range(m.end()):
                put(" ")
            i += m.end()
        else:
            if c == "}":
                depth -= 1
            put(c)
            if c == "{":
                depth += 1
            i += 1
    text = "".join(masked)
    return {bare(fm.group(1)) for fm in IMPL_FN_RE.finditer(text) if depths[fm.start(1)] == 0}


def extras_reserved(rust_out: Path) -> dict:
    """-> {azalea class: names of methods already hand-written in extra_*.rs} (Rust `impl <Lua type>` blocks and
    `// luau[Class]: function name(` lines), so a generated method never defines the same name twice."""
    out: dict = defaultdict(set)
    by_rust = {c["rust"]: n for n, c in CLASSES.items()}
    by_luau = {c["luau"]: n for n, c in CLASSES.items()}
    for f in sorted([*rust_out.glob("extra_*.rs"), *rust_out.glob("real_*.rs")]):
        src = f.read_text(errors="replace")
        for im in IMPL_BLOCK_RE.finditer(src):
            cls = by_rust.get(im.group(1))
            if cls:
                out[cls] |= impl_top_fns(im.group(2))
        for ln in src.splitlines():
            m = EXTRA_RE.match(ln)
            fn = m and extra_fn_name(m.group(2))
            if fn and m.group(1) in by_luau:
                out[by_luau[m.group(1)]].add(fn)
    return out


def scan_extras(rust_out: Path) -> dict:
    """-> {luau class: [(file, line)]} for every `// luau[Class]: ...` line in extra_*.rs."""
    out: dict = defaultdict(list)
    for f in sorted([*rust_out.glob("extra_*.rs"), *rust_out.glob("real_*.rs")]):
        for ln in f.read_text().splitlines():
            m = EXTRA_RE.match(ln)
            if m:
                out[m.group(1)].append((f.name, m.group(2).rstrip()))
    return out



def rust_sig_ret(w: Wrapper):
    """-> (attr, return clause, body-wrapping function)"""
    fallible_params = any(ti.fallible_from for _n, ti in w.wparams)
    inner = w.ret.rust if w.ret else "()"
    if w.ret and w.ret.is_result:
        return "", f" -> mlua::Result<{inner}>", lambda e: e
    if fallible_params:
        return "", f" -> mlua::Result<{inner}>", lambda e: f"Ok({e})"
    return "#[lua(infallible)]", ("" if not w.ret else f" -> {inner}"), lambda e: e


def emit_client(gen: Generator, src_label: str) -> str:
    """gen_client.rs: one `#[derive(UserData)]` struct per emitted class (Client first)."""
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, clippy::all)]", ""]
    if gen.has_events:
        L += [
            "use std::sync::Arc;",
            "",
            "use azalea::prelude::*;",
            "use mlua::UserData;",
            "",
            "use super::convert::*;",
            "use super::gen_events::EVENT_NAMES;",
            "use super::gen_types::*;",
            "use super::Handlers;",
        ]
    else:
        L += ["use mlua::UserData;", "", "use super::convert::*;", "use super::gen_types::*;"]
    for tr in sorted(gen.ix.client_traits):
        p = gen.ix.resolve_path(tr)
        if p:
            L.append(f"use {p} as _;")
    for cg in gen.classes.values():
        if cg.emit and not cg.cfg.get("free"):
            L += [""] + emit_class(gen, cg)
    free = [cg for cg in gen.classes.values() if cg.emit and cg.cfg.get("free") and cg.wrappers]
    for cg in free:
        L += [""] + emit_free_table(cg)
    stat = static_classes(gen)
    for cg in stat:
        L += [""] + emit_statics_table(gen, cg)
    if free or stat:
        L += ["", "/// Install every free-function table and every class's associated-function table as a Luau global (`toy.add(..)`, `Handle.new(..)`).",
              "pub fn register_globals(lua: &mlua::Lua) -> mlua::Result<()> {"]
        L += [f"    register_{cg.luau}(lua)?;" for cg in free]
        L += [f"    register_statics_{snake(cg.luau)}(lua)?;" for cg in stat]
        L += ["    Ok(())", "}"]
    return "\n".join(L) + "\n"


def emit_free_table(cg: ClassGen) -> list:
    """`pub fn register_<crate>(lua)`: a Luau table of the crate's free fns, set as the global `<crate>`."""
    L = [f"/// Global table `{cg.luau}`: {cg.cfg['doc']}", f"pub fn register_{cg.luau}(lua: &mlua::Lua) -> mlua::Result<()> {{",
         "    let t = lua.create_table()?;"]
    for w in sorted(cg.wrappers, key=lambda w: w.name):
        attr, _ret_clause, wrap = rust_sig_ret(w)
        names = "".join(f"{n}, " for n, _ in w.wparams)
        types = "".join(f"{ti.rust}, " for _, ti in w.wparams)
        call = f"{w.owner}({', '.join(w.call_args)})"
        expr = w.ret.to_lua(call) if w.ret else call
        body = wrap(expr)
        inner = w.ret.rust if w.ret else "()"
        L += [f'    t.set("{w.name}", lua.create_function(|_, ({names}): ({types})| -> mlua::Result<{inner}> {{',
              f"        {body if not attr else f'Ok({body})'}", "    })?)?;"]
    L += [f'    lua.globals().set("{cg.luau}", t)', "}"]
    return L


def static_classes(gen: Generator) -> list:
    """Emitted classes that have associated fns (slice 23), in class order."""
    return [cg for cg in gen.classes.values() if cg.emit and not cg.cfg.get("free") and cg.statics]


def emit_statics_table(gen: Generator, cg: ClassGen) -> list:
    """`pub fn register_statics_<class>(lua)`: the Luau global `<Class>` = a table of the class's associated fns (`Handle.new(..)`)."""
    path = class_rust_path(gen, cg)
    L = [f"/// Global table `{cg.luau}`: associated functions of `{cg.name}` (called `{cg.luau}.name(..)`, no receiver).",
         f"pub fn register_statics_{snake(cg.luau)}(lua: &mlua::Lua) -> mlua::Result<()> {{",
         "    let t = lua.create_table()?;"]
    for w in sorted(cg.statics, key=lambda w: w.name):
        attr, _ret_clause, wrap = rust_sig_ret(w)
        names = "".join(f"{n}, " for n, _ in w.wparams)
        types = "".join(f"{ti.rust}, " for _, ti in w.wparams)
        call = f"{path}::{w.origin_name}({', '.join(w.call_args)})"
        expr = w.ret.to_lua(call) if w.ret else call
        body = wrap(expr)
        inner = w.ret.rust if w.ret else "()"
        L += [f'    t.set("{w.name}", lua.create_function(|_, ({names}): ({types})| -> mlua::Result<{inner}> {{',
              f"        {body if not attr else f'Ok({body})'}", "    })?)?;"]
    L += [f'    lua.globals().set("{cg.luau}", t)', "}"]
    return L


def class_rust_path(gen: Generator, cg: ClassGen) -> str:
    if cg.name == "Client":
        return "azalea::Client"
    p = gen.ix.resolve_path(cg.name)
    if not p:
        sys.exit(f"error: no public `azalea::` path for class `{cg.name}`")
    return p


def emit_class(gen: Generator, cg: ClassGen) -> list:
    field_ = cg.cfg["field"]
    if cg.name == "Client":
        L = [
            "/// The `bot` global in Luau. Methods are generated from azalea's `Client`.",
            "#[derive(UserData)]",
            "pub struct LuaClient {",
            "    #[lua(skip)]",
            "    pub client: azalea::Client,",
            "    #[lua(skip)]",
            "    pub handlers: Arc<Handlers>,",
            "}",
        ]
    else:
        L = [
            f"/// Luau class `{cg.luau}`. Methods are generated from {'azalea' + chr(39) + 's ' if gen.has_events else ''}`{cg.name}`.",
            "#[derive(UserData)]",
            f"pub struct {cg.rust} {{",
            "    #[lua(skip)]",
            f"    pub {field_}: {class_rust_path(gen, cg)},",
            "}",
        ]
    L += ["", "#[mlua::userdata_impl]", f"impl {cg.rust} {{"]
    if cg.cfg["events"]:
        L += [
            "    /// `bot:on(event, handler)` - register a handler for an `azalea::Event`.",
            "    fn on(&self, event: String, handler: mlua::Function) -> mlua::Result<()> {",
            "        if !EVENT_NAMES.contains(&event.as_str()) {",
            '            return Err(mlua::Error::runtime(format!("unknown event \'{event}\' (known: {})", EVENT_NAMES.join(", "))));',
            "        }",
            "        self.handlers.add(event, handler);",
            "        Ok(())",
            "    }",
            "",
            "    /// `bot:wait_for(event)` - yield until `event` fires next and return its payload.",
            "    async fn wait_for(&self, event: String) -> mlua::Result<mlua::MultiValue> {",
            "        if !EVENT_NAMES.contains(&event.as_str()) {",
            '            return Err(mlua::Error::runtime(format!("unknown event \'{event}\' (known: {})", EVENT_NAMES.join(", "))));',
            "        }",
            "        self.handlers.wait(&event).await",
            "    }",
        ]
    first = not cg.cfg["events"]
    for w, role in sorted([(w, "method") for w in cg.wrappers] + [(p.gw, "getter") for p in cg.props] + [(p.sw, "setter") for p in cg.props if p.sw],
                          key=lambda t: (t[0].name if t[1] == "method" else next(p.name for p in cg.props if t[0] in (p.gw, p.sw)), t[1] == "setter")):
        attr, ret_clause, wrap = rust_sig_ret(w)
        if role != "method":   # slice 26: a field half; the macro attribute is mlua's field form (unverified here, see CONTEXT open)
            attr = f"#[lua({role}, infallible)]" if attr else f"#[lua({role})]"
        params = "".join(f", {n}: {ti.rust}" for n, ti in w.wparams)
        call = f"self.{field_}.{w.origin_name}({', '.join(w.call_args)})" + (".await" if w.is_async else "")
        expr = w.ret.to_lua(call) if w.ret else call
        if not first:
            L.append("")
        first = False
        if attr:
            L.append(f"    {attr}")
        recv = "&mut self" if w.kind == "mut" else "&self"
        L.append(f"    {'async ' if w.is_async else ''}fn {w.name}({recv}{params}){ret_clause} {{")
        L.append(f"        {wrap(expr)}")
        L.append("    }")
    L.append("}")
    return L


def emit_option_struct(name: str, e: dict) -> list:
    """Rust for one option struct: a Luau table with any of the fields (the rest keep `Default`); an unknown key or a wrongly typed value is an error."""
    r, p = e["rust"], e["path"]
    known = ", ".join(f for f, _ in e["fields"])
    L = [
        f"/// `{name}` <- Luau table with any of: {known} (missing keys keep their `Default` value)",
        f"pub struct {r}(pub {p});",
        "",
        f"impl FromLua for {r} {{",
        "    fn from_lua(value: Value, lua: &Lua) -> mlua::Result<Self> {",
        "        let t = match value {",
        "            Value::Table(t) => t,",
        f'            other => return Err(mlua::Error::runtime(format!("{name}: expected a table, got {{}}", other.type_name()))),',
        "        };",
        f"        let mut out = {p}::default();",
        "        for pair in t.pairs::<String, Value>() {",
        "            let (key, v) = pair?;",
        "            match key.as_str() {",
    ]
    for f, fi in e["fields"]:
        L += [
            f'                "{f}" => {{',
            f"                    let v = <{fi.rust} as FromLua>::from_lua(v, lua)",
            f'                        .map_err(|err| mlua::Error::runtime(format!("{name}.{f}: {{err}}")))?;',
            f"                    out.{rust_ident(f)} = {fi.from_lua('v')};",
            "                }",
        ]
    L += [
        f'                other => return Err(mlua::Error::runtime(format!("{name}: unknown field `{{other}}` (known: {known})"))),',
        "            }",
        "        }",
        "        Ok(Self(out))",
        "    }",
        "}",
        "",
    ]
    return L


def emit_value_helpers() -> list:
    """Rust for the table readers of slice 20 (only when a value parameter is used): the key check and the required-field read."""
    return [
        "/// `what` must only have keys from `known` (a Luau table that a script passed for a struct / enum parameter).",
        "fn lua_check_keys(t: &mlua::Table, what: &str, known: &[&str]) -> mlua::Result<()> {",
        "    for pair in t.pairs::<String, Value>() {",
        '        let (key, _) = pair.map_err(|_| mlua::Error::runtime(format!("{what}: keys must be strings")))?;',
        "        if !known.contains(&key.as_str()) {",
        '            return Err(mlua::Error::runtime(format!("{what}: unknown field `{key}` (known: {})", known.join(", "))));',
        "        }",
        "    }",
        "    Ok(())",
        "}",
        "",
        "/// Field `key` of `t`; nil is an error unless the field is optional (`Option<T>` reads nil as `None`).",
        "fn lua_field(t: &mlua::Table, what: &str, key: &str, optional: bool) -> mlua::Result<Value> {",
        "    let v = t.raw_get::<Value>(key)?;",
        "    if !optional && matches!(v, Value::Nil) {",
        '        return Err(mlua::Error::runtime(format!("{what}: missing field `{key}`")));',
        "    }",
        "    Ok(v)",
        "}",
        "",
    ]


def _vp_reads(name: str, fields: list, ind: str) -> list:
    """`let f<i> = <read and convert field i>;` for each (luau name, TypeInfo) of a struct / variant."""
    L = []
    for i, (f, fi) in enumerate(fields):
        opt = "true" if fi.rust.startswith("Option<") else "false"
        L += [
            f"{ind}let f{i} = {{",
            f'{ind}    let v = lua_field(&t, "{name}", "{f}", {opt})?;',
            f"{ind}    let v = <{fi.rust} as FromLua>::from_lua(v, lua)",
            f'{ind}        .map_err(|err| mlua::Error::runtime(format!("{name}.{f}: {{err}}")))?;',
            f"{ind}    {fi.from_lua('v')}",
            f"{ind}}};",
        ]
    return L


def emit_value_param(name: str, e: dict) -> list:
    """Rust for one value parameter (slice 20): a struct from a table with every key required (`Option` fields may be nil) or an enum from
    `{ kind = .., ... }`; an unknown key, a missing key, an unknown kind or a wrongly typed value is an error naming the type and the field."""
    r, p = e["rust"], e["path"]
    L = []
    head = [
        f"pub struct {r}(pub {p});",
        "",
        f"impl FromLua for {r} {{",
        "    fn from_lua(value: Value, lua: &Lua) -> mlua::Result<Self> {",
        "        let t = match value {",
        "            Value::Table(t) => t,",
        f'            other => return Err(mlua::Error::runtime(format!("{name}: expected a table, got {{}}", other.type_name()))),',
        "        };",
    ]
    if e["kind"] == "struct":
        fs = [(f, fi) for f, fi, _r in e["fields"]]
        known = ", ".join(f'"{f}"' for f, _ in fs)
        L += [f"/// `{name}` <- Luau table with the keys: " + ", ".join(f + ("?" if fi.rust.startswith("Option<") else "") for f, fi in fs)] + head
        L += [f"        lua_check_keys(&t, \"{name}\", &[{known}])?;"]
        L += _vp_reads(name, fs, "        ")
        L += [f"        Ok(Self({p} {{ " + ", ".join(f"{rid}: f{i}" for i, (_f, _fi, rid) in enumerate(e["fields"])) + " }))", "    }", "}", ""]
        return L
    kinds = ", ".join(v["kind"] for v in e["variants"])
    L += [f"/// `{name}` <- Luau table `{{ kind = {' | '.join(chr(34) + v['kind'] + chr(34) for v in e['variants'])}, ... }}`"] + head
    L += [
        '        let kind = match t.raw_get::<Value>("kind")? {',
        f'            Value::Nil => return Err(mlua::Error::runtime("{name}: missing field `kind`")),',
        "            v => <String as FromLua>::from_lua(v, lua)",
        f'                .map_err(|err| mlua::Error::runtime(format!("{name}.kind: {{err}}")))?,',
        "        };",
        "        Ok(Self(match kind.as_str() {",
    ]
    for v in e["variants"]:
        known = ", ".join(f'"{x}"' for x in ["kind"] + [f for f, _ in v["fields"]])
        L.append(f'            "{v["kind"]}" => {{')
        L.append(f'                lua_check_keys(&t, "{name}", &[{known}])?;')
        L += _vp_reads(name, v["fields"], "                ")
        L += [f"                {v['build']}", "            }"]
    L += [
        f'            other => return Err(mlua::Error::runtime(format!("{name}: invalid kind \'{{other}}\', expected one of: {kinds}"))),',
        "        }))",
        "    }",
        "}",
        "",
    ]
    return L


def luau_option_struct(name: str, e: dict) -> str:
    """The Luau type of an option struct: every field optional."""
    fs = ", ".join(f"{luau_field(f)}: {fi.luau if fi.luau.endswith('?') else fi.luau + '?'}" for f, fi in e["fields"])
    return f"type {name} = {{ {fs} }}"


SERDE_OPTIONS = "mlua::serde::SerializeOptions::new().serialize_none_to_null(false).serialize_unit_to_null(false)"


def emit_callback_helpers(used: bool) -> list:
    """`lua_callback_error` (slice 25), only when a callback parameter was mapped: where a failed Luau callback is reported (see Mapper.callback_ty)."""
    if not used:
        return []
    return [
        "/// A Luau callback that raised (or returned something its Rust type can't be read from, or yielded: mlua can't yield across the Rust frame",
        "/// that called it) cannot raise into the Rust code that called the closure: it is reported here, the closure returns `Default::default()`.",
        "pub(crate) fn lua_callback_error(what: &str, e: &mlua::Error) {",
        '    eprintln!("[luaxite] {what} failed: {e}");',
        "}",
        "",
    ]


def emit_serde_helpers(used: bool) -> list:
    """Rust for `LuaSerde<T>` (slice 21), only when a serde type was bound: a return goes through `to_value_with` (nil for None / unit, as
    tools/bind_serde.py), a parameter through `from_value` (serde's own error text). Needs mlua `serialize` and a `serde` dependency."""
    if not used:
        return []
    return [
        "/// A `serde` value copied to / from Luau with mlua's serde support.",
        "pub struct LuaSerde<T>(pub T);",
        "",
        "impl<T: serde::Serialize> IntoLua for LuaSerde<T> {",
        "    fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
        f"        mlua::LuaSerdeExt::to_value_with(lua, &self.0, {SERDE_OPTIONS})",
        "    }",
        "}",
        "",
        "impl<T: serde::de::DeserializeOwned> FromLua for LuaSerde<T> {",
        "    fn from_lua(value: Value, lua: &Lua) -> mlua::Result<Self> {",
        "        mlua::LuaSerdeExt::from_value::<T>(lua, value).map(LuaSerde)",
        "    }",
        "}",
        "",
    ]


def emit_seq_helpers(used: set) -> list:
    """Rust for the tuple / array wrappers of slice 16, only the ones a binding uses (nothing when no tuple or array was mapped):
    `LuaTuple<(A, B)>` and `LuaArray<T, N>` <-> a Luau array. Reading checks the table: only the keys 1..=N, each element converted by its own `FromLua`."""
    if not used:
        return []
    L = [
        "/// Check that `value` is a table whose only keys are 1..=n (a nil slot is fine: `Option` elements) and hand it back.",
        "fn lua_seq_table(value: Value, n: usize) -> mlua::Result<mlua::Table> {",
        "    let t = match value {",
        "        Value::Table(t) => t,",
        '        other => return Err(mlua::Error::runtime(format!("expected an array of {n} values, got {}", other.type_name()))),',
        "    };",
        "    for pair in t.pairs::<Value, Value>() {",
        "        let (k, _) = pair?;",
        "        let ok = match k {",
        "            Value::Integer(i) => (i as i64) >= 1 && (i as i64) <= n as i64,",
        "            Value::Number(f) => f.fract() == 0.0 && f >= 1.0 && f <= n as f64,",
        "            _ => false,",
        "        };",
        "        if !ok {",
        '            return Err(mlua::Error::runtime(format!("expected an array of {n} values (keys 1 to {n}), found another key")));',
        "        }",
        "    }",
        "    Ok(t)",
        "}",
        "",
    ]
    if "LuaTuple" in used:
        L += [
            "/// Rust tuple <-> Luau array: element `i` sits at index `i + 1`; a `None` element leaves its slot nil.",
            "pub struct LuaTuple<T>(pub T);",
            "",
            "macro_rules! lua_tuple_impls {",
            "    ($(($n:expr; $($t:ident $i:tt),+)),+ $(,)?) => {$(",
            "        impl<$($t: IntoLua),+> IntoLua for LuaTuple<($($t,)+)> {",
            "            fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
            "                let t = lua.create_table_with_capacity($n, 0)?;",
            "                $( t.raw_set($i + 1, self.0.$i)?; )+",
            "                Ok(Value::Table(t))",
            "            }",
            "        }",
            "",
            "        impl<$($t: FromLua),+> FromLua for LuaTuple<($($t,)+)> {",
            "            fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {",
            "                let t = lua_seq_table(value, $n)?;",
            "                Ok(Self(($(t.raw_get::<$t>($i + 1)?,)+)))",
            "            }",
            "        }",
            "    )+};",
            "}",
            "",
            "lua_tuple_impls! {",
        ]
        names = "ABCDEFGHIJKL"[:TUPLE_MAX]
        for n in range(1, TUPLE_MAX + 1):
            L.append(f"    ({n}; " + ", ".join(f"{names[j]} {j}" for j in range(n)) + "),")
        L += ["}", ""]
    if "LuaArray" in used:
        L += [
            "/// Rust `[T; N]` <-> Luau array of exactly N elements.",
            "pub struct LuaArray<T, const N: usize>(pub [T; N]);",
            "",
            "impl<T: IntoLua, const N: usize> IntoLua for LuaArray<T, N> {",
            "    fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
            "        Ok(Value::Table(lua.create_sequence_from(self.0)?))",
            "    }",
            "}",
            "",
            "impl<T: FromLua, const N: usize> FromLua for LuaArray<T, N> {",
            "    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {",
            "        let t = lua_seq_table(value, N)?;",
            "        let mut v = Vec::with_capacity(N);",
            "        for i in 1..=(N as i64) {",
            "            v.push(t.raw_get::<T>(i)?);",
            "        }",
            '        let a: [T; N] = v.try_into().map_err(|_| mlua::Error::runtime("array length mismatch"))?;',
            "        Ok(Self(a))",
            "    }",
            "}",
            "",
        ]
    return L


def emit_types(gen: Generator, src_label: str) -> str:
    m = gen.m
    used = {n for k, n in m.touched if k == "type"}
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, clippy::all)]", ""]
    L += ["use mlua::{FromLua, IntoLua, Lua, Value};", "", "use super::convert::*;"]
    if any(x.get("needs_client") for x in [*m.records.values(), *m.tagged.values()]):
        L.append("use super::gen_client::LuaEntityRef;")
    L.append("")
    L += emit_seq_helpers({n for k, n in m.touched if k == "seq"})
    L += emit_serde_helpers(any(k == "serde" for k, _n in m.touched))
    L += emit_callback_helpers(any(k == "callback" for k, _n in m.touched))
    for name in sorted(n for n in m.enums if n in used):
        e = m.enums[name]
        r, p = e["rust"], e["path"]
        strs = [snake(bare(v)) for v in e["variants"]]
        L += [
            f"/// `{name}` <-> Luau string ({', '.join(repr(s) for s in strs)})",
            f"pub struct {r}(pub {p});",
            "",
            f"impl FromLua for {r} {{",
            "    fn from_lua(value: Value, lua: &Lua) -> mlua::Result<Self> {",
            "        let s = <String as FromLua>::from_lua(value, lua)?;",
            "        Ok(Self(match s.as_str() {",
        ]
        for v, s in zip(e["variants"], strs):
            L.append(f'            "{s}" => {p}::{v},')
        L += [
            f'            other => return Err(mlua::Error::runtime(format!("invalid {name} \'{{other}}\', expected one of: {", ".join(strs)}"))),',
            "        }))",
            "    }",
            "}",
            "",
            f"impl IntoLua for {r} {{",
            "    fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
            "        let s: &str = match self.0 {",
        ]
        for v, s in zip(e["variants"], strs):
            L.append(f'            {p}::{v} => "{s}",')
        L += [
            "            #[allow(unreachable_patterns)]",
            '            _ => "unknown",',
            "        };",
            "        Ok(Value::String(lua.create_string(s)?))",
            "    }",
            "}",
            "",
        ]
    for name in sorted(n for n in m.records if n in used):
        rec = m.records[name]
        r, p = rec["rust"], rec["path"]
        nc = rec.get("needs_client")
        L += [
            f"/// `{name}` -> Luau table" + (" (holds an `Entity`: carries the client)" if nc else ""),
            f"pub struct {r}(pub {p}, pub azalea::Client);" if nc else f"pub struct {r}(pub {p});",
            "",
            f"impl IntoLua for {r} {{",
            "    fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
            "        let (v, client) = (self.0, self.1);" if nc else "        let v = self.0;",
            "        let t = lua.create_table()?;",
        ]
        for fname, _lt, expr in rec["fields"]:
            L.append(f'        t.set("{fname}", {expr})?;')
        L += ["        Ok(Value::Table(t))", "    }", "}", ""]
    for name in sorted(n for n in m.tagged if n in used):
        e = m.tagged[name]
        r, p, nc = e["rust"], e["path"], e["needs_client"]
        L += [
            f"/// `{name}` -> Luau table `{{ kind = {' | '.join(chr(34) + v['kind'] + chr(34) for v in e['variants'])}, ... }}`" + (" (holds an `Entity`: carries the client)" if nc else ""),
            f"pub struct {r}(pub {p}, pub azalea::Client);" if nc else f"pub struct {r}(pub {p});",
            "",
            f"impl IntoLua for {r} {{",
            "    fn into_lua(self, lua: &Lua) -> mlua::Result<Value> {",
        ]
        if nc:
            L.append("        let client = self.1;")
        L += ["        let t = lua.create_table()?;", "        match self.0 {"]
        for v in e["variants"]:
            L.append(f"            {p}::{v['name']}{v['pat']} => {{")
            L.append(f'                t.set("kind", "{v["kind"]}")?;')
            for fname, _lt, expr in v["fields"]:
                L.append(f'                t.set("{fname}", {expr})?;')
            L.append("            }")
        L += ["            #[allow(unreachable_patterns)]", "            _ => {}", "        }", "        Ok(Value::Table(t))", "    }", "}", ""]
    for name in sorted(n for n in m.option_structs if n in used):
        L += emit_option_struct(name, m.option_structs[name])
    vps = [n for n in sorted(m.vparams) if n in used]
    if vps:
        L += emit_value_helpers()
    for name in vps:
        L += emit_value_param(name, m.vparams[name])
    return "\n".join(L) + "\n"


def emit_packets_in(gen: Generator, src_label: str) -> str:
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, unused_variables, clippy::all)]", ""]
    L += [
        "use azalea::protocol::packets::game::ClientboundGamePacket;",
        "use mlua::{Lua, Table};",
        "",
        "use super::convert::*;",
        "use super::gen_types::*;",
        "",
        "/// Every packet name (`kind`) a script can see, in azalea's declaration order.",
        "pub const PACKET_KINDS: &[&str] = &[",
    ]
    L += [f'    "{p["name"]}",' for p in gen.packets_in]
    L += [
        "];",
        "",
        "/// The `kind` of a packet (`\"add_entity\"`, ...).",
        "pub fn packet_kind(packet: &ClientboundGamePacket) -> &'static str {",
        "    match packet {",
    ]
    L += [f'        ClientboundGamePacket::{p["variant"]}(_) => "{p["name"]}",' for p in gen.packets_in]
    L += [
        "        #[allow(unreachable_patterns)]",
        '        _ => "unknown",',
        "    }",
        "}",
        "",
        "/// A packet as a Luau table `{ kind = .., <fields> }` (fields azalea's types cannot convert are left out: see GEN_REPORT.md).",
        "pub fn packet_to_table(lua: &Lua, packet: &ClientboundGamePacket) -> mlua::Result<Table> {",
        "    let t = lua.create_table()?;",
        "    t.set(\"kind\", packet_kind(packet))?;",
        "    match packet.clone() {",
    ]
    for p in gen.packets_in:
        L.append(f'        ClientboundGamePacket::{p["variant"]}(v) => {{')
        for fname, _lt, expr in p["fields"]:
            L.append(f'            t.set("{fname}", {expr})?;')
        L.append("        }")
    L += ["        #[allow(unreachable_patterns)]", "        _ => {}", "    }", "    Ok(t)", "}", ""]
    return "\n".join(L)


def emit_ecs(gen: Generator, src_label: str) -> str:
    comps = [e for e in gen.ecs if e["kind"] == "component"]
    ress = [e for e in gen.ecs if e["kind"] == "resource"]
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, unused_variables, clippy::all)]", ""]
    L += [
        "use azalea::ecs::entity::Entity;",
        "use azalea::prelude::Client;",
        "use mlua::{IntoLua, Lua, Value};",
        "",
        "use super::convert::*;",
        "use super::gen_types::*;",
        "",
        "/// One ECS type scripts can name: `name` is the Rust type name, `luau` its Luau type (empty when `readable` is false).",
        "pub struct EcsEntry {",
        "    pub name: &'static str,",
        "    pub kind: &'static str,",
        "    pub rust: &'static str,",
        "    pub luau: &'static str,",
        "    pub readable: bool,",
        "}",
        "",
        "/// Every azalea component and resource found in the source, in name order.",
        "pub const ECS_ENTRIES: &[EcsEntry] = &[",
    ]
    for e in gen.ecs:
        ok = "true" if e["to_lua"] else "false"
        L.append(f'    EcsEntry {{ name: "{e["name"]}", kind: "{e["kind"]}", rust: "{e["path"] or ""}", luau: "{e["luau"] or ""}", readable: {ok} }},')
    L += [
        "];",
        "",
        "/// Reads component `name` of `entity` as a Luau value: Ok(None) when the entity lacks it, Err for an unknown or unreadable name.",
        "pub fn read_component(lua: &Lua, client: &Client, entity: Entity, name: &str) -> mlua::Result<Option<Value>> {",
        "    match name {",
    ]
    for e in comps:
        if not e["to_lua"]:
            continue
        if e["marker"]:
            L += [
                f'        "{e["name"]}" => Ok(client.get_entity_component::<{e["path"]}>(entity).map(|_| Value::Boolean(true))),',
            ]
            continue
        L += [
            f'        "{e["name"]}" => {{',
            f'            let Some(guard) = client.get_entity_component::<{e["path"]}>(entity) else {{ return Ok(None) }};',
            "            let v = (*guard).clone();",
            f'            Ok(Some(IntoLua::into_lua({e["to_lua"]}, lua)?))',
            "        }",
        ]
    L += [
        "        other => Err(lua_err(ecs_name_error(other, \"component\"))),",
        "    }",
        "}",
        "",
        "/// Same as `read_component`, through an `EntityRef` (an `Entity` value carries its own client).",
        "pub fn read_ref_component(lua: &Lua, entity: &azalea::EntityRef, name: &str) -> mlua::Result<Option<Value>> {",
        "    match name {",
    ]
    for e in comps:
        if not e["to_lua"]:
            continue
        if e["marker"]:
            L.append(f'        "{e["name"]}" => Ok(entity.get_component::<{e["path"]}>().map(|_| Value::Boolean(true))),')
            continue
        L += [
            f'        "{e["name"]}" => {{',
            f'            let Some(guard) = entity.get_component::<{e["path"]}>() else {{ return Ok(None) }};',
            "            let v = (*guard).clone();",
            f'            Ok(Some(IntoLua::into_lua({e["to_lua"]}, lua)?))',
            "        }",
        ]
    L += [
        "        other => Err(lua_err(ecs_name_error(other, \"component\"))),",
        "    }",
        "}",
        "",
        "/// Reads resource `name` as a Luau value: Ok(None) when the ECS has no such resource.",
        "pub fn read_resource(lua: &Lua, client: &Client, name: &str) -> mlua::Result<Option<Value>> {",
        "    match name {",
    ]
    for e in ress:
        if not e["to_lua"]:
            continue
        if e["marker"]:
            L.append(f'        "{e["name"]}" => Ok(client.map_get_resource::<{e["path"]}, _>(|r| r.map(|_| Value::Boolean(true)))),')
            continue
        L += [
            f'        "{e["name"]}" => {{',
            f'            let Some(v) = client.map_get_resource::<{e["path"]}, _>(|r| r.cloned()) else {{ return Ok(None) }};',
            f'            Ok(Some(IntoLua::into_lua({e["to_lua"]}, lua)?))',
            "        }",
        ]
    L += [
        "        other => Err(lua_err(ecs_name_error(other, \"resource\"))),",
        "    }",
        "}",
        "",
        "/// The error text for a name that is not in the table (or not readable).",
        "pub fn ecs_name_error(name: &str, kind: &str) -> String {",
        "    match ECS_ENTRIES.iter().find(|e| e.name == name) {",
        "        Some(e) if !e.readable => format!(\"{kind} `{name}` exists but cannot be read from Luau (see GEN_REPORT.md)\"),",
        "        Some(e) if e.kind != kind => format!(\"`{name}` is a {}, not a {kind}\", e.kind),",
        "        _ => format!(\"unknown {kind} `{name}` (see `bot:ecs_names()`)\"),",
        "    }",
        "}",
        "",
    ]
    return "\n".join(L)


def emit_packets_out(gen: Generator, src_label: str) -> str:
    ok = [p for p in gen.packets_out if p["ok"]]
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, unused_variables, clippy::all)]", ""]
    L += [
        "use azalea::protocol::packets::game::ServerboundGamePacket;",
        "use mlua::Table;",
        "",
        "use super::convert::*;",
        "use super::gen_types::*;",
        "",
        "/// Every serverbound packet a script can build (`kind` -> fields table), in azalea's declaration order.",
        "pub const OUT_PACKET_KINDS: &[&str] = &[",
    ]
    L += [f'    "{p["name"]}",' for p in ok]
    L += [
        "];",
        "",
        "/// Build a serverbound packet from its `kind` and a Luau table of its fields (a missing or mistyped field is a Luau error naming it).",
        "pub fn packet_from_table(kind: &str, t: &Table) -> mlua::Result<ServerboundGamePacket> {",
        "    Ok(match kind {",
    ]
    for p in ok:
        L.append(f'        "{p["name"]}" => {{')
        for i, (lname, _lt, rty, _frm, _rid) in enumerate(p["fields"]):
            L.append(f'            let f{i}: {rty} = t.get("{lname}").map_err(|e| lua_err(format!("packet \'{p["name"]}\', field \'{lname}\': {{e}}")))?;')
        conv = [frm(f"f{i}") for i, (_l, _t, _r, frm, _rid) in enumerate(p["fields"])]
        path = f"azalea::protocol::packets::game::{p['struct']}"
        if p["tuple_style"]:
            body = f"{path}({', '.join(conv)})"
        else:
            body = f"{path} {{ " + ", ".join(f"{rid}: {c}" for (_l, _t, _r, _f, rid), c in zip(p["fields"], conv)) + " }" if conv else f"{path} {{}}"
        L.append(f'            ServerboundGamePacket::{p["variant"]}({body})')
        L.append("        }")
    L += [
        '        other => return Err(lua_err(format!("unknown or unsupported packet kind \'{other}\' (known: {})", OUT_PACKET_KINDS.join(", ")))),',
        "    })",
        "}",
        "",
    ]
    return "\n".join(L)


def active_extra_events(gen: "Generator") -> dict:
    """`[extra_events]` that apply to this azalea: name -> (doc, payload Luau type or None). An event with a payload type
    (`[extra_event_payloads]`) is only declared when that type is generated (`ClientboundPacket`: real azalea only, not the fake)."""
    return {n: (why, EXTRA_EVENT_PAYLOADS.get(n)) for n, why in EXTRA_EVENTS.items()
            if n not in EXTRA_EVENT_PAYLOADS or gen.packets_in}


def emit_events(gen: Generator, src_label: str) -> str:
    L = [HEADER.format(src=src_label), "#![allow(dead_code, unused_imports, unused_variables, clippy::all)]", ""]
    L += [
        "use azalea::Event;",
        "",
        "use super::convert::*;",
        "use super::gen_types::*;",
        "use super::Handlers;",
        "",
        "/// Every event name scripts can pass to `bot:on(...)`.",
        "pub const EVENT_NAMES: &[&str] = &[",
    ]
    L += [f'    "{e["lname"]}",' for e in gen.events]
    L += [f'    "{n}",' for n in active_extra_events(gen)]
    L += [
        "];",
        "",
        "/// Fire the Luau handlers registered for `event`.",
        "pub async fn dispatch(lua: &mlua::Lua, handlers: &Handlers, event: Event) -> mlua::Result<()> {",
        "    match event {",
    ]
    for e in gen.events:
        ln, var, pay = e["lname"], e["variant"], e["payload"]
        if pay is None:
            L.append(f'        Event::{var}{event_pat(gen, var)} => handlers.fire(lua, "{ln}", ()).await?,')
        else:
            L.append(f'        Event::{var}(p) => {{')
            L.append(f'            if handlers.has("{ln}") {{')
            L.append("                let v = p;")
            L.append(f'                handlers.fire(lua, "{ln}", ({pay.to_lua("v")},)).await?;')
            L.append("            }")
            L.append("        }")
    L += ["        _ => {}", "    }", "    Ok(())", "}"]
    return "\n".join(L) + "\n"


def event_pat(gen: Generator, var: str) -> str:
    d = gen.event_def
    for v in d.variants:
        if v["name"] == var:
            return {"unit": "", "tuple": "(..)", "struct": " { .. }"}[v["kind"]]
    return ""


def luau_params(w: Wrapper) -> str:
    return "".join(f", {n}: {ti.luau}" for n, ti in w.wparams)


def luau_union(name: str, alts: list[str], limit: int = 400) -> list[str]:
    """`type name = a | b | ...` as source lines. Luau's parser rejects a single union of more than 1000 members
    (FInt::LuauTypeLengthLimit, "Exceeded allowed type length") and luau-lsp then drops the whole definitions file, so a long union
    (azalea's Item / Block / SoundEvent registries have 1200-2000 variants) is split into `<name>Part<N>` aliases of `limit` members each."""
    if len(alts) <= limit:
        return [f"type {name} = " + " | ".join(alts)]
    parts = [alts[i:i + limit] for i in range(0, len(alts), limit)]
    L = [f"type {name}Part{i} = " + " | ".join(c) for i, c in enumerate(parts, 1)]
    L.append(f"type {name} = " + " | ".join(f"{name}Part{i}" for i in range(1, len(parts) + 1)))
    return L


def emit_dluau(gen: Generator, src_label: str, extras: dict) -> str:
    m = gen.m
    used = {n for k, n in m.touched if k == "type"}
    builtins = {n for k, n in m.touched if k == "builtin"}
    L = [f"-- @generated by tools/luaxite_gen.py from {src_label} - DO NOT EDIT.", ""]
    builtins |= {BUILTIN[n]["needs"] for n in builtins if "needs" in BUILTIN[n]}
    for name in sorted(builtins):
        d = BUILTIN[name]["luau_def"]
        if d:
            L.append(f"type {BUILTIN[name].get('alias', name)} = {d}")
    for name in sorted(n for n in m.enums if n in used):
        L += luau_union(name, [f'"{snake(bare(v))}"' for v in m.enums[name]["variants"]])
    for name in sorted(n for n in m.records if n in used):
        fields = ", ".join(f"{luau_field(f)}: {t}" for f, t, _e in m.records[name]["fields"])
        L.append(f"type {name} = {{ {fields} }}")
    for name in sorted(n for n in m.tagged if n in used):
        alts = []
        for v in m.tagged[name]["variants"]:
            fs = [f'kind: "{v["kind"]}"'] + [f"{luau_field(f)}: {t}" for f, t, _e in v["fields"]]
            alts.append("{ " + ", ".join(fs) + " }")
        L += luau_union(name, alts)
    for name in sorted(n for n in m.option_structs if n in used):
        L.append(luau_option_struct(name, m.option_structs[name]))
    for name in sorted(n for n in m.vparams if n in used and n not in m.records and n not in m.tagged):   # a returned twin has the same text
        e = m.vparams[name]
        if e["kind"] == "struct":
            L.append(f"type {name} = {{ " + ", ".join(f"{luau_field(f)}: {fi.luau}" for f, fi, _r in e["fields"]) + " }")
        else:
            alts = []
            for v in e["variants"]:
                alts.append("{ " + ", ".join([f'kind: "{v["kind"]}"'] + [f"{luau_field(f)}: {fi.luau}" for f, fi in v["fields"]]) + " }")
            L += luau_union(name, alts)
    if gen.packets_in:
        for p in gen.packets_in:
            fs = [f'kind: "{p["name"]}"'] + [f"{luau_field(f)}: {t}" for f, t, _e in p["fields"]]
            L.append(f"type {p['luau']} = {{ " + ", ".join(fs) + " }")
        L.append("-- Every clientbound game packet (tagged by `kind`).")
        if len(gen.packets_in) <= 400:
            L.append("type ClientboundPacket =")
            L += [f"    | {p['luau']}" for p in gen.packets_in]
        else:
            L += luau_union("ClientboundPacket", [p["luau"] for p in gen.packets_in])
    okp = [p for p in gen.packets_out if p["ok"]]
    if okp:
        for p in okp:
            fs = [f"{luau_field(f)}: {t}" for f, t, _r, _fr, _id in p["fields"]]
            L.append(f"type {p['luau']} = {{ " + ", ".join(fs) + " }" if fs else f"type {p['luau']} = {{}}")
        L.append("-- The serverbound game packets a script can build: the `kind` of a packet and its fields table (`OutPacket<Kind>`).")
        L += luau_union("OutPacketKind", [f'"{p["name"]}"' for p in okp])
    L.append("")
    declared = set()
    for name in sorted(n for n in builtins if "class_doc" in BUILTIN[n]):
        b = BUILTIN[name]
        if b["luau"] in declared:
            continue
        declared.add(b["luau"])
        L += [f"-- {b['class_doc']}", f"declare extern type {b['luau']} with"]
        L += [f"    {ln}" for _f, ln in extras.get(b["luau"], [])]
        L += ["end", ""]
    for cname, doc in HAND_CLASSES.items():
        if cname in declared or not extras.get(cname):
            continue
        declared.add(cname)
        L += [f"-- {doc}", f"declare extern type {cname} with"]
        L += [f"    {ln}" for _f, ln in extras[cname]]
        L += ["end", ""]
    for cg in sorted((c for c in gen.classes.values() if c.emit and not c.cfg.get("free")), key=lambda c: c.name == "Client"):
        L.append(f"-- {cg.cfg['doc']}")
        L.append(f"declare extern type {cg.luau} with")
        if cg.cfg["events"]:
            L.append("    --- Register a handler for an event.")
            for e in gen.events:
                arg = f"{e['arg']}: {e['payload'].luau}" if e["payload"] else ""
                L.append(f'    function on(self, event: "{e["lname"]}", handler: ({arg}) -> ()): ()')
            active = active_extra_events(gen)
            for n, (why, pay) in active.items():
                L.append(f"    --- {why}")
                arg = f"packet: {pay}" if pay else ""
                L.append(f'    function on(self, event: "{n}", handler: ({arg}) -> ()): ()')
            L.append("    --- Yields until `event` fires next, then returns its payload (nothing for events without one). "
                     "Works in the script body and in handlers. Raises an error if the bot disconnects first.")
            for e in gen.events:
                ret = e["payload"].luau if e["payload"] else "()"
                L.append(f'    function wait_for(self, event: "{e["lname"]}"): {ret}')
            for n, (_why, pay) in active.items():
                L.append(f'    function wait_for(self, event: "{n}"): {pay or "()"}')
            if "packet" in active:
                L.append("    --- Register a handler for one kind of clientbound packet only (`name` = the packet's `kind`).")
                L += [f'    function on_packet(self, name: "{p["name"]}", handler: (packet: {p["luau"]}) -> ()): ()' for p in gen.packets_in]
                L.append("    --- Yields until a packet of that kind arrives, then returns it.")
                L += [f'    function wait_for_packet(self, name: "{p["name"]}"): {p["luau"]}' for p in gen.packets_in]
        for p in sorted(cg.props, key=lambda p: p.name):   # slice 26: fields before methods
            doc = clean_doc(p.gw.doc) + ("" if p.sw else " (read-only)")
            if doc.strip():
                L.append(f"    --- {doc.strip()}")
            L.append(f"    {luau_field(p.name)}: {p.luau}")
        for w in sorted(cg.wrappers, key=lambda w: w.name):
            doc = ("Yields until finished. " if w.is_async else "") + clean_doc(w.doc)
            if doc:
                L.append(f"    --- {doc.strip()}")
            ret = w.ret.luau if w.ret else "()"
            L.append(f"    function {w.name}(self{luau_params(w)}): {ret}")
        if extras.get(cg.luau):
            L.append("    -- hand-written (rust_binds/luau/extra_*.rs)")
            L += [f"    {ln}" for _f, ln in extras[cg.luau]]
        L += ["end", ""]
    for gname, cname in HAND_GLOBALS.items():
        if cname in declared:
            L.append(f"declare {gname}: {cname}")
    for cg in gen.classes.values():
        if cg.emit and cg.cfg.get("free") and cg.wrappers:
            L.append(f"-- {cg.cfg['doc']}")
            L.append(f"declare {cg.luau}: {{")
            for w in sorted(cg.wrappers, key=lambda w: w.name):
                doc = clean_doc(w.doc)
                if doc:
                    L.append(f"    --- {doc.strip()}")
                L.append(f"    {w.name}: ({luau_params(w)[2:]}) -> {w.ret.luau if w.ret else '()'},")
            L += ["}", ""]
    for cg in static_classes(gen):
        L.append(f"-- Associated functions of `{cg.luau}` (no receiver: `{cg.luau}.name(..)`).")
        L.append(f"declare {cg.luau}: {{")
        for w in sorted(cg.statics, key=lambda w: w.name):
            doc = clean_doc(w.doc)
            if doc:
                L.append(f"    --- {doc.strip()}")
            L.append(f"    {w.name}: ({luau_params(w)[2:]}) -> {w.ret.luau if w.ret else '()'},")
        L += ["}", ""]
    if gen.has_events:
        L += ["declare bot: Bot", ""]
    return "\n".join(L)


REPORT_SCHEMA = 1


def report_model(gen: Generator, src_label: str, ix: Any, extras: dict) -> dict:
    """Everything GEN_REPORT says, as plain data. This is what other tools read (GEN_REPORT.json); GEN_REPORT.md is only rendered from it."""
    m = gen.m
    classes = []
    for cg in gen.classes.values():
        classes.append(dict(
            name=cg.name, rust=cg.rust, luau=cg.luau, emitted=bool(cg.emit),
            generated=sorted(w.name for w in cg.wrappers),
            **({"statics": sorted(w.name for w in cg.statics)} if cg.statics else {}),
            **({"properties": sorted(p.name for p in cg.props)} if cg.props else {}),
            async_count=sum(bool(w.is_async) for w in cg.wrappers),
            skipped=[dict(name=sk.name, sig=sk.sig, origin=sk.origin, reason=sk.reason) for sk in cg.skipped],
        ))
    hand = {}
    for cls, lines in extras.items():
        names = [n for _f, ln in lines if (n := extra_fn_name(ln))]
        if names:
            hand[cls] = names
    events = dict(
        enabled=bool(gen.has_events),
        generated=[dict(name=e["lname"], arg=e["arg"] if e["payload"] else None, payload=e["payload"].luau if e["payload"] else None)
                   for e in gen.events],
        hand_fired=[dict(name=n, packet=pay, why=why) for n, (why, pay) in active_extra_events(gen).items()],
        not_exposed=[dict(variant=v, why=why) for v, why in gen.event_rows],
    )
    packets_out = None
    if gen.packets_out:
        packets_out = dict(
            total=len(gen.packets_out),
            buildable=sum(1 for p in gen.packets_out if p["ok"]),
            unbuildable=[dict(name=p["name"], why=p["why"]) for p in gen.packets_out if not p["ok"]],
        )
    ecs = None
    if gen.ecs:
        kinds = []
        for kind in ("component", "resource"):
            rows = [e for e in gen.ecs if e["kind"] == kind]
            good = [e for e in rows if e["to_lua"]]
            reasons: dict = {}
            for e in rows:
                if not e["to_lua"]:
                    reasons.setdefault(e["reason"], []).append(e["name"])
            kinds.append(dict(
                kind=kind, total=len(rows), readable=len(good),
                types=[dict(name=e["name"], luau=e["luau"]) for e in good if not e["marker"]],
                markers=[e["name"] for e in good if e["marker"]],
                unreadable=[dict(reason=r, names=names) for r, names in sorted(reasons.items())],
            ))
        ecs = dict(total=len(gen.ecs), readable=sum(1 for e in gen.ecs if e["to_lua"]), kinds=kinds)
    packets_in = None
    if gen.packets_in:
        packets_in = dict(
            total=len(gen.packets_in),
            complete=sum(1 for p in gen.packets_in if not p["nodef"] and not p["skipped"]),
            partial=[dict(name=p["name"], left_out=[dict(field=f, why=why) for f, why in p["skipped"]])
                     for p in gen.packets_in if not p["nodef"] and p["skipped"]],
            kind_only=[dict(name=p["name"], why=p["nodef"]) for p in gen.packets_in if p["nodef"]],
        )
    return dict(
        schema=REPORT_SCHEMA,
        source=dict(label=src_label, files=ix.files),
        front_end=ix.frontend_label,
        classes=classes,
        skipped_by_code=dict(sorted(Counter(sk.code or "uncoded" for cg in gen.classes.values() for sk in cg.skipped).items())),
        types=dict(generated=len([1 for k, _ in m.touched if k == "type"]), builtin=len([1 for k, _ in m.touched if k == "builtin"])),
        hand_written=hand,
        **({"serde_types": dict(sorted(m.serde_used.items()))} if m.serde_used else {}),
        **({"callbacks": sorted(m.callbacks)} if m.callbacks else {}),
        events=events,
        packets_out=packets_out,
        ecs=ecs,
        packets_in=packets_in,
        paths=dict(sorted(m.paths.items())),
        goals_not_exposed=[dict(name=n, why=why) for n, why in gen.goal_rows],
        macros_not_expanded=[dict(macro=mac, file=f) for f, mac in sorted(set(ix.macro_notes))],
        enum_notes=sorted(set(m.enum_notes)),
        front_end_skipped=ix.report_items() if hasattr(ix, "report_items") else [],
    )


def emit_report_json(model: dict) -> str:
    """GEN_REPORT.json: the machine-readable report (tools/coverage.py reads it). Same input, same bytes."""
    return json.dumps(model, indent=2, ensure_ascii=False) + "\n"


def render_report_md(r: dict) -> str:
    """GEN_REPORT.md: a human-readable view of the model. Nothing reads it back; read GEN_REPORT.json instead."""
    L = ["# Luau bridge report", "", f"Source: `{r['source']['label']}` ({r['source']['files']} files)",
         f"Front end: {r['front_end']}", ""]
    for c in r["classes"]:
        state = "" if c["emitted"] else " (collected, bindings not emitted yet)"
        L.append(
            f"- **`{c['name']}` -> `{c['rust']}` / `{c['luau']}`{state}:** {len(c['generated'])} methods generated "
            f"({c['async_count']} async/yielding), {len(c['skipped'])} skipped"
        )
    ev = r["events"]
    if ev["enabled"]:
        L.append(f"- **azalea -> Luau:** {len(ev['generated'])} events (`bot:on(...)`)")
    L += [
        f"- **Types:** {r['types']['generated']} generated conversions, {r['types']['builtin']} built-in",
        "",
        "## Generated methods",
        "",
    ]
    for c in r["classes"]:
        L += [f"### {c['name']}", "", ", ".join(f"`{n}`" for n in c["generated"]) or "(none)", ""]
    L += ["## Hand-written methods (rust_binds/luau/extra_*.rs)", ""]
    for cls, names in r["hand_written"].items():
        L.append(f"- `{cls}`: " + ", ".join(f"`{n}`" for n in names))
    if not r["hand_written"]:
        L.append("(none)")
    L += ["", "## Events", ""]
    for e in ev["generated"]:
        L.append(f"- `{e['name']}` " + (f"({e['arg']}: {e['payload']})" if e["payload"] else "()"))
    L += [f"- `{h['name']}` ({'packet: ' + h['packet'] if h['packet'] else ''}) - hand-fired: {h['why']}" for h in ev["hand_fired"]]
    if ev["not_exposed"]:
        L += ["", "Notes:", ""] + [f"- `{n['variant']}`: {n['why']}" for n in ev["not_exposed"]]
    po = r["packets_out"]
    if po:
        L += ["", "## Packets out (Luau table -> ServerboundGamePacket, gen_packets_out.rs)", "",
              f"{po['total']} packets: {po['buildable']} can be built, {len(po['unbuildable'])} cannot (a field has no Luau -> Rust conversion).", ""]
        L += [f"- `{p['name']}`: {p['why']}" for p in po["unbuildable"]]
    ecs = r["ecs"]
    if ecs:
        L += ["", "## ECS components and resources (gen_ecs.rs)", "",
              f"{ecs['total']} types: {ecs['readable']} readable, {ecs['total'] - ecs['readable']} not.", ""]
        for k in ecs["kinds"]:
            L += [f"### {k['kind']}s: {k['readable']} readable of {k['total']}", ""]
            L += [f"- `{e['name']}`: `{e['luau']}`" for e in k["types"]]
            if k["markers"]:
                L.append(f"- marker types (read as `true` when present): {len(k['markers'])}, e.g. " + ", ".join(f"`{n}`" for n in k["markers"][:8]))
            for u in k["unreadable"]:
                L.append(f"- not readable, {u['reason']}: {len(u['names'])}: " + ", ".join(f"`{n}`" for n in u["names"]))
            L.append("")
    pi = r["packets_in"]
    if pi:
        L += ["", "## Packets in (ClientboundGamePacket -> gen_packets_in.rs)", "",
              f"{pi['total']} packets: {pi['complete']} complete, {len(pi['partial'])} with fields left out, {len(pi['kind_only'])} kind only.", ""]
        for p in pi["partial"]:
            L.append(f"- `{p['name']}`: left out " + ", ".join(f"`{x['field']}` ({x['why']})" for x in p["left_out"]))
        for p in pi["kind_only"]:
            L.append(f"- `{p['name']}`: kind only ({p['why']})")
    L += ["", "## Resolved public paths", ""]
    for n, path in r["paths"].items():
        L.append(f"- `{n}` -> `{path}`")
    if r["goals_not_exposed"]:
        L += ["", "## Goals not exposed", ""] + [f"- `{g['name']}`: {g['why']}" for g in r["goals_not_exposed"]]
    if r["macros_not_expanded"]:
        L += ["", "## Macros not expanded (their methods are missing)", ""]
        L += [f"- `{x['macro']}!` in `{x['file']}`" for x in r["macros_not_expanded"]]
    if r.get("callbacks"):
        L += ["", "## callback parameter types (Luau function, called synchronously)", ""] + [f"- `{c}`" for c in r["callbacks"]]
    if r.get("serde_types"):
        L += ["", "## serde types (copied through mlua serde)", ""] + [f"- `{n}`: {f}" for n, f in r["serde_types"].items()]
    by_code = r.get("skipped_by_code") or {}
    if by_code:
        L += ["", "## Skipped by code", "", "| code | methods |", "| --- | ---: |"]
        L += [f"| `{c}` | {n} |" for c, n in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))]
    L += ["", "## Skipped methods", ""]
    for c in r["classes"]:
        L += [f"### {c['name']}", ""]
        by_reason = defaultdict(list)
        for sk in c["skipped"]:
            reason = sk["reason"]
            by_reason[re.sub(r"`[^`]*`", "`_`", reason) if reason.startswith("duplicate") else reason].append(sk["sig"])
        for reason, sigs in sorted(by_reason.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            L += [f"#### {reason} ({len(sigs)})", ""]
            L += [f"- `{sig}`" for sig in sorted(sigs)]
            L.append("")
        if not by_reason:
            L += ["(none)", ""]
    if r["enum_notes"]:
        L += ["## Enums with variants not emitted", ""] + [f"- {n}" for n in r["enum_notes"]] + [""]
    notes = []
    for it in r["front_end_skipped"]:
        if it["kind"] == "type":
            notes.append(f"- type `{it['subject']}`: {it['why']}")
        elif it["kind"] == "reexport":
            notes.append(f"- `{it['crate']}` re-exports `{it['path']}`: {it['why']}")
        else:
            notes.append(f"- `{it['subject']}`: {it['why']}")
    if notes:
        L += ["## rustdoc front end: skipped items", "", "Never fatal: the rest of the bindings were generated without them.", ""] + notes + [""]
    return "\n".join(L)



CliArgs = getattr(argparse, "Argument" + "Pars" + "er")


def main():
    ap = CliArgs(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--azalea", default="bind/azalea-src", help="path to the azalea workspace (default: bind/azalea-src)")
    ap.add_argument("--rust-out", default="rust_binds/luau", help="where gen_*.rs go (default: rust_binds/luau)")
    ap.add_argument("--luau-out", default="luau", help="where azalea.d.luau and GEN_REPORT.json/.md go (default: luau)")
    ap.add_argument("--rustdoc", required=True, help="directory of <crate>.json files (cargo rustdoc --output-format json)")
    ap.add_argument("--config", default=None, metavar="PATH",
                    help="TOML data tables (default: <project root>/bindgen.toml; when that file is missing the built-in empty tables apply; "
                         "an explicit PATH must exist; `--config builtin` = the built-in empty tables)")
    ap.add_argument("--rdj-hidden", choices=("keep", "skip"), default="keep",
                    help="rustdoc front end: what to do with #[doc(hidden)] fns present in the JSON (made with --document-hidden-items): "
                         "keep = bind them; skip = drop them (each is named in GEN_REPORT). Default: keep")
    args = ap.parse_args()
    if args.config is not None:
        try:
            globals().update(load_config(Path(args.config)))
        except ConfigError as e:
            sys.exit(f"error: {Path(args.config).name}: {e}")

    root = Path(args.azalea)
    if not root.is_dir():
        sys.exit(f"error: {root} is not a directory (pass --azalea <path to your azalea checkout>)")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.modules.setdefault("luaxite_gen", sys.modules[__name__])
    import rdj_index
    ix = rdj_index.RdjIndex(root, args.rustdoc, hidden=args.rdj_hidden)
    try:
        ix.load()
    except rdj_index.RdjError as e:
        sys.exit(f"error: {e}")
    except Exception as e:
        sys.exit(f"error: rustdoc front end failed on {args.rustdoc}: {type(e).__name__}: {' '.join(str(e).split())[:200]}")
    CLASSES.update(ix.discovered)
    gen = Generator(ix)
    gen.reserved = extras_reserved(Path(args.rust_out))
    gen.build()
    if gen.has_events:
        gen.build_events()
    gen.build_packets_in(root)
    gen.build_packets_out(root)
    gen.build_ecs()
    for n in sorted(set(gen.m.enum_notes)):
        print(f"warning: enum {n}", file=sys.stderr)

    src_label = f"{root.name} @ {ix.digest.hexdigest()[:10]}"
    rust_out, luau_out = Path(args.rust_out), Path(args.luau_out)
    rust_out.mkdir(parents=True, exist_ok=True)
    luau_out.mkdir(parents=True, exist_ok=True)
    (rust_out / "gen_client.rs").write_text(emit_client(gen, src_label))
    (rust_out / "gen_types.rs").write_text(emit_types(gen, src_label))
    if gen.has_events:
        (rust_out / "gen_events.rs").write_text(emit_events(gen, src_label))
    elif (rust_out / "gen_events.rs").exists():
        (rust_out / "gen_events.rs").unlink()
    packets_rs = rust_out / "gen_packets_in.rs"
    if gen.packets_in:
        packets_rs.write_text(emit_packets_in(gen, src_label))
    elif packets_rs.exists():
        packets_rs.unlink()
    packets_out_rs = rust_out / "gen_packets_out.rs"
    if any(p["ok"] for p in gen.packets_out):
        packets_out_rs.write_text(emit_packets_out(gen, src_label))
    elif packets_out_rs.exists():
        packets_out_rs.unlink()
    ecs_rs = rust_out / "gen_ecs.rs"
    if any(e["to_lua"] for e in gen.ecs):
        ecs_rs.write_text(emit_ecs(gen, src_label))
    elif ecs_rs.exists():
        ecs_rs.unlink()
    extras = scan_extras(rust_out)
    (luau_out / "azalea.d.luau").write_text(emit_dluau(gen, src_label, extras))
    report = report_model(gen, src_label, ix, extras)
    (luau_out / "GEN_REPORT.json").write_text(emit_report_json(report))
    (luau_out / "GEN_REPORT.md").write_text(render_report_md(report))
    print(
        f"generated {sum(len(c.wrappers) for c in gen.classes.values())} methods ({sum(len(c.skipped) for c in gen.classes.values())} skipped), "
        f"{len(gen.events)} events, "
        f"{len([1 for k, _ in gen.m.touched if k == 'type'])} converted types -> {rust_out}/, {luau_out}/"
    )


if __name__ == "__main__":
    main()
