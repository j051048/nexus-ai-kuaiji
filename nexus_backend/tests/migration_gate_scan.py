"""静态扫描迁移里的表级隔离属性（供结构测试使用）。

只做「能证伪」的窄解析：注释与字符串先被等长掩码，再做表名与触发器配对。
动态安装（``EXECUTE format('...%I...')`` + ``VALUES``/``ARRAY`` 表名列表，或同一语句
里的字面量参数）与直接语句等价对待，因为线上 PG 里两者装出来的触发器形态相同。

三类保护分开记录，避免互相冒充：

* ``rls_enabled`` 只认 ``ENABLE ROW LEVEL SECURITY``；``FORCE`` 是加固，不是启用。
* ``tenant_isolation`` 要求策略里出现真实的租户判定（``company_id = ...``、
  ``book_allowed(...)`` 等）；``company_id = company_id``、``true`` 这类恒真条件不算。
* ``gate_functions`` 是公司写入围栏（取公司级咨询锁的触发器）。RLS 策略只做隔离，
  不能替代写入围栏。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

IDENT = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?accounting\.([a-z_][a-z0-9_]*)\s*\(", re.I
)
ADD_COLUMN = re.compile(
    rf"ALTER\s+TABLE\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?({IDENT})",
    re.I,
)
RLS_ENABLE = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
    re.I,
)
RLS_FORCE = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
    re.I,
)
POLICY = re.compile(
    rf"CREATE\s+POLICY\s+{IDENT}\s+ON\s+accounting\.([a-z_][a-z0-9_]*)([^;]*);", re.I | re.S
)
GATE_TRIGGER = re.compile(
    rf"CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+{IDENT}[^;]*?\bON\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)"
    r"[^;]*?EXECUTE\s+FUNCTION\s+accounting\.([a-z_][a-z0-9_]*)\s*\(",
    re.I | re.S,
)
FORMAT_BODY = re.compile(r"format\s*\(\s*'((?:[^']|'')*)'", re.I)
SINGLE_QUOTED = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*)'")
ARRAY_LIST = re.compile(r"ARRAY\s*\[([^\]]*)\]", re.I | re.S)
VALUES_LIST = re.compile(r"VALUES(.*?)\)\s*AS\s+\w+", re.S | re.I)
STATEMENT_END = re.compile(r";|\bEND\b", re.I)

TENANT_COLUMNS = ("company_id", "firm_id")
TENANT_TOKENS = ("company_id", "firm_id", "book_allowed", "actor_firm", "company_visible")
TENANT_HELPERS = ("book_allowed", "actor_firm", "company_visible")
SELF_COMPARE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=\s*\1\b", re.I)
RECOGNIZED_GATES = {
    "company_write_table_guard",
    "close_dependency_write_guard",
    "monthly_delivery_guard",
    "tax_workpaper_guard",
    "<dynamic>",
}
# 围栏函数进白名单的前提：函数体调用 accounting.lock_company_write（或
# lock_dispatch_write/lock_legacy_write）真正取锁；扫描器只认名字，真锁由
# PostgreSQL 运行时用例（tmp_test/p03_pg_workpaper.py）实证。


def mask_sql(source: str) -> str:
    """把注释、单引号字符串、美元引用体替换为等长空格，保留换行。"""

    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        pair = source[index : index + 2]
        if pair == "--":
            end = source.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
            continue
        if pair == "/*":
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append("".join("\n" if c == "\n" else " " for c in source[index:end]))
            index = end
            continue
        if char == "'":
            end = index + 1
            while end < length:
                if source[end : end + 2] == "''":
                    end += 2
                    continue
                if source[end] == "'":
                    end += 1
                    break
                end += 1
            out.append("".join("\n" if c == "\n" else " " for c in source[index:end]))
            index = end
            continue
        if char == "$":
            tag_match = re.match(r"\$[A-Za-z0-9_]*\$", source[index:])
            if tag_match:
                tag = tag_match.group(0)
                end = source.find(tag, index + len(tag))
                end = length if end < 0 else end + len(tag)
                out.append("".join("\n" if c == "\n" else " " for c in source[index:end]))
                index = end
                continue
        out.append(char)
        index += 1
    return "".join(out)


def mask_comments(source: str) -> str:
    """只掩码注释，保留字符串字面量：动态 DDL 的模板就在字符串里。"""

    out = list(source)
    index, length = 0, len(source)
    while index < length:
        if source.startswith("--", index):
            end = source.find("\n", index)
            end = length if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
        else:
            index += 1
            continue
        for position in range(index, min(end, length)):
            out[position] = "\n" if out[position] == "\n" else " "
        index = end
    return "".join(out)


@dataclass
class TableFacts:
    version: int
    file: str
    columns: set[str] = field(default_factory=set)
    rls_enabled: bool = False
    rls_forced: bool = False
    policies: int = 0
    tautological_policies: int = 0
    gate_functions: set[str] = field(default_factory=set)
    tenant_isolation: bool = False
    company_isolation: bool = False
    created: int | None = None

    @property
    def rls(self) -> bool:
        """兼容旧调用：RLS 覆盖只由 ENABLE 决定。"""
        return self.rls_enabled

    @property
    def tenant_write_policy(self) -> bool:
        """兼容旧调用：只有真实租户判定才算写策略。"""
        return self.tenant_isolation


def _table_body(masked: str, start: int) -> str:
    depth = 0
    index = start
    while index < len(masked):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return masked[start + 1 : index]
        index += 1
    return masked[start:]


def _parenthesised(clause: str, start: int) -> str:
    depth = 0
    for index in range(start, len(clause)):
        if clause[index] == "(":
            depth += 1
        elif clause[index] == ")":
            depth -= 1
            if depth == 0:
                return clause[start + 1 : index]
    return clause[start + 1 :]


def policy_expressions(clause: str) -> list[str]:
    """返回 USING / WITH CHECK 括号里的表达式（忽略其他子句）。"""

    found: list[str] = []
    for match in re.finditer(r"\b(?:using|with\s+check)\s*\(", clause, re.I):
        found.append(_parenthesised(clause, match.end() - 1))
    return found


def policy_verdict(expression: str) -> str:
    """把一条策略表达式分成 ``tenancy`` / ``tautology`` / ``other``。"""

    text = expression.lower()
    if re.fullmatch(r"\s*(?:true|1\s*=\s*1)\s*", text):
        return "tautology"
    if SELF_COMPARE.search(text) and not any(f"{helper}(" in text for helper in TENANT_HELPERS):
        return "tautology"
    if any(token in text for token in TENANT_TOKENS):
        return "tenancy"
    return "other"


def _dynamic_installs(source: str) -> list[tuple[str, set[str]]]:
    """返回 (format 体, 该文件可静态读到的目标表名)。

    表名可能来自三处：同一 ``EXECUTE`` 语句的字面量参数、``ARRAY[...]`` 循环列表、
    ``VALUES (...) AS t(name)`` 列表。三者都读，再与「本仓库真实建过的表」取交集，
    避免把触发器名或策略名当成表名。
    """

    masked = mask_comments(source)
    list_targets: set[str] = set()
    for array in ARRAY_LIST.finditer(masked):
        list_targets |= {value.lower() for value in SINGLE_QUOTED.findall(array.group(1))}
    for values in VALUES_LIST.finditer(masked):
        list_targets |= {value.lower() for value in SINGLE_QUOTED.findall(values.group(1))}

    installs: list[tuple[str, set[str]]] = []
    for match in FORMAT_BODY.finditer(masked):
        body = match.group(1).replace("''", "'").lower()
        if "accounting.%i" not in body:
            continue
        tail = STATEMENT_END.split(masked[match.end() :], maxsplit=1)[0]
        arguments = {value.lower() for value in SINGLE_QUOTED.findall(tail)}
        installs.append((body, arguments | list_targets))
    return installs


def scan(files: dict[int, tuple[str, str]]) -> dict[str, TableFacts]:
    """files: version -> (filename, source)。返回表名到事实的映射。"""

    order = sorted(files)
    masked_by_version = {version: mask_sql(files[version][1]) for version in order}
    facts: dict[str, TableFacts] = {}

    def entry_for(table: str, version: int) -> TableFacts:
        name = files[version][0]
        entry = facts.setdefault(table, TableFacts(version=version, file=name))
        if entry.created is None:
            entry.created = version
        return entry

    for version in order:
        masked = masked_by_version[version]
        for match in TABLE.finditer(masked):
            body = _table_body(masked, match.end() - 1)
            entry_for(match.group(1).lower(), version).columns |= {
                column.lower() for column in re.findall(rf"(?:^|,)\s*({IDENT})\s", body, re.M)
            }
        for match in ADD_COLUMN.finditer(masked):
            entry_for(match.group(1).lower(), version).columns.add(
                match.group(2).strip('"').lower()
            )
        for match in RLS_ENABLE.finditer(masked):
            entry_for(match.group(1).lower(), version).rls_enabled = True
        for match in RLS_FORCE.finditer(masked):
            entry_for(match.group(1).lower(), version).rls_forced = True
        for match in POLICY.finditer(masked):
            entry = entry_for(match.group(1).lower(), version)
            entry.policies += 1
            verdicts = [
                policy_verdict(expression) for expression in policy_expressions(match.group(2))
            ]
            if "tenancy" in verdicts:
                entry.tenant_isolation = True
                if any(
                    "company_id" in expression
                    for expression, verdict in zip(policy_expressions(match.group(2)), verdicts)
                    if verdict == "tenancy"
                ):
                    entry.company_isolation = True
            elif "tautology" in verdicts:
                entry.tautological_policies += 1
        for match in GATE_TRIGGER.finditer(masked):
            entry_for(match.group(1).lower(), version).gate_functions.add(match.group(2).lower())
        for body, targets in _dynamic_installs(files[version][1]):
            for table in targets:
                if table not in facts:
                    continue
                entry = facts[table]
                if "row level security" in body:
                    entry.rls_enabled = True
                if "create trigger" in body:
                    entry.gate_functions.add("<dynamic>")
                if "create policy" in body and "with check" in body:
                    entry.tenant_isolation = True
    return facts


def structure_problems(
    facts: dict[str, TableFacts], *, since: int | None = None
) -> list[dict[str, str]]:
    """把表级事实转成可逐条解释的问题，三类保护分别报。"""

    problems: list[dict[str, str]] = []
    for table, entry in sorted(facts.items()):
        if entry.created is None:
            continue
        if since is not None and entry.created < since:
            continue
        tenant = any(column in entry.columns for column in TENANT_COLUMNS)
        if not tenant:
            continue
        if not entry.rls_enabled:
            problems.append(
                {"table": table, "kind": "missing_rls", "detail": "缺 ENABLE ROW LEVEL SECURITY"}
            )
        if entry.tautological_policies and not entry.tenant_isolation:
            problems.append(
                {
                    "table": table,
                    "kind": "tautological_policy",
                    "detail": f"{entry.tautological_policies} 条策略只有恒真条件，未证明企业隔离",
                }
            )
        if entry.company_isolation and not (entry.gate_functions & RECOGNIZED_GATES):
            problems.append(
                {
                    "table": table,
                    "kind": "missing_write_gate",
                    "detail": "按 company_id 隔离但没有公司写入围栏，写入不会被串行化",
                }
            )
    return problems


def load(root: Path) -> dict[str, TableFacts]:
    directory = root / "nexus_backend" / "migrations" / "postgres"
    files = {
        int(path.name.split("_", 1)[0]): (path.name, path.read_text(encoding="utf-8-sig"))
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    }
    return scan(files)
