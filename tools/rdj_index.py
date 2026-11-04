#!/usr/bin/env python3
"""
rdj_index.py - the rustdoc-JSON front end: fills the generator's data model (Def / Method, tools/luaxite_gen.py)
from rustdoc JSON. Slice 4 adds the type definitions (`build_defs`);
slice 5 adds the impls (`build_impls`: methods, client traits, Goal impls), slice 6 the public paths
(`RdjPaths`: re-exports, globs, canonical `azalea::..` path); the `RdjIndex` interface itself follows in slice 8. Imports the generator for the dataclasses only.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdj
from rdj import RdjError, convert_type, doc_summary, attr_texts, inner_kind, payload, render_type, visibility
import luaxite_gen as gen

STRIPPED = ("<stripped>", ("other", "_"), False)


def describe_item(c, id, it=None) -> str:
    """`azalea::position::BlockPos`-style text for an item, never raises (falls back to `crate#id (name)`)."""
    try:
        path = c.item_path(id)
    except Exception:
        path = None
    if path:
        return "::".join(str(x) for x in path)
    name = it.get("name") if isinstance(it, dict) else None
    return f"{getattr(c, 'name', '?')}#{id} ({name or 'unnamed'})"


def note_skip(skips, c, id, it, exc) -> None:
    """Record an item the front end could not read (one line: item path + reason) instead of crashing. `skips=None`
    (direct callers) drops the note; RdjIndex passes one shared list that GEN_REPORT prints."""
    if skips is not None:
        reason = " ".join(f"{type(exc).__name__}: {exc}".split())
        skips.append((describe_item(c, id, it), reason[:200]))


def field_type(item: dict):
    """A `struct_field` item's inner is the field's Type itself."""
    inner = item.get("inner")
    return next(iter(inner.values())) if isinstance(inner, dict) and inner else None


def _fields(c: rdj.Crate, ids) -> list:
    """(name, type_ast, is_pub) per field id; a stripped tuple field (null id) keeps its position as a non-pub `_`."""
    out = []
    for i, fid in enumerate(ids):
        it = c.item(fid) if isinstance(fid, int) else None
        if it is None:
            out.append((str(i), ("other", "_"), False))
            continue
        out.append((it.get("name") or str(i), convert_type(field_type(it), c), visibility(it) == "public"))
    return out


def _kind_ids(kind) -> list:
    """Raw ids (None kept for stripped tuple fields) of a StructKind / VariantKind."""
    if isinstance(kind, dict):
        for k, v in kind.items():
            if k == "tuple" and isinstance(v, list):
                return list(v)
            if isinstance(v, dict):
                return list(v.get("fields") or [])
    return []


def _struct_fields(c: rdj.Crate, p: dict):
    kind = p.get("kind")
    tuple_style = isinstance(kind, dict) and "tuple" in kind
    out = _fields(c, _kind_ids(kind))
    if isinstance(kind, dict) and isinstance(kind.get("plain"), dict) and kind["plain"].get("has_stripped_fields"):
        out.append(STRIPPED)
    return out, tuple_style


def _variants(c: rdj.Crate, p: dict, skips: Optional[list] = None, owner: str = "", dropped: Optional[list] = None) -> list:
    """The variants of an enum payload. One that cannot be read is left out and reported: its `variant #id` goes to `dropped`
    (-> `Def.dropped_variants`, so the generator can say the emitted enum is narrower) and a line to `skips` under `owner`."""
    out = []
    for vid in p.get("variants") or []:
        v = c.item(vid)
        if v is None:
            label = f"variant #{vid}"
            if dropped is not None:
                dropped.append(label)
            if skips is not None:
                skips.append((f"{owner or getattr(c, 'name', '?')}::{label}",
                              "variant dropped: not in the JSON index (#[doc(hidden)] without --document-hidden-items, or stripped)"))
            continue
        if not v.get("name"):
            note_skip(skips, c, vid, v, ValueError("variant without a name"))
            if dropped is not None:
                dropped.append(f"variant #{vid}")
            continue
        k = payload(v).get("kind")
        kind = "unit" if k == "plain" or k is None else ("tuple" if isinstance(k, dict) and "tuple" in k else "struct")
        fields = _fields(c, _kind_ids(k))
        if isinstance(k, dict) and isinstance(k.get("struct"), dict) and k["struct"].get("has_stripped_fields"):
            fields.append(STRIPPED)
        out.append(dict(name=v.get("name"), kind=kind, types=[f[1] for f in fields], attrs=attr_texts(v),
                        doc=doc_summary(v.get("docs"))))
    return out


def def_attrs(c: rdj.Crate, id: int, item: dict) -> list:
    """`#[derive(..)]` rebuilt from the derived impls (one attribute, source order) then the other attributes the JSON keeps."""
    derived = c.derived_traits(id)
    return ([f"#[derive({', '.join(derived)})]"] if derived else []) + attr_texts(item)


def make_def(c: rdj.Crate, id: int, it: dict, modpath=None, skips: Optional[list] = None) -> Optional["gen.Def"]:
    """The `Def` of one struct / enum / union / trait item of crate `c` (None for other kinds or nameless items).
    `modpath` defaults to the item's own module path (`item_path`)."""
    k = inner_kind(it)
    if k not in ("struct", "enum", "union", "trait") or not it.get("name"):
        return None
    if modpath is None:
        path = c.item_path(id)
        if not path or len(path) < 2 or path[0] != c.name:
            return None
        modpath = tuple(path[1:-1])
    p = payload(it)
    d = gen.Def(kind=k, name=it["name"], crate=c.name, modpath=tuple(modpath), is_pub=visibility(it) == "public",
                generics=bool((p.get("generics") or {}).get("params")), src=b"")
    if k == "struct":
        d.fields, d.tuple_style = _struct_fields(c, p)
    elif k == "union":
        d.fields = _fields(c, p.get("fields") or [])
    elif k == "enum":
        d.dropped_variants = []
        d.variants = _variants(c, p, skips, describe_item(c, id, it), d.dropped_variants)
    if k != "trait":
        d.attrs = def_attrs(c, id, it)
    return d


def build_defs(r: rdj.Rdj, crates=None, skips: Optional[list] = None) -> dict:
    """name -> [Def] for every struct / enum / union / trait defined in the loaded crates (`crate_id == 0` items that have
    a path): kind, crate (the crate's name), modpath (module path below the crate), is_pub (`pub` only),
    generics (any generic parameter, lifetimes included), tuple_style, fields, variants, attrs. Sorted by (crate, modpath,
    source line). Items rustdoc cannot see (macro-free cfg(test) code, items inside fn bodies) are not here, items macros
    generate are."""
    found = []
    for cname in sorted(crates or r.crates):
        c = r.crate(cname)
        for id, it in c.index.items():
            if it.get("crate_id") != 0:
                continue
            try:
                d = make_def(c, id, it, skips=skips)
                if d is not None:
                    found.append(((c.name, d.modpath, ((it.get("span") or {}).get("begin") or [0])[0], d.name), d))
            except Exception as e:
                note_skip(skips, c, id, it, e)
    defs: dict = defaultdict(list)
    for _, d in sorted(found, key=lambda x: x[0]):
        defs[d.name].append(d)
    return defs



