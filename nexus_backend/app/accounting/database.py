"""SQLite demo and explicitly migrated, restricted PostgreSQL runtime."""
import hashlib
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
WRITE_LOCK = 6_420_031_786_281_903_101


def migration_files(dialect):
    return sorted((MIGRATIONS / dialect).glob("[0-9][0-9][0-9]_*.sql"))


def migration_digest(path):
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()


class CompatibleRow(Mapping):
    """Both SQLite Row indexing conventions, preserving integer amounts."""
    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(int(x) if isinstance(x, Decimal) and x == x.to_integral_value() else x for x in values)
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
        following = statement[index:index + 2]
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
    def __init__(self, connection):
        self.raw = connection

    def execute(self, statement, parameters=None):
        return self.raw.execute(postgres_sql(statement), parameters)

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        # psycopg executemany may enter pipeline mode, which Supabase's shared
        # transaction pooler does not support. Keep each execution sequential.
        sql = postgres_sql(statement)
        for values in parameters:
            cursor.execute(sql, values, prepare=False)
        return cursor

    def executescript(self, script):
        return self.raw.execute(script, prepare=False)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path if path is not None else os.getenv("ACCOUNTING_DATABASE_URL") or "data/accounting.db")
        self.is_postgres = self.path.startswith(("postgres://", "postgresql://"))
        self.dialect = "postgres" if self.is_postgres else "sqlite"
        if self.is_postgres:
            self._validate_postgres()
        else:
            if self.path == ":memory:":
                raise ValueError("请使用临时文件数据库，以确保多连接事务验证有效")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._migrate_sqlite()

    def connect(self):
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as error:
                raise RuntimeError("PostgreSQL 需要安装 psycopg[binary]>=3.2,<4") from error
            # Supabase transaction pooler cannot retain prepared statements.
            return PostgresConnection(psycopg.connect(self.path, autocommit=True, prepare_threshold=None, connect_timeout=10, row_factory=compatible_row_factory))
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _validate_postgres(self):
        try:
            with closing(self.connect()) as conn:
                principal = conn.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if principal["rolsuper"] or principal["rolbypassrls"]:
                    raise RuntimeError("运行服务禁止使用超级用户或 BYPASSRLS 数据库账号，请运行 migrate_accounting.py provision-login 建立受限账号")
                conn.execute("BEGIN READ ONLY")
                conn.execute("SET LOCAL ROLE accounting_app")
                effective = conn.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if effective["rolsuper"] or effective["rolbypassrls"]:
                    raise RuntimeError("accounting_app 必须为非超级用户且不得具备 BYPASSRLS")
                records = {row["version"]: row["checksum"] for row in conn.execute("SELECT version,checksum FROM accounting.schema_migrations")}
                files = migration_files("postgres")
                if set(records) - {int(path.name.split("_", 1)[0]) for path in files}:
                    raise RuntimeError("数据库包含当前代码未知的迁移版本，请先更新代码")
                for path in files:
                    version = int(path.name.split("_", 1)[0])
                    if records.get(version) != migration_digest(path):
                        raise RuntimeError("PostgreSQL 迁移缺失或校验不符，请使用管理员连接显式执行 migrate_accounting.py migrate")
                conn.commit()
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("PostgreSQL 连接或迁移验证失败；请检查受限账号、数据库配置及迁移状态") from None

    def _migrate_sqlite(self):
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            files = migration_files("sqlite")
            known = {int(path.name.split("_", 1)[0]) for path in files}
            existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            if existing - known:
                raise RuntimeError("SQLite 包含当前代码未知的迁移版本，请先更新代码")
            for path in files:
                version = int(path.name.split("_", 1)[0])
                checksum = migration_digest(path)
                record = conn.execute("SELECT checksum FROM schema_migrations WHERE version=?", (version,)).fetchone()
                if record:
                    if record[0] != checksum:
                        raise RuntimeError(f"SQLite 迁移 {version} 校验不符，禁止修改已应用的迁移文件")
                    continue
                try:
                    # executescript commits prior work; include BEGIN in script.
                    conn.executescript("BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8"))
                    if conn.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError(f"SQLite 迁移 {version} 后发现外键不一致，已回滚")
                    conn.execute("INSERT INTO schema_migrations(version,checksum) VALUES(?,?)", (version, checksum))
                    conn.execute(f"PRAGMA user_version={version}")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def transaction(self, write: bool = False, actor=None, system: bool = False):
        if self.is_postgres and actor is None and not system:
            raise ValueError("PostgreSQL 事务必须提供 actor 或显式使用 system=True 的身份事务")
        if actor is not None and system:
            raise ValueError("actor 与 system 身份事务不能混用")
        conn = self.connect()
        try:
            if self.is_postgres:
                conn.execute("BEGIN" if write else "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL ROLE accounting_system" if system else "SET LOCAL ROLE accounting_app")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                conn.execute("SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)", (getattr(actor, "user_id", ""), getattr(actor, "firm_id", "")))
                if write:
                    conn.execute("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK,))
            else:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
