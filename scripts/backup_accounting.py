"""Consistent database + referenced immutable originals, verified new-target restore.

Credentials are read only from environment and never written to a manifest or
printed. A partial failed backup/restore is left for investigation, never erased.
"""

from __future__ import annotations
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

FORMAT_VERSION = 1
ORIGINALS_POLICY_VERSION = 3
ENCRYPTION_FORMAT = "nxbk-gcm-v1"
ENCRYPTION_ALGORITHM = "AES-256-GCM"
ENCRYPTION_MAGIC = b"NXBK1"


def _production_environment():
    return os.getenv("ACCOUNTING_ENV", "local").lower() in {"production", "prod"} or any(
        os.getenv(key, "").lower() in {"production", "prod"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    )


def _backup_key():
    """Return (key bytes, key id) or None; the secret never enters a manifest."""
    encoded = os.getenv("ACCOUNTING_BACKUP_KEY", "").strip()
    if not encoded:
        if _production_environment():
            raise ValueError("生产备份必须配置备份加密密钥 ACCOUNTING_BACKUP_KEY")
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("备份加密密钥不是有效的 base64") from error
    if len(key) != 32:
        raise ValueError("备份加密密钥必须是 32 字节（AES-256）")
    key_id = os.getenv("ACCOUNTING_BACKUP_KEY_ID", "").strip()
    if not key_id:
        key_id = "sha256:" + hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


def _encryption_key_for_manifest(manifest):
    encryption = manifest.get("encryption")
    if encryption is None:
        return None
    if (
        not isinstance(encryption, dict)
        or encryption.get("format") != ENCRYPTION_FORMAT
        or encryption.get("algorithm") != ENCRYPTION_ALGORITHM
        or not isinstance(encryption.get("key_id"), str)
        or not encryption["key_id"]
    ):
        raise ValueError("不支持的备份加密格式")
    loaded = _backup_key()
    if loaded is None:
        raise ValueError("加密备份需要配置备份加密密钥")
    return loaded[0]


def _encrypt_file(source, destination, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    destination = Path(destination)
    try:
        with Path(source).open("rb") as src, destination.open("xb") as dst:
            dst.write(ENCRYPTION_MAGIC + nonce)
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(encryptor.update(chunk))
            encryptor.finalize()
            dst.write(encryptor.tag)
        destination.chmod(0o600)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _decrypt_file(source, destination, key):
    """Stream-decrypt and write plaintext; never leave a partial destination."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    source = Path(source)
    destination = Path(destination)
    size = source.stat().st_size
    if size < len(ENCRYPTION_MAGIC) + 12 + 16:
        raise ValueError("加密备份文件过短")
    with source.open("rb") as src:
        header = src.read(len(ENCRYPTION_MAGIC) + 12)
        if not header.startswith(ENCRYPTION_MAGIC):
            raise ValueError("备份文件不是支持的加密格式")
        nonce = header[len(ENCRYPTION_MAGIC) :]
        src.seek(size - 16)
        tag = src.read(16)
        src.seek(len(ENCRYPTION_MAGIC) + 12)
        remaining = size - len(ENCRYPTION_MAGIC) - 12 - 16
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        digest = hashlib.sha256()
        written = 0
        try:
            with destination.open("xb") as dst:
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    plain = decryptor.update(chunk)
                    digest.update(plain)
                    dst.write(plain)
                    written += len(plain)
                if remaining:
                    raise ValueError("加密备份文件被截断")
                tail = decryptor.finalize()
                digest.update(tail)
                dst.write(tail)
                written += len(tail)
            destination.chmod(0o600)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return digest.hexdigest(), written


def _decrypt_digest(source, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    source = Path(source)
    size = source.stat().st_size
    if size < len(ENCRYPTION_MAGIC) + 12 + 16:
        raise ValueError("加密备份文件过短")
    with source.open("rb") as src:
        header = src.read(len(ENCRYPTION_MAGIC) + 12)
        if not header.startswith(ENCRYPTION_MAGIC):
            raise ValueError("备份文件不是支持的加密格式")
        nonce = header[len(ENCRYPTION_MAGIC) :]
        src.seek(size - 16)
        tag = src.read(16)
        src.seek(len(ENCRYPTION_MAGIC) + 12)
        remaining = size - len(ENCRYPTION_MAGIC) - 12 - 16
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        digest = hashlib.sha256()
        written = 0
        while remaining:
            chunk = src.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            plain = decryptor.update(chunk)
            digest.update(plain)
            written += len(plain)
        if remaining:
            raise ValueError("加密备份文件被截断")
        tail = decryptor.finalize()
        digest.update(tail)
        written += len(tail)
    return digest.hexdigest(), written


def _plaintext_sha_for_item(manifest, original):
    path = "originals/" + original["storage_key"].replace("\\", "/")
    for item in manifest["files"]:
        if item.get("path") == path:
            return item.get("plaintext_sha256") if item.get("encrypted") else item["sha256"]
    raise ValueError("备份清单缺少原件身份")


# Single registry for every database row that points at a private original.
# A backup must cover all of them; a restore must verify the same set. New
# attachment tables must be added here and to the restore verification test.
ORIGINAL_TABLES = (
    ("business_attachments", "1=1", ("firm_id", "company_id")),
    ("case_attachments", "1=1", ("firm_id", "company_id")),
    ("intake_attachments", "1=1", ("firm_id", "company_id")),
    ("bank_import_files", "1=1", ("firm_id", "company_id")),
    ("migration_files", "1=1", ("firm_id", "company_id")),
    ("material_attachments", "1=1", ("firm_id", "company_id")),
    ("invoice_import_batches", "1=1", ("firm_id", "company_id")),
    ("tax_filing_attachments", "1=1", ("firm_id", "company_id")),
    ("source_originals", "1=1", ("firm_id", "company_id")),
    # Upload intents are never deleted. Stored/consuming rows may hold an
    # original that has not yet been attached to a business record.
    ("upload_intents", "state IN ('stored','consuming','consumed')", ("firm_id", "user_id")),
)

# Only these server-defined relationships may consume an upload. Backup checks
# them independently against the same database snapshot; a string ref is not
# evidence that an attachment belongs to its claimed enterprise or parent.
CONSUMED_TARGETS = {
    "intake_attachment": ("intake_draft", "intake_drafts", "intake_attachments", "intake_id"),
    "event_attachment": ("business_event", "business_events", "business_attachments", "event_id"),
    "case_attachment": ("case_task", "case_tasks", "case_attachments", "case_id"),
    "material_attachment": (
        "material_request",
        "material_requests",
        "material_attachments",
        "request_id",
    ),
    "bank_import": ("book", "books", "bank_import_files", "book_id"),
    "invoice_import": ("book", "books", "invoice_import_batches", "book_id"),
    "tax_filing_attachment": (
        "tax_filing",
        "tax_filings",
        "tax_filing_attachments",
        "filing_id",
    ),
    "source_original": ("source", "sources", "source_originals", "source_id"),
}
REFERENCE_COLUMNS = (
    "id",
    "filing_id",
    "event_id",
    "intake_id",
    "case_id",
    "response_id",
    "request_id",
    "project_id",
    "book_id",
    "source_id",
    "purpose",
    "state",
    "consumed_ref",
    "binding_json",
    "created_by",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def empty_destination(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("目标目录不能包含符号链接")
    resolved = path.resolve()
    if (
        resolved == resolved.parent
        or resolved.exists()
        and (not resolved.is_dir() or any(resolved.iterdir()))
    ):
        raise ValueError("只能使用明确指定的新目录或现有空目录，不能覆盖已有资料")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    return resolved


def safe_child(root, relative):
    relative = Path(relative)
    if (
        relative.is_absolute()
        or any(part in {"..", "."} for part in relative.parts)
        or not relative.parts
    ):
        raise ValueError("备份文件路径非法")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()) or any(
        part.is_symlink()
        for part in [root / relative, *(root / relative).parents]
        if part.is_relative_to(root)
    ):
        raise ValueError("备份文件不能越出指定目录或经由符号链接")
    return result


def _collect_originals(conn, table, predicate, scope_columns):
    # Historical/minimal schemas may predate binding_json. Keep their provenance
    # explicit rather than inventing a binding for a legacy consumed intent.
    description = conn.execute(f"SELECT * FROM {table} WHERE 1=0").description
    columns = {column.name if hasattr(column, "name") else column[0] for column in description}
    extra = [name for name in REFERENCE_COLUMNS if name in columns and name not in scope_columns]
    scope_sql = ",".join(scope_columns)
    extra_sql = "," + ",".join(extra) if extra else ""
    rows = conn.execute(
        f"SELECT storage_key,sha256,size_bytes,{scope_sql}{extra_sql} FROM {table} WHERE {predicate}"
    )
    result = []
    for row in rows:
        key, digest, size = row[0], row[1], row[2]
        scope = dict(zip(scope_columns, row[3 : 3 + len(scope_columns)]))
        metadata = dict(zip(extra, row[3 + len(scope_columns) :]))
        normalized_table = table.removeprefix("accounting.")
        reference = {
            "table": normalized_table,
            "row_id": metadata.get("id"),
            "scope": scope,
            "links": {
                name: metadata[name]
                for name in REFERENCE_COLUMNS
                if name.endswith("_id") and name != "id" and name in metadata
            },
        }
        if metadata.get("created_by") is not None:
            reference["created_by"] = metadata["created_by"]
        if normalized_table == "upload_intents":
            raw = metadata.get("binding_json")
            try:
                binding = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                raise ValueError("上传消费引用绑定不是有效 JSON") from None
            reference["upload"] = {
                "purpose": metadata.get("purpose"),
                "state": metadata.get("state"),
                "consumed_ref": metadata.get("consumed_ref"),
                "binding": binding,
                "binding_status": "bound"
                if binding is not None
                else "legacy_unbound"
                if metadata.get("state") == "consumed"
                else "unconsumed",
            }
        result.append(
            {
                "storage_key": key,
                "tables": [normalized_table],
                "scope": scope,
                "references": [reference],
                "sha256": digest,
                "size_bytes": size,
                # The current local/Supabase adapters do not expose an object
                # version. Keep the field explicit instead of pretending atomic
                # consistency with the database snapshot.
                "object_version": None,
            }
        )
    return result


def _merge_original(originals, item):
    """Merge content identity while preserving every authorized reference.

    A storage key may be referenced by an attachment (firm/company) and a
    consumed upload intent (firm/user). The scope dictionaries are not the
    object identity; hash and size are. References must still belong to one
    firm, and are retained for restore/diagnosis.
    """
    prior = originals.get(item["storage_key"])
    if prior is None:
        originals[item["storage_key"]] = item
        return
    if prior["sha256"] != item["sha256"] or prior["size_bytes"] != item["size_bytes"]:
        raise ValueError("同一原件存储键对应不同哈希或大小")
    scopes = [
        ref.get("scope", {}) for source in (prior, item) for ref in source.get("references", [])
    ]
    for field, label in (("firm_id", "事务所"), ("company_id", "企业")):
        if len({scope[field] for scope in scopes if scope.get(field)}) > 1:
            raise ValueError(f"同一原件存储键跨{label}引用")
    references = prior.setdefault(
        "references", [{"table": prior["tables"][0], "scope": prior["scope"]}]
    )
    for reference in item.get("references", []):
        if reference not in references:
            references.append(reference)
    for table in item["tables"]:
        if table not in prior["tables"]:
            prior["tables"].append(table)


def _canonical_references(references):
    return sorted(references, key=lambda ref: json.dumps(ref, sort_keys=True, ensure_ascii=False))


def _validate_references(item):
    """Validate the portable manifest, without trusting a tenant scope string."""
    references = item.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError("原件清单缺少完整引用")
    allowed = {table for table, _, _ in ORIGINAL_TABLES}
    for reference in references:
        if not isinstance(reference, dict) or reference.get("table") not in allowed:
            raise ValueError("原件引用表不合法")
        if not isinstance(reference.get("scope"), dict) or not isinstance(
            reference.get("links"), dict
        ):
            raise ValueError("原件引用范围或目标信息缺失")
    if len({json.dumps(ref, sort_keys=True) for ref in references}) != len(references):
        raise ValueError("原件引用重复")
    if {ref["table"] for ref in references} != set(item.get("tables", [])):
        raise ValueError("原件引用表清单不一致")
    for field, label in (("firm_id", "事务所"), ("company_id", "企业")):
        if len({ref["scope"][field] for ref in references if ref["scope"].get(field)}) > 1:
            raise ValueError(f"同一原件存储键跨{label}引用")
    for reference in references:
        if reference["table"] != "upload_intents":
            continue
        upload = reference.get("upload")
        if not isinstance(upload, dict):
            raise ValueError("原件缺少上传消费引用")
        binding = upload.get("binding")
        if binding is None:
            expected = "legacy_unbound" if upload.get("state") == "consumed" else "unconsumed"
            if upload.get("binding_status") != expected:
                raise ValueError("历史未绑定引用不得标记为已验证")
            continue
        if (
            not isinstance(binding, dict)
            or type(binding.get("version")) is not int
            or binding.get("version") != 1
        ):
            raise ValueError("不支持的上传消费引用绑定版本")
        expected_fields = {
            "version",
            "firm_id",
            "company_id",
            "purpose",
            "target_kind",
            "target_id",
            "attachment_table",
            "attachment_id",
            "event_id",
            "source_id",
            "book_id",
            "response_id",
            "storage",
        }
        if set(binding) != expected_fields:
            raise ValueError("上传消费引用绑定字段不完整")
        if upload.get("state") != "consumed" or upload.get("binding_status") != "bound":
            raise ValueError("上传消费状态与绑定不一致")
        purpose = upload.get("purpose")
        target = CONSUMED_TARGETS.get(purpose)
        if target is None:
            raise ValueError("上传消费用途不合法")
        target_kind, _, attachment_table, parent_column = target
        if (
            binding.get("purpose") != purpose
            or binding.get("target_kind") != target_kind
            or binding.get("attachment_table") != attachment_table
        ):
            raise ValueError("上传消费用途与目标种类不一致")
        if not all(
            isinstance(binding.get(key), str) and binding[key]
            for key in ("firm_id", "company_id", "target_id", "attachment_id")
        ):
            raise ValueError("上传消费引用缺少企业或目标标识")
        if binding["firm_id"] != reference["scope"].get("firm_id"):
            raise ValueError("上传消费引用跨事务所")
        storage = binding.get("storage")
        if (
            not isinstance(storage, dict)
            or type(storage.get("size_bytes")) is not int
            or storage
            != {
                "storage_key": item["storage_key"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
        ):
            raise ValueError("上传消费引用与原件身份不一致")
        if upload.get("consumed_ref") != attachment_table + ":" + binding["attachment_id"]:
            raise ValueError("上传消费引用标识不一致")
        candidates = [
            ref
            for ref in references
            if ref["table"] == attachment_table and ref.get("row_id") == binding["attachment_id"]
        ]
        if len(candidates) != 1:
            raise ValueError("上传消费引用的附件不存在或重复")
        attachment = candidates[0]
        if any(attachment["scope"].get(key) != binding[key] for key in ("firm_id", "company_id")):
            raise ValueError("上传消费引用与附件企业不一致")
        if attachment["links"].get(parent_column) != binding["target_id"]:
            raise ValueError("上传消费引用与附件事项不一致")
        if attachment.get("created_by") is not None and attachment["created_by"] != reference[
            "scope"
        ].get("user_id"):
            raise ValueError("上传消费引用与附件创建人不一致")
        for column in ("event_id", "source_id", "book_id", "response_id"):
            if binding.get(column) is not None and column in attachment["links"]:
                if binding[column] != attachment["links"][column]:
                    raise ValueError("上传消费引用的关联标识不一致")


def _snapshot_row(conn, dialect, table, row_id):
    qualified = "accounting." + table if dialect == "postgres" else table
    cursor = conn.execute(
        f"SELECT * FROM {qualified} WHERE id={'%s' if dialect == 'postgres' else '?'}", (row_id,)
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("上传消费引用的目标不存在")
    names = [column.name if hasattr(column, "name") else column[0] for column in cursor.description]
    return dict(zip(names, row))


def _validate_consumed_targets(conn, dialect, item):
    for reference in item["references"]:
        upload = reference.get("upload", {})
        binding = upload.get("binding")
        if binding is None:
            continue
        _, table, _, _ = CONSUMED_TARGETS[upload["purpose"]]
        parent = _snapshot_row(conn, dialect, table, binding["target_id"])
        if any(parent.get(key) != binding[key] for key in ("firm_id", "company_id")):
            raise ValueError("上传消费引用与目标企业不一致")
        for column in ("event_id", "source_id", "book_id"):
            if (
                binding.get(column) is not None
                and column in parent
                and parent[column] != binding[column]
            ):
                raise ValueError("上传消费引用与目标事项不一致")
        attachment = next(
            ref
            for ref in item["references"]
            if ref["table"] == binding["attachment_table"]
            and ref["row_id"] == binding["attachment_id"]
        )
        expected_links = {
            "event_id": binding["target_id"]
            if upload["purpose"] == "event_attachment"
            else parent.get("event_id")
            if upload["purpose"] == "case_attachment"
            else None,
            "source_id": parent.get("source_id")
            if upload["purpose"] == "case_attachment"
            else None,
            "book_id": binding["target_id"]
            if table == "books"
            else parent.get("book_id")
            if upload["purpose"] in {"case_attachment", "material_attachment"}
            else None,
            "response_id": attachment["links"].get("response_id")
            if upload["purpose"] == "case_attachment"
            else None,
        }
        if any(binding[column] != value for column, value in expected_links.items()):
            raise ValueError("上传消费引用的完整关联与目标不一致")
        if binding.get("response_id") is not None:
            response = _snapshot_row(conn, dialect, "case_responses", binding["response_id"])
            if response.get("case_id") != binding["target_id"] or any(
                response.get(key) != binding[key] for key in ("firm_id", "company_id")
            ):
                raise ValueError("上传消费引用与补件回复不一致")


def _validate_shared_lineage(conn, dialect, item):
    """A shared original does not authorize attaching it to any same-company event.

    Only the existing intake completion and accepted case response flows copy
    an attachment into business evidence, preserving its ID, creator and bytes.
    Read their parent state from the same DB snapshot, not from mutable manifest
    labels. Legacy originals without an upload intent keep their provenance;
    when a donor is present its transfer still has to be verifiable.
    """
    references = item["references"]
    donors = [
        ref for ref in references if ref["table"] in {"intake_attachments", "case_attachments"}
    ]
    bindings = [
        ref["upload"]["binding"]
        for ref in references
        if ref["table"] == "upload_intents" and ref.get("upload", {}).get("binding") is not None
    ]
    if not donors and not bindings:
        return
    direct = {
        binding["attachment_id"] for binding in bindings if binding["purpose"] == "event_attachment"
    }
    for business in (ref for ref in references if ref["table"] == "business_attachments"):
        if business["row_id"] in direct:
            continue
        event = _snapshot_row(conn, dialect, "business_events", business["links"].get("event_id"))
        if any(event.get(key) != business["scope"].get(key) for key in ("firm_id", "company_id")):
            raise ValueError("共享原件的业务事项企业不一致")
        valid = False
        for donor in donors:
            if donor.get("row_id") != business.get("row_id") or donor.get(
                "created_by"
            ) != business.get("created_by"):
                continue
            if donor["table"] == "intake_attachments":
                parent = _snapshot_row(
                    conn, dialect, "intake_drafts", donor["links"].get("intake_id")
                )
                valid = (
                    parent.get("status") == "completed" and parent.get("event_id") == event["id"]
                )
            else:
                parent = _snapshot_row(conn, dialect, "case_tasks", donor["links"].get("case_id"))
                response = _snapshot_row(
                    conn, dialect, "case_responses", donor["links"].get("response_id")
                )
                valid = (
                    parent.get("event_id") == event["id"]
                    and response.get("state") == "accepted"
                    and response.get("case_id") == parent["id"]
                    and all(
                        response.get(key) == business["scope"].get(key)
                        for key in ("firm_id", "company_id")
                    )
                )
            valid = valid and all(
                parent.get(key) == business["scope"].get(key) for key in ("firm_id", "company_id")
            )
            if valid:
                break
        if not valid:
            raise ValueError("共享原件缺少已确认的录入或补件来源关系")


def original_rows(conn, dialect):
    """Return deduplicated originals referenced by every registered table."""
    originals = {}
    present = []
    if dialect == "postgres":
        for table, predicate, scope_columns in ORIGINAL_TABLES:
            qualified = "accounting." + table
            if conn.execute("SELECT to_regclass(%s)", (qualified,)).fetchone()[0] is None:
                continue
            present.append(table)
            rows = _collect_originals(conn, qualified, predicate, scope_columns)
            for item in rows:
                _merge_original(originals, item)
    else:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, predicate, scope_columns in ORIGINAL_TABLES:
            if table not in tables:
                continue
            present.append(table)
            rows = _collect_originals(conn, table, predicate, scope_columns)
            for item in rows:
                _merge_original(originals, item)
    for item in originals.values():
        _validate_references(item)
        _validate_consumed_targets(conn, dialect, item)
        _validate_shared_lineage(conn, dialect, item)
        item["references"] = _canonical_references(item["references"])
    return present, list(originals.values())


def sqlite_metadata(conn):
    versions = [
        dict(zip(("version", "checksum"), row))
        for row in conn.execute("SELECT version,checksum FROM schema_migrations ORDER BY version")
    ]
    tables, originals = original_rows(conn, "sqlite")
    return versions, originals, tables


def pg_environment(url):
    # libpq URI/options are parsed by psycopg, not interpolated into shell code.
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(url)
    env = os.environ.copy()
    for name, value in values.items():
        key = {
            "dbname": "PGDATABASE",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "host": "PGHOST",
            "hostaddr": "PGHOSTADDR",
            "port": "PGPORT",
            "sslmode": "PGSSLMODE",
            "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT",
            "sslkey": "PGSSLKEY",
            "connect_timeout": "PGCONNECT_TIMEOUT",
        }.get(name)
        if key:
            env[key] = str(value)
    return env


_RESTORE_SOURCE = re.compile(r"Dumped from database version (\d+)")
_VERSION_MAJOR = re.compile(r"(\d+)(?:\.\d+)?")
# pg_restore cannot read archives newer than its own major version and cannot
# load settings introduced by a newer server (e.g. PG17's transaction_timeout
# into a PG16 target). Both cases must stop before the target is touched.
_VERSION_HINT = (
    "请使用与备份来源同主版本或更新的 PostgreSQL 客户端与目标库；不得用过滤选项绕过该检查"
)


def tool_major_version(name):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"未找到 {name}，请安装与数据库兼容的 PostgreSQL 客户端")
    result = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, timeout=60, shell=False
    )
    match = _VERSION_MAJOR.search(result.stdout or "")
    if not match:
        raise ValueError(f"无法识别 {name} 的版本{_VERSION_HINT}")
    return int(match.group(1))


def dump_source_major(dump_path, url):
    executable = shutil.which("pg_restore")
    if not executable:
        raise ValueError("未找到 pg_restore，请安装与数据库兼容的 PostgreSQL 客户端")
    result = subprocess.run(
        [executable, "--list", str(dump_path)],
        env=pg_environment(url),
        capture_output=True,
        text=True,
        timeout=600,
        shell=False,
    )
    if result.returncode:
        # Newer archive format than the installed client, or a corrupt dump.
        raise ValueError(f"本机 pg_restore 无法读取该归档{_VERSION_HINT}")
    match = _RESTORE_SOURCE.search(result.stdout or "")
    return int(match.group(1)) if match else None


def restore_version_conflict(client_major, source_major, target_major):
    """Return the reason to refuse, or None when the restore is supported."""
    if source_major and target_major and source_major > target_major:
        return (
            f"备份来自 PostgreSQL {source_major}，目标服务器是 {target_major}："
            f"低主版本无法还原高主版本设置（如 transaction_timeout）。{_VERSION_HINT}"
        )
    if source_major and client_major < source_major:
        return (
            f"本机 pg_restore 是 {client_major}，备份由 PostgreSQL {source_major} 生成。"
            f"{_VERSION_HINT}"
        )
    return None


def pg_tool(name, args, url):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"未找到 {name}，请安装与数据库兼容的 PostgreSQL 客户端")
    result = subprocess.run(
        [executable, *args],
        env=pg_environment(url),
        capture_output=True,
        text=True,
        timeout=600,
        shell=False,
    )
    if result.returncode:
        # Vendor stderr may echo credentials, URL or host. Keep it private.
        raise ValueError(
            f"{name} 未成功；请在本机检查数据库授权、客户端版本和目标，未导出任何连接信息"
        )


def copy_originals(storage_root, target, originals, *, storage_store=None):
    storage_root = Path(storage_root).resolve() if storage_store is None else None
    files = []
    for item in originals:
        if storage_store is None:
            source = safe_child(storage_root, item["storage_key"])
            if (
                not source.is_file()
                or source.stat().st_size != item["size_bytes"]
                or sha256(source) != item["sha256"]
            ):
                raise ValueError("数据库快照引用的原件不存在或哈希不符，备份未完成")
        else:
            # Read through the same private backend as the API. It bounds bytes
            # and checks snapshot SHA/size; no signed/public object URLs.
            data = storage_store.read(item)
        relative = "originals/" + item["storage_key"].replace("\\", "/")
        destination = safe_child(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open("xb") as dst:
            if storage_store is None:
                with source.open("rb") as src:
                    shutil.copyfileobj(src, dst)
            else:
                dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        destination.chmod(0o600)
        if sha256(destination) != item["sha256"]:
            raise ValueError("复制后的原件哈希不符")
        files.append(
            {
                "path": relative,
                "storage_key": item["storage_key"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "tables": item.get("tables", []),
                "scope": item.get("scope", {}),
                "references": item["references"],
                "object_version": item.get("object_version"),
            }
        )
    return files


def reference_verification(files):
    references = [
        reference
        for item in files
        if item.get("path", "").startswith("originals/")
        for reference in item.get("references", [])
    ]
    legacy = sum(
        reference.get("upload", {}).get("binding_status") == "legacy_unbound"
        for reference in references
    )
    unidentified = sum(not reference.get("row_id") for reference in references)
    return {
        "reference_count": len(references),
        "legacy_unbound_uploads": legacy,
        "unidentified_references": unidentified,
        "all_consumed_bindings_verified": not legacy and not unidentified,
    }


def _verify_reference_snapshot(manifest, originals):
    if manifest.get("originals_policy_version", 1) < 3:
        return
    archived = {
        item["storage_key"]: item
        for item in manifest["files"]
        if item["path"].startswith("originals/")
    }
    snapshot = {item["storage_key"]: item for item in originals}
    if set(archived) != set(snapshot):
        raise ValueError("备份清单与数据库原件引用集合不同")
    for key, item in snapshot.items():
        copied = archived[key]
        expected_sha = (
            copied.get("plaintext_sha256") if copied.get("encrypted") else copied["sha256"]
        )
        if (
            expected_sha != item["sha256"]
            or copied["size_bytes"] != item["size_bytes"]
            or _canonical_references(copied["references"])
            != _canonical_references(item["references"])
        ):
            raise ValueError("备份清单与数据库原件引用不一致")


def _encrypt_manifest_files(target, files, key):
    for item in files:
        path = safe_child(target, item["path"])
        plaintext_sha256, plaintext_size = item["sha256"], item["size_bytes"]
        temporary = safe_child(target, item["path"] + ".enc.tmp")
        _encrypt_file(path, temporary, key)
        temporary.replace(path)
        path.chmod(0o600)
        item["encrypted"] = True
        item["plaintext_sha256"] = plaintext_sha256
        item["stored_size_bytes"] = path.stat().st_size
        item["sha256"] = sha256(path)


def backup(database, storage_root, destination, *, storage_store=None):
    if storage_store is not None:
        storage_store.verify_private()
    key_info = _backup_key()
    target = empty_destination(destination)
    postgres = database.startswith(("postgres://", "postgresql://"))
    security_manifest = None
    if postgres:
        import psycopg

        with psycopg.connect(database) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            versions = [
                {"version": row[0], "checksum": row[1]}
                for row in conn.execute(
                    "SELECT version,checksum FROM accounting.schema_migrations ORDER BY version"
                )
            ]
            original_tables, originals = original_rows(conn, "postgres")
            try:
                from scripts.recovery_security import capture_security
            except ModuleNotFoundError:
                from recovery_security import capture_security
            security_manifest = capture_security(conn)
            dump = target / "database.dump"
            pg_tool(
                "pg_dump",
                [
                    "--format=custom",
                    "--schema=accounting",
                    "--no-password",
                    "--snapshot",
                    snapshot,
                    "--file",
                    str(dump),
                ],
                database,
            )
            files = copy_originals(storage_root, target, originals, storage_store=storage_store)
    else:
        source = Path(database).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("SQLite 源数据库不存在或不是普通文件")
        dump = target / "database.sqlite"
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(dump)) as snapshot,
        ):
            src.backup(snapshot)
            if (
                snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or snapshot.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("SQLite 快照完整性验证失败")
            versions, originals, original_tables = sqlite_metadata(snapshot)
        files = copy_originals(storage_root, target, originals, storage_store=storage_store)
    dump.chmod(0o600)
    files.insert(0, {"path": dump.name, "sha256": sha256(dump), "size_bytes": dump.stat().st_size})
    encryption = None
    if key_info is not None:
        key, key_id = key_info
        _encrypt_manifest_files(target, files, key)
        encryption = {
            "format": ENCRYPTION_FORMAT,
            "algorithm": ENCRYPTION_ALGORITHM,
            "key_id": key_id,
        }
    manifest = {
        "format_version": FORMAT_VERSION,
        "originals_policy_version": ORIGINALS_POLICY_VERSION,
        "dialect": "postgres" if postgres else "sqlite",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "migrations": versions,
        "database_schema_version": versions[-1]["version"] if versions else 0,
        "original_tables": original_tables,
        "reference_verification": reference_verification(files),
        "object_version_supported": False,
        "files": files,
        "consistency": "exported_repeatable_read_snapshot"
        if postgres
        else "sqlite_backup_snapshot",
        "consistency_note": "数据库为快照；对象版本由存储后端决定，当前记录 object_version=null，不宣称原子一致",
        "contains_credentials": False,
    }
    if security_manifest is not None:
        manifest["security"] = security_manifest
    if encryption is not None:
        manifest["encryption"] = encryption
    with (target / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    (target / "manifest.json").chmod(0o600)
    return manifest


def verify(directory):
    directory = Path(directory).resolve()
    path = safe_child(directory, "manifest.json")
    if path.stat().st_size > 50_000_000:
        raise ValueError("备份清单过大")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION or manifest.get("dialect") not in {
        "sqlite",
        "postgres",
    }:
        raise ValueError("不支持的备份格式")
    if manifest.get("originals_policy_version", 1) not in (1, 2, ORIGINALS_POLICY_VERSION):
        raise ValueError("不支持的原件引用策略版本")
    encryption_key = _encryption_key_for_manifest(manifest)
    if encryption_key is None:
        for item in manifest.get("files", []):
            if isinstance(item, dict) and item.get("encrypted"):
                raise ValueError("备份清单含加密文件但缺少加密配置")
    if "security" in manifest:
        try:
            from scripts.recovery_security import validate_security_manifest
        except ModuleNotFoundError:
            from recovery_security import validate_security_manifest
        validate_security_manifest(manifest["security"])
    names = set()
    expected_database = "database.sqlite" if manifest["dialect"] == "sqlite" else "database.dump"
    for item in manifest["files"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or type(item.get("size_bytes")) is not int
            or item["size_bytes"] <= 0
        ):
            raise ValueError("备份清单文件身份不合法")
        name = item["path"]
        if name in names or (name != expected_database and not name.startswith("originals/")):
            raise ValueError("备份清单文件重复或非预期")
        names.add(name)
        source = safe_child(directory, name)
        if not source.is_file():
            raise ValueError("备份文件缺失或哈希不符")
        if item.get("encrypted"):
            if (
                encryption_key is None
                or type(item.get("stored_size_bytes")) is not int
                or item["stored_size_bytes"] < len(ENCRYPTION_MAGIC) + 28
                or not isinstance(item.get("plaintext_sha256"), str)
                or source.stat().st_size != item["stored_size_bytes"]
                or sha256(source) != item["sha256"]
            ):
                raise ValueError("备份文件缺失或哈希不符")
            try:
                digest, plaintext_size = _decrypt_digest(source, encryption_key)
            except Exception as error:
                if isinstance(error, ValueError):
                    raise
                raise ValueError("备份解密失败：密钥错误或密文被篡改") from error
            if plaintext_size != item["size_bytes"] or digest != item["plaintext_sha256"]:
                raise ValueError("备份解密后的明文哈希不符")
        elif source.stat().st_size != item["size_bytes"] or sha256(source) != item["sha256"]:
            raise ValueError("备份文件缺失或哈希不符")
        if name.startswith("originals/") and manifest.get("originals_policy_version", 1) >= 3:
            if (
                not isinstance(item.get("storage_key"), str)
                or "originals/" + item["storage_key"].replace("\\", "/") != name
            ):
                raise ValueError("原件清单存储键与路径不一致")
            _validate_references(item)
    if expected_database not in names:
        raise ValueError("备份缺少数据库")
    if manifest.get("originals_policy_version", 1) >= 3:
        if manifest.get("reference_verification") != reference_verification(manifest["files"]):
            raise ValueError("原件引用验证摘要不一致")
        if manifest["dialect"] == "sqlite":
            database_item = next(
                item for item in manifest["files"] if item["path"] == expected_database
            )
            with tempfile.TemporaryDirectory(prefix="nxbk-verify-") as temporary:
                database_path = safe_child(directory, expected_database)
                if database_item.get("encrypted"):
                    database_path = Path(temporary) / expected_database
                    _decrypt_file(
                        safe_child(directory, expected_database), database_path, encryption_key
                    )
                with closing(
                    sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
                ) as conn:
                    _, originals = original_rows(conn, "sqlite")
                    _verify_reference_snapshot(manifest, originals)
    return manifest


def _record_restore_hold(conn, dialect, restore_id):
    """Set the DB hold as soon as it exists, including incomplete restores."""
    if dialect == "sqlite":
        conn.execute(
            "CREATE TABLE IF NOT EXISTS restore_state("
            "instance_id TEXT PRIMARY KEY,hold INTEGER NOT NULL DEFAULT 0,"
            "restore_id TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',"
            "updated_by TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO restore_state(instance_id,hold,restore_id,reason,updated_by,updated_at) "
            "VALUES('restored',1,?,'恢复到隔离目标，等待核对','system',?) "
            "ON CONFLICT(instance_id) DO UPDATE SET hold=1,restore_id=excluded.restore_id,"
            "reason=excluded.reason,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (restore_id, datetime.now(timezone.utc).isoformat()),
        )
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS accounting.restore_state("
            "instance_id TEXT PRIMARY KEY,hold BOOLEAN NOT NULL DEFAULT false,"
            "restore_id TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',"
            "updated_by TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO accounting.restore_state(instance_id,hold,restore_id,reason,updated_by,updated_at) "
            "VALUES('restored',TRUE,%s,'恢复到隔离目标，等待核对','system',%s) "
            "ON CONFLICT(instance_id) DO UPDATE SET hold=TRUE,restore_id=EXCLUDED.restore_id,"
            "reason=EXCLUDED.reason,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at",
            (restore_id, datetime.now(timezone.utc).isoformat()),
        )


def restore(directory, destination, postgres_target=None, *, restore_permissions=False):
    manifest = verify(directory)
    encryption_key = _encryption_key_for_manifest(manifest)
    if restore_permissions and (manifest["dialect"] != "postgres" or "security" not in manifest):
        raise ValueError("精确授权恢复需要带安全清单的 PostgreSQL 备份")
    source, target = Path(directory).resolve(), empty_destination(destination)
    restore_id = hashlib.sha256(str(manifest).encode("utf-8")).hexdigest()[:32]
    if manifest["dialect"] == "postgres":
        if not postgres_target:
            raise ValueError("PostgreSQL 恢复需要显式指定新空数据库")
        import psycopg

        with psycopg.connect(postgres_target) as conn:
            # Extensions' system objects are allowed, application/user objects
            # in ANY schema are not. Never restore over an existing accounting DB.
            count = conn.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')"
            ).fetchone()[0]
            custom_schemas = conn.execute(
                "SELECT count(*) FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname NOT IN ('public','information_schema') AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_namespace'::regclass AND d.objid=n.oid AND d.deptype='e')"
            ).fetchone()[0]
            custom_functions = conn.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')"
            ).fetchone()[0]
            if count or custom_schemas or custom_functions:
                raise ValueError("PostgreSQL 目标不是空数据库，禁止覆盖")
        database_item = next(
            (item for item in manifest["files"] if item["path"] == "database.dump"), None
        )
        dump_source = source / "database.dump"
        decrypted_dump = None
        try:
            if database_item is not None and database_item.get("encrypted"):
                decrypted_dump = target / ".database.dump.restore"
                _decrypt_file(dump_source, decrypted_dump, encryption_key)
                dump_source = decrypted_dump
            target_major = conn.execute("SELECT current_setting('server_version')").fetchone()[0]
            target_major = int(str(target_major).split(".")[0])
            conflict = restore_version_conflict(
                tool_major_version("pg_restore"),
                dump_source_major(dump_source, postgres_target),
                target_major,
            )
            if conflict:
                raise ValueError(conflict)
            if decrypted_dump is not None:
                decrypted_dump.unlink(missing_ok=True)
                decrypted_dump = None
            pg_tool(
                "pg_restore",
                [
                    "--no-owner",
                    "--no-privileges",
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-password",
                    "--dbname",
                    pg_environment(postgres_target).get("PGDATABASE", ""),
                    str(dump_source),
                ],
                postgres_target,
            )
        finally:
            if decrypted_dump is not None:
                decrypted_dump.unlink(missing_ok=True)
        with psycopg.connect(postgres_target) as conn:
            _record_restore_hold(conn, "postgres", restore_id)
            # Persist isolation BEFORE ACL reconstruction. A failed structural
            # check must roll back permissions, never the recovery hold itself.
            conn.commit()
            if restore_permissions:
                try:
                    from scripts.recovery_security import restore_security
                except ModuleNotFoundError:
                    from recovery_security import restore_security
                restore_security(conn, manifest["security"])
    elif postgres_target:
        raise ValueError("SQLite 备份不能按 PostgreSQL 恢复")
    for item in manifest["files"]:
        if item["path"] == "database.dump":
            continue
        destination_file = safe_child(target, item["path"])
        destination_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if item.get("encrypted"):
            _decrypt_file(safe_child(source, item["path"]), destination_file, encryption_key)
            if destination_file.stat().st_size != item["size_bytes"] or sha256(
                destination_file
            ) != item.get("plaintext_sha256"):
                raise ValueError("恢复文件解密后的明文哈希不符")
        else:
            with (
                safe_child(source, item["path"]).open("rb") as src,
                destination_file.open("xb") as dst,
            ):
                shutil.copyfileobj(src, dst)
            destination_file.chmod(0o600)
            if sha256(destination_file) != item["sha256"]:
                raise ValueError("恢复原件哈希不符")
        if item["path"] == "database.sqlite":
            with closing(sqlite3.connect(destination_file)) as conn:
                _record_restore_hold(conn, "sqlite", restore_id)
                conn.commit()
    if manifest["dialect"] == "sqlite":
        with closing(sqlite3.connect(target / "database.sqlite")) as conn:
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("恢复数据库完整性检查失败")
            migrations, originals, _ = sqlite_metadata(conn)
            if migrations != manifest["migrations"]:
                raise ValueError("恢复数据库迁移清单不同")
            _verify_reference_snapshot(manifest, originals)
            for item in originals:
                file = safe_child(target, "originals/" + item["storage_key"].replace("\\", "/"))
                expected_sha = _plaintext_sha_for_item(manifest, item)
                if file.stat().st_size != item["size_bytes"] or sha256(file) != expected_sha:
                    raise ValueError("恢复后的数据库与原件不一致")
    else:
        with psycopg.connect(postgres_target) as conn:
            try:
                from scripts.recovery_security import verify_sequences
            except ModuleNotFoundError:
                from recovery_security import verify_sequences
            sequence_receipt = verify_sequences(conn)
            migrations = [
                {"version": row[0], "checksum": row[1]}
                for row in conn.execute(
                    "SELECT version,checksum FROM accounting.schema_migrations ORDER BY version"
                )
            ]
            if migrations != manifest["migrations"]:
                raise ValueError("恢复数据库迁移清单不同")
            _, originals = original_rows(conn, "postgres")
            _verify_reference_snapshot(manifest, originals)
            for item in originals:
                file = safe_child(target, "originals/" + item["storage_key"].replace("\\", "/"))
                expected_sha = _plaintext_sha_for_item(manifest, item)
                if (
                    not file.is_file()
                    or file.stat().st_size != item["size_bytes"]
                    or sha256(file) != expected_sha
                ):
                    raise ValueError("恢复后的数据库与原件不一致")
    receipt = {
        "restore_id": restore_id,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "dialect": manifest["dialect"],
        "verified_files": len(manifest["files"]),
        "migration_count": len(manifest["migrations"]),
        "serving_enabled": False,
        "outbound_disabled": True,
        "worker_autostart": False,
        "in_flight_jobs_hold": True,
        "security_verified": bool(restore_permissions),
        "security_manifest_sha256": hashlib.sha256(
            json.dumps(manifest.get("security"), sort_keys=True).encode()
        ).hexdigest()
        if restore_permissions
        else None,
        "sequences": sequence_receipt if manifest["dialect"] == "postgres" else None,
        "originals_policy_version": manifest.get("originals_policy_version", 1),
        "reference_verification": reference_verification(manifest["files"])
        if manifest.get("originals_policy_version", 1) >= 3
        else {
            "all_consumed_bindings_verified": False,
            "reason": "legacy_manifest_without_complete_references",
        },
        "note": "恢复环境须先核对在途任务与结果未知状态，确认前不得启动 worker 或外发",
    }
    with (target / "restore-verification.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("--database-env", default="ACCOUNTING_DATABASE_URL")
    source_storage = create.add_mutually_exclusive_group(required=True)
    source_storage.add_argument("--storage-root", help="本地原件的持久目录")
    source_storage.add_argument(
        "--configured-storage",
        action="store_true",
        help="使用 ACCOUNTING_STORAGE_* 的私有共享存储；不创建或删除对象",
    )
    create.add_argument("--destination", required=True)
    check = sub.add_parser("verify")
    check.add_argument("directory")
    recover = sub.add_parser("restore")
    recover.add_argument("directory")
    recover.add_argument("--destination", required=True)
    recover.add_argument("--postgres-target-env")
    recover.add_argument(
        "--restore-permissions",
        action="store_true",
        help="仅对带安全清单且处于 hold 的 PG 副本重建逐对象和逐列授权",
    )
    args = parser.parse_args()
    try:
        if args.command == "backup":
            database = os.getenv(args.database_env)
            if not database:
                raise ValueError("指定数据库环境变量未配置")
            storage = None
            if args.configured_storage:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
                from app.storage.factory import create_attachment_store

                storage = create_attachment_store()
            result = backup(database, args.storage_root, args.destination, storage_store=storage)
            print(
                json.dumps(
                    {
                        "completed": True,
                        "dialect": result["dialect"],
                        "verified_files": len(result["files"]),
                        "migration_count": len(result["migrations"]),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "verify":
            result = verify(args.directory)
            print(
                json.dumps(
                    {
                        "verified": True,
                        "files": len(result["files"]),
                        "migrations": len(result["migrations"]),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            result = restore(
                args.directory,
                args.destination,
                os.getenv(args.postgres_target_env) if args.postgres_target_env else None,
                restore_permissions=args.restore_permissions,
            )
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # File paths may contain names; external errors may contain credentials.
        print(
            str(error)
            if isinstance(error, ValueError)
            else "备份或恢复未完成，请在本机检查路径、授权和客户端环境",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