MACRO_LABEL = "(via macro)"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ImplFact(NamedTuple):
    """One `impl Trait for T` header: `trait` = canonical path of the trait from the JSON's `paths` (\"core::clone::Clone\", \"\" when the
    JSON does not list it), `written` = the path as the impl wrote it (\"Clone\", \"fmt::Display\"), `negative` = `impl !Trait for T`,
    `at` = source position of the impl item (file, line, column, id, crate; () when the item has no span) which gives
    `implementors` its order, `generic` = the impl has generic parameters (`impl<T> Foo for Vec<T>`)."""
    trait: str
    written: str
    negative: bool
    at: tuple = ()
    generic: bool = False


# Crates that are one trait namespace seen from different places: std/alloc re-export core, serde re-exports serde_core.
_TRAIT_CRATE_ALIAS = {"std": "core", "alloc": "core", "serde_core": "serde"}


def _trait_segs(path: str) -> list:
    segs = [x for x in path.strip().split("::") if x]
    if segs:
        segs[0] = _TRAIT_CRATE_ALIAS.get(segs[0], segs[0])
    return segs


def trait_matches(fact: ImplFact, trait: str) -> bool:
    """Does the impl header `fact` implement `trait`? `trait` is a canonical path (\"core::fmt::Display\", std/alloc count as core) or
    a bare name. A bare name matches the last segment. A path matches the canonical path of the fact when the JSON lists it;
    when it does not, the written path must agree with `trait` on the segments both have (`fmt::Display` fits `core::fmt::Display`,
    `my::Display` does not): two traits with one name are not conflated."""
    t = _trait_segs(trait)
    if not t:
        return False
    if len(t) == 1:
        return (_trait_segs(fact.trait) or _trait_segs(fact.written) or [""])[-1] == t[0]
    if fact.trait:
        return _trait_segs(fact.trait) == t
    w = _trait_segs(fact.written)
    n = min(len(w), len(t))
    return bool(w) and w[-n:] == t[-n:]


def _trait_canon(c: rdj.Crate, p: dict) -> str:
    tr = p.get("trait")
    if not isinstance(tr, dict) or not isinstance(tr.get("id"), int):
        return ""
    path = c.item_path(tr["id"])
    return "::".join(path) if path else ""


@dataclass
class ImplData:
    """What `RdjIndex` keeps from impls: `methods` (Method per fn of an impl whose target is a bound class), `client_traits`
    (traits written out in `impl Trait for Client`), `goal_impls` (type names with an `impl Goal for T`)."""
    methods: list = field(default_factory=list)
    client_traits: set = field(default_factory=set)
    goal_impls: set = field(default_factory=set)
    clone_impls: set = field(default_factory=set)
    display_impls: set = field(default_factory=set)
    default_impls: set = field(default_factory=set)
    facts: dict = field(default_factory=dict)  # type name -> [ImplFact] of every trait impl header (derived ones too; not synthetic/blanket)
    macro_notes: list = field(default_factory=list)
    hidden: list = field(default_factory=list)


def _bounds_text(bounds) -> str:
    return rdj._render_bounds(bounds)


def render_generic_params(params) -> str:
    """`<'a: 'b, T: A + B = D, const N: usize = 3>` of a generics list. Synthetic params (the hidden ones behind `impl Trait`
    arguments) are left out, like the source shows them."""
    out = []
    for gp in params or []:
        if not isinstance(gp, dict) or (isinstance(gp.get("kind"), dict) and (gp["kind"].get("type") or {}).get("is_synthetic")):
            continue
        name, kind = gp.get("name") or "_", gp.get("kind") if isinstance(gp.get("kind"), dict) else {}
        if "lifetime" in kind:
            outl = (kind["lifetime"] or {}).get("outlives") or []
            out.append(name + (f": {' + '.join(map(str, outl))}" if outl else ""))
        elif "const" in kind:
            c = kind["const"] if isinstance(kind["const"], dict) else {}
            out.append(f"const {name}: {render_type(c.get('type'))}" + (f" = {c['default']}" if c.get("default") else ""))
        else:
            t = kind.get("type") if isinstance(kind.get("type"), dict) else {}
            b = _bounds_text(t.get("bounds"))
            out.append(name + (f": {b}" if b else "") + (f" = {render_type(t['default'])}" if t.get("default") is not None else ""))
    return f"<{', '.join(out)}>" if out else ""


def render_where(preds) -> str:
    out = []
    for w in preds or []:
        if not isinstance(w, dict):
            continue
        if "bound_predicate" in w:
            b = w["bound_predicate"] or {}
            gp = [g.get("name", "") for g in b.get("generic_params") or [] if isinstance(g, dict)]
            out.append((f"for<{', '.join(gp)}> " if gp else "") + f"{render_type(b.get('type'))}: {_bounds_text(b.get('bounds'))}")
        elif "lifetime_predicate" in w:
            lp = w["lifetime_predicate"] or {}
            out.append(f"{lp.get('lifetime')}: {' + '.join(map(str, lp.get('outlives') or []))}")
        elif "eq_predicate" in w:
            e = w["eq_predicate"] or {}
            rhs = e.get("rhs")
            out.append(f"{render_type(e.get('lhs'))} == {render_type(rhs.get('type')) if isinstance(rhs, dict) and 'type' in rhs else '_'}")
    return f" where {', '.join(out)}" if out else ""


def split_receiver(inputs):
    """(receiver text | None, remaining inputs). The receiver is the first input named `self`: `&self`, `&mut self`,
    `&'a self`, `self` (JSON cannot tell `mut self`: it reads as `self`), or `self: Type` for typed receivers."""
    if not inputs or not (isinstance(inputs[0], (list, tuple)) and len(inputs[0]) == 2 and inputs[0][0] == "self"):
        return None, list(inputs or [])
    t = inputs[0][1]
    rest = list(inputs[1:])
    if isinstance(t, dict) and set(t) == {"generic"} and t["generic"] == "Self":
        return "self", rest
    br = t.get("borrowed_ref") if isinstance(t, dict) else None
    if isinstance(br, dict) and br.get("type") == {"generic": "Self"}:
        lt = f"{br['lifetime']} " if br.get("lifetime") else ""
        return f"&{lt}{'mut ' if br.get('is_mutable') else ''}self", rest
    return f"self: {render_type(t)}", rest


def type_params_of(fp: dict):
    """Slice 24: the declared type parameters of a fn payload as [(name, (bound trait last segments,..))] in declaration order, lifetimes dropped,
    synthetic (`impl Trait`) ones dropped; None when a const parameter exists (no turbofish form is written for it). Bounds come from the
    parameter itself and from `where T: ..` predicates."""
    g = fp.get("generics") or {}
    out, names = [], {}
    for gp in g.get("params") or []:
        if not isinstance(gp, dict):
            continue
        k = gp.get("kind") or {}
        if "const" in k:
            return None
        ty = k.get("type")
        if not isinstance(ty, dict) or ty.get("is_synthetic"):
            continue
        names[gp.get("name")] = len(out)
        out.append([gp.get("name"), list(ty.get("bounds") or [])])
    for wp in g.get("where_predicates") or []:
        bp = wp.get("bound_predicate") if isinstance(wp, dict) else None
        if isinstance(bp, dict) and isinstance(bp.get("type"), dict) and bp["type"].get("generic") in names:
            out[names[bp["type"]["generic"]]][1] += list(bp.get("bounds") or [])
    res = []
    for n, bounds in out:
        tr = []
        for b in bounds:
            tb = b.get("trait_bound") if isinstance(b, dict) else None
            if isinstance(tb, dict) and isinstance(tb.get("trait"), dict):
                tr.append(rdj._last_seg(tb["trait"].get("path") or ""))
        res.append((n, tuple(tr)))
    return res


