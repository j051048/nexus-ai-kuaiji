"""Reuse repeated plain evidence reads inside one assistant read snapshot only.

This is deliberately not a database cache. The instance lives on the stack of
detail/workspace's read-only transaction; enqueue, workers and writes never use
it. PostgreSQL uses REPEATABLE READ READ ONLY and SQLite keeps its read snapshot.
Native capabilities, clocks, locks and SELECTs without FROM remain uncached.
"""

import re

# SQL syntax that uses parentheses plus the deterministic scalar/aggregate
# functions currently used by the domain readers. Unknown functions execute
# normally, even if a future helper introduces an unqualified volatile call.
_PURE_CALLS = {
    "in",
    "exists",
    "and",
    "or",
    "not",
    "as",
    "select",
    "join",
    "values",
    "filter",
    "over",
    "cast",
    "count",
    "sum",
    "max",
    "min",
    "coalesce",
    "nullif",
    "ifnull",
    "length",
    "lower",
    "upper",
    "trim",
    "substr",
    "substring",
    "abs",
    "round",
    "json_extract",
    "json_each",
    "json_array_length",
    "json_valid",
    "jsonb_exists",
    "jsonb_extract_path_text",
}


class _Rows:
    def __init__(self, rows):
        self.rows, self.index = rows, 0

    def fetchone(self):
        if self.index == len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row

    def fetchall(self):
        rows = list(self.rows[self.index :])
        self.index = len(self.rows)
        return rows

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class AssistantSnapshotReads:
    def __init__(self, conn):
        self._conn, self._rows = conn, {}

    def execute(self, sql, args=()):
        if not sql.lstrip().upper().startswith("SELECT ") or ";" in sql:
            raise RuntimeError("助手只读快照不允许写操作或多条语句")
        # Only plain table SELECTs emitted by these existing domain guards.
        # Functions (including SECURITY DEFINER capabilities) always execute.
        cacheable = (
            not re.search(
                r"\b(?:FOR\s+(?:UPDATE|SHARE)|NOWAIT|SKIP\s+LOCKED|clock_timestamp|random|nextval|setval|set_config|pg_\w+|strftime|julianday)\b",
                sql,
                re.I,
            )
            and not re.search(r"\baccounting\.\w+\s*\(", sql, re.I)
            and all(
                name.lower() in _PURE_CALLS for name in re.findall(r"\b([a-zA-Z_]\w*)\s*\(", sql)
            )
            and " FROM " in sql.upper()
            and ";" not in sql
        )
        if not cacheable:
            return self._conn.execute(sql, args)
        key = (sql, tuple(args))
        if key not in self._rows:
            self._rows[key] = tuple(self._conn.execute(sql, args).fetchall())
        # A fresh replay cursor prevents one guard consuming another's rows.
        return _Rows(self._rows[key])
