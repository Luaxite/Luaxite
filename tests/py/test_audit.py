"""Slice 30: repo audit. (1) no credential-looking text anywhere in the tree (patterns are assembled at run time, so this file holds no
literal token and does not match itself; a hit prints path:line and the pattern name, never the text); (2) THIRD_PARTY.md has a complete,
well-formed row per ported/idea piece. Stdlib only."""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "vendor", "__pycache__", "target", "node_modules"}
SKIP_FILES = {"PROMPT.md"}   # the user's own text
MAX_BYTES = 5_000_000
SPDX_OK = {"MIT", "Apache-2.0", "MIT OR Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unicode-3.0", "Zlib"}


def token_patterns():
    """name -> compiled regex, built from fragments so no literal credential shape exists in this source."""
    j = "".join
    return {
        "github token": re.compile(j(["gh", "[pousr]", "_[A-Za-z0-9]{36,}"])),
        "github fine-grained token": re.compile(j(["github", "_pat_", "[A-Za-z0-9_]{22,}"])),
        "aws access key id": re.compile(j(["A", "KIA", "[0-9A-Z]{16}"])),
        "slack token": re.compile(j(["x", "ox", "[baprs]-[A-Za-z0-9-]{10,}"])),
        "api key (sk-)": re.compile(j(["\\bs", "k-", "[A-Za-z0-9]{32,}"])),
        "private key block": re.compile(j(["-----BEGIN ", "(?:RSA |EC |DSA |OPENSSH )?PRIVATE", " KEY-----"])),
        "authorization header value": re.compile(j(["Author", "ization:\\s*(?:Bearer|token|Basic)\\s+[A-Za-z0-9._~+/=-]{20,}"]), re.I),
        "url with credentials": re.compile(j(["https?://[^/\\s:@]+", ":[^/\\s@]{3,}", "@[A-Za-z0-9.-]+"])),
    }


def tree_files():
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            p = Path(d) / f
            if f in SKIP_FILES and d == str(ROOT):
                continue
            yield p


def scan(paths, pats):
    hits = []
    for p in paths:
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        for n, line in enumerate(raw.decode("utf-8", "ignore").splitlines(), 1):
            for name, rx in pats.items():
                if rx.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{n}: {name}")
    return hits


class TokenScan(unittest.TestCase):
    def test_tree_has_no_credentials(self):
        hits = scan(tree_files(), token_patterns())
        self.assertEqual(hits, [], "credential-looking text (the text itself is not printed):\n" + "\n".join(hits[:20]))

    def test_no_secret_files(self):
        bad = [str(p.relative_to(ROOT)) for p in tree_files() if p.name in (".env", ".netrc", "id_rsa", "id_ed25519", ".git-credentials") or p.suffix in (".pem", ".key")]
        self.assertEqual(bad, [])

    def test_git_config_has_no_credentials(self):
        cfg = ROOT / ".git" / "config"
        if not cfg.exists():
            self.skipTest("no .git/config")
        text = cfg.read_text(encoding="utf-8", errors="ignore").lower()
        for word in ("extraheader", "password", "credential", "token", "authorization"):
            self.assertNotIn(word, text, f".git/config mentions `{word}`")
        self.assertEqual(scan([cfg], token_patterns()), [])

    def test_the_scanner_finds_a_planted_token(self):   # the scan must be able to fail
        import tempfile
        pats = token_patterns()
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "x.txt"
            fake.write_text("t = " + "gh" + "p_" + "a1" * 18 + "\n")
            # scan() reports paths relative to ROOT, so check the matcher directly
            self.assertTrue(pats["github token"].search(fake.read_text()))
            self.assertTrue(pats["url with credentials"].search("https://" + "u:" + "secret1" + "@host.example"))
            self.assertFalse(pats["url with credentials"].search("https://github.com/x/y"))


def third_party_rows():
    rows = []
    for ln, line in enumerate((ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| our file"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        rows.append((ln, cells))
    return rows


class ThirdParty(unittest.TestCase):
    def test_rows_complete(self):
        rows = third_party_rows()
        self.assertGreaterEqual(len(rows), 20)
        problems = []
        for ln, c in rows:
            if len(c) != 7:
                problems.append(f"line {ln}: {len(c)} cells, want 7")
                continue
            ours, repo, commit, theirs, spdx, kind, notice = c
            if not all(c):
                problems.append(f"line {ln}: empty cell")
            if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", repo):
                problems.append(f"line {ln}: repo `{repo}` is not a github URL")
            if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
                problems.append(f"line {ln}: commit `{commit}` is not a hash")
            if not re.search(r"\d", theirs) or ":" not in theirs:
                problems.append(f"line {ln}: their file:lines `{theirs}` has no file:line")
            if spdx not in SPDX_OK:
                problems.append(f"line {ln}: SPDX `{spdx}` is not on the permissive list")
            if kind not in ("ported", "idea"):
                problems.append(f"line {ln}: kind `{kind}` is not ported|idea")
            if kind == "idea" and not notice.startswith("n/a"):
                problems.append(f"line {ln}: an idea row must say `n/a (...)` for the notice")
            m = re.match(r"([\w./-]+)", ours)
            if not m or not (ROOT / m.group(1)).exists():
                problems.append(f"line {ln}: our file `{ours[:40]}` does not exist")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_gpl_repo_in_the_table(self):   # PROMPT.md: never use GPL/AGPL/LGPL/unlicensed repos
        text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
        for repo in ("ErrorNoInternet/ErrorNoWatcher", "torch-client/torch-client", "ejulien/FABGen", "milyin/prebindgen"):
            self.assertNotIn(repo, text)


if __name__ == "__main__":
    unittest.main()
