#!/usr/bin/env python3
"""Scan the files Git tracks for committed secrets. Exits 1 when something is found, 0 otherwise.

    python scripts/secret_scan.py            # tracked files (what CI runs)
    python scripts/secret_scan.py PATH ...   # specific files, e.g. before `git add`

Rules:
- Known credential formats: Anthropic, AWS, GitHub, Shopify, Slack, Stripe, Google API keys, private key blocks.
- `NEXT_PUBLIC_*` names that look like secrets (they are bundled into the browser, see ADR-018).
- Secret-looking names (SECRET, PASSWORD, TOKEN, API_KEY, ...) assigned a long, high-entropy literal value.

Findings that are known to be safe (test fixtures, documented placeholders) go in `.secret-scan-allow` at the repo
root, one per line as `path-glob:rule[:sha256-prefix-of-the-matched-text]`. Without the hash the whole file is
allowed for that rule, so prefer the hash. The scanner prints the line to add for every finding. Matched values are
never printed in full.

Standard library only. This is a guard against mistakes, not a replacement for GitHub secret scanning or a
dedicated tool such as gitleaks; it only sees the current tree, not history.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / ".secret-scan-allow"
MAX_BYTES = 1_000_000
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".pt",
    ".gz",
    ".zip",
    ".xlsx",
    ".lock",
}
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}

PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic-key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "shopify-token": re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "stripe-key": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"),
    "public-env-secret": re.compile(
        r"\bNEXT_PUBLIC_[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE|API_KEY|ACCESS_KEY|CREDENTIAL)[A-Z0-9_]*"
    ),
}

# NAME = "value", NAME: value, NAME=value (env files, YAML, Python, TS). The name must look like a secret.
ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>[A-Z0-9_.\-]*(?:secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key|signing[_-]?key)[A-Z0-9_]*)
    ["']?\s*(?::|=|:=|=>)\s*
    (?P<q>["'`]?)(?P<value>[^\s"'`#,;]{20,})(?P=q)
    """
)
PLACEHOLDER = re.compile(
    r"(?i)change[-_]?me|replace|example|placeholder|your[-_]|dummy|sample|redacted|xxxx|\*\*\*|<[^>]+>|\$\{|\$\(|"
    r"process\.env|os\.environ|settings\.|secrets\.|getenv|^env\.|\{\{"
)
ENTROPY_MIN = 3.5
# In source code an unquoted right-hand side is an expression (a call, a variable), not a literal.
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    fingerprint: str
    preview: str

    def allow_line(self) -> str:
        return f"{self.path}:{self.rule}:{self.fingerprint}"


def entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def mask(text: str) -> str:
    return text if len(text) <= 8 else f"{text[:4]}…{text[-2:]} ({len(text)} chars)"


def scan_text(path: str, text: str) -> list[Finding]:
    findings = []
    code = Path(path).suffix.lower() in CODE_SUFFIXES
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                shown = m.group(0) if rule in ("public-env-secret", "private-key") else mask(m.group(0))
                findings.append(Finding(path, lineno, rule, fingerprint(m.group(0)), shown))
        for m in ASSIGNMENT.finditer(line):
            value = m.group("value")
            if code and not m.group("q"):
                continue
            if "$" in value or "{" in value:
                continue  # a shell or template reference to a value kept elsewhere
            if PLACEHOLDER.search(value) or entropy(value) < ENTROPY_MIN:
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?", value) and not re.search(r"\d", value):
                continue  # an identifier or attribute access, not a literal
            findings.append(
                Finding(path, lineno, "secret-assignment", fingerprint(value), f"{m.group('name')}={mask(value)}")
            )
    return findings


def load_allowlist(path: Path = ALLOW_FILE) -> list[tuple[str, str, str | None]]:
    entries = []
    if not path.exists():
        return entries
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            raise SystemExit(f"{path.name}: bad entry {raw!r} (expected path-glob:rule[:hash])")
        entries.append((parts[0], parts[1], parts[2] if len(parts) == 3 else None))
    return entries


def allowed(f: Finding, allowlist: list[tuple[str, str, str | None]]) -> bool:
    return any(
        fnmatch.fnmatch(f.path, glob) and rule in (f.rule, "*") and (fp is None or f.fingerprint.startswith(fp))
        for glob, rule, fp in allowlist
    )


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout  # noqa: S607
    return [p for p in out.decode().split("\0") if p]


def scan_paths(paths: list[str]) -> list[Finding]:
    findings = []
    for rel in paths:
        p = ROOT / rel
        if p.suffix.lower() in SKIP_SUFFIXES or p.name in SKIP_NAMES or not p.is_file() or p.is_symlink():
            continue
        if p.stat().st_size > MAX_BYTES:
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        findings.extend(scan_text(rel, data.decode("utf-8", errors="replace")))
    return findings


def main(argv: list[str]) -> int:
    paths = [str(Path(a).resolve().relative_to(ROOT)) for a in argv] if argv else tracked_files()
    allowlist = load_allowlist()
    findings = [f for f in scan_paths(paths) if not allowed(f, allowlist)]
    for f in findings:
        print(f"{f.path}:{f.line}: {f.rule}: {f.preview}")
        print(f"    if this is safe, add to .secret-scan-allow: {f.allow_line()}")
    if findings:
        print(f"\n{len(findings)} possible secret(s) found in {len(paths)} files.", file=sys.stderr)
        return 1
    print(f"No secrets found in {len(paths)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
