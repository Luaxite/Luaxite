proto: 2
task: 4018424446
step_done: 3
state: IN_PROGRESS
digest:
plan: 1 Result<_,E:Display> | 2 is_default fact + option-struct detector | 3 emit option-struct FromLua + luau type | 4 wire ClientInformation, drop hand code, coverage.toml | 5 goal combinators via DynGoal (optional; skip if hard)
api:
- s1 RdjIndex.is_display(name)->True|False|None; Mapper.error_ok(errty)->bool
- s2 RdjIndex.is_default(name)->True|False|None (derive(Default) or impl Default); gen.option_struct(ix,name)->dict(name,fields=[(rust,luau,kind)]) or raises Skip(reason); kinds: bool|number|string|enum|nested
- s3 Mapper.option_structs: dict name->entry; generator emits Rust `impl FromLua for L<Name>` (table keys -> fields, rest Default, unknown key = error) + Luau type `{ field: T? }`
- s4 bindgen.toml `[option_structs]` name list (opt-in); extra_misc.rs keeps only goto_reach
now:
next: s4: wire option structs to config + params. (a) tools/luaxite_gen.py: add table key `option_structs` (name = doc string) to BUILTIN_CONFIG tuple + _STR_TABLES (-> global OPTION_STRUCTS, declared like the other _STR_TABLES globals: grep PATH_OVERRIDES). (b) Mapper.path_ty/named: role=="param" and name in OPTION_STRUCTS -> e=self.use_option_struct(name); return TypeInfo(e["rust"], name, from_lua=lambda x: f"{x}.0"); other roles unchanged. (c) bindgen.toml: [option_structs] ClientInformation = "..." (ModelCustomization is picked up as nested automatically). (d) test in tools/mapper_selftest.py (inject def, set g.OPTION_STRUCTS, map_ty(P(name),"param")) + load_config validation test in tests/py if a config-table test exists (grep _STR_TABLES in tests). DO NOT delete hand code in extra_misc.rs / coverage.toml rows: real azalea can't be generated here (no nightly); hand names stay reserved (extras_reserved) so no clash; log in open. Generator output on fake harness must stay unchanged (golden hash for gen_client.rs stays 2428708c..).
done:
- s1: is_display fact (rdj_index.py), Mapper.error_ok + Result<T,E:Display> accepted (luaxite_gen.py), tests in mapper_selftest.py
- s2: is_default fact + Mapper.option_struct detector + tests
- s3: use_option_struct/nested_option_struct + emit_option_struct (Rust FromLua) + luau_option_struct, wired in emit_types/emit_dluau, exact-text tests
touched: tools/rdj_index.py tools/luaxite_gen.py tools/mapper_selftest.py CACHE.md
tried:
why:
- A: PROMPT.md = user's 3 lines verbatim + appended context (chain has no memory; bare text meaningless)
- A: "16≤ requirement if too thin" read as: no filler steps, DONE early allowed (<=6 slices anyway)
- A: nested option structs done in s3 (field = plain path to Default struct -> nested table, cycle guard opt_stack); other fields via map_ty(param)
- A: Rust field names via rust_ident (r#type); Luau type name = Rust struct name, must not clash with taken names/records/enums
- A: Opts wrapper named Lua<Name>Opts so it cannot clash with Lua<Name> records
- A: Display unknown (None) = refuse, conservative; MissingComponentError kept explicitly so fake golden output is unchanged
- A: no cargo here, so Rust emission verified by exact-text asserts only
open:
- hand LClientInformation / set_client_information (extra_misc.rs) + coverage.toml row stay until a real-azalea generation can be checked (needs nightly)
broken:
verify: python3 tools/mapper_selftest.py && python3 -m unittest discover -s tests/py -> OK mapper (100 checks); unittest 396 tests, only the 2 baseline golden FAILs (gen_client.rs sha 2428708c unchanged) @3
