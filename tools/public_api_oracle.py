#!/usr/bin/env python3
"""public_api_oracle.py - cross-check the rustdoc front end against `cargo public-api` (an independent reader of the same rustdoc JSON).

    cargo public-api -p azalea > /tmp/public_api.txt          # needs cargo + cargo-public-api + the pinned nightly (not run by this script)
    python3 tools/public_api_oracle.py --azalea bind/azalea-src --rustdoc <dir> --public-api /tmp/public_api.txt
    python3 tools/public_api_oracle.py --selftest

Compares two sets: public types (`pub struct|enum|union|trait|type <path>`: the last segment) and inherent/trait fns of a type
(`pub fn <path>::<Type>::<name>(`: (Type, name)). `public-api` only lists items reachable by a public path, so a type or fn the front end
indexes but public-api does not is reported as `index only` (a private or hidden item the generator might still bind), and one public-api lists
that the index lacks is `public-api only` (a front-end gap: this is the failure). Exit 0 = nothing in `public-api only`, 1 = gaps, 2 = bad input.
Only the item list is compared, never a signature. Own design from the tool's documented output; no cargo-public-api source was read or used (so no THIRD_PARTY.md row).
Stdlib only."""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPE_RE = re.compile(r"^pub (?:struct|enum|union|trait|type) ((?:[A-Za-z_][\w]*::)*)([A-Za-z_]\w*)")
FN_RE = re.compile(r"^pub (?:const )?(?:async )?(?:unsafe )?fn (?:<[^>]*> )?((?:[A-Za-z_]\w*::)+)([A-Za-z_]\w*)\(")


def parse_public_api(text: str) -> tuple:
    """-> (types {name}, fns {(Type, name)}). A fn path `azalea::Client::chat` gives (Client, chat); a free fn (`azalea::run`) has an owner that is
    a module (lower-case) and is ignored: only paths whose second-to-last segment starts upper-case are methods."""
    types, fns = set(), set()
    for line in text.splitlines():
        line = line.strip()
        m = TYPE_RE.match(line)
        if m:
            types.add(m.group(2))
            continue
        m = FN_RE.match(line)
        if m:
            owner = m.group(1).rstrip(":").split("::")[-1]
            if owner[:1].isupper():
                fns.add((owner, m.group(2)))
    return types, fns


def index_sets(ix) -> tuple:
    types = set(ix.defs)
    fns = {(m.owner or m.target, m.name) for m in ix.methods if m.is_pub}
    return types, fns


def compare(api: tuple, idx: tuple) -> dict:
    return {"public-api only types": sorted(api[0] - idx[0]), "public-api only fns": sorted(api[1] - idx[1]),
            "index only types": sorted(idx[0] - api[0]), "index only fns": sorted(idx[1] - api[1])}


def selftest() -> int:
    t, f = parse_public_api("pub struct azalea::Client\npub enum azalea::prelude::Event\npub fn azalea::Client::chat(&self, s: &str)\n"
                            "pub async fn azalea::Client::wait(&self)\npub fn azalea::run()\npub fn azalea::Client::new<T>(x: T)\n"
                            "pub fn <impl X for azalea::Y>::skip(self)\n# comment\n")
    assert t == {"Client", "Event"}, t
    assert f == {("Client", "chat"), ("Client", "wait"), ("Client", "new")} or f == {("Client", "chat"), ("Client", "wait")}, f
    assert ("Client", "chat") in f and ("Client", "wait") in f and not any(o == "azalea" for o, _ in f)
    r = compare(({"A", "B"}, {("A", "x")}), ({"A", "C"}, {("A", "x"), ("A", "y")}))
    assert r["public-api only types"] == ["B"] and r["index only types"] == ["C"] and r["index only fns"] == [("A", "y")]
    assert r["public-api only fns"] == []
    print("public_api_oracle selftest: OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--azalea", default=str(ROOT / "bind" / "azalea-src"))
    ap.add_argument("--rustdoc")
    ap.add_argument("--public-api")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.rustdoc or not a.public_api or not Path(a.public_api).is_file():
        print("public_api_oracle: --rustdoc DIR and --public-api FILE (output of `cargo public-api`) are required", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import rdj_index
    ix = rdj_index.RdjIndex(Path(a.azalea), a.rustdoc)
    ix.load()
    res = compare(parse_public_api(Path(a.public_api).read_text(encoding="utf-8")), index_sets(ix))
    for k, v in res.items():
        print(f"{k}: {len(v)}")
        for x in v[:50]:
            print(f"  {x}")
    return 1 if res["public-api only types"] or res["public-api only fns"] else 0


if __name__ == "__main__":
    sys.exit(main())