def param_name(name) -> Optional[str]:
    """A parameter has a name only when its pattern is a plain identifier; rustdoc writes `_` (or the
    pattern's text) for destructured ones. `mut x` / `ref x` keep their identifier in the JSON (name None)."""
    return name if isinstance(name, str) and name != "_" and IDENT.fullmatch(name) else None


def fn_sig_text(it: dict, p: dict) -> str:
    """The method's signature as source-like text (the fn text up to the `{`, whitespace-normalised): `pub async fn f<T: A>(&self,
    x: X) -> R where ..`. Canonical form: no trailing comma, no space after `(` (rustfmt-wrapped signatures lose both
    in the JSON)."""
    vis = visibility(it)
    v = it.get("visibility")
    if vis == "public":
        pre = "pub "
    elif vis == "crate":
        pre = "pub(crate) "
    elif vis == "restricted":
        path = ((v or {}).get("restricted") or {}).get("path") if isinstance(v, dict) else None
        pre = f"pub(in {path.lstrip(':')}) " if path else "pub(restricted) "
    else:
        pre = ""
    h = p.get("header") or {}
    abi = rdj.abi_text(h.get("abi"))
    ext = f'extern "{abi}" ' if abi else ""
    quals = ("const " if h.get("is_const") else "") + ("async " if h.get("is_async") else "") + ("unsafe " if h.get("is_unsafe") else "") + ext
    g = p.get("generics") or {}
    sig = p.get("sig") or {}
    recv, rest = split_receiver(sig.get("inputs"))
    args = ([recv] if recv else []) + [f"{n}: {render_type(t)}" for n, t in rest if isinstance(n, str)]
    out = sig.get("output")
    return (f"{pre}{quals}fn {it.get('name')}{render_generic_params(g.get('params'))}({', '.join(args)})"
            + (f" -> {render_type(out)}" if out is not None else "") + render_where(g.get("where_predicates")))


def _span(it: dict):
    sp = it.get("span") if isinstance(it.get("span"), dict) else {}
    return sp.get("filename"), tuple(sp.get("begin") or ()), tuple(sp.get("end") or ())


def method_file(impl: dict, fn: dict, strip_prefix: str = "") -> str:
    """`Method.file`: the source file of the fn (span filename, relative to the workspace root, minus `strip_prefix`),
    plus ` (via macro)` when a macro made it. A macro-made fn is recognised by the span rustdoc gives it: the call site,
    the same span as the impl block around it (a real impl block is always bigger than the fns inside)."""
    name = (_span(fn)[0] or "<unknown>").replace("\\", "/")
    if strip_prefix and name.startswith(strip_prefix):
        name = name[len(strip_prefix):]
    fs, isp = _span(fn), _span(impl)
    return name + (f" {MACRO_LABEL}" if fs[1] and fs[1:] == isp[1:] and fs[0] == isp[0] else "")


def _target_name(p: dict) -> str:
    """Name of an impl's `for` type when it is a plain (generic) path type, else ''."""
    t = convert_type(p.get("for") or {})
    return t[1] if t[0] == "path" else ""


def _trait_name(p: dict) -> Optional[str]:
    tr = p.get("trait")
    return rdj._last_seg(tr["path"]) if isinstance(tr, dict) and tr.get("path") else None




def is_hidden(it: dict) -> bool:
    """`#[doc(hidden)]` on an item (format 57 lists it as {"other": "#[doc(hidden)]"}; older/newer ones as a bare string)."""
    for a in it.get("attrs") or []:
        t = a if isinstance(a, str) else (a.get("other") if isinstance(a, dict) else None)
        if isinstance(t, str):
            t = "".join(t.split())
            if t in ("doc(hidden)", "#[doc(hidden)]"):
                return True
    return False


def _is_user_impl(it: dict, p: dict) -> bool:
    """Impls somebody wrote (or a macro wrote): not auto-trait/blanket impls rustdoc lists, not `#[derive]` output."""
    return not (p.get("is_synthetic") or p.get("blanket_impl")) and "automatically_derived" not in (it.get("attrs") or [])


def _note_fact(data: "ImplData", c: rdj.Crate, it: dict, p: dict) -> None:
    """Keep the header of a trait impl (derived or written, negative too) in `data.facts`; rustdoc's synthetic/blanket impls and
    impls on a non-path type are not facts. Never raises: a header that cannot be read is simply not a fact."""
    try:
        if p.get("is_synthetic") or p.get("blanket_impl"):
            return
        tname, tr = _target_name(p), p.get("trait")
        if tname and isinstance(tr, dict) and tr.get("path"):
            fname, begin = _span(it)[0], (_span(it)[1] + (0, 0))[:2]
            at = (str(fname or "").replace("\\", "/"), begin[0], begin[1], it.get("id") if isinstance(it.get("id"), int) else 0, c.name)
            data.facts.setdefault(tname, []).append(ImplFact(_trait_canon(c, p), tr["path"], bool(p.get("is_negative")), at,
                                                             bool((p.get("generics") or {}).get("params"))))
    except Exception:
        return


def build_impls(r: rdj.Rdj, crates=None, classes=None, strip_prefix: str = "", hidden: str = "keep",
                skips: Optional[list] = None) -> ImplData:
    """Fill the impl tables (methods, client traits, Goal impls) from the JSON of every loaded crate (or `crates`): one `Method` per fn of
    an impl block whose target type is in `classes` (default: the generator's CLASSES), plus `client_traits` (traits of
    `impl Trait for Client`) and `goal_impls` (any `impl Goal for T`). Order = source order (file, line,
    column, id), which is what the generator's stable (file, name) sort relies on. Macro-expanded impls are ordinary impls
    here (impl_entity_functions! is already expanded: no regex), `#[derive]` impls and rustdoc's synthetic/blanket impls are
    skipped. is_pub: `pub`, or any fn of a trait impl. `hidden`: see the treatment notes above. `macro_notes` stays empty."""
    if hidden not in ("keep", "skip"):
        raise ValueError(f"hidden must be 'keep' or 'skip', not {hidden!r}")
    classes = set(classes if classes is not None else gen.CLASSES)
    data = ImplData()
    found = []
    for cname in sorted(crates or r.crates):
        c = r.crate(cname)
        for id, it in c.index.items():
            if it.get("crate_id") != 0:
                continue
            try:
                k = inner_kind(it)
                if k != "impl":
                    continue
                p = payload(it)
                _note_fact(data, c, it, p)
                if not _is_user_impl(it, p):
                    continue
                tname, trait = _target_name(p), _trait_name(p)
            except Exception as e:
                note_skip(skips, c, id, it, e)
                continue
            if trait == "Goal":
                data.goal_impls.add(tname)
            if tname and trait in ("Clone", "Display", "Default") and not p.get("is_negative"):   # no `''` for impls on a non-path type
                fact = ImplFact(_trait_canon(c, p), p["trait"]["path"], False)
                for std, bucket in (("core::clone::Clone", data.clone_impls), ("core::fmt::Display", data.display_impls),
                                    ("core::default::Default", data.default_impls)):
                    if trait_matches(fact, std):  # `my::Display` is not std's Display
                        bucket.add(tname)
            if tname not in classes:
                continue
            if trait and tname == "Client":
                data.client_traits.add(trait)
            for fid in p.get("items") or []:
                fn = None
                try:
                    fn = c.item(fid)
                    if fn is None or inner_kind(fn) != "function":
                        continue
                    if is_hidden(fn) or is_hidden(it):
                        data.hidden.append((tname, fn.get("name"), "skipped" if hidden == "skip" else "kept"))
                        if hidden == "skip":
                            continue
                    found.append(((method_file(it, fn, strip_prefix), _span(fn)[1], fid), c, it, fn, tname, trait))
                except Exception as e:
                    note_skip(skips, c, fid, fn, e)
    for (file, _, _), c, impl, fn, tname, trait in sorted(found, key=lambda x: x[0]):
        try:
            if not fn.get("name"):
                raise ValueError("fn item without a name")
            fp = payload(fn)
            sig = fp.get("sig") or {}
            recv, rest = split_receiver(sig.get("inputs"))
            out = sig.get("output")
            data.methods.append(gen.Method(
                name=fn.get("name"), doc=doc_summary(fn.get("docs")),
                is_pub=visibility(fn) == "public" or trait is not None,
                is_async=bool((fp.get("header") or {}).get("is_async")),
                generics=any(not ((gp.get("kind") or {}).get("type") or {}).get("is_synthetic")
                             for gp in (fp.get("generics") or {}).get("params") or [] if isinstance(gp, dict)),
                receiver=recv,
                params=[(param_name(n), convert_type(t, c)) for n, t in rest],
                ret=convert_type(out, c) if out is not None else None,
                owner=trait or tname, file=file, sig=fn_sig_text(fn, fp), target=tname,
                type_params=type_params_of(fp)))
        except Exception as e:
            note_skip(skips, c, fn.get("id"), fn, e)
    return data



