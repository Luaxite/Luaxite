<p align="left"> <img src="https://raw.githubusercontent.com/Luaxite/Luaxite/main/logo.svg" width="100"> </p>

# Luaxite /ˈluː.ək.saɪt/

A small project that lets you code Minecraft bots in Luau instead of Rust, using [Azalea](https://github.com/azalea-rs/azalea).

It uses a generator to create Azalea bindings, though some bindings need to be handwritten.
You can add extra Cargo dependencies.

## Installation

### Linux, macOS, and Termux

```bash
curl -fsSL https://raw.githubusercontent.com/Luaxite/Luaxite/HEAD/install.sh | sh
```

### Windows

Run in PowerShell 5.1+:

```powershell
irm https://raw.githubusercontent.com/Luaxite/Luaxite/HEAD/install.ps1 | iex
```

## Running a script

```bash
luaxite                      # runs the script embedded at build time (src/main.luau)
luaxite path bot.luau        # runs bot.luau instead of main
luaxite path a.luau b.luau   # runs several scripts, each in its own Luau state, in one process
```

### Arguments

Every argument is passed, unchanged, to `process.args` of `require("@lune/process")`:

```bash
luaxite debug=true           # process.args == { "debug=true" }
luaxite debug true           # process.args == { "debug", "true" }
luaxite path bot.luau debug=true
```

`path` is the one keyword: the `.luau` / `.lua` files after it are the scripts to run instead of main, and are not arguments.
`-h` / `--help` as the first argument prints the usage.

The process exits when nothing is left to run (bots, swarms, tasks, servers, child processes), or when `process.exit` is called.

## Example

```luau
local bot = azalea.join { account = "Luaxite", address = "localhost" }

bot:on("spawn", function()
    bot:chat("spawned")
end)

bot:on("chat", function(packet)
    if packet.content == "foo" then
        bot:chat("bar")
    end
end)

bot:on("disconnect", function(reason)
    print("disconnected:", reason)
end)
```

## Luau

Scripts have access to azalea global and can `require` other files and `@lune/*` modules.
The nine Lune modules are also globals (`datetime`, `fs`, `luau`, `net`, `process`, `regex`, `serde`, `stdio`, `task`), the same tables `require("@lune/<name>")` returns. `process.exit(code)` is part of `process`.

`require` of files is relative to the script or module that calls it, not to the directory you run `luaxite` from:

```luau
local util = require("./lib/util")      -- lib/util.luau
local cfg = require("../shared/config") -- one directory up
local pkg = require("./pkg")            -- pkg/init.luau
```

It tries `.luau`, `.lua`, then `init.luau` / `init.lua`. A module runs once; later requires (by any path to the same file) get the cached value. Names starting with `./` or `../` look for a file first, then for a module embedded from `src/`; other names do the reverse.

With no `path`, `src/main.luau` (embedded at build time) is the script that runs.

## Binding your own Rust code

### Macro
Rust code under `rust/` (`main.rs` and any module it declares) can be exposed to scripts as Luau globals, with no glue:

`#[bind]` binds this function.
```rust
use luaxite_bind::bind;

#[bind]
pub fn Name() {
    // code here
}
```

### The crate's global table

Everything bound under `rust/` is a field of one global table, like `azalea`. Its name comes from the **top comment (line 1)**
of `rust/main.rs`:

`//BIND TNAME name`

```rust
//BIND TNAME luaxite
use luaxite_bind::bind;

#[bind]
pub fn add(a: i64, b: i64) -> i64 { a + b }   // Luau: luaxite.add(2, 3)
```

`name` must match `^(?!RESERVED$)[A-Za-z_][A-Za-z0-9_]*$`, RESERVED being the Luau keywords
(`and break do else elseif end false for function if in local nil not or repeat return then true until while`);
anything else stops `tools/bind_gen.py` with `error: rust/main.rs:1 TNAME ..` and nothing is bound.
Without `TNAME` the fns are flat globals.

There are no per-file types (`//BIND pub|all|macro` is gone): a fn is bound when it is marked `#[bind]`. Any other `//BIND` line is ignored with a warning.

To use the mlua instance itself, take `&mlua::Lua` as the first parameter (Luau does not see it);
other Rust code can call `luau::current_lua()`,
and a `Runtime` has `lua()`.

main (not required) runs on start once. cannot be binded.

#### Your own types (`#[bind(serde)]`)

A parameter or return of your own type is copied through serde when the fn says so:

```rust
use serde::{Deserialize, Serialize};
use luaxite_bind::bind;

#[derive(Serialize, Deserialize)]
struct Config { name: String, size: u32 }

#[bind(serde)]
fn area(c: Config) -> u32 { c.size * c.size }

#[bind(serde)]
fn defaults() -> Config { Config { name: "x".into(), size: 4 } }
```

```lua
print(area({ name = "a", size = 3 }))  -- 9
print(defaults().name)                 -- x
```

The type needs `Serialize` (return) / `Deserialize` (parameter); it is a copy in and out, with no methods and no shared state.
It can sit in `Option<T>` (nil is `None`), `Vec<T>` (a Luau sequence), nested (`Vec<Option<T>>`), or be the `T` of a `Result<T, E>` (Err raises a Luau error with E's text).
`None` and `()` come back as nil; a `None` inside a returned `Vec` leaves a hole in the Luau array. A bad shape (missing field, wrong type) raises a Luau error naming the field.
Not supported (the fn is left out, stderr `unsupported`): references (`&Config`), tuples, generics, `Option<Option<T>>`.
This replaces hand config such as `[records]` / `[from_table]` for such types. `raw` and `serde` on one attribute is a `conflict` on stderr and the fn is left out.

## Binding other crates

Crates get Luau bindings by sitting in `bind/`, nothing else: `bind/<name>/` with its own `Cargo.toml`, or a workspace checkout `bind/<name>-src/` that holds the crate `<name>/` (azalea is `bind/azalea-src/azalea`). Path crates those depend on are bound too.

## Lune

`require("@lune/<name>")` supports nine of Lune's modules:

- **Lune's own crates:** `fs`, `serde`, `regex`, `datetime`, `luau`
- **Reimplemented:** `net`, `process`, `stdio`, `task`

The second group is rewritten because Lune's versions depend on its
task scheduler, which doesn't build with mlua's `send` feature.
Their edge cases may differ from real Lune.
`@lune/roblox` is not supported. Requires the `lune` cargo feature
(on by default).

## API Documentation

The API documentation contains signatures, types, and definitions, but no usage examples.
Refer to the [Azalea documentation](https://azalea.matdoes.dev/azalea/) for usage examples.

[View the API documentation](https://github.com/Luaxite/Luaxite/releases/tag/api-latest).

## License

This project is licensed under the [MIT License](LICENSE.md).

## Slop?

yes, i made this entirely with Claude desktop app.
there wasn't an existing project that did this or any bindings gen for mlua.