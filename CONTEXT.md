proto: 2
task: 4018424446
step_done: 4
state: DONE
digest:
plan: 1 Result<_,E:Display> | 2 is_default fact + option-struct detector | 3 emit option-struct FromLua + luau type | 4 wire ClientInformation, drop hand code, coverage.toml | 5 goal combinators via DynGoal (optional; skip if hard)
api:
- s1 RdjIndex.is_display(name)->True|False|None; Mapper.error_ok(errty)->bool
- s2 RdjIndex.is_default(name)->True|False|None (derive(Default) or impl Default); gen.option_struct(ix,name)->dict(name,fields=[(rust,luau,kind)]) or raises Skip(reason); kinds: bool|number|string|enum|nested
- s3 Mapper.option_structs: dict name->entry; generator emits Rust `impl FromLua for L<Name>` (table keys -> fields, rest Default, unknown key = error) + Luau type `{ field: T? }`
- s4 bindgen.toml `[option_structs]` name list (opt-in); extra_misc.rs keeps only goto_reach
now:
next: none
done:
- s1: is_display fact (rdj_index.py), Mapper.error_ok + Result<T,E:Display> accepted (luaxite_gen.py), tests in mapper_selftest.py
- s2: is_default fact + Mapper.option_struct detector + tests
- s3: use_option_struct/nested_option_struct + emit_option_struct (Rust FromLua) + luau_option_struct, wired in emit_types/emit_dluau, exact-text tests
- s4: [option_structs] config key + OPTION_STRUCTS + Mapper.named param hook + bindgen.toml ClientInformation; harness reports refreshed; rdj_selftest key list; ALLOWED_DIFF entry
- s5: skipped (goal combinators), see why
touched: bindgen.toml tools/luaxite_gen.py tools/mapper_selftest.py tools/rdj_selftest.py tools/golden/ALLOWED_DIFF.md harness/luau/GEN_REPORT.json harness/luau/GEN_REPORT.md CACHE.md
tried:
why:
- A: s5 goal combinators NOT automated: AndGoal/OrGoal/InverseGoal are exposed as variadic hand API goal.all/any/inverse (real_goal.rs); a generated 2-arg constructor per generic would be a worse API and DynGoal substitution gives no gain; ledger rows stay `hand`
- A: hand LClientInformation + set_client_information kept: generated version cannot be compiled here (no cargo) and differs in behavior (hand accepts float view_distance, long doc string); generator is ready (listed in [option_structs]); switch = delete hand fn + coverage row + regen harness, needs cargo
- A: PROMPT.md = user's 3 lines verbatim + appended context (chain has no memory; bare text meaningless)
- A: "16≤ requirement if too thin" read as: no filler steps, DONE early allowed (<=6 slices anyway)
- A: nested option structs done in s3 (field = plain path to Default struct -> nested table, cycle guard opt_stack); other fields via map_ty(param)
- A: Rust field names via rust_ident (r#type); Luau type name = Rust struct name, must not clash with taken names/records/enums
- A: Opts wrapper named Lua<Name>Opts so it cannot clash with Lua<Name> records
- A: Display unknown (None) = refuse, conservative; MissingComponentError kept explicitly so fake golden output is unchanged
- A: no cargo here, so Rust emission verified by exact-text asserts only
open:
- optional follow-up needing cargo: remove hand LClientInformation/ModelCustomization glue + set_client_information (extra_misc.rs), delete its coverage.toml row, regenerate harness outputs, build harness; real-azalea generation unchecked
broken:
verify: python3 tools/mapper_selftest.py && python3 -m unittest discover -s tests/py -> OK mapper (105 checks); unittest 396 tests, only the 2 baseline golden FAILs @4
