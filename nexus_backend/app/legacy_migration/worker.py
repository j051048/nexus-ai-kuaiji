"""Separate deterministic migration lane; no model calls or external writes."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.accounting.domain import Actor, AccountingError
from app.accounting.service import fingerprint, now
from .normalization import normalize_records, canonical_payload
from .validation import validate_records, deduplicate_records
from .service import encode


logger = logging.getLogger(__name__)


class MigrationWorker:
    def __init__(self, service, concurrency=1, lease_seconds=120, poll_seconds=1):
        if concurrency not in {1, 2} or not 10 <= lease_seconds <= 600:
            raise ValueError("迁移处理并发为1–2，租约10–600秒")
        self.service, self.db = service, service.db
        self.concurrency, self.lease_seconds, self.poll_seconds = (
            concurrency,
            lease_seconds,
            poll_seconds,
        )
        self.worker_id = "migration-" + uuid4().hex
        self._stop = threading.Event()
        self._thread = None

    def _private(self, name, args=(), rows=False):
        with closing(self.db.connect()) as conn:
            try:
                conn.begin(write=True, role="accounting_worker")
                placeholders = ",".join("?" for _ in args)
                result = conn.execute(
                    (
                        "SELECT row_to_json(j)::text FROM accounting."
                        + name
                        + "("
                        + placeholders
                        + ") j"
                    )
                    if rows
                    else "SELECT accounting." + name + "(" + placeholders + ")",
                    args,
                )
                value = (
                    [json.loads(r[0]) for r in result.fetchall()] if rows else result.fetchone()[0]
                )
                conn.commit()
                return value
            except Exception:
                conn.rollback()
                raise

    def claim(self):
        if self.db.is_postgres:
            rows = self._private("migration_claim", (self.worker_id, self.lease_seconds), rows=True)
            return rows[0] if rows else None
        with self.db.transaction(write=True) as conn:
            stamp = now()
            for j in conn.execute(
                "SELECT * FROM migration_jobs WHERE status='running' AND lease_until<=?", (stamp,)
            ).fetchall():
                status = "retry_wait" if j["attempt_count"] < j["max_attempts"] else "failed"
                conn.execute(
                    "UPDATE migration_jobs SET status=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,available_at=?,updated_at=?,finished_at=?,error_code='lease_expired',error_detail='后台已恢复过期任务，将从检查点继续' WHERE id=?",
                    (status, stamp, stamp, stamp if status == "failed" else None, j["id"]),
                )
            for j in conn.execute(
                "SELECT j.*,p.book_id,p.version AS project_version FROM migration_jobs j JOIN migration_projects p ON p.id=j.project_id WHERE j.status IN ('queued','retry_wait') AND j.available_at<=? ORDER BY j.created_at,j.id",
                (stamp,),
            ).fetchall():
                try:
                    p, target, _ = self.service._project(
                        conn,
                        Actor(j["execution_user_id"], j["firm_id"]),
                        j["book_id"],
                        j["project_id"],
                        "manage",
                    )
                    self.service._mutable(p, target)
                    self.service._version(p, j["expected_version"])
                    if (
                        j["file_id"]
                        and conn.execute(
                            "SELECT 1 FROM migration_file_replacements WHERE old_file_id=?",
                            (j["file_id"],),
                        ).fetchone()
                    ):
                        raise AccountingError("原件已修订", 409)
                except AccountingError:
                    conn.execute(
                        "UPDATE migration_jobs SET status='stale',finished_at=?,updated_at=?,error_code='authorization_or_input_changed',error_detail='授权或资料已变化，原经办任务不会替换身份继续' WHERE id=?",
                        (stamp, stamp, j["id"]),
                    )
            if (
                conn.execute(
                    "SELECT count(*) FROM migration_jobs WHERE status='running'"
                ).fetchone()[0]
                >= 2
            ):
                return None
            row = conn.execute(
                """SELECT j.* FROM migration_jobs j LEFT JOIN migration_company_schedule s ON s.firm_id=j.firm_id AND s.company_id=j.company_id
              WHERE j.status IN ('queued','retry_wait') AND j.available_at<=? AND j.attempt_count<j.max_attempts
              AND NOT EXISTS(SELECT 1 FROM migration_jobs active WHERE active.firm_id=j.firm_id AND active.company_id=j.company_id AND active.status='running')
              ORDER BY coalesce(s.last_dispatch_seq,0),j.created_at,j.id LIMIT 1""",
                (stamp,),
            ).fetchone()
            if not row:
                return None
            job = dict(row)
            token = uuid4().hex
            until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
            seq = conn.execute(
                "SELECT coalesce(max(last_dispatch_seq),0)+1 FROM migration_company_schedule"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO migration_company_schedule(firm_id,company_id,last_dispatch_seq) VALUES(?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET last_dispatch_seq=excluded.last_dispatch_seq",
                (job["firm_id"], job["company_id"], seq),
            )
            conn.execute(
                "UPDATE migration_jobs SET status='running',lease_token=?,lease_until=?,worker_id=?,attempt_count=attempt_count+1,updated_at=? WHERE id=?",
                (token, until, self.worker_id, stamp, job["id"]),
            )
            return {
                **job,
                "status": "running",
                "lease_token": token,
                "lease_until": until,
                "worker_id": self.worker_id,
                "attempt_count": job["attempt_count"] + 1,
            }

    def _actor(self, job):
        return Actor(job["execution_user_id"], job["firm_id"])

    def _check(self, conn, job):
        if self.db.is_postgres:
            conn.execute("SET LOCAL ROLE accounting_worker")
            try:
                valid = conn.execute(
                    "SELECT accounting.migration_check(?,?)", (job["id"], job["lease_token"])
                ).fetchone()[0]
            finally:
                conn.execute("SET LOCAL ROLE accounting_app")
            conn.execute(
                "SELECT set_config('app.migration_job_id',?,true),set_config('app.migration_token',?,true)",
                (job["id"], job["lease_token"]),
            )
        else:
            valid = conn.execute(
                "SELECT 1 FROM migration_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                (job["id"], job["lease_token"], now()),
            ).fetchone()
        if not valid:
            raise AccountingError("迁移任务租约已失效，旧结果不会发布", 409)
        p = conn.execute(
            "SELECT book_id FROM migration_projects WHERE id=?", (job["project_id"],)
        ).fetchone()
        if not p:
            raise AccountingError("迁移项目授权已失效", 404)
        project, target, _ = self.service._project(
            conn, self._actor(job), p["book_id"], job["project_id"], "manage"
        )
        self.service._mutable(project, target)
        self.service._version(project, job["expected_version"])
        return project

    def _heartbeat(self, conn, job, checkpoint, processed, total):
        checkpoint.setdefault("metadata", {})["expected_record_count"] = total
        persisted = conn.execute(
            "SELECT count(*) FROM migration_records WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
        if self.db.is_postgres:
            conn.execute("SET LOCAL ROLE accounting_worker")
            try:
                valid = conn.execute(
                    "SELECT accounting.migration_heartbeat(?,?,?,?,?,?)",
                    (
                        job["id"],
                        job["lease_token"],
                        encode(checkpoint),
                        processed,
                        persisted,
                        self.lease_seconds,
                    ),
                ).fetchone()[0]
            finally:
                conn.execute("SET LOCAL ROLE accounting_app")
            if not valid:
                raise AccountingError("迁移检查点已经失效", 409)
        else:
            until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
            conn.execute(
                "UPDATE migration_jobs SET checkpoint_json=?,processed_count=?,record_count=?,lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_token=?",
                (
                    encode(checkpoint),
                    processed,
                    persisted,
                    until,
                    now(),
                    job["id"],
                    job["lease_token"],
                ),
            )

    def _parse(self, job):
        actor = self._actor(job)
        with self.db.transaction(actor=actor) as conn:
            project = self._check(conn, job)
            file = self.service._file(
                conn, actor, project["book_id"], project["id"], job["file_id"]
            )
        data = self.service._read_blob(file)
        records, issues, metadata = normalize_records(
            file["filename"],
            data,
            file["category"],
            json.loads(file["mapping_json"]),
            self.service._context(project),
        )
        metadata["raw_record_count"] = len(records)
        records, identical, duplicate_issues = deduplicate_records(records)
        issues.extend(duplicate_issues)
        metadata["deduplicated_count"] = identical
        if identical:
            metadata["source_controls_verified"] = False
        checkpoint = {"metadata": metadata, "issues": issues, "next": 0}
        previous = json.loads(job["checkpoint_json"])
        start = previous.get("next", 0)
        if type(start) is not int or not 0 <= start <= len(records):
            raise AccountingError("迁移检查点与原件不一致", 409)
        checkpoint["next"] = start
        # These batches hold the global write lock only for bounded inserts.
        for position in range(start, len(records), 250):
            batch = records[position : position + 250]
            with self.db.transaction(
                write=True, actor=actor, company_id=project["company_id"]
            ) as conn:
                self._check(conn, job)
                for ordinal, r in enumerate(batch, position):
                    digest = fingerprint(
                        {
                            "kind": r["kind"],
                            "external_id": r["external_id"],
                            "period": r["period"],
                            "payload": json.loads(canonical_payload(r["payload"])),
                        }
                    )
                    identifier = hashlib_id(job["id"], ordinal)
                    prior = conn.execute(
                        "SELECT fingerprint FROM migration_records WHERE job_id=? AND kind=? AND external_id=? AND period=?",
                        (job["id"], r["kind"], r["external_id"], r["period"]),
                    ).fetchone()
                    if prior:
                        if prior["fingerprint"] != digest:
                            checkpoint["issues"].append(
                                {
                                    "code": "DUPLICATE_RECORD_CONFLICT",
                                    "severity": "blocking",
                                    "message": "同一历史身份对应不同内容，原记录保留",
                                    "kind": r["kind"],
                                    "external_id": r["external_id"],
                                    "row_number": r["row_number"],
                                    "file_id": file["id"],
                                }
                            )
                        continue
                    conn.execute(
                        "INSERT INTO migration_records(id,project_id,file_id,job_id,firm_id,company_id,kind,external_id,period,payload_json,fingerprint,row_number,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            project["id"],
                            file["id"],
                            job["id"],
                            project["firm_id"],
                            project["company_id"],
                            r["kind"],
                            r["external_id"],
                            r["period"],
                            encode(r["payload"]),
                            digest,
                            r["row_number"],
                            now(),
                        ),
                    )
                checkpoint["next"] = position + len(batch)
                self._heartbeat(conn, job, checkpoint, checkpoint["next"], len(records))
        if not records:
            with self.db.transaction(write=True, actor=actor) as conn:
                self._check(conn, job)
                self._heartbeat(conn, job, checkpoint, 0, 0)
        return None

    def _validate(self, job):
        actor = self._actor(job)
        with self.db.transaction(actor=actor) as conn:
            project = self._check(conn, job)
            records = self.service._records_in_transaction(conn, project["id"])
            mapping = self.service._mapping(conn, project["id"])
            basis = self.service._basis(conn, project)
            target_accounts = {
                r["code"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM accounts WHERE book_id=?", (project["book_id"],)
                )
            }
            saved = conn.execute(
                "SELECT * FROM migration_validations WHERE job_id=? AND project_id=?",
                (job["id"], project["id"]),
            ).fetchone()
            saved = dict(saved) if saved else None
            files = [
                dict(r)
                for r in conn.execute(
                    "SELECT f.* FROM migration_files f WHERE f.project_id=? AND "
                    + self.service._active_file_sql(),
                    (project["id"],),
                )
            ]
            inputs = []
            for f in files:
                if f["category"] == "attachment":
                    continue
                prior = conn.execute(
                    "SELECT checkpoint_json FROM migration_jobs WHERE file_id=? AND phase='parse' AND status='succeeded' ORDER BY created_at DESC,id DESC LIMIT 1",
                    (f["id"],),
                ).fetchone()
                if not prior:
                    raise AccountingError("原件尚未成功解析", 409)
                inputs.append((f, json.loads(prior["checkpoint_json"])))
        for f in files:
            self.service._read_blob(f)
        if saved:
            if (
                saved["input_version"] != project["version"]
                or saved["mapping_version"] != mapping["version"]
                or saved["basis_token"] != basis
            ):
                raise AccountingError("已保存的校验报告依据发生变化，须明确提交新任务", 409)
            with self.db.transaction(write=True, actor=actor) as conn:
                fresh = self._check(conn, job)
                if basis != self.service._basis(conn, fresh):
                    raise AccountingError("恢复校验时原件、映射或目标目录已变化", 409)
                self._heartbeat(
                    conn, job, {"validation_id": saved["id"]}, len(records), len(records)
                )
            return saved["id"]
        context = self.service._context(project)
        context["source_controls_verified"] = bool(inputs) and all(
            c.get("metadata", {}).get("source_controls_verified", False) for _, c in inputs
        )
        report = validate_records(records, context, mapping)
        for f, checkpoint in inputs:
            for issue in checkpoint.get("issues", []):
                report["issues"].append({**issue, "file_id": f["id"]})
        report["issues"].extend(
            self.service.mapping_issues(project, records, mapping, target_accounts)
        )
        report["blocking_count"] = sum(r["severity"] == "blocking" for r in report["issues"])
        report["warning_count"] = sum(r["severity"] == "warning" for r in report["issues"])
        report["coverage"]["complete"] = report["blocking_count"] == 0 and report["coverage"].get(
            "complete", False
        )
        report["source"] = {
            "product": project["source_product"],
            "product_version": project["product_version"],
            "native_vendor_format_verified": False,
            "source_controls_are_user_supplied": True,
        }
        with self.db.transaction(write=True, actor=actor) as conn:
            fresh = self._check(conn, job)
            if basis != self.service._basis(conn, fresh):
                raise AccountingError("核验过程中原件、映射或目标目录已变化", 409)
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO migration_validations(id,project_id,job_id,input_version,mapping_version,firm_id,company_id,basis_token,report_json,blocking_count,warning_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project["id"],
                    job["id"],
                    project["version"],
                    mapping["version"],
                    project["firm_id"],
                    project["company_id"],
                    basis,
                    encode(report),
                    report["blocking_count"],
                    report["warning_count"],
                    now(),
                ),
            )
            self._heartbeat(conn, job, {"validation_id": identifier}, len(records), len(records))
        return identifier

    def finish(self, job, status, error_code="", error_detail=""):
        if self.db.is_postgres:
            return self._private(
                "migration_finish",
                (job["id"], job["lease_token"], status, error_code, error_detail, 0),
            )
        with self.db.transaction(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM migration_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                (job["id"], job["lease_token"], now()),
            ).fetchone()
            if not row:
                return False
            if status == "succeeded":
                try:
                    p = self._check(conn, job)
                    if job["phase"] == "validate":
                        v = conn.execute(
                            "SELECT * FROM migration_validations WHERE job_id=?", (job["id"],)
                        ).fetchone()
                        if not v or v["basis_token"] != self.service._basis(conn, p):
                            raise AccountingError("核验结果已过期", 409)
                        conn.execute(
                            "UPDATE migration_projects SET status=?,active_validation_id=?,updated_at=? WHERE id=?",
                            (
                                "needs_attention" if v["blocking_count"] else "ready",
                                v["id"],
                                now(),
                                p["id"],
                            ),
                        )
                except AccountingError:
                    status, error_code, error_detail = (
                        "stale",
                        "inputs_changed",
                        "输入或权限已改变，旧结果不会发布",
                    )
            stamp = now()
            conn.execute(
                "UPDATE migration_jobs SET status=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=?,error_detail=?,updated_at=?,finished_at=? WHERE id=?",
                (status, error_code, error_detail, stamp, stamp, job["id"]),
            )
            return True

    def execute(self, job):
        try:
            self._parse(job) if job["phase"] == "parse" else self._validate(job)
            return self.finish(job, "succeeded")
        except AccountingError as error:
            stale = error.status in {403, 404, 409}
            return self.finish(
                job,
                "stale" if stale else "failed",
                "authorization_or_inputs_changed" if stale else "migration_input_invalid",
                error.detail[:1000],
            )
        except Exception:
            logger.warning("迁移任务暂未完成；租约和检查点将保留，原件与连接详情不记录")
            try:
                return self.finish(
                    job,
                    "failed",
                    "migration_processing_failed",
                    "后台处理失败，请读取任务与原件核验后重试",
                )
            except Exception:
                return False

    def run_once(self):
        job = self.claim()
        if job:
            self.execute(job)
        return job

    def _loop(self):
        with ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="legacy-migration"
        ) as pool:
            pending = set()
            while not self._stop.is_set():
                pending = {f for f in pending if not f.done()}
                try:
                    if len(pending) < self.concurrency:
                        job = self.claim()
                        if job:
                            pending.add(pool.submit(self.execute, job))
                            continue
                except Exception:
                    logger.warning("迁移后台暂不可用，将保留持久任务等待恢复")
                self._stop.wait(self.poll_seconds)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="legacy-migration-dispatcher", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def hashlib_id(job_id, position):
    import hashlib

    return hashlib.sha256((job_id + ":" + str(position)).encode()).hexdigest()


def main(ready_callback=None):
    import argparse
    import os
    import signal
    from pathlib import Path
    from urllib.parse import urlsplit, parse_qs
    from dotenv import load_dotenv
    from app.accounting.database import Database
    from app.storage.factory import create_attachment_store
    from .service import LegacyMigrationService

    parser = argparse.ArgumentParser(description="独立旧账迁移后台，不调用模型或发送外部凭证")
    parser.add_argument("--env-file")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        path = Path(args.env_file).resolve()
        if not path.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(path, override=False)
    url, root = (
        os.getenv("ACCOUNTING_WORKER_DATABASE_URL", ""),
        os.getenv("ACCOUNTING_ATTACHMENT_ROOT", ""),
    )
    if not url:
        parser.error("须显式配置独立worker连接；原件存储须使用共享 Supabase 或本地持久目录")
    production = os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(
        os.getenv(k, "").lower() in {"production", "prod"}
        for k in ("APP_ENV", "ENV", "ENVIRONMENT")
    )
    if production and (
        not url.startswith(("postgres://", "postgresql://"))
        or parse_qs(urlsplit(url).query).get("sslmode", [""])[0]
        not in {"require", "verify-ca", "verify-full"}
    ):
        parser.error("生产迁移worker须独立PostgreSQL账号及显式TLS连接")
    worker_started = False
    worker = None
    database = None
    try:
        storage = create_attachment_store(root)
        storage.verify_private()
        database = Database(url)
        service = LegacyMigrationService(database, storage)
        worker = MigrationWorker(
            service, concurrency=int(os.getenv("ACCOUNTING_MIGRATION_CONCURRENCY", "1"))
        )
        if args.once:
            worker.run_once()
            return 0
        worker.start()
        worker_started = True
        if ready_callback:
            ready_callback()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        return 0
    except Exception:
        logger.error("迁移后台初始化失败，请检查专用账号、迁移和原件配置；连接信息不输出")
        return 1
    finally:
        if worker_started:
            worker.stop()
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
