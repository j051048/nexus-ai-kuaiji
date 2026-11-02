"""Immutable declarative snapshots; hashes detect drift, not authorization.

No upstream source copied. The resource allowlist is a runner-owned constant,
not an OS sandbox. Untrusted prompt bytes are data, never Python or a command.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from tools.ocr_replay import replay

ALLOWLIST = ("mock.json", "prompt.txt")
SCHEMA = "rsi-snapshot-v1"
STRATEGIES = ("baseline", "gross-label-regression", "gross-label-safe")


class SnapshotError(ValueError):
    pass


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_id(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def atomic_create(path: Path, content: bytes):
    """Atomic create-if-absent; no replacement, even between processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".rsi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        os.unlink(temp)


def read(path: Path):
    return replay.read_json(path)


def validate_resources(resources):
    if type(resources) is not dict or set(resources) != set(ALLOWLIST):
        raise SnapshotError("resource_allowlist: only prompt.txt and mock.json")
    if any(
        type(v) is not str or not v.strip() or len(v.encode("utf-8")) > 32768
        for v in resources.values()
    ):
        raise SnapshotError("resource_size_or_type")
    try:
        settings = replay._json(resources["mock.json"])
    except replay.ReplayError as error:
        raise SnapshotError("invalid_settings") from error
    if (
        type(settings) is not dict
        or set(settings) != {"strategy"}
        or settings["strategy"] not in STRATEGIES
    ):
        raise SnapshotError("strategy_allowlist")
    # Refuse common accidental credential assignments; this is not a DLP claim.
    if re.search(
        r"(?i)(api[_-]?key|access[_-]?token|password)\s*[:=]\s*\S+", resources["prompt.txt"]
    ):
        raise SnapshotError("credential_assignment")
    return {"prompt.txt": resources["prompt.txt"], "mock.json": canonical(settings).decode()}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, candidate_id):
        if not valid_id(candidate_id):
            raise SnapshotError("invalid_candidate_id")
        path = self.root / (candidate_id + ".json")
        if path.resolve().parent != self.root or path.is_symlink():
            raise SnapshotError("snapshot_path_escape")
        return path

    def snapshot(self, resources, *, reason, parent=None):
        resources = validate_resources(resources)
        if type(reason) is not str or not 1 <= len(reason.strip()) <= 1000:
            raise SnapshotError("reason_required")
        if parent is not None and not valid_id(parent):
            raise SnapshotError("invalid_parent")
        ancestor = self.load(parent) if parent is not None else None
        content = digest(resources)
        identity = {"schema_version": SCHEMA, "content_sha256": content, "parent_id": parent}
        candidate_id = digest(identity)
        changed = [
            key for key in ALLOWLIST if not ancestor or ancestor["resources"][key] != resources[key]
        ]
        row = {
            **identity,
            "id": candidate_id,
            "root_id": ancestor["root_id"] if ancestor else candidate_id,
            "resources": resources,
            "changed_paths": changed,
            "reason": reason,
        }
        try:
            atomic_create(self._path(candidate_id), canonical(row))
        except FileExistsError:
            # Reason/display metadata is not an executable difference.
            old = self.load(candidate_id)
            if old["resources"] != resources:
                raise SnapshotError("snapshot_collision") from None
            return old
        return row

    def load(self, candidate_id, _seen=()):
        if candidate_id in _seen or len(_seen) >= 32:
            raise SnapshotError("lineage_cycle_or_depth")
        try:
            row = read(self._path(candidate_id))
        except (OSError, replay.ReplayError) as error:
            raise SnapshotError("snapshot_missing_or_invalid") from error
        keys = {
            "schema_version",
            "content_sha256",
            "parent_id",
            "id",
            "root_id",
            "resources",
            "changed_paths",
            "reason",
        }
        if type(row) is not dict or set(row) != keys or row["schema_version"] != SCHEMA:
            raise SnapshotError("snapshot_schema")
        if type(row["reason"]) is not str or not 1 <= len(row["reason"].strip()) <= 1000:
            raise SnapshotError("snapshot_reason")
        resources = validate_resources(row["resources"])
        identity = {k: row[k] for k in ("schema_version", "content_sha256", "parent_id")}
        if (
            row["id"] != candidate_id
            or digest(identity) != candidate_id
            or digest(resources) != row["content_sha256"]
        ):
            raise SnapshotError("snapshot_digest")
        ancestor = (
            self.load(row["parent_id"], (*_seen, candidate_id))
            if row["parent_id"] is not None
            else None
        )
        expected_root = ancestor["root_id"] if ancestor else candidate_id
        changed = [k for k in ALLOWLIST if not ancestor or resources[k] != ancestor["resources"][k]]
        if row["root_id"] != expected_root or row["changed_paths"] != changed:
            raise SnapshotError("lineage_binding")
        return row

    def diff(self, first, second):
        a, b = self.load(first), self.load(second)
        return [k for k in ALLOWLIST if a["resources"][k] != b["resources"][k]]

    def policy(self, candidate_id):
        row = self.load(candidate_id)
        strategy = json.loads(row["resources"]["mock.json"])["strategy"]
        base = replay.POLICIES["baseline" if strategy == "baseline" else "candidate"]
        return replace(
            base,
            name=candidate_id,
            version=candidate_id,
            prompt=row["resources"]["prompt.txt"],
            strip_invoice_zeros=strategy == "gross-label-regression",
        )

    def from_directory(self, directory: Path, *, reason, parent=None):
        directory = directory.resolve()
        files = list(directory.rglob("*"))
        if {p.relative_to(directory).as_posix() for p in files} != set(ALLOWLIST):
            raise SnapshotError("directory_allowlist")
        for path in files:
            if path.is_symlink() or path.resolve().parent != directory:
                raise SnapshotError("resource_path_escape")
            if not path.is_file() or path.stat().st_size > 32768:
                raise SnapshotError("resource_size_or_type")
        return self.snapshot(
            {key: (directory / key).read_text(encoding="utf-8") for key in ALLOWLIST},
            reason=reason,
            parent=parent,
        )


def dependencies(root: Path):
    """Bind actual reused implementation and RSI tests; excludes unrelated DCP."""
    paths = [
        *Path(__file__).parent.glob("*.py"),
        root / "tools/ocr_replay/replay.py",
        root / "tools/ai_inference_lab/examples.py",
        root / "scripts/evaluate_ocr_fields.py",
        *(root / "tools/ocr_replay/vendor").glob("*.py"),
        *(root / "nexus_backend/tests").glob("test_rsi*.py"),
        *(p for p in (root / "nexus_backend/tests/fixtures/rsi").rglob("*") if p.is_file()),
    ]
    return {p.relative_to(root).as_posix(): replay.sha256_path(p) for p in sorted(paths)}