class RdjPaths:
    """The path half of the generator's index, from rustdoc JSON: the module tree is walked the way rustc
    resolves names: from the root crate's root module through every *public* item, following `pub use` items (renames, globs,
    re-exports of whole modules/crates) into the other loaded crates. The canonical path of a type is the shortest
    (fewest `::`, then alphabetical) public path from the root crate that ends in the type's own name
    (a re-export under another name is not used: the generated code spells the type by its name).

    Interface: `crates` `defs` `mod_pub` `reexports` `globs` `aliases()` `chain_public()` `to_azalea()` `unique_def()`
    `resolve_path()`. Not modelled: `#[doc(hidden)]`, `pub(in path)` restrictions beyond
    "public or not", enum-variant globs. A `use` whose target is in a crate that is not loaded is skipped and listed in
    `unresolved` (slice 10 reports them in GEN_REPORT)."""

    MAX_DEPTH = 16
    DEF_KINDS = ("struct", "enum", "union", "trait")

    def __init__(self, r: rdj.Rdj, defs: Optional[dict] = None, root: str = "azalea", skips: Optional[list] = None, roots: Optional[tuple] = None):
        if root not in r.crates:
            raise RdjError(f"no rustdoc JSON for the root crate `{root}` (have: {', '.join(sorted(r.crates)) or 'none'})")
        self.r, self.root = r, root
        self.roots = tuple(roots) if roots else (root,)
        self.skips = skips
        self.defs = defs if defs is not None else build_defs(r, skips=skips)
        self.crates: dict = {n: None for n in sorted(r.crates)}
        self.mod_pub: dict = {}
        self.reexports: list = []
        self.globs: list = []
        self.unresolved: list = []
        self.ambiguous: dict = {}   # name -> sorted `crate::mod::Name` of every reachable pub def, when unique_def found several
        self._def_ids: dict = {}
        self._ns: dict = {}
        self._cut = False
        self._paths = None
        self._scan()

    def _scan(self):
        for cname in self.crates:
            c = self.r.crate(cname)
            for id, it in sorted(c.index.items()):
                if it.get("crate_id") != 0:
                    continue
                try:
                    self._scan_item(c, id, it)
                except Exception as e:
                    note_skip(self.skips, c, id, it, e)

    def _scan_item(self, c, id, it):
        k = inner_kind(it)
        if k == "module" and id != c.root:
            path = c.item_path(id)
            if path and len(path) >= 2 and path[0] == c.name:
                self.mod_pub[(c.name, tuple(path[1:-1]) + (it.get("name"),))] = visibility(it) == "public"
        elif k in self.DEF_KINDS and it.get("name"):
            path = c.item_path(id)
            if path and len(path) >= 2 and path[0] == c.name:
                self._def_ids.setdefault((c.name, tuple(path[1:-1]), it["name"], k), (c.name, id))
        elif k == "use" and visibility(it) == "public":
            path, p = c.item_path(id), payload(it)
            if path and len(path) >= 2 and path[0] == c.name:
                modpath = tuple(path[1:-1])
                glob = bool(p.get("is_glob"))
                self.reexports.append((c.name, modpath, "*" if glob else (it.get("name") or p.get("name")), p.get("source") or ""))
                if glob:
                    self.globs.append((c.name, modpath, p.get("source") or ""))

    def aliases(self) -> dict:
        """`pub use azalea_core as core;` at the root crate's root: {crate name: exported name}."""
        return {full: exported for crate, modpath, exported, full in self.reexports
                if crate == self.root and modpath == () and exported != "*" and "::" not in full and full in self.crates}

    def chain_public(self, crate, modpath) -> bool:
        return all(self.mod_pub.get((crate, tuple(modpath[:i])), False) for i in range(1, len(modpath) + 1))

    def _reachable(self, d) -> Optional[bool]:
        """Does a public path from a root crate lead to `d`? None: `d` is not an item of the loaded JSON (a hand-built def), so
        nothing can be said and it stays a candidate."""
        ids = [v for k, v in self._def_ids.items() if k[:3] == (d.crate, tuple(d.modpath), d.name)]
        if not ids:
            return None
        pp = self.public_paths()
        return any(pp.get(i) for i in ids)

    def unique_def(self, name: str):
        """The one public definition called `name`. Several: only those a public path reaches count (a same-name type in a private
        module no longer blocks the resolution); exactly one left -> it; several left -> None, and `self.ambiguous[name]` lists
        them (never ranked silently). A single public def is returned as before, reachable or not (its missing path is then
        `no_path`, not `no_definition`)."""
        pubs = [d for d in self.defs.get(name, []) if d.is_pub]
        if len(pubs) <= 1:
            return pubs[0] if pubs else None
        cands = [d for d in pubs if self._reachable(d) is not False]
        if len(cands) >= 2:
            self.ambiguous[name] = sorted("::".join((d.crate, *d.modpath, d.name)) for d in cands)
        return cands[0] if len(cands) == 1 else None

    def _resolve(self, c: rdj.Crate, id):
        """(crate name, item id) of a `use` target: a local item, or an item of another crate found by its `paths` entry
        in that crate's own JSON (`Rdj.resolve`); misses go to `self.unresolved`."""
        return self.r.resolve(c, id, self.unresolved)

    def _names(self, cname: str, mid: int, stack: tuple = ()) -> list:
        """[(name, (crate, id))]: the public names of a module's namespace, own items and `pub use` items (globs expanded)."""
        key = (cname, mid)
        if key in self._ns:
            return self._ns[key]
        if key in stack:
            self._cut = True
            return []
        c = self.r.crate(cname)
        cut_before, self._cut = self._cut, False
        out, from_glob = [], []
        for cid in c.children(mid):
            it = c.index.get(cid)
            try:
                if it is None or visibility(it) != "public":
                    continue
                if inner_kind(it) == "use":
                    p = payload(it)
                    tgt = self._resolve(c, p.get("id"))
                    if tgt is None:
                        continue
                    if p.get("is_glob"):
                        if self.r.crate(tgt[0]).kind(tgt[1]) == "module":
                            got = self._names(tgt[0], tgt[1], stack + (key,))
                            out += got
                            from_glob += [True] * len(got)
                    elif p.get("name"):
                        out.append((p["name"], tgt))
                        from_glob.append(False)
                elif it.get("name"):
                    out.append((it["name"], (cname, cid)))
                    from_glob.append(False)
            except Exception as e:
                note_skip(self.skips, c, cid, it, e)
        explicit = {(n, self._ns_of(t)) for (n, t), g in zip(out, from_glob) if not g}
        out = [nt for nt, g in zip(out, from_glob) if not (g and (nt[0], self._ns_of(nt[1])) in explicit)]
        if not self._cut:
            self._ns[key] = out
        self._cut = self._cut or cut_before
        return out

    def _ns_of(self, tgt: tuple) -> str:
        """Namespace of an item (`type` / `value` / `macro`): a glob-imported name is only shadowed by an explicit name of the same one."""
        k = self.r.crate(tgt[0]).kind(tgt[1])
        return "value" if k in ("function", "constant", "static") else "macro" if k.startswith("macro") or k.startswith("proc_") else "type"

    def public_paths(self) -> dict:
        """{(crate, id): {path tuple from the root crate}}: every public path of every item reachable from the root crate."""
        if self._paths is not None:
            return self._paths
        out: dict = defaultdict(set)

        def visit(cname, mid, path, chain):
            for name, tgt in self._names(cname, mid):
                p = path + (name,)
                out[tgt].add(p)
                if len(p) < self.MAX_DEPTH and tgt not in chain and self.r.crate(tgt[0]).kind(tgt[1]) == "module":
                    visit(tgt[0], tgt[1], p, chain | {tgt})

        for root in self.roots:
            rc = self.r.crate(root)
            visit(root, rc.root, (root,), frozenset({(root, rc.root)}))
        self._paths = out
        return out

    def paths_of(self, crate: str, modpath, name: str, kind=None) -> list:
        """Every public `azalea::..` path (as text, shortest first) of the item defined at crate::modpath::name."""
        ids = [v for k, v in self._def_ids.items() if k[:3] == (crate, tuple(modpath), name) and kind in (None, k[3])]
        found = {"::".join(p) for i in ids for p in self.public_paths().get(i, ())}
        return sorted(found, key=lambda p: (p.count("::"), p))

    def to_azalea(self, crate, modpath, name) -> Optional[str]:
        """Shortest public path of the type defined at crate::modpath::name, or None when it is not reachable."""
        ps = [p for p in self.paths_of(crate, modpath, name) if p.split("::")[-1] == name]
        return ps[0] if ps else None

    def resolve_path(self, name: str, prefer: Optional[str] = None) -> Optional[str]:
        """Canonical `azalea::..` path of the type called `name`. `prefer` (a path prefix such as `azalea::entity::metadata`, cut at
        a `::` boundary) picks among same-name definitions: those with a public path under it; exactly one such definition ->
        its shortest path under the prefix, else None. Without `prefer` this is `unique_def` + `to_azalea`."""
        if not prefer:
            d = self.unique_def(name)
            if d is None:
                return None
            return self.to_azalea(d.crate, d.modpath, d.name)
        found = {}
        for d in self.defs.get(name, []):
            if not d.is_pub or self._reachable(d) is False:
                continue
            ps = [p for p in self.paths_of(d.crate, d.modpath, d.name) if p.split("::")[-1] == name
                  and (p == prefer or p.startswith(prefer + "::"))]
            if ps:
                found[(d.crate, tuple(d.modpath), d.name)] = ps[0]
        return next(iter(found.values())) if len(found) == 1 else None

    NOT_USER_CRATES = frozenset({"std", "core", "alloc", "proc_macro", "test"})

    def missing_crates(self) -> list:
        """Sorted names of the crates that define a type (struct, enum, union, trait) some loaded crate's `paths` mentions and that
        is itself not loaded: load its JSON to follow that type. std, core, alloc, proc_macro and test are never listed, nor the crates std
        itself is built from (their `html_root_url` is the Rust docs: hashbrown, gimli, libc, ...)."""
        loaded = set(self.r.crates)
        out = set()
        for c in self.r.crates.values():
            for s in c.paths.values():
                cid = s.get("crate_id")
                if not cid or s.get("kind") not in self.DEF_KINDS:
                    continue
                name = c.crate_name(cid)
                root = (c.external_crates.get(cid) or {}).get("html_root_url") or ""
                if name and name not in loaded and name not in self.NOT_USER_CRATES and "doc.rust-lang.org" not in root:
                    out.add(name)
        return sorted(out)



