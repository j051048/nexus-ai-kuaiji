"""Read-only identity for the complete application candidate.

The older AK05 fingerprint intentionally remains unchanged. This identity also
covers frontend, dependencies and deployment configuration; no Git write or
network operation is performed. Hashes describe local bytes, not an approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PREFIXES = (
    "src/",
    "e2e/",
    "services/",
    "api/",
    "config/",
    "third_party/",
    "tools/",
    "scripts/",
    ".github/",
    "public/",
    "deploy/",
    "nexus_backend/app/",
    "nexus_backend/migrations/",
    "nexus_backend/tests/",
)
ROOT_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "index.html",
    "vercel.json",
    "zbpack.json",
    "pytest.ini",
    "ruff.toml",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    ".gitignore",
    ".gitattributes",
    ".vercelignore",
    ".dockerignore",
    ".npmrc",
    ".nvmrc",
    ".node-version",
    ".python-version",
}
ROOT_PATTERNS = re.compile(
    r"(?:Dockerfile(?:\..+)?|docker-compose(?:\..+)?\.ya?ml|"
    r"(?:vite|vitest|playwright|eslint)\.config\.[^/]+|playwright\.[^/]+\.config\.ts|"
    r"tsconfig(?:\.[^/]+)?\.json|requirements(?:\.[^/]+|-[^/]+))\Z"
)


class CandidateError(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False, timeout=20)
    if result.returncode:
        raise CandidateError("Cannot inspect local Git candidate")
    return result.stdout


def selected_path(name: str) -> bool:
    parts = Path(name).parts
    if any(
        part in {"node_modules", "__pycache__", ".venv", ".codex", "tmp_test"} for part in parts
    ):
        return False
    base = parts[-1]
    if (base.startswith(".env") and not base.endswith(".example")) or base.endswith(
        (".pyc", ".pyo", ".log", ".clixml", ".local")
    ):
        return False
    if name.startswith(PREFIXES):
        return True
    if "/" not in name and base.startswith(".env") and base.endswith(".example"):
        return True
    if name in ROOT_FILES or ("/" not in name and ROOT_PATTERNS.fullmatch(name)):
        return True
    if name.startswith("nexus_backend/") and name.count("/") == 1:
        return bool(ROOT_PATTERNS.fullmatch(base)) or base in ROOT_FILES
    return False


def source_inventory(root: Path | None = None) -> list[tuple[str, str | None]]:
    root = Path(root or ROOT).resolve()
    names = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    result = []
    for name in sorted(set(names.decode("utf-8").split("\0")) - {""}):
        if not selected_path(name):
            continue
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise CandidateError("Candidate contains an external or symbolic source path")
        if path.exists() and not path.is_file():
            raise CandidateError("Candidate source is not a regular file")
        # A tracked deletion remains part of the candidate; never hash it as an
        # empty file or silently accept the previous checkout's evidence.
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        result.append((name, digest))
    if not result:
        raise CandidateError("Candidate contains no selected application sources")
    return result


def _digest(inventory) -> str:
    return hashlib.sha256(
        json.dumps(inventory, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def candidate_fingerprint(root: Path | None = None) -> dict:
    root = Path(root or ROOT).resolve()
    files = source_inventory(root)
    head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise CandidateError("Candidate Git revision is invalid")
    migrations = [item for item in files if item[0].startswith("nexus_backend/migrations/")]
    return {
        "schema": "release-candidate-v1",
        "head": head,
        "source_files": len(files),
        "source_sha256": _digest(files),
        "migration_files": len(migrations),
        "migration_sha256": _digest(migrations),
    }


def worktree_clean(root: Path | None = None) -> bool:
    root = Path(root or ROOT).resolve()
    return not _git(root, "status", "--porcelain", "--untracked-files=all").strip()
