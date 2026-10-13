"""SQLite demo and explicitly migrated, restricted PostgreSQL runtime."""

import hashlib
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from threading import Lock

from .telemetry import SharedTransactionMetrics, TransactionMetrics, transaction_scope

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
WRITE_LOCK = 6_420_031_786_281_903_101


def migration_files(dialect):
    return sorted((MIGRATIONS / dialect).glob("[0-9][0-9][0-9]_*.sql"))


def migration_digest(path):
    return hashlib.sha256(
        path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
    ).hexdigest()


class CompatibleRow(Mapping):
    """Both SQLite Row indexing conventions, preserving integer amounts."""

    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(
            int(x) if isinstance(x, Decimal) and x == x.to_integral_value() else x for x in values
        )
        self._data = dict(zip(self._names, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self._data[key]

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)


def compatible_row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: CompatibleRow(names, values)


def postgres_sql(statement):
    """Translate bind markers without touching quoted text or SQL comments."""
    result, index, state, dollar = [], 0, "normal", ""
    while index < len(statement):
        char = statement[index]
        following = statement[index : index + 2]
        if state == "normal":
            if following in ("--", "/*"):
                state = "line" if following == "--" else "comment"
                result.append(following)
                index += 2
                continue
            if char in ("'", '"'):
                state = char
            elif char == "$":
                match = re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", statement[index:])
                if match:
                    dollar = match.group()
                    state = "dollar"
                    result.append(dollar)
                    index += len(dollar)
                    continue
            elif char == "?":
                char = "%s"
        elif state in ("'", '"') and char == state:
            if index + 1 < len(statement) and statement[index + 1] == state:
                result.append(char * 2)
                index += 2
                continue
            state = "normal"
        elif state == "line" and char == "\n":
            state = "normal"
        elif state == "comment" and following == "*/":
            result.append(following)
            state = "normal"
            index += 2
            continue
        elif state == "dollar" and statement.startswith(dollar, index):
            result.append(dollar)
            state = "normal"
            index += len(dollar)
            continue
        result.append(char)
        index += 1
    sql = "".join(result)
    if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.I):
        sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class PostgresConnection:
    def __init__(self, connection, pool=None):
        self.raw = connection
        self.pool = pool
        self._closed = False

    def _require_open(self):
        if self._closed:
            raise RuntimeError("数据库连接已归还连接池")

    def execute(self, statement, parameters=None):
        self._require_open()
        return self.raw.execute(postgres_sql(statement), parameters)

    def executemany(self, statement, parameters):
        self._require_open()
        cursor = self.raw.cursor()
        # psycopg executemany may enter pipeline mode, which Supabase's shared
        # transaction pooler does not support. Keep each execution sequential.
        sql = postgres_sql(statement)
        for values in parameters:
            cursor.execute(sql, values, prepare=False)
        return cursor

    def executescript(self, script):
        self._require_open()
        return self.raw.execute(script, prepare=False)

    def commit(self):
        self._require_open()
        self.raw.commit()

    def rollback(self):
        self._require_open()
        self.raw.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        # A failed or unfinished transaction must never reach the next borrower.
        # Identity/search_path are SET LOCAL only, so rollback also clears them
        # on Supabase's transaction pooler without session-level RESET commands.
        try:
            self.raw.rollback()
        except Exception:
            self.raw.close()
        finally:
            if self.pool is None:
                self.raw.close()
            else:
                self.pool.putconn(self.raw)