def source_crates(root: Path) -> dict:
    """The crates under `root` (`<dir>/src/lib.rs`): {crate name: <dir>/src}."""
    out = {}
    for d in sorted(Path(root).iterdir()):
        if (d / "src" / "lib.rs").exists():
            out[d.name.replace("-", "_")] = d / "src"
    return out


def source_digest(root: Path):
    """(sha1 hexdigest, file count) over the same source files, in a fixed order: the label
    in every generated header is `<root dir> @ <digest[:10]>` (the JSON has no notion of the checkout it came from)."""
    import hashlib
    h, n = hashlib.sha1(), 0
    for crate, src in source_crates(root).items():
        for f in sorted(src.rglob("*.rs")):
            if not f.is_file() or any(p in gen.SKIP_DIRS for p in f.relative_to(src).parts):
                continue
            h.update(f.read_bytes())
            n += 1
    return h, n


def stale_json(r: rdj.Rdj, crates: dict) -> list:
    """Warning lines for crate JSONs that cannot be shown to match the sources under `crates` ({name: <dir>/src}, see
    `source_crates`): the `.meta` next to each JSON records `source_sha1` (tools/make_rustdoc_json.sh), compared here with
    `rdj.source_sha1` of the live crate dir. Stale = sources changed since the JSON was made; the bindings would describe old code."""
    out = []
    for name, src in sorted(crates.items()):
        c = r.crates.get(name)
        if c is None:
            continue
        want = (c.meta or {}).get("source_sha1")
        if not want:
            out.append(f"{c.stem}.json: its .meta has no source_sha1, so it cannot be checked against the sources (re-make it with tools/make_rustdoc_json.sh)")
            continue
        try:
            have = rdj.source_sha1(Path(src).parent)
        except OSError as e:
            out.append(f"{c.stem}.json: cannot be checked against the sources of `{name}` ({type(e).__name__}: {e})")
            continue
        if have != want:
            out.append(f"{c.stem}.json is stale: the sources of `{name}` changed since it was made (source_sha1 {want[:10]}, now {have[:10]}); "
                       "re-make it with tools/make_rustdoc_json.sh")
    return out


def missing_json(root: Path, rdj_dir) -> str:
    """Why `rdj_dir` cannot serve the sources under `root` (no directory, no crates, a crate without its JSON); "" = complete."""
    d = Path(rdj_dir) if rdj_dir else None
    if d is None or not d.is_dir():
        return f"no rustdoc JSON directory ({rdj_dir})" if rdj_dir else "no --rustdoc directory given"
    have = {p.stem.replace("-", "_") for p in d.glob("*.json")}
    need = set(source_crates(root))
    if not need:
        return f"no crates under {root}"
    miss = sorted(need - have)
    return f"missing JSON for: {', '.join(miss)}" if miss else ""


