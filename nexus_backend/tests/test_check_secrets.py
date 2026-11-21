import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_secrets  # noqa: E402


@pytest.fixture()
def tree():
    # A temp directory inside the repo is git-ignored, and git-ignored files are
    # intentionally skipped — so these cases use the system temp directory.
    root = Path(tempfile.mkdtemp(prefix="secrets-scan-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_private_key_block_is_reported(tree):
    _write(
        tree,
        "deploy/key.pem",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\n",
    )
    findings = check_secrets.scan(tree)
    assert [(path, rule) for path, _, rule in findings] == [("deploy/key.pem", "private-key-block")]


def test_dsn_with_password_is_reported_but_placeholder_is_not(tree):
    _write(
        tree,
        "deploy/live.env",
        "ACCOUNTING_DATABASE_URL=postgresql://app:S3cretPassw0rd@db.internal:5432/books\n",
    )
    _write(
        tree,
        "deploy/template.env",
        "ACCOUNTING_DATABASE_URL=postgresql://app:REPLACE_PASSWORD@YOUR_POOLER:5432/books\n",
    )
    findings = check_secrets.scan(tree)
    assert [path for path, _, _ in findings] == ["deploy/live.env"]


def test_allowlist_comment_suppresses_a_fixture(tree):
    _write(
        tree,
        "nexus_backend/tests/test_fixture.py",
        'TOKEN = "xoxb-1234567890-abcdefghij"  # pragma: allowlist secret\n',
    )
    _write(tree, "scripts/app.py", 'TOKEN = "xoxb-1234567890-abcdefghij"\n')
    findings = check_secrets.scan(tree)
    assert [path for path, _, _ in findings] == ["scripts/app.py"]


def test_test_paths_are_allowlisted_by_rule(tree):
    _write(
        tree,
        "nexus_backend/tests/test_more.py",
        "PRIVATE = '-----BEGIN PRIVATE KEY-----'\n",
    )
    assert check_secrets.scan(tree) == []


def test_clean_tree_reports_nothing(tree):
    _write(tree, "src/app.ts", "export const name = 'kuaiji';\n")
    assert check_secrets.scan(tree) == []


@pytest.mark.parametrize(
    "line",
    [
        'api_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        "password='0123456789abcdef0123456789abcdef'",
    ],
)
def test_long_assigned_secrets_are_reported(tree, line):
    _write(tree, "src/config.py", line + "\n")
    findings = check_secrets.scan(tree)
    assert [rule for _, _, rule in findings] == ["hardcoded-secret-assignment"]
