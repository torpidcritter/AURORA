"""scripts/secret_scan.py: it reports credential formats and secret literals, and ignores code and placeholders."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "secret_scan.py"
_spec = importlib.util.spec_from_file_location("secret_scan", _SCRIPT)
assert _spec and _spec.loader
scan = importlib.util.module_from_spec(_spec)
sys.modules["secret_scan"] = scan  # dataclasses look the module up while the class is created
_spec.loader.exec_module(scan)

# Built from pieces so this file does not itself look like it contains credentials.
FAKE_ANTHROPIC = "sk-" + "ant-" + "api03-" + "Q7x9" * 8
FAKE_AWS = "AKIA" + "Z7Q2" * 4
FAKE_SHOPIFY = "shp" + "at_" + "0f9e8d7c" * 4
FAKE_GITHUB = "gh" + "p_" + "aB3dE5fG7h" * 4
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
PUBLIC_SECRET = "NEXT_PUBLIC_" + "SHOPIFY_API_" + "SECRET=abc"
PASSWORD_LINE = "pass" + 'word = "' + "Tr0ub4dor&3-horse-battery-staple" + '"'


def rules(path: str, text: str) -> list[str]:
    return [f.rule for f in scan.scan_text(path, text)]


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f'ANTHROPIC_API_KEY="{FAKE_ANTHROPIC}"', "anthropic-key"),
        (f"aws = '{FAKE_AWS}'", "aws-access-key"),
        (f"token: {FAKE_SHOPIFY}", "shopify-token"),
        (f"export GH={FAKE_GITHUB}", "github-token"),
        (PEM, "private-key"),
        (PUBLIC_SECRET, "public-env-secret"),
        ("SECRET_KEY=Zk3P9qL2mN8vR4tY7wX1cB6dF0gH5jK2", "secret-assignment"),
        (PASSWORD_LINE, "secret-assignment"),
    ],
)
def test_reports_secrets(text: str, rule: str) -> None:
    assert rule in rules("config/app.py" if "=" in text and '"' in text else ".env", text)


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("app/deps.py", "secret = get_settings().bff_shared_secret.get_secret_value()"),
        ("app/x.ts", "const token = await refreshTokens(cookieStore);"),
        (".env.example", "SECRET_KEY=change-me-to-a-long-random-string-please"),
        ("docker-compose.yml", "DB_PASSWORD: ${APP_DB_PASSWORD:?set APP_DB_PASSWORD}"),
        ("docs/x.md", "Set `SECRET_KEY=<64 random characters>` before starting."),
        (".env", "SECRET_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),  # low entropy
        (".env", "NEXT_PUBLIC_API_URL=https://api.example.com"),
    ],
)
def test_ignores_code_placeholders_and_public_values(path: str, text: str) -> None:
    assert rules(path, text) == []


def test_findings_never_print_the_full_value() -> None:
    [f] = scan.scan_text(".env", f"KEY={FAKE_ANTHROPIC}")
    assert FAKE_ANTHROPIC not in f.preview
    assert f.allow_line() == f".env:anthropic-key:{f.fingerprint}"


def test_allowlist_matches_by_glob_rule_and_hash(tmp_path: Path) -> None:
    [f] = scan.scan_text("tests/fixtures/x.py", f'secret = "{FAKE_ANTHROPIC}"')
    allow = tmp_path / "allow"
    allow.write_text(f"# comment\ntests/fixtures/*:anthropic-key:{f.fingerprint}\n")
    assert scan.allowed(f, scan.load_allowlist(allow))
    allow.write_text("tests/fixtures/*:anthropic-key:000000000000\n")  # a different value is still reported
    assert not scan.allowed(f, scan.load_allowlist(allow))
    allow.write_text("bad-entry\n")
    with pytest.raises(SystemExit):
        scan.load_allowlist(allow)


def test_repository_is_clean() -> None:
    """The same check CI runs: no unallowed findings in the files Git tracks."""
    files = scan.tracked_files()
    found = [f for f in scan.scan_paths(files) if not scan.allowed(f, scan.load_allowlist())]
    assert found == [], "\n".join(f"{f.path}:{f.line}: {f.rule}" for f in found)