def workspace_prefix(r: rdj.Rdj, root: str = "azalea") -> str:
    """JSON `span.filename`s are relative to the directory cargo ran in; `Method.file` is relative to the scanned
    root (`azalea/src/lib.rs`). The prefix to strip = whatever precedes `<crate dir>/src/lib.rs` in the root module's span."""
    c = r.crates.get(root)
    sp = (c.index.get(c.root) or {}).get("span") if c else None
    fn = ((sp or {}).get("filename") or "").replace("\\", "/")
    parts = fn.split("/")
    return "/".join(parts[:-3]) + "/" if len(parts) > 3 and parts[-2:] == ["src", "lib.rs"] else ""



def _skip_blank(t: str, i: int) -> int:
    """Index of the first char at/after `i` that is not whitespace or inside a `//` / (nested) `/* */` comment."""
    n = len(t)
    while i < n:
        if t[i].isspace():
            i += 1
        elif t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j + 1
        elif t.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if t.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif t.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        else:
            break
    return i


def _skip_string(t: str, i: int) -> int:
    """`t[i]` is a `"`: index after the closing quote (backslash escapes honoured)."""
    i += 1
    while i < len(t) and t[i] != '"':
        i += 2 if t[i] == "\\" else 1
    return i + 1


def _skip_balanced(t: str, i: int) -> int:
    """`t[i]` opens `(` `[` or `{`: index after its match, ignoring brackets in strings and comments."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, n = [pairs[t[i]]], len(t)
    i += 1
    while i < n and stack:
        c = t[i]
        if c == '"':
            i = _skip_string(t, i)
        elif t.startswith("//", i) or t.startswith("/*", i):
            i = _skip_blank(t, i)
        elif c in pairs:
            stack.append(pairs[c])
            i += 1
        else:
            if c == stack[-1]:
                stack.pop()
            i += 1
    return i


def enum_variant_cfgs(text: str, name: str) -> Optional[dict]:
    """{variant: [`#[cfg(..)]` attributes]} of the first `enum <name>` in `text` (attribute text whitespace-normalised); None when there is no such enum. A small scanner, no syntax tree: it only has to
    find each variant's name and the attributes in front of it."""
    m = re.search(r"\benum\s+" + re.escape(name) + r"\b", text)
    i = text.find("{", m.end()) if m else -1
    if i < 0:
        return None
    body = text[i + 1:_skip_balanced(text, i) - 1]
    out: dict = {}
    attrs: list = []
    j, n = 0, len(body)
    while True:
        j = _skip_blank(body, j)
        if j >= n:
            return out
        if body.startswith("#[", j):
            k = _skip_balanced(body, j + 1)
            attrs.append(" ".join(body[j:k].split()))
            j = k
            continue
        m = re.match(r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*", body[j:])
        if not m:
            j += 1
            continue
        out[m.group(0)] = [a for a in attrs if a.startswith("#[cfg(")]
        attrs = []
        j += m.end()
        while j < n:
            j = _skip_blank(body, j)
            if j >= n:
                break
            if body[j] in "({[":
                j = _skip_balanced(body, j)
            elif body[j] == '"':
                j = _skip_string(body, j)
            else:
                j += 1
                if body[j - 1] == ",":
                    break


def event_cfg_gates(r: rdj.Rdj, defs: dict, root: Path, prefix: str = "") -> list:
    """Puts the `#[cfg(..)]` attributes of `enum Event`'s variants (read from the source file the JSON points at; JSON
    `span.filename` minus `prefix`, under `root`) into the Def's variant `attrs`, where `build_events` looks for them: a
    cfg-gated variant is exposed by `--all-features` JSON but must not be bound. Returns warning lines for the enums whose gates
    could not be read: variants that may be gated, and are neither hidden nor owned by an extra event, are named."""
    warns = []
    owned = lambda v: v in getattr(gen, "HIDDEN_EVENTS", {}) or gen.snake(gen.bare(v)) in getattr(gen, "EXTRA_EVENT_PAYLOADS", {})
    for cname in sorted(r.crates):
        c = r.crates[cname]
        for id, it in sorted(c.index.items()):
            if it.get("crate_id") != 0 or inner_kind(it) != "enum" or it.get("name") != "Event":
                continue
            path = c.item_path(id)
            d = next((x for x in defs.get("Event", []) if path and x.kind == "enum" and x.crate == c.name
                      and x.modpath == tuple(path[1:-1])), None)
            if d is None:
                continue
            sp = it.get("span") or {}
            fn = (sp.get("filename") or "").replace("\\", "/")
            f = Path(root) / (fn[len(prefix):] if prefix and fn.startswith(prefix) else fn)
            cfgs, why = None, ""
            try:
                text = f.read_text(encoding="utf-8")
                line = ((sp.get("begin") or [1])[0] or 1) - 1
                cfgs = enum_variant_cfgs("\n".join(text.split("\n")[line:]), "Event")
                why = "" if cfgs is not None else f"no `enum Event` at {fn}:{line + 1}"
            except (OSError, UnicodeDecodeError) as e:
                why = f"{fn}: {type(e).__name__}"
            cfgs = cfgs or {}
            unknown = []
            for v in d.variants:
                if v["name"] in cfgs:
                    v["attrs"] += [a for a in cfgs[v["name"]] if a not in v["attrs"]]
                elif not owned(v["name"]):
                    unknown.append(v["name"])
            if unknown:
                warns.append(f"enum Event ({'::'.join([c.name, *d.modpath])}): cannot read its `#[cfg]` gates ({why or 'variants not found in its source'}); "
                             f"these variants are exposed as ungated, check none is feature-gated: {', '.join(unknown)}")
    return warns


SERDE_SHAPE_KEYS = frozenset("rename rename_all rename_all_fields flatten tag content untagged transparent skip skip_serializing skip_deserializing "
                             "with serialize_with deserialize_with from try_from into variant_identifier field_identifier other".split())
SERDE_NEUTRAL_KEYS = frozenset({"deny_unknown_fields", "alias", "default", "skip_serializing_if"})


def serde_attr_bodies(attr: str) -> list:
    """The bodies of `serde(..)` in one attribute text (`#[serde(rename_all = "x")]`, or rustdoc's `#[attr = ..]` object text)."""
    return [m.group(1) for m in re.finditer(r"\bserde\s*\(((?:[^()]|\([^()]*\))*)\)", attr or "")]


def serde_shape(attrs) -> str:
    """\"plain\" (no key), \"optional-keys\" (only neutral keys that may drop a key) or \"any\" (a shape-changing or unknown key). Keys are the
    idents before `=` / `,` / `(` at the top level of each body; a serialize/deserialize split such as `rename(serialize = ..)` is its key `rename`."""
    keys = []
    for body in attrs or ():
        keys += re.findall(r"(?:^|,)\s*([A-Za-z_]\w*)", re.sub(r'"(?:[^"\\]|\\.)*"|\([^()]*\)|=\s*[^,]*', "", body))
    if any(k not in SERDE_NEUTRAL_KEYS for k in keys):
        return "any"
    return "optional-keys" if any(k in ("default", "skip_serializing_if") for k in keys) else "plain"



class RdjIndex:
    """The index the generator consumes, filled from rustdoc JSON. Attributes and methods:
    `defs mod_pub reexports methods goal_impls client_traits globs macro_notes files digest crates`, `aliases()
    chain_public() to_azalea() unique_def() resolve_path()`.
    `digest`/`files` come from hashing the sources anyway (see source_digest). `hidden`: see the treatment notes above `is_hidden`."""

    def __init__(self, root: Path, rdj_dir, hidden: str = "keep"):
        self.root, self.rdj_dir, self.hidden = Path(root), rdj_dir, hidden
        self.crates: dict = {}
        self.files = 0
        self.digest = None
        self.rdj = None
        self.clone_impls: set = set()
        self.display_impls: set = set()
        self.default_impls: set = set()
        self.impl_facts: dict = {}
        self.skipped_items: list = []
        self.discovered: dict = {}
        self.discover_skipped: list = []
        self.hidden_fns: list = []

    # trait path -> the legacy name set that tests and callers may still fill by hand
    _LEGACY_SETS = ((("core", "clone", "Clone"), "clone_impls"), (("core", "fmt", "Display"), "display_impls"),
                    (("core", "default", "Default"), "default_impls"))

    def implements(self, name: str, trait: str):
        """True/False/None: does the type `name` implement `trait` (canonical path like \"core::clone::Clone\", or a bare name)?
        True = a written or derived `impl Trait for T` header (not rustdoc's synthetic/blanket impls) whose trait is `trait` (see
        `trait_matches`: `fmt::Display` and `my::Display` are not conflated), or a `#[derive(..)]` naming it on a type that has
        no impl headers loaded, or the name sits in the matching legacy set (`clone_impls` ..). False = the type is defined in the
        loaded crates and has no such impl, or an explicit `impl !Trait`. None = no definition in the loaded crates (std, other crates)."""
        facts = [f for f in self.impl_facts.get(name, ()) if trait_matches(f, trait)]
        if any(f.negative for f in facts):
            return False
        if facts:
            return True
        want = _trait_segs(trait)
        for segs, attr in self._LEGACY_SETS:
            if name in getattr(self, attr) and trait_matches(ImplFact("::".join(segs), "", False), trait):
                return True
        ds = self.defs.get(name)
        if not ds:
            return None
        if name not in self.impl_facts and want:
            for d in ds:
                for a in d.attrs:
                    if a.startswith("#[derive") and want[-1] in IDENT.findall(a[len("#[derive"):]):
                        return True
        return False

    def implementors(self, trait: str, include_generic: bool = False) -> list:
        """Names of the types that implement `trait` (same match as `implements`: True only, so `impl !Trait`, unknown types,
        rustdoc's synthetic/blanket impls and `my::Display` for `core::fmt::Display` are not listed; a `#[derive(..)]`
        naming the trait counts). Source order of the impl items (file, line, column, id, crate); a type that is only known
        through a derive or a legacy name set follows them, in the order of `defs` (crate, module, line). No duplicates.
        Generic types (a Def with generic parameters; for a type without a Def, an impl with generic parameters) are left
        out unless `include_generic`. An empty or unusable `trait` -> []. Names, not paths: two types of one name in
        different crates are one entry. The feeder for generic dispatch (slice 24), like `implements` for the flags."""
        if not _trait_segs(trait):
            return []
        rank = {n: i for i, n in enumerate(self.defs)}
        names = (set(self.impl_facts) | set(self.defs) | self.clone_impls | self.display_impls | self.default_impls) - {""}
        found = []
        for name in names:
            if self.implements(name, trait) is not True:
                continue
            facts = [f for f in self.impl_facts.get(name, ()) if trait_matches(f, trait)]
            if not include_generic:
                ds = self.defs.get(name)
                if any(d.generics for d in ds) if ds else any(f.generic for f in facts):
                    continue
            at = sorted(f.at for f in facts if f.at)
            found.append(((0, at[0], name) if at else (1, (rank.get(name, len(rank)),), name), name))
        return [n for _, n in sorted(found)]

    def is_serialize(self, name: str):
        """True/False/None for `serde::ser::Serialize` (see `implements`; derived impls count)."""
        return self.implements(name, "serde::ser::Serialize")

    def is_deserialize(self, name: str):
        """True/False/None for `serde::de::Deserialize` (see `implements`; derived impls count)."""
        return self.implements(name, "serde::de::Deserialize")

    def serde_attrs(self, name: str):
        """The `serde(..)` attribute bodies visible on the type and its variants (`[]` = none visible, None = no definition). Field
        attributes are not kept in a `Def`, so `[]` means \"none seen\", not \"none exist\" (the caller treats it as unknown)."""
        ds = self.defs.get(name)
        if not ds:
            return None
        out = []
        for d in ds:
            for a in list(d.attrs or ()) + [x for v in d.variants or () for x in (v.get("attrs") or ())]:
                out += serde_attr_bodies(a)
        return out

    def is_clone(self, name: str):
        """True/False for `Clone` (see `implements`); None when the type has no definition in the loaded crates (std, other crates)."""
        return self.implements(name, "core::clone::Clone")

    def is_display(self, name: str):
        """Like `is_clone`, for `Display` (an `impl Display for T`, or `#[derive(Error)]` which writes one)."""
        r = self.implements(name, "core::fmt::Display")
        if r is False and any(re.search(r"\bError\b", a) for d in self.defs.get(name) or () for a in d.attrs if a.startswith("#[derive")):
            return True
        return r

    def is_default(self, name: str):
        """Like `is_clone`, for `Default` (`#[derive(Default)]` or a written `impl Default for T`)."""
        return self.implements(name, "core::default::Default")

    def load(self):
        need = source_crates(self.root)
        why = missing_json(self.root, self.rdj_dir)
        if why:
            raise RdjError(f"rustdoc front end: {why}")
        self.digest, self.files = source_digest(self.root)
        self.rdj = rdj.load_dir(self.rdj_dir)
        for w in self.rdj.warnings + stale_json(self.rdj, need):
            self.warn(w)
        sk = self.skipped_items
        self.defs = build_defs(self.rdj, skips=sk)
        top = "azalea" if "azalea" in need else sorted(need)[0]
        self.paths = RdjPaths(self.rdj, self.defs, root=top, roots=None if top == "azalea" else tuple(sorted(need)), skips=sk)
        self.crates = need
        try:
            prefix = workspace_prefix(self.rdj, top)
        except Exception:
            prefix = ""
        for w in event_cfg_gates(self.rdj, self.defs, self.root, prefix):
            self.warn(w)
        cands = self._class_candidates() if not gen.CLASSES else None
        hcands = self.handle_candidates() if (cands is None and getattr(gen, "HANDLES", {}).get("mode", "off") != "off") else set()
        impls = build_impls(self.rdj, classes=(set(gen.CLASSES) | hcands) if hcands else cands, strip_prefix=prefix, hidden=self.hidden, skips=sk)
        if cands is not None:
            impls.methods = self._keep_discovered(impls.methods) + self._free_fns()
        elif hcands:   # slice 22: a handle class gets its public inherent methods only (trait impls are not script API), like discovery
            impls.methods = [m for m in impls.methods if m.target in gen.CLASSES or (m.is_pub and m.owner == m.target)]
        self.methods = impls.methods
        self.goal_impls, self.client_traits, self.macro_notes = impls.goal_impls, impls.client_traits, impls.macro_notes
        self.clone_impls = impls.clone_impls
        self.display_impls = impls.display_impls
        self.default_impls = impls.default_impls
        self.impl_facts = impls.facts
        self.hidden_fns = impls.hidden
        self.mod_pub, self.reexports, self.globs = self.paths.mod_pub, self.paths.reexports, self.paths.globs

    def _class_candidates(self) -> set:
        """Type names that may become userdata classes: a plain (non-generic) pub struct/enum with one definition and a public path.
        The rest is listed in `discover_skipped` (name, reason)."""
        out = set()
        for name in sorted(self.defs):
            ds = [d for d in self.defs[name] if d.kind in ("struct", "enum")]
            if not ds:
                continue
            if len(ds) > 1 or len(self.defs[name]) > 1:
                self.discover_skipped.append((name, "several types of that name"))
            elif ds[0].generics:
                self.discover_skipped.append((name, "generic type"))
            elif self.resolve_path(name) is None:
                self.discover_skipped.append((name, "no public path"))
            else:
                out.add(name)
        return out

    def handle_candidates(self) -> set:
        """Type names that may become a handle class (slice 22): one definition, a non-generic struct with a private field or no field
        (a type that is not a plain value), a public path. Config (`[handles]`) and `Clone` are judged by the generator's Mapper."""
        out = set()
        for name in sorted(self.defs):
            if len(self.defs[name]) != 1:
                continue
            d = self.defs[name][0]
            if d.kind != "struct" or d.generics or (d.fields and all(f[2] for f in d.fields)):
                continue
            if self.resolve_path(name) is not None:
                out.add(name)
        return out

    def _keep_discovered(self, methods: list) -> list:
        """Classes = candidates with a pub inherent method (trait impls and private fns are not script API)."""
        keep = [m for m in methods if m.is_pub and m.owner == m.target]
        for name in sorted({m.target for m in keep}):
            self.discovered[name] = dict(rust=f"Lua{name}", luau=name, field="inner", renames={}, events=False, emit=True, deny={}, doc=f"Methods of `{name}`.")
        return keep

    def _free_fns(self) -> list:
        """Generic mode: every free `pub fn` with a public path -> a Method (receiver None, target `@<crate>` = the global table named
        after the crate, owner = its shortest public path, which is also the Rust call path). One class entry per crate that has any."""
        out = []
        for (cname, fid), paths in sorted(self.paths.public_paths().items(), key=lambda kv: (kv[0][0], sorted(kv[1])[0])):
            c = self.rdj.crate(cname)
            fn = None
            try:
                fn = c.item(fid)
                if fn is None or inner_kind(fn) != "function" or fn.get("crate_id") != 0:
                    continue
                if is_hidden(fn) and self.hidden == "skip":
                    continue
                path = min(paths, key=lambda q: (len(q), q))
                fp = payload(fn)
                sig = fp.get("sig") or {}
                recv, rest = split_receiver(sig.get("inputs"))
                outp = sig.get("output")
                out.append(gen.Method(
                    name=path[-1], doc=doc_summary(fn.get("docs")), is_pub=True,
                    is_async=bool((fp.get("header") or {}).get("is_async")),
                    generics=any(not ((gp.get("kind") or {}).get("type") or {}).get("is_synthetic")
                                 for gp in (fp.get("generics") or {}).get("params") or [] if isinstance(gp, dict)),
                    receiver=recv, params=[(param_name(n), convert_type(t, c)) for n, t in rest],
                    ret=convert_type(outp, c) if outp is not None else None,
                    owner="::".join(path), file="::".join(path), sig=fn_sig_text(fn, fp), target=f"@{path[0]}", type_params=type_params_of(fp)))
            except Exception as e:
                note_skip(self.skipped_items, c, fid, fn, e)
        for t in sorted({m.target for m in out}):
            crate = t[1:]
            self.discovered[t] = dict(rust="", luau=crate, field="", renames={}, events=False, emit=True, deny={}, free=True,
                                      doc=f"Free functions of crate `{crate}`.")
        return out

    def warn(self, msg: str) -> None:
        """One `warning: ...` line on stderr; never fatal. Every load-time check reports through here (slices 2+)."""
        print(f"warning: {msg}", file=sys.stderr)

    @property
    def frontend_label(self) -> str:
        """GEN_REPORT's `Front end:` line: the JSON's toolchain, from the `.meta` next to it (hand-built fixtures say so)."""
        c = (self.rdj.crates.get("azalea") or next(iter(self.rdj.crates.values()), None)) if self.rdj else None
        rustc = ((c.meta or {}).get("rustc") if c else None) or "rustc unknown"
        fvs = sorted({x.format_version for x in self.rdj.crates.values()}) if self.rdj else []
        return f"rustdoc JSON ({rustc}, format_version {'/'.join(map(str, fvs)) or '?'})"

    @property
    def unresolved(self) -> list:
        """`use` targets that could not be followed ((crate, path)): their crate is not loaded, or its JSON lacks the path (see
        `Rdj.not_found`); filled lazily by the path walk, so read it after the run."""
        return self.paths.unresolved

    def report_items(self) -> list:
        """The items of GEN_REPORT's "rustdoc front end: skipped items" section as data (empty = no section): items the JSON reader could
        not handle (never fatal) and `use` targets it could not follow. Deduplicated, sorted. Each is a dict with a `kind`
        (item | hidden_fn | type | reexport), the `subject` it is about and the `why`."""
        self.paths.public_paths()
        out = [dict(kind="item", subject=p, why=why) for p, why in sorted(set(self.skipped_items))]
        out += [dict(kind="hidden_fn", subject=f"{t}::{n}", why="#[doc(hidden)] fn skipped (--rdj-hidden skip)")
                for t, n, how in sorted({(t, str(n), h) for t, n, h in self.hidden_fns}) if how == "skipped"]
        out += [dict(kind="type", subject=n, why=f"not bound as a class ({why})") for n, why in sorted(set(self.discover_skipped))]
        nf = self.rdj.not_found if self.rdj else set()
        out += [dict(kind="reexport", subject=f"{crate}::{path}", crate=crate, path=path,
                     why=("not found in its loaded JSON" if (crate, path) in nf else "its crate has no loaded JSON")
                     + " (items behind it are not followed)")
                for crate, path in sorted(set(self.unresolved))]
        return out

    def report_lines(self) -> list:
        """`report_items` as the bullet lines of GEN_REPORT.md (human view; nothing parses them)."""
        out = []
        for it in self.report_items():
            if it["kind"] == "type":
                out.append(f"- type `{it['subject']}`: {it['why']}")
            elif it["kind"] == "reexport":
                out.append(f"- `{it['crate']}` re-exports `{it['path']}`: {it['why']}")
            else:
                out.append(f"- `{it['subject']}`: {it['why']}")
        return out

    def _safe(self, what, default, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            self.skipped_items.append((what, " ".join(f"{type(e).__name__}: {e}".split())[:200]))
            return default

    def aliases(self): return self._safe("aliases()", {}, self.paths.aliases)
    def chain_public(self, crate, modpath): return self._safe(f"chain_public({crate}, {modpath})", False, self.paths.chain_public, crate, modpath)
    def to_azalea(self, crate, modpath, name): return self._safe(f"{crate}::{'::'.join(modpath)}::{name}", None, self.paths.to_azalea, crate, modpath, name)
    def unique_def(self, name): return self._safe(f"unique_def({name})", None, self.paths.unique_def, name)
    def resolve_path(self, name, prefer=None): return self._safe(f"resolve_path({name})", None, self.paths.resolve_path, name, prefer)
    def missing_crates(self): return self._safe("missing_crates()", [], self.paths.missing_crates)
    @property
    def ambiguous(self) -> dict: return self.paths.ambiguous
