proto: 2
task: 4018424446
## idx   path | purpose; symbols Lx-y | sig | prov@step (read|skim|inferred|unread)
rust/main.rs | bin entry: `use luaxite_bind::bind;` L3-5, parse_args, signal handler, run per Runtime | 1251627317/3373 | read@5
rust_binds/luau/mod.rs | glue (s10: CURRENT/TASK_LUA/current_lua near top, Runtime::lua; s9: `binds::register(&lua)` in with_source): Handlers, require shim, Runtime{lua,handlers} new/embedded/with_source L322-355 (Lua::new L346) | 686232666/12142 | skim@0
rust_binds/luau/ | generated.rs = OUT_DIR includes (gen_*, scripts; harness copy differs on purpose), convert, extra_*, real_* (feature real), lune_* (feature lune), generated.rs (OUT_DIR includes) | - | skim@0
rust_binds/luau/real_hub.rs | EMPTY (1 byte), declared mod.rs L30 (+harness copy), no refs; kept | 3515105045/1 | read@0
rust_binds/luau/real_packets.rs | packets glue; merge_injected L55-75, #[cfg(test)] 4 assert_eq! L77-103 (rustfmt-wrapped) | 3152814727/6352 | read@1
build.rs | runs tools/bindgen.py L66-90, then write_binds L62-91 (bind_gen -> OUT_DIR/binds.rs; EMPTY_BINDS L58), embeds src/**.luau -> OUT_DIR/scripts.rs; main L93; std only | 3368258663/5612 | read@8
Cargo.toml | bin luaxite=rust/main.rs; mlua 0.12 luau,async,send,macros; features real,packets,lune; path dep luaxite_bind=rust_binds/bind | 2002463076/5559 | read@5
harness/ | own workspace, fake azalea; runs test.luau; src/luau = copies of rust_binds/luau glue; STAYS PUT | - | skim@0
harness/src/luau/ | copies of mod,convert,extra_* + gen_*.rs; generated.rs differs on purpose | - | skim@0
harness/fake/ | fake azalea + rustdoc fixture | - | unread
tools/sync_harness.sh | cp rust_binds/luau/{mod,convert,extra_*}.rs -> harness/src/luau | 2044519931/454 | read@0
tools/check.sh | verify script; steps 1-6 incl harness build (skips w/o cargo) | 1839602376/7222 | skim@0
tools/bind_scan.py | bind scanner; FN named groups pub/mods/name L24; walk L75 close_of L94 split_top L102 parse_param L112 signature L124 (params,ret,body,generic) attrs_before L157 file_mode L176 brace_kind L190 enclosing L198 skip_reason L214 scan_text L227 (yields label,line,fn,mode,params,ret) scan_root L247 main L256 | 287084658/8970 | read@4
tools/ | bindgen*.py, luaxite_gen.py, rdj*.py, golden*, coverage*, fuzz/, testdata/ | - | unread
tests/py/ | stdlib unittest suite (_common.py ROOT, FAKE=harness/fake, run(), TOOLS on sys.path); 282 tests; test_packets_race.py L27 regex; test_bind_scan.py (29, s4) | 2200485911/1357 | read@1
rust_binds/bind/ | crate luaxite_bind: Cargo.toml (proc-macro, no deps), src/lib.rs `#[proc_macro_attribute] bind` passthrough | 465961607/691 | read@5
tools/bind_gen.py | register() generator; module_path L23 arg_ok L36 split_sig L41 statement L54 generate L68 main L88; imports bind_scan (split_top) | 485747434/4204 | read@7
tests/py/test_bind_gen.py | exact-text asserts on tmp trees (Text/Lua/Optional/Results/Cli), rustfmt syntax check (25) | 2740479566/9437 | read@7
tests/py/test_bind_wiring.py | static wiring of mod binds / register call / harness sync (5) | 1243414367/1708 | read@9
tests/py/test_bind_build_rs.py | static + real rustc build of build.rs on a scratch tree (6) | 2584029506/4479 | read@8
tests/py/test_bind_crate.py | static wiring asserts + a real stable cargo compile of the attr (skips w/o cargo) | 2656115712/3124 | read@5
tests/e2e/ | *.luau e2e + run.sh BIN SERVER | - | skim@0
src/ | .luau scripts embedded by build.rs (main.luau entry) | - | unread
bindgen.toml | bindgen config for azalea | - | unread
README.md | project readme | - | unread
.github/workflows/ | api-docs generator release rust rustdoc tests (no compile.yml); refs harness/; tests.yml runs unittest + check.sh | - | skim@1
## facts
[P] cmd: check=SKIP_HARNESS=1 sh tools/check.sh; py=python3 -m unittest discover -s tests/py (~70s)
[P] env@9: crates.io reachable via proxy: `cd harness && RUSTUP_TOOLCHAIN=stable cargo build` works (~45s), so `sh tools/check.sh` (no SKIP_HARNESS) runs the harness for real and passes; root build (azalea checkout bind/azalea-src) still not possible
[P] env: cargo 1.97 STABLE works (`RUSTUP_TOOLCHAIN=stable`, as tools/env.sh does), nightly cannot be fetched offline; dep-free crates can be built for real, azalea/mlua builds unverified; .env exists = secrets, never read
[P] conv: rustfmt style 4sp, edition 2024; stdlib-only python tools
[P] skip: .git vendor target __pycache__ *.lock logo.svg harness/fake/rustdoc/azalea.json .env
tools/rdj_index.py | rustdoc-JSON index; ImplData (clone/display/default_impls); RdjIndex.is_clone L785, is_display, is_default L813 (s1,s2) | 1822266347/47309 | read@2
tools/luaxite_gen.py | generator; Mapper.map_ty Result branch ~L430, error_ok, option_struct L463 (s2), not_clone; emit_types L1499 (enum/record wrappers: s3 adds option-struct FromLua here) | 1757231953/105440 | skim@2
tools/mapper_selftest.py | mapper checks vs harness/fake index; DispIx stub + Result tests L331 (s1); option-struct tests L349 (s2, injects defs into ix.defs + PATH_OVERRIDES); run by tests/py/test_bindings.py | 3717185199/21886 | read@2
tools/coverage.toml | ledger of unbound azalea APIs: hand/todo/no rows per class | - | skim@1
bindgen.toml | generator data tables (renames, records, accessors, hand_classes, handwritten_goals) | - | skim@1
rust_binds/luau/extra_misc.rs | hand glue: ClientInformation FromLua (option-struct, s4 target), goto_reach | - | skim@1
[P] cmd: unit=python3 -m unittest discover -s tests/py (~17s); mapper=python3 tools/mapper_selftest.py; check=SKIP_HARNESS=1 sh tools/check.sh
[P] env: no cargo/rustc in sandbox: generated Rust only checkable by exact-text tests
[P] conv: stdlib-only python, 4sp, rustfmt style rust
[P] skip: .git vendor target __pycache__ *.lock logo.svg
[T] baseline@1: unittest 396 tests, 2 pre-existing FAIL (test_generator GoldenScript.test_golden_compare_passes, Outputs.test_matches_golden_sha1_list: gen_client.rs sha) - not regressions
[T] fake ix lacks is_display facts; tests inject via DispIx stub