class Database:
    def __init__(self, path: str | Path | None = None):
        self._pool = None
        self._pool_lock = Lock()
        self._closed = False
        self.metrics = TransactionMetrics()
        self.shared_metrics = SharedTransactionMetrics(self)
        self.path = str(
            path
            if path is not None
            else os.getenv("ACCOUNTING_DATABASE_URL") or "data/accounting.db"
        )
        self.is_postgres = self.path.startswith(("postgres://", "postgresql://"))
        self.dialect = "postgres" if self.is_postgres else "sqlite"
        if self.is_postgres:
            try:
                self._validate_postgres()
            except BaseException:
                self.close()
                raise
        else:
            if self.path == ":memory:":
                raise ValueError("请使用临时文件数据库，以确保多连接事务验证有效")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._migrate_sqlite()
        if self.is_postgres:
            self.shared_metrics.start()

    def connect(self):
        if self._closed:
            raise RuntimeError("数据库已关闭")
        if self.is_postgres:
            pool = self._postgres_pool()
            try:
                return PostgresConnection(pool.getconn(), pool)
            except Exception:
                # Pool errors may contain host/user data; API/log callers only
                # receive a useful generic diagnostic, never the supplied DSN.
                raise RuntimeError(
                    "数据库连接暂不可用或连接池等待超时，请检查容量和连接配置"
                ) from None
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @staticmethod
    def _bounded_setting(name, default, low, high, kind=int):
        try:
            value = kind(os.getenv(name, str(default)))
            if not low <= value <= high:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(f"{name} 必须在 {low}–{high} 范围内") from None
        return value

    def _postgres_pool(self):
        with self._pool_lock:
            if self._closed:
                raise RuntimeError("数据库已关闭")
            if self._pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                except ImportError:
                    raise RuntimeError("PostgreSQL 连接复用需要安装 psycopg_pool>=3.2,<4") from None
                maximum = self._bounded_setting("ACCOUNTING_DB_POOL_MAX", 4, 1, 32)
                minimum = self._bounded_setting("ACCOUNTING_DB_POOL_MIN", 0, 0, maximum)
                timeout = self._bounded_setting(
                    "ACCOUNTING_DB_POOL_TIMEOUT_SECONDS", 5, 0.1, 30, float
                )
                waiting = self._bounded_setting("ACCOUNTING_DB_POOL_MAX_WAITING", 32, 1, 256)
                self._pool = ConnectionPool(
                    self.path,
                    min_size=minimum,
                    max_size=maximum,
                    timeout=timeout,
                    max_waiting=waiting,
                    max_idle=60,
                    max_lifetime=1800,
                    reconnect_timeout=10,
                    num_workers=1,
                    name="accounting-runtime",
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": None,
                        "connect_timeout": 10,
                        "row_factory": compatible_row_factory,
                    },
                    check=ConnectionPool.check_connection,
                    open=False,
                )
                self._pool.open()
            return self._pool

    def pool_snapshot(self):
        if self._pool is None:
            return {"enabled": False, "scope": "current_process"}
        # Only operational capacity is public here. Global request/error counts
        # would combine traffic of other firms sharing this process.
        values = self._pool.get_stats()
        capacity = {
            key: values[key]
            for key in ("pool_min", "pool_max", "pool_size", "pool_available")
            if key in values
        }
        return {"enabled": True, "scope": "current_process", **capacity}

    def close(self):
        if self._closed:
            return
        self.shared_metrics.stop()
        self._closed = True
        with self._pool_lock:
            if self._pool:
                self._pool.close(timeout=5)

    def persist_transaction_bucket(self, actor, payload):
        """Independent diagnostic write, never invoked inside a read transaction.

        PostgreSQL rechecks current company authorization in the restricted
        function. Ordinary app/worker logins cannot access telemetry tables
        directly. Snapshot replacement makes flush retries idempotent.
        """
        with closing(self.connect()) as conn:
            try:
                conn.execute("BEGIN" if self.is_postgres else "BEGIN IMMEDIATE")
                if self.is_postgres:
                    conn.execute("SET LOCAL ROLE accounting_app")
                    conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                    conn.execute(
                        "SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)",
                        (actor.user_id, actor.firm_id),
                    )
                    conn.execute(
                        "SELECT accounting.save_transaction_bucket("
                        + ",".join("?" for _ in payload)
                        + ")",
                        payload,
                    )
                else:
                    from .access import AccessControl

                    AccessControl().company(conn, actor, payload[1])
                    fields = "firm_id,company_id,stream_id,bucket_minute,tx_count,write_count,rollback_count,connection_sum,lock_sum,transaction_sum,connection_max,lock_max,transaction_max"
                    updates = ",".join(
                        f"{field}=excluded.{field}" for field in fields.split(",")[4:]
                    )
                    conn.execute(
                        "INSERT INTO transaction_metric_buckets("
                        + fields
                        + ") VALUES("
                        + ",".join("?" for _ in payload)
                        + ") ON CONFLICT(firm_id,company_id,stream_id,bucket_minute) DO UPDATE SET "
                        + updates,
                        payload,
                    )
                    conn.execute(
                        "DELETE FROM transaction_metric_buckets WHERE rowid IN (SELECT rowid FROM transaction_metric_buckets WHERE bucket_minute<CAST(strftime('%s','now') AS INTEGER)/60-16 ORDER BY bucket_minute LIMIT 256)"
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _validate_postgres(self):
        try:
            with closing(self.connect()) as conn:
                principal = conn.execute(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
                if principal["rolsuper"] or principal["rolbypassrls"]:
                    raise RuntimeError(
                        "运行服务禁止使用超级用户或 BYPASSRLS 数据库账号，请运行 migrate_accounting.py provision-login 建立受限账号"
                    )
                conn.execute("BEGIN READ ONLY")
                conn.execute("SET LOCAL ROLE accounting_app")
                effective = conn.execute(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
                if effective["rolsuper"] or effective["rolbypassrls"]:
                    raise RuntimeError("accounting_app 必须为非超级用户且不得具备 BYPASSRLS")
                records = {
                    row["version"]: row["checksum"]
                    for row in conn.execute(
                        "SELECT version,checksum FROM accounting.schema_migrations"
                    )
                }
                files = migration_files("postgres")
                if set(records) - {int(path.name.split("_", 1)[0]) for path in files}:
                    raise RuntimeError("数据库包含当前代码未知的迁移版本，请先更新代码")
                for path in files:
                    version = int(path.name.split("_", 1)[0])
                    if records.get(version) != migration_digest(path):
                        raise RuntimeError(
                            "PostgreSQL 迁移缺失或校验不符，请使用管理员连接显式执行 migrate_accounting.py migrate"
                        )
                conn.commit()
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError(
                "PostgreSQL 连接或迁移验证失败；请检查受限账号、数据库配置及迁移状态"
            ) from None

    def _migrate_sqlite(self):
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            files = migration_files("sqlite")
            known = {int(path.name.split("_", 1)[0]) for path in files}
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            if existing - known:
                raise RuntimeError("SQLite 包含当前代码未知的迁移版本，请先更新代码")
            for path in files:
                version = int(path.name.split("_", 1)[0])
                checksum = migration_digest(path)
                record = conn.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=?", (version,)
                ).fetchone()
                if record:
                    if record[0] != checksum:
                        raise RuntimeError(
                            f"SQLite 迁移 {version} 校验不符，禁止修改已应用的迁移文件"
                        )
                    continue
                try:
                    # executescript commits prior work; include BEGIN in script.
                    conn.executescript("BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8"))
                    if conn.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError(f"SQLite 迁移 {version} 后发现外键不一致，已回滚")
                    conn.execute(
                        "INSERT INTO schema_migrations(version,checksum) VALUES(?,?)",
                        (version, checksum),
                    )
                    conn.execute(f"PRAGMA user_version={version}")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(
        self, write: bool = False, actor=None, system: bool = False, *, company_id=None
    ):
        if self.is_postgres and actor is None and not system:
            raise ValueError("PostgreSQL 事务必须提供 actor 或显式使用 system=True 的身份事务")
        if actor is not None and system:
            raise ValueError("actor 与 system 身份事务不能混用")
        started = perf_counter()
        try:
            conn = self.connect()
        except Exception:
            self.metrics.record(
                firm_id=getattr(actor, "firm_id", None),
                company_id=company_id,
                write=write,
                outcome="connection_failed",
                connect_ms=(perf_counter() - started) * 1000,
            )
            raise
        connected = perf_counter()
        lock_wait_ms = 0
        outcome = "rolled_back"
        companies = {company_id} if company_id else set()
        metric_token = transaction_scope.set(companies)
        try:
            if self.is_postgres:
                conn.execute(
                    "BEGIN" if write else "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                conn.execute(
                    "SET LOCAL ROLE accounting_system"
                    if system
                    else "SET LOCAL ROLE accounting_app"
                )
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                conn.execute(
                    "SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)",
                    (getattr(actor, "user_id", ""), getattr(actor, "firm_id", "")),
                )
                if write:
                    lock_started = perf_counter()
                    conn.execute("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK,))
                    lock_wait_ms = (perf_counter() - lock_started) * 1000
            else:
                lock_started = perf_counter()
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                if write:
                    lock_wait_ms = (perf_counter() - lock_started) * 1000
            yield conn
            conn.commit()
            outcome = "committed"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            transaction_scope.reset(metric_token)
            self.metrics.record(
                firm_id=getattr(actor, "firm_id", None),
                company_id=next(iter(companies)) if len(companies) == 1 else None,
                write=write,
                outcome=outcome,
                connect_ms=(connected - started) * 1000,
                lock_wait_ms=lock_wait_ms,
                transaction_ms=(perf_counter() - connected) * 1000,
            )
            self.shared_metrics.record(
                actor=actor,
                company_id=next(iter(companies)) if len(companies) == 1 else None,
                write=write,
                outcome=outcome,
                connect_ms=(connected - started) * 1000,
                lock_wait_ms=lock_wait_ms,
                transaction_ms=(perf_counter() - connected) * 1000,
            )
