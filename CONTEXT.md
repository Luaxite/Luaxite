proto: 2
task: 4018424446
step_done: 2
state: IN_PROGRESS
digest:
plan: 1 Result<_,E:Display> | 2 is_default fact + option-struct detector | 3 emit option-struct FromLua + luau type | 4 wire ClientInformation, drop hand code, coverage.toml | 5 goal combinators via DynGoal (optional; skip if hard)
api:
- s1 RdjIndex.is_display(name)->True|False|None; Mapper.error_ok(errty)->bool
- s2 RdjIndex.is_default(name)->True|False|None (derive(Default) or impl Default); gen.option_struct(ix,name)->dict(name,fields=[(rust,luau,kind)]) or raises Skip(reason); kinds: bool|number|string|enum|nested
- s3 Mapper.option_structs: dict name->entry; generator emits Rust `impl FromLua for L<Name>` (table keys -> fields, rest Default, unknown key = error) + Luau type `{ field: T? }`
- s4 bindgen.toml `[option_structs]` name list (opt-in); extra_misc.rs keeps only goto_reach
now:
next: s3: in tools/luaxite_gen.py add Mapper.option_structs dict (filled by a new Mapper.use_option_struct(name) that calls option_struct(name), caches entry, touches ("type",name)) and in emit_types (L~1499) emit for each entry `struct LOpt<Name>(pub <path>);` + `impl FromLua for LOpt<Name>` (copy the style of the hand code in rust_binds/luau/extra_misc.rs LClientInformation: table or error, `<path>::default()`, each key read as Option<field TypeInfo.rust> via `t.get`, assign with field from_lua expr, unknown key = lua_err listing known keys, wrong type = error naming field). Add luau type decl in emit_dluau: `export type <Name> = { field: T? }`. Only emitted when entries exist, so fake-harness output stays unchanged. Tests: exact-text asserts in tools/mapper_selftest.py (inject def like s2 tests). No rust compile available.
done:
- s1: is_display fact (rdj_index.py), Mapper.error_ok + Result<T,E:Display> accepted (luaxite_gen.py), tests in mapper_selftest.py
- s2: is_default fact + Mapper.option_struct detector + tests
touched: tools/rdj_index.py tools/luaxite_gen.py tools/mapper_selftest.py CACHE.md
tried:
why:
- A: PROMPT.md = user's 3 lines verbatim + appended context (chain has no memory; bare text meaningless)
- A: "16≤ requirement if too thin" read as: no filler steps, DONE early allowed (<=6 slices anyway)
- A: option_struct nested option structs deferred: a field must already map via map_ty(param) (scalars, unit enums, classes)
- A: Display unknown (None) = refuse, conservative; MissingComponentError kept explicitly so fake golden output is unchanged
- A: no cargo here, so Rust emission verified by exact-text asserts only
open:
broken:
verify: python3 tools/mapper_selftest.py && python3 -m unittest discover -s tests/py -> OK mapper (89 checks); unittest 396 tests, only the 2 baseline golden FAILs @2
