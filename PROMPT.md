Make the automate able parts automated.

Each AI must do easy-medium work never hard.
Bypass 16≤ requirement if too thin.

## Context (added by the entry AI; the three lines above are the user's text verbatim)
Project: Luaxite (Luau bots on azalea). Generator = tools/luaxite_gen.py (type mapper `map_ty`) fed by the rustdoc-JSON front end (tools/rdj.py loader, tools/rdj_index.py index). Glue that the generator cannot make is hand-written in rust_binds/luau/extra_*.rs, real_*.rs and listed in tools/coverage.toml / bindgen.toml.
Finding: the rustdoc JSON already holds the facts; the blockers are mapper `Skip(...)` rules + missing index facts. "Automatable parts" =
 1. `Result<T, E>` for any `E: Display` (mapper now accepts only MissingComponentError).
 2. Option structs: pub-field struct + `Default`, fields bool/number/string/unit-enum/nested option struct -> generated table->struct `FromLua` (replaces hand-written ClientInformation / ModelCustomization in extra_misc.rs).
 3. Goal combinators generic over `impl Goal` (AndGoal, OrGoal, InverseGoal...) -> generated via DynGoal (real_goal.rs).
Not automatable (leave): Lua predicates (any_entity_by...), lifecycle/events, lune_*.rs, #[doc(hidden)] items, macro `pub use` paths (path_overrides).
Limits: stdlib-only python; no cargo/rustc here, so generated Rust is checked by exact-text tests only; keep generated Rust simple. Generator output on the fake harness must not change unless a fixture is added for the new case (tools/golden/ALLOWED_DIFF.md documents allowed diffs). Hand-written code is removed only when the generated replacement is covered by a test; coverage.toml rows flip/delete accordingly.
Done-when: slices 1-3 implemented (or a slice logged `open` as too hard and skipped), python unittests have no new failures vs baseline, coverage.toml/bindgen.toml consistent.
