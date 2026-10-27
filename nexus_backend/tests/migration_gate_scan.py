"""静态扫描迁移里的表级隔离属性（供结构测试使用）。

只做「能证伪」的窄解析：注释与字符串先被等长掩码，再做表名与触发器配对。
动态安装（``EXECUTE format('...%I...')`` + ``VALUES`` 表名列表）与直接语句等价对待，
因为线上 PG 里两者装出来的触发器形态相同。
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
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)\s+(?:ENABLE|FORCE)\s+ROW\s+LEVEL\s+SECURITY",
    re.I,
)
GATE_TRIGGER = re.compile(
    rf"CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+{IDENT}[^;]*?\bON\s+(?:ONLY\s+)?accounting\.([a-z_][a-z0-9_]*)"
    r"[^;]*?EXECUTE\s+FUNCTION\s+accounting\.([a-z_][a-z0-9_]*)\s*\(",
    re.I | re.S,
)
FORMAT_TABLE = re.compile(r"format\s*\(\s*'([^']*accounting\.%I[^']*)'", re.I)
VALUES_TABLES = re.compile(r"\(\s*'([a-z_][a-z0-9_]*)'\s*,", re.I)


def mask_sql(source: str) -> str:
    """把注释、单引号字符串、美元引用体替换为等长空格，保留换行。"""

    out = []
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
                if source[end] == "'" and source[end : end + 2] == "''":
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


@dataclass
class TableFacts:
    version: int
    file: str
    columns: set[str] = field(default_factory=set)
    rls: bool = False
    policies: int = 0
    gate_functions: set[str] = field(default_factory=set)
    tenant_write_policy: bool = False
    created: int | None = None


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


def scan(files: dict[int, tuple[str, str]]) -> dict[str, TableFacts]:
    """files: version -> (filename, source)。返回表名到事实的映射。"""

    order = sorted(files)
    masked_by_version = {version: mask_sql(files[version][1]) for version in order}
    created: dict[str, int] = {}
    for version in order:
        masked = masked_by_version[version]
        for match in TABLE.finditer(masked):
            created.setdefault(match.group(1).lower(), version)
        for match in ADD_COLUMN.finditer(masked):
            created.setdefault(match.group(1).lower(), version)
    facts: dict[str, TableFacts] = {}
    for version in order:
        name, _ = files[version]
        masked = masked_by_version[version]
        for match in TABLE.finditer(masked):
            table = match.group(1).lower()
            body = _table_body(masked, match.end() - 1)
            entry = facts.setdefault(table, TableFacts(version=version, file=name))
            entry.created = version if entry.created is None else entry.created
            entry.columns |= {
                column.lower() for column in re.findall(rf"^\s*({IDENT})\s", body, re.M)
            }
        for match in ADD_COLUMN.finditer(masked):
            facts.setdefault(
                match.group(1).lower(), TableFacts(version=version, file=name)
            ).columns.add(match.group(2).strip('"').lower())
        for match in RLS_ENABLE.finditer(masked):
            facts.setdefault(
                match.group(1).lower(), TableFacts(version=version, file=name)
            ).rls = True
        for match in re.finditer(
            rf"CREATE\s+POLICY\s+{IDENT}\s+ON\s+accounting\.([a-z_][a-z0-9_]*)([^;]*);",
            masked,
            re.I | re.S,
        ):
            entry = facts.setdefault(match.group(1).lower(), TableFacts(version=version, file=name))
            entry.policies += 1
            clause = match.group(2).lower()
            if ("for all" in clause or "for insert" in clause or "for update" in clause) and (
                "with check" in clause
            ):
                if any(token in clause for token in ("book_allowed", "actor_firm", "company_id")):
                    entry.tenant_write_policy = True
        for match in GATE_TRIGGER.finditer(masked):
            entry = facts.setdefault(match.group(1).lower(), TableFacts(version=version, file=name))
            entry.gate_functions.add(match.group(2).lower())
        for body, names in _dynamic_targets(files[version][1]):
            for table in names:
                if table not in created:
                    continue
                entry = facts.setdefault(table, TableFacts(version=version, file=name))
                if "row level security" in body:
                    entry.rls = True
                if "create trigger" in body:
                    entry.gate_functions.add("<dynamic>")
                if "create policy" in body and "with check" in body:
                    entry.tenant_write_policy = True
    return facts


ARRAY_LIST = re.compile(r"ARRAY\s*\[([^\]]*)\]", re.I | re.S)
FORMAT_BODY = re.compile(r"format\s*\(\s*'((?:[^']|'')*)'", re.I)
SINGLE_QUOTED = re.compile(r"'([a-z_][a-z0-9_]*)'", re.I)


def _dynamic_targets(source: str) -> list[tuple[str, set[str]]]:
    """返回 (format 体, 该文件里由 VALUES/ARRAY 列表给出的表名)。

    ``EXECUTE format('CREATE TRIGGER %I ... ON accounting.%I ...', trig, item.table_name)``
    的表名来自循环变量，静态不可读；能读的是同一文件里 ``VALUES`` / ``ARRAY[...]``
    列表中的字面量。文件级配对仍在可证伪范围内：这类「批量补触发器」迁移只登记
    自己那一批表，误credit 的代价由下面的等价性用例兜底。
    """

    bodies = [match.group(1).replace("''", "'").lower() for match in FORMAT_BODY.finditer(source)]
    installs_gate = any(
        "accounting.%i" in body and "create trigger" in body and "company_write_table_guard" in body
        for body in bodies
    )
    installs_rls = any("accounting.%i" in body and "row level security" in body for body in bodies)
    if not installs_gate and not installs_rls:
        return []
    targets: set[str] = set()
    for array in ARRAY_LIST.finditer(source):
        targets |= {value.lower() for value in SINGLE_QUOTED.findall(array.group(1))}
    for values in re.finditer(r"VALUES(.*?)\)\s*AS\s+\w+", source, re.S | re.I):
        targets |= {value.lower() for value in SINGLE_QUOTED.findall(values.group(1))}
    result = []
    if installs_gate:
        result.append(("create trigger company_write_table_guard", targets))
    if installs_rls:
        result.append(("alter table row level security", targets))
    return result


def load(root: Path) -> dict[str, TableFacts]:
    directory = root / "nexus_backend" / "migrations" / "postgres"
    files = {
        int(path.name.split("_", 1)[0]): (path.name, path.read_text(encoding="utf-8-sig"))
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    }
    return scan(files)
