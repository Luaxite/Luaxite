24+ step Task
that:
Extraxts their source codes
See if anything is usable
Port it
Implement it

the goal is to extend the generator itself so whole classes of unsupported signatures get bound, not to patch individual functions

do not store my token.

## Context (added by the entry AI; the block above is the user's text verbatim, minus the first words "Okay, make a" addressed to the entry AI; everything below is derived from it)
Project: Luaxite (Luau bots on azalea via mlua). Generator = tools/luaxite_gen.py (Mapper.map_ty/path_ty/named/build_named, Generator.build_method, emit_*), rustdoc-JSON front end tools/rdj.py + tools/rdj_index.py (+ tools/rdj_synth.py synthetic fixtures), config bindgen.toml, ledger tools/coverage.toml, tests tests/py + tools/*_selftest.py, verify tools/check.sh, golden tools/golden.sh. Hand glue: rust_binds/luau/extra_*.rs real_*.rs.
Today the mapper skips: tuples, arrays, &T params/returns (only &str param), tuple structs, Box/Rc/Cow/lock wrappers, Duration/char/PathBuf, generic methods, no-&self fns, closure params, struct/enum PARAMETERS (records and tagged enums are return-only; only option structs w/ Default are params), non-configured opaque types. Skips are free-text; no frequency report. mlua feature `serialize` is on; azalea feature `serde` is default-on (many types derive Serialize/Deserialize).
"Their source" = other binding generators (any target language). Findings so far: Egui.NET egui_net_bindgen (rustdoc JSON->C#; handle/value/pointer classification, getter/setter pairing, generics, find_public_paths, merge_crates), juxc-bindgen (rustdoc->stub IR, naming, skip taxonomy), mochi-rust (rustdoc->typemap->wrappers; monomorphise manifest, SkipReason), cargo-public-api/public-api + trustfall-rustdoc (rustdoc edge cases, format versions). No Lua generator for foreign crates exists; nothing drops in (Rust/Go vs Python): port algorithms to stdlib Python.

## PROTOCOL OVERRIDE (user-authorized; PROMPT.md outranks the chain protocol's numeric limits)
- Chain length: no cap of 12 (that cap is void). One AI per step. Base plan = the 30 slices below (replaces "<=6 slices"); a hard slice is split into pieces A-Z (SPLIT RULE), so total AIs = 30 + extra pieces (max 30*26). Step 1 copies the `plan:` line below verbatim into CONTEXT.
- step_done = number of AIs that have run (one per slice or piece). In `lint`, replace the step_done check with: grep -qE '^step_done: [0-9]+$' CONTEXT.md||echo step_done
- Void: "step_done=6 -> no edits; reply status" and "step 6 reached unfinished -> BLOCKED". Stop only on state DONE or BLOCKED. Final reply: `STEP <step_done>/<N> <state>`, N = number of entries now in `plan:` (a split slice counts its pieces). "6 failed tries on one problem -> BLOCKED" stays 6.
- SPLIT RULE (replaces skipping; a slice is never skipped, dropped or weakened): an AI that judges its slice not easy-medium, or that hits the loop breaker (3 failed approaches), must BEFORE further edits split it into pieces <N>A, <N>B ... up to <N>Z (as many as needed, <=26, ordered, each easy-medium, no nested splits). It rewrites that entry of `plan:` (e.g. `17 refs` -> `17A ref-params|17B ref-returns|17C ref-option`), adds `api` lines for the seams between pieces (`- s17B <spec>`), sets `next:` to the first unfinished piece, and does piece A in the same step. Work already done in the slice counts as piece A. The slice's item text applies to every piece; the slice is done when all its pieces are. Later AIs run the first unfinished piece as their step; `done`/`touched`/`why` entries and the commit message name the piece (`s17B`).
- `api` seams: N = owner slice or piece (s17, s17B). "Never add slices later" stays: splitting is not adding.
- Everything else in the protocol (frozen api, loop breaker, safety, CACHE <=150 lines) is unchanged.
plan: 1 clone | 2 licenses | 3 extract | 4 baseline | 5 gaps | 6 classify-eval | 7 getset-eval | 8 generics-eval | 9 paths-eval | 10 ir-eval | 11 implements | 12 implementors | 13 skip-codes | 14 classify-fn | 15 paths-port | 16 tuples | 17 refs | 18 tuple-structs | 19 wrappers | 20 value-params | 21 serde | 22 handles | 23 statics | 24 generic-methods | 25 callbacks | 26 properties | 27 retire-hand | 28 fixtures | 29 automation | 30 audit

## Rules specific to this task
- SHARE THE ZIP (user instruction, added at s10B, binding on every AI of the chain): at the end of EVERY step, zip the repo (with .git, without /tmp content, never a token or .env) as `Luaxite_step<piece>.zip` (e.g. Luaxite_step10B.zip), put it in /mnt/user-data/outputs and share it with the user (present_files). A step without the shared zip is not finished.
- Network allowed ONLY: (a) anonymous `git clone --depth 1` (use `--filter=blob:none --sparse` + `git sparse-checkout set <dir>` for big repos) of the URLs in slice 1 into /tmp/ref; (b) optional Rust toolchain into /tmp/rustenv via micromamba (github.com/mamba-org/micromamba-releases + conda-forge `rust`) if `cargo` is missing; (c) cargo crate downloads for harness/ only. Log each `[P] dep: ...`.
- TOKEN/SECRETS: no token is provided; never use or write any credential, Authorization header, credential helper, `gh` auth, or GitHub API call with auth. Never write a token-looking value into any file (repo, /tmp, git config, logs, CONTEXT, CACHE). Env var names only.
- Never clone GPL/AGPL/LGPL/unlicensed repos: ErrorNoInternet/ErrorNoWatcher, torch-client/torch-client, ejulien/FABGen, milyin/prebindgen. MPL-2.0 (uniffi-rs) = ideas only, no translation of its code.
- License gate (slice 2): port code only from repos whose LICENSE text in the clone is MIT/Apache-2.0/BSD/ISC/Unicode-3.0/Zlib. Else idea-only.
- Port = reimplement in this repo's Python/Rust glue, in repo style; never vendor their files. Every ported or idea-derived piece gets a row in root THIRD_PARTY.md (PROMPT asks for this doc): `our file::function | repo URL | commit | their file:lines | SPDX | ported|idea | notice kept`. Keep their copyright/NOTICE text where the license requires it.
- Extracted copies live only in /tmp/ref/extract (never committed).
- Generator output on harness/fake must not change unless a fixture exists for the new case; document every golden change in tools/golden/ALLOWED_DIFF.md. New behavior needs exact-text tests (mapper_selftest.py / rdj_selftest.py / tests/py) using synthetic rdj (tools/rdj_synth.py) when the fake azalea can't express it.
- Hand code is deleted only when its generated replacement has a test (and compiled in harness when cargo exists); else keep it, log `open`.
- Real azalea (bind/azalea-src, nightly-2026-10-30) is not available: generation against it stays unchecked, log `open`. Harness (fake azalea, edition 2024) builds on stable >=1.85 when a toolchain exists.
- No push. Commits local only. Do not touch vendor/. Baseline has 2 known golden FAILs: do not worsen, do not weaken tests.
- A hard slice is never skipped: split it (SPLIT RULE). Parts that cannot be verified here (real azalea, no cargo) -> `open`, not skipped. Each AI does ONE slice or piece = easy-medium work.

## Plan = 30 base slices, one AI each; any slice may be split into pieces A-Z, one AI each (phases A-F below are labels only, not pieces)
### A acquire, audit, baseline
1 Clone to /tmp/ref: DouglasDwyer/Egui.NET (sparse: egui_net_bindgen + LICENSE*), xdsswar/juxlang (sparse: crates/juxc-bindgen), mochilang/mochi-rust, cargo-public-api/cargo-public-api, obi1kenobi/trustfall-rustdoc, rust-lang/rustdoc-types, mozilla/uniffi-rs, rust-diplomat/diplomat, fzyzcjy/flutter_rust_bridge, Cysharp/csbindgen, 1Password/typeshare, specta-rs/specta, lenscas/tealr, StepanTheGreat/mlua_bindgen.
2 Per repo: commit hash, LICENSE file names, SPDX read from the text, permissive yes/no -> /tmp/ref/LICENSES.tsv; one-line [T] summary in CACHE.
3 Extract (copy to /tmp/ref/extract/<repo>/ with origin header) only: Egui.NET lib.rs bound_ty/bound_ty_name/gather_getters/gather_setter_pairs/cs_binding_generics/find_public_paths/merge_crates/CrateIdRemapper; juxc ty.rs model.rs naming.rs family.rs + ingest.rs type/generic parts; mochi bridge/typemap monomorphise errors rustdoc wrapper; public-api re-export/impl handling; trustfall adapters (format versions).
4 Baseline: run verify commands; save to /tmp/base/*.txt; record counts in `why`.
5 Gap catalogue: generator on harness/fake; GEN_REPORT skipped + every Skip(...) in luaxite_gen.py -> [T] table: skip category | where | candidate source | verdict pending; plus coverage.toml `hand` rows by category.
### B evaluate (decide per candidate: port | idea | reject; reason; license gate; write `spec:` lines (Python signature + tests) into CACHE [T] for the later slice)
6 Handle/value/reference classification (Egui bound_ty, HANDLE_TYPES) vs Mapper.named/build_named/CLASSES.
7 Getter/setter pairing (gather_getters, gather_setter_pairs) -> Luau fields.
8 Generics: cs_binding_generics, mochi monomorphise manifest, trait-implementer enumeration.
9 Paths/crates: find_public_paths, merge_crates, CrateIdRemapper, public-api re-exports, trustfall format versions vs rdj.py/rdj_index.py resolve_path/PATH_OVERRIDES.
10 IR + skip taxonomy + naming (juxc model/ty/naming, mochi SkipReason/typemap) and serde detection (Egui serde-reflection, typeshare/specta type model).
### C foundations (no behavior change on fake output except documented)
11 rdj_index: generic `implements(name, trait)`->True|False|None; rebuild is_display/is_default on it (API frozen). Tests.
12 rdj_index: `implementors(trait)` list (for generic dispatch). Tests.
13 Structured skips: Skip(reason, code=); taxonomy from slice 10; GEN_REPORT json/md gain skipped-by-code counts (ALLOWED_DIFF entry).
14 Mapper.classify(name)->value|scalar|handle|class|opaque with reason; pure, tested.
15 Port path/crate-merge improvements from slice 9 (or log idea-only); tests via rdj_synth.
### D value shapes (ret + param each; Luau types emitted; exact-text tests)
16 Tuples and [T;N] -> Luau arrays.
17 References: &T &[T] &String &Option<T> params; &str &T &[T] returns; &mut -> coded skip unless handle.
18 Tuple structs and newtypes (single field transparent, multi-field array).
19 Box/Rc/Cow<str>/Mutex/RwLock unwrap; Duration (seconds number), char, PathBuf, i128/u128 consistent with existing.
20 Value params: pub-field structs from table (required keys, no Default needed) and data-carrying enums from `{kind=...}` tables; missing/unknown key errors.
### E shapes
21 Serde path: Serialize/Deserialize facts -> LuaSerdeExt to_value/from_value for value types; bindgen.toml `[serde]` mode auto|off|allow-list; Luau type from fields; precedence vs [records].
22 Auto opaque handle classes for non-value types (private fields, Arc<RwLock<T>>): userdata wrapper + collected methods; deny/cap config.
23 No-&self associated fns -> `Class.fn` statics/constructors; &mut self methods via wrapper (document borrow model).
24 Generic methods <T: Trait>: `[monomorphise]` config + auto from implementors, suffixed names, max N.
25 Callback params impl Fn(A)->B / Box<dyn Fn>: Luau function wrapper (sync call, Send/'static, error mapping, may-yield note).
### F retire, verify, automate
26 Getter/setter pairs -> Luau fields (opt-in `property_pairs`).
27 Re-run coverage.py: flip/delete ledger rows the generator now covers; remove hand code only per rule above.
28 Fixtures: one harness/fake/azalea case per new shape (or synthetic rdj); regen harness outputs; golden + ALLOWED_DIFF; build harness + test.luau if cargo.
29 Automation: workflow that regenerates harness/luau + golden and fails on drift (no push step); skip-histogram usage in coverage.py; optional public-api oracle script comparing its item list to the index.
30 THIRD_PARTY.md complete; test in tests/py scanning repo (excl. .git, vendor, PROMPT.md) for token patterns built at runtime (never written literally) and for THIRD_PARTY.md row completeness; final audit.

## Done-when
All 30 slices done (a split slice is done when all its pieces are; none skipped); verify passes with no new failures vs the slice-4 baseline; each of the mapper gaps listed under Project has a coded skip OR a generated binding; THIRD_PARTY.md has a row per ported/idea piece; token scan clean; bindgen.toml/coverage.toml consistent.
## verify
sh tools/check.sh (SKIP_HARNESS=1 when no cargo) && python3 -m unittest discover -s tests/py && python3 tools/mapper_selftest.py && python3 tools/rdj_selftest.py && python3 tools/coverage.py --selftest
