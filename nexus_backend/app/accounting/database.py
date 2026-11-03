"""SQLite demo and explicitly migrated, restricted PostgreSQL runtime."""

import hashlib
import logging
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from threading import Lock

from .telemetry import (
    SharedTransactionMetrics,
    TransactionMetrics,
    transaction_scope,
    transaction_actor,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
WRITE_LOCK = 6_420_031_786_281_903_101
COMPATIBILITY_INC = "incremental"
COMPATIBILITY_BREAKING = "breaking"
COMPATIBILITY_PATTERN = re.compile(
    r"^\s*--\s*compatibility\s*:\s*(incremental|breaking)\s*$", re.I | re.M
)

logger = logging.getLogger(__name__)


class DatabaseCapacityError(RuntimeError):
    """No connection was borrowed; do not infer the outcome of a whole workflow."""

    def __init__(self, message, *, reason="unavailable"):
        super().__init__(message)
        self.reason = reason


def migration_files(dialect):
    return sorted((MIGRATIONS / dialect).glob("[0-9][0-9][0-9]_*.sql"))


def migration_digest(path):
    return hashlib.sha256(
        path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
    ).hexdigest()


def migration_compatibility(path):
    """Expand-and-contract classification; unknown or unmarked files stay breaking.

    A migration that only adds tables, columns, indexes or functions can be
    declared ``-- compatibility: incremental`` so a previously deployed image
    may keep starting (with a warning) while the database is ahead of its code.
    Anything else defaults to ``breaking`` and still refuses to start.
    """
    head = path.read_text(encoding="utf-8")[:2048]
    match = COMPATIBILITY_PATTERN.search(head)
    return match.group(1).lower() if match else COMPATIBILITY_BREAKING


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

    def begin(self, *, write=False, role="accounting_app", user_id="", firm_id=""):
        """Install transaction-local identity in one pooler-compatible round trip.

        ClientCursor uses the simple query protocol, not pipeline/prepared
        statements. Values remain psycopg-bound; only allowlisted roles and
        fixed transaction clauses are composed. Never expose a connection to
        domain code before every setup statement has completed successfully.
        """
        self._require_open()
        if role not in {
            "accounting_app",
            "accounting_system",
            "accounting_worker",
            "accounting_projection",
        }:
            raise ValueError("数据库事务角色不受支持")
        from psycopg import ClientCursor
        from psycopg.pq import TransactionStatus

        if self.raw.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("数据库连接已有未完成事务")
        begin = "BEGIN" if write else "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
        with ClientCursor(self.raw) as cursor:
            cursor.execute(
                begin
                + "; SET LOCAL ROLE "
                + role
                + "; SET LOCAL search_path TO accounting,pg_catalog; "
                "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
                (user_id, firm_id),
            )
            while cursor.nextset():
                pass

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


def validate_database_locator(value: str) -> None:
    """Reject ambiguous configuration before creating files or opening a pool.

    This adapter accepts PostgreSQL URIs or filesystem SQLite paths, not libpq
    keyword conninfo. Never echo the supplied value: it may contain a password.
    """
    postgres = value.startswith(("postgres://", "postgresql://"))
    if (
        not value
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
        or (not postgres and "://" in value)
        or (
            not postgres
            and re.search(
                r"(?i)(?:^|[\s/\\])(?:host|hostaddr|user|password|dbname|service|port|sslmode|options)\s*=",
                value,
            )
        )
    ):
        raise ValueError("数据库地址格式无效；请使用 PostgreSQL URI 或本地 SQLite 文件路径")


class Database:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        default_role: str = "accounting_app",
        validation_role: str | None = None,
    ):
        if default_role not in {"accounting_app", "accounting_projection"}:
            raise ValueError("数据库默认角色不受支持")
        validation_role = validation_role or default_role
        if validation_role not in {"accounting_app", "accounting_projection"}:
            raise ValueError("数据库校验角色不受支持")
        self.default_role = default_role
        self.validation_role = validation_role
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
        validate_database_locator(self.path)
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
            from psycopg_pool import PoolTimeout, TooManyRequests

            pool = self._postgres_pool()
            try:
                return PostgresConnection(pool.getconn(), pool)
            except TooManyRequests:
                raise DatabaseCapacityError(
                    "数据库连接池等待队列已满，请稍后核对任务状态",
                    reason="queue_full",
                ) from None
            except PoolTimeout:
                raise DatabaseCapacityError(
                    "数据库连接池等待超时，请稍后核对任务状态",
                    reason="wait_timeout",
                ) from None
            except Exception:
                # Pool errors may contain host/user data; API/log callers only
                # receive a useful generic diagnostic, never the supplied DSN.
                raise RuntimeError(
                    "数据库连接暂不可用或连接池等待超时，请检查容量和连接配置"
                ) from None
        from .sqlite_connection import AccountingSQLiteConnection

        conn = sqlite3.connect(self.path, timeout=15, factory=AccountingSQLiteConnection)
        # Savepoint statement journals otherwise depend on the process temp
        # directory; keep SQLite's small demo/test temp store in memory.
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.row_factory = sqlite3.Row
        from .invariants import money_to_cents

        conn.create_function("accounting_money_cents", 1, money_to_cents, deterministic=True)
        conn.create_function(
            "accounting_sha256",
            1,
            lambda text: hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
            deterministic=True,
        )
        conn.create_function(
            "accounting_actor_user", 0, lambda: getattr(transaction_actor.get(), "user_id", "")
        )
        conn.create_function(
            "accounting_actor_firm", 0, lambda: getattr(transaction_actor.get(), "firm_id", "")
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        preparation_triggers = {
            "rr_draft_insert",
            "rr_draft_update",
            "rr_line_insert",
            "rr_line_update",
            "rr_line_delete",
            "rr_aux_insert",
            "rr_aux_update",
            "rr_aux_delete",
        }
        internal_writers = {
            "draft_preparations": preparation_triggers,
            "draft_preparation_history": preparation_triggers,
            "draft_review_receipts": {"rr_draft_approve"},
            "voucher_review_completions": {"rr_posting_complete"},
        }

        def authorize(action, table, column, database_name, trigger):
            # Runtime SQL may inspect enforcement settings but cannot turn off
            # foreign keys/checks or write the schema around review receipts.
            # The connection sets its safe defaults before installing this
            # authorizer; only the dedicated migration connection opts out.
            if (
                action == sqlite3.SQLITE_PRAGMA
                and column is not None
                and str(table).lower()
                in {
                    "foreign_keys",
                    "writable_schema",
                    "ignore_check_constraints",
                }
            ):
                return sqlite3.SQLITE_DENY
            # A runtime connection cannot replace one of the trusted trigger
            # names or alter its supporting schema. Migrations use a separate
            # private connection with this authorizer disabled.
            ddl = {
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_DROP_VIEW,
                sqlite3.SQLITE_CREATE_TRIGGER,
                sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
                sqlite3.SQLITE_DROP_TEMP_TRIGGER,
                sqlite3.SQLITE_ALTER_TABLE,
            }
            if action in ddl and (
                str(table).startswith("rr_")
                or table in internal_writers
                or column in internal_writers
            ):
                return sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                allowed = internal_writers.get(table)
                if allowed is not None and trigger not in allowed:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
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
                # Backpressure, not more DB connections: a 50-request burst
                # needs 46 wait slots with four connections (50 during cold
                # start). Reserve a small margin for diagnostic/worker reads.
                # Reuse psycopg's FIFO queue and ONE getconn deadline, including
                # connection checks; no outer retry, sleep loop or second queue.
                waiting = self._bounded_setting("ACCOUNTING_DB_POOL_MAX_WAITING", 64, 1, 256)
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
        return {
            "enabled": True,
            "scope": "current_process",
            **capacity,
            "pool_max_waiting": self._pool.max_waiting,
            "pool_timeout_seconds": self._pool.timeout,
        }

    def assert_restore_hold(self):
        """Fail closed when the target database carries an unreleased hold.

        The marker table is intentionally global/operational, not tenant data;
        it is checked after connecting so PG/remote targets do not depend on a
        local restore directory or an optional environment variable.
        """
        try:
            with closing(self.connect()) as conn:
                if getattr(self, "is_postgres", False):
                    conn.begin(write=False, role=self.validation_role, user_id="", firm_id="")
                row = conn.execute(
                    "SELECT hold,instance_id,restore_id FROM restore_state WHERE hold<>FALSE OR hold IS NULL LIMIT 1"
                ).fetchone()
        except Exception as error:
            # Only an explicitly absent legacy table is compatible. Permission,
            # connection and malformed-state failures must never enable serving.
            missing_sqlite = isinstance(error, sqlite3.OperationalError) and str(error) == (
                "no such table: restore_state"
            )
            if missing_sqlite:
                return
            raise SystemExit("无法核验数据库恢复隔离状态，拒绝启动 API/worker") from None
        if row is not None:
            raise SystemExit(
                "数据库处于恢复隔离核对状态，禁止启动正常 API/worker；"
                "请先核对在途任务与未知结果，再按受控流程释放 hold"
            )

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
                if self.is_postgres:
                    conn.begin(write=True, user_id=actor.user_id, firm_id=actor.firm_id)
                    conn.execute(
                        "SELECT accounting.save_transaction_bucket("
                        + ",".join("?" for _ in payload)
                        + ")",
                        payload,
                    )
                else:
                    conn.execute("BEGIN IMMEDIATE")
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
                conn.execute("SET LOCAL ROLE " + self.validation_role)
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
                unknown = set(records) - {int(path.name.split("_", 1)[0]) for path in files}
                if unknown:
                    has_column = bool(
                        conn.execute(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema='accounting' AND table_name='schema_migrations' "
                            "AND column_name='compatibility'"
                        ).fetchone()
                    )
                    compatibility = {}
                    if has_column:
                        marks = ",".join("?" for _ in unknown)
                        compatibility = {
                            row["version"]: row["compatibility"]
                            for row in conn.execute(
                                "SELECT version,compatibility FROM accounting.schema_migrations "
                                "WHERE version IN (" + marks + ")",
                                tuple(sorted(unknown)),
                            )
                        }
                    if any(compatibility.get(version) != COMPATIBILITY_INC for version in unknown):
                        raise RuntimeError("数据库包含当前代码未知的迁移版本，请先更新代码")
                    logger.warning(
                        "数据库包含更高版本且声明为兼容增量的迁移 %s，按向前兼容策略继续启动；"
                        "回滚旧镜像前请确认这些迁移仅新增表、列、索引或函数",
                        sorted(unknown),
                    )
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
            # Only this dedicated schema-migration connection may backfill
            # historical unverified responsibility. Runtime connections retain
            # the fixed-trigger write restriction throughout their lifetime.
            conn.set_authorizer(None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
            files = migration_files("sqlite")
            known = {int(path.name.split("_", 1)[0]) for path in files}
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            unknown = existing - known
            if unknown:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
                compatibility = {}
                if "compatibility" in columns:
                    marks = ",".join("?" for _ in unknown)
                    compatibility = {
                        row[0]: row[1]
                        for row in conn.execute(
                            "SELECT version,compatibility FROM schema_migrations "
                            "WHERE version IN (" + marks + ")",
                            tuple(sorted(unknown)),
                        )
                    }
                if any(compatibility.get(version) != COMPATIBILITY_INC for version in unknown):
                    raise RuntimeError("SQLite 包含当前代码未知的迁移版本，请先更新代码")
                logger.warning(
                    "SQLite 数据库包含更高版本且声明为兼容增量的迁移 %s，按向前兼容策略继续启动",
                    sorted(unknown),
                )
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
                    columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")
                    }
                    if "compatibility" in columns:
                        conn.execute(
                            "INSERT INTO schema_migrations(version,checksum,compatibility) "
                            "VALUES(?,?,?)",
                            (version, checksum, migration_compatibility(path)),
                        )
                    else:
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
        self,
        write: bool = False,
        actor=None,
        system: bool = False,
        projection: bool = False,
        *,
        company_id=None,
        firm_id=None,
        write_scope="global",
        jit=None,
    ):
        privileged = system or projection or self.default_role == "accounting_projection"
        if write_scope not in {"global", "company"}:
            raise ValueError("写锁范围仅支持 global 或 company")
        if write_scope == "company" and (
            not write or not company_id or (actor is None and not privileged)
        ):
            raise ValueError(
                "企业写事务必须显式提供 actor（或 system/projection）与 company_id、write=True"
            )
        if write_scope == "company" and privileged and not firm_id:
            raise ValueError("系统/投影身份的企业写事务必须显式提供 firm_id")
        if jit is not None and write:
            raise ValueError("JIT 计划设置只允许用于只读事务，避免写路径携带隐式计划假设")
        if self.is_postgres and actor is None and not privileged:
            raise ValueError("PostgreSQL 事务必须提供 actor 或显式使用 system/projection 身份事务")
        if actor is not None and privileged:
            raise ValueError("actor 与 system/projection 身份事务不能混用")
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
        actor_token = transaction_actor.set(actor)
        try:
            if self.is_postgres:
                conn.begin(
                    write=write,
                    role=(
                        "accounting_projection"
                        if projection
                        else "accounting_system"
                        if system
                        else self.default_role
                    ),
                    user_id=getattr(actor, "user_id", ""),
                    firm_id=getattr(actor, "firm_id", ""),
                )
                if jit is False:
                    # Transaction-local only: pooling must never inherit this,
                    # and the database default stays untouched.
                    conn.execute("SET LOCAL jit = off")
                if write:
                    lock_started = perf_counter()
                    from .locking import write_lock

                    write_lock(
                        conn,
                        scope=write_scope,
                        firm_id=actor.firm_id if actor else firm_id,
                        company_id=company_id,
                        system=system,
                        projection=projection or self.default_role == "accounting_projection",
                    )
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
            metric_actor = transaction_actor.get()
            transaction_scope.reset(metric_token)
            transaction_actor.reset(actor_token)
            conn.close()
            self.metrics.record(
                firm_id=getattr(metric_actor, "firm_id", None),
                company_id=next(iter(companies)) if len(companies) == 1 else None,
                write=write,
                outcome=outcome,
                connect_ms=(connected - started) * 1000,
                lock_wait_ms=lock_wait_ms,
                transaction_ms=(perf_counter() - connected) * 1000,
            )
            self.shared_metrics.record(
                actor=metric_actor,
                company_id=next(iter(companies)) if len(companies) == 1 else None,
                write=write,
                outcome=outcome,
                connect_ms=(connected - started) * 1000,
                lock_wait_ms=lock_wait_ms,
                transaction_ms=(perf_counter() - connected) * 1000,
            )
