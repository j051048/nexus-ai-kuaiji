from __future__ import annotations

import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.accounting.database import Database


path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_tax16", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_external_evidence_row_survives_full_restore_without_originals(tmp_path):
    source = tmp_path / "database.sqlite"
    Database(source)
    with sqlite3.connect(source) as conn:
        conn.executemany(
            "INSERT INTO users(id,name) VALUES(?,?)",
            [("maker-g1", "恢复制单人"), ("reviewer-g1", "恢复复核人")],
        )
        conn.execute("INSERT INTO firms(id,name) VALUES('firm-g1','恢复合成事务所')")
        conn.execute("INSERT INTO companies(id,name,tax_id) VALUES('company-g1','恢复合成企业','')")
        conn.execute("INSERT INTO firm_clients(firm_id,company_id) VALUES('firm-g1','company-g1')")
        conn.execute(
            "INSERT INTO books(id,firm_id,company_id,name,period) VALUES('book-g1','firm-g1','company-g1','恢复合成账套','2026-10')"
        )
        conn.execute(
            "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
            "inputs_fingerprint,content_json,content_hash,created_by,created_at,"
            "mode,standard_kind,policy_version,policy_approver) "
            "VALUES('report-g1','book-g1','2026-10','synthetic',1,'published',1,?,?,?,?,?,'formal','small_enterprise',1,'reviewer-g1')",
            ("c" * 64, "{}", "d" * 64, "maker-g1", "2026-11-12T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO monthly_external_evidence("
            "id,book_id,firm_id,company_id,period,report_id,report_version,source_kind,"
            "source_name,source_version,content_text,sha256,status,note,created_by,created_at,"
            "verified_by,verified_at,review_note,fingerprint"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "g1-recovery-evidence",
                "book-g1",
                "firm-g1",
                "company-g1",
                "2026-10",
                "report-g1",
                1,
                "tplus_report",
                "恢复合成 T+ 报表",
                "2026-10-v1",
                '{"balanced":true}',
                "a" * 64,
                "verified",
                "恢复演练证据",
                "maker-g1",
                "2026-11-12T00:00:00Z",
                "reviewer-g1",
                "2026-11-12T00:10:00Z",
                "第二复核人核对",
                "b" * 64,
            ),
        )
        conn.commit()
    storage = tmp_path / "storage"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(str(source), storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)
    with sqlite3.connect(restored / "database.sqlite") as conn:
        saved = conn.execute(
            "SELECT source_kind,source_version,status,verified_by,sha256,fingerprint "
            "FROM monthly_external_evidence WHERE id='g1-recovery-evidence'"
        ).fetchone()
    assert saved == (
        "tplus_report",
        "2026-10-v1",
        "verified",
        "reviewer-g1",
        "a" * 64,
        "b" * 64,
    )
