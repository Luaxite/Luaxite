proto: 2
task: 4018424446
step_done: 1
state: IN_PROGRESS
digest:
plan: 1 Result<_,E:Display> | 2 is_default fact + option-struct detector | 3 emit option-struct FromLua + luau type | 4 wire ClientInformation, drop hand code, coverage.toml | 5 goal combinators via DynGoal (optional; skip if hard)
api:
- s1 RdjIndex.is_display(name)->True|False|None; Mapper.error_ok(errty)->bool
- s2 RdjIndex.is_default(name)->True|False|None (derive(Default) or impl Default); gen.option_struct(ix,name)->dict(name,fields=[(rust,luau,kind)]) or raises Skip(reason); kinds: bool|number|string|enum|nested
- s3 Mapper.option_structs: dict name->entry; generator emits Rust `impl FromLua for L<Name>` (table keys -> fields, rest Default, unknown key = error) + Luau type `{ field: T? }`
- s4 bindgen.toml `[option_structs]` name list (opt-in); extra_misc.rs keeps only goto_reach
now:
next: s2: in tools/rdj_index.py add default_impls (like display_impls: ImplData field, build_impls `trait == "Default"`, RdjIndex init+load assign) and is_default (True if in set or def attrs derive has Default; None if no def). Then in tools/luaxite_gen.py add option_struct(ix,name): struct def with all pub fields (Def.fields), is_default True, each field type bool/number prim/String/unit enum/other option struct else Skip(reason). Test in tools/mapper_selftest.py with a stub ix (pattern: DispIx). Do not change generator output on fake harness.
done:
- s1: is_display fact (rdj_index.py), Mapper.error_ok + Result<T,E:Display> accepted (luaxite_gen.py), tests in mapper_selftest.py
touched: tools/rdj_index.py tools/luaxite_gen.py tools/mapper_selftest.py PROMPT.md CACHE.md
tried:
why:
- A: PROMPT.md = user's 3 lines verbatim + appended context (chain has no memory; bare text meaningless)
- A: "16≤ requirement if too thin" read as: no filler steps, DONE early allowed (<=6 slices anyway)
- A: Display unknown (None) = refuse, conservative; MissingComponentError kept explicitly so fake golden output is unchanged
- A: no cargo here, so Rust emission verified by exact-text asserts only
open:
broken:
verify: python3 tools/mapper_selftest.py && python3 -m unittest discover -s tests/py -> OK mapper (79 checks); unittest 396 tests, only the 2 baseline golden FAILs @1
