# Luau bridge report

Source: `shapes @ 3d35c91523` (2 files)
Front end: rustdoc JSON (rustc 1.97.0 (2d8144b78 2026-07-07), format_version 57)

- **`Client` -> `LuaClient` / `Bot`:** 29 methods generated (0 async/yielding), 3 skipped
- **`Door` -> `LuaDoor` / `Door`:** 2 methods generated (0 async/yielding), 0 skipped
- **azalea -> Luau:** 2 events (`bot:on(...)`)
- **Types:** 2 generated conversions, 0 built-in

## Generated methods

### Client

`apply`, `blob`, `boxed`, `component_hunger`, `door`, `draw`, `draw_all`, `each_tick`, `elapsed`, `for_each`, `home`, `initial`, `later`, `load`, `locked`, `lookup`, `name`, `note`, `open`, `open_door`, `parse_u32`, `pos`, `rc_name`, `set_pos`, `shared`, `sum`, `tags`, `title`, `with_opt`

### Door

`bump`, `id`

## Hand-written methods (rust_binds/luau/extra_*.rs)

(none)

## Events

- `init` ()
- `tick` ()
- `update` () - hand-fired: Fires on every ECS update (at least once per game tick, usually faster). Only handlers that finish quickly should use it.

## Resolved public paths

- `Blob` -> `azalea::Blob`
- `Hunger` -> `azalea::Hunger`
- `Meters` -> `azalea::Meters`
- `Pair` -> `azalea::Pair`
- `Shape` -> `azalea::Shape`
- `Spec` -> `azalea::Spec`
- `Thirst` -> `azalea::Thirst`

## callback parameter types (Luau function, called synchronously)

- `() -> ()`
- `(number) -> boolean`
- `(number) -> string`
- `(string) -> ()`

## serde types (copied through mlua serde)

- `Blob`: any

## Skipped by code

| code | methods |
| --- | ---: |
| `excluded` | 1 |
| `impl_trait` | 1 |
| `reference` | 1 |

## Skipped methods

### Client

#### `wait_for` is hand-written (extra_*.rs) or generated elsewhere (1)

- `pub fn wait_for(&self, d: Duration)`

#### mutable reference type `&mut f32` (a `&mut` borrow has no Luau form; handle `&mut self` is s23) (1)

- `pub fn scale(&self, v: &mut f32)`

#### returns `impl Fn(u32) -> u32` (1)

- `pub fn make(&self) -> impl Fn(u32) -> u32`

### Door

(none)
