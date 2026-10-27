"""Human-authorized CSV projection of the existing deterministic read models.

Q05: the export is split into three phases so a download never holds the company
write lock while rows are formatted:

1. read-only snapshot — authorization, row materialization, source digest;
2. serialization outside any transaction — pure string work, no connection;
3. short write transaction — re-authorization, source re-check, append-only audit.

If the sources moved between (1) and (3) the export fails with 409 instead of
returning a file whose content no longer matches the audited hash.
"""

import csv
import hashlib
import io
import json
from decimal import Decimal

from ..accounting.domain import AccountingError
from ..accounting.service import safe_csv_text

ROW_LIMIT = 50_000
BYTE_LIMIT = 16 * 1024 * 1024


def _money(cents):
    return format(Decimal(int(cents)) / 100, ".2f")


def _render_csv(header_rows, rows):
    """Pure serialization. No database access, no authorization, no audit."""

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    for row in header_rows:
        writer.writerow(row)
    writer.writerows(rows)
    content = "\ufeff" + output.getvalue()
    if len(content.encode("utf-8")) > BYTE_LIMIT:
        raise AccountingError("账簿文件过大，请按账期拆分", 413)
    return content


def _check_rows(rows):
    if len(rows) > ROW_LIMIT:
        raise AccountingError("账簿超过本地下载上限，请按账期拆分", 413)
    return rows


def _digest(parts):
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_digest(conn, book_ids, ledger_id=None):
    """Cheap fingerprint of everything a single-book export depends on.

    Counts, sums, last voucher id, period state, active opening binding and
    internal locks notice any posting, close or rebind. The account identity of
    the ledger is included as well: a rename, re-parenting or version change
    rewrites the meaning of the same amounts, so a snapshot that ignored it
    would hand out a file that still looks current.
    """

    parts = []
    for book_id in sorted(book_ids):
        book = conn.execute(
            "SELECT b.period,b.company_id,p.status AS period_status FROM books b "
            "LEFT JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=?",
            (book_id,),
        ).fetchone()
        lines = conn.execute(
            "SELECT COUNT(*) AS n,COALESCE(SUM(l.debit_cents),0) AS debit,"
            "COALESCE(SUM(l.credit_cents),0) AS credit,COALESCE(MAX(v.id),'') AS last "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=?",
            (book_id,),
        ).fetchone()
        binding = conn.execute(
            "SELECT id,snapshot_fingerprint FROM opening_bindings "
            "WHERE book_id=? AND status='active'",
            (book_id,),
        ).fetchone()
        locks = conn.execute(
            "SELECT COUNT(*) FROM internal_period_locks WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        opening = conn.execute(
            "SELECT COUNT(*) AS n,COALESCE(SUM(opening_cents),0) AS total FROM opening_lines "
            "WHERE package_version_id IN (SELECT package_version_id FROM opening_bindings "
            "WHERE book_id=? AND status='active')",
            (book_id,),
        ).fetchone()
        names = conn.execute(
            "SELECT DISTINCT l.account_id,l.name_snapshot FROM voucher_lines l "
            "JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? "
            "ORDER BY l.account_id,l.name_snapshot",
            (book_id,),
        ).fetchall()
        parts.append(
            [
                book_id,
                book["period"] if book else None,
                book["company_id"] if book else None,
                book["period_status"] if book else None,
                lines["n"],
                lines["debit"],
                lines["credit"],
                lines["last"],
                binding["id"] if binding else None,
                binding["snapshot_fingerprint"] if binding else None,
                locks,
                opening["n"],
                opening["total"],
                [[row["account_id"], row["name_snapshot"]] for row in names],
            ]
        )
    if ledger_id:
        parts.append(
            [
                [
                    row["id"],
                    row["current_version"],
                    row["version"],
                    row["code"],
                    row["name"],
                    row["parent_account_id"],
                    row["level"],
                    row["is_leaf"],
                    row["effective_from"],
                    row["effective_to"],
                ]
                for row in conn.execute(
                    "SELECT a.id,a.current_version,v.version,v.code,v.name,v.parent_account_id,"
                    "v.level,v.is_leaf,v.effective_from,v.effective_to "
                    "FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id "
                    "WHERE a.ledger_id=? ORDER BY a.id,v.version",
                    (ledger_id,),
                )
            ]
        )
    return _digest(parts)


_COVERAGE_MONEY = ("opening_cents", "debit_cents", "credit_cents", "closing_cents")


def _coverage_digest(items, blockers):
    """Fingerprint the coverage view only: numbers, states and blocker codes."""

    return _digest(
        {
            "rows": [
                [item.get("period"), item.get("status")]
                + [item.get(key) for key in _COVERAGE_MONEY]
                for item in items
            ],
            "blockers": [[item.get("period"), item.get("code")] for item in blockers],
        }
    )


def _ledger_of(conn, book_id):
    """Ledger linked to one monthly book, if any."""

    row = conn.execute(
        "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
    ).fetchone()
    return row["ledger_id"] if row else None


def _range_digest(conn, items, blockers, ledger_id):
    """Everything a range export depends on: coverage *and* ledger identity."""

    books = [
        item["book_id"] for item in items if item.get("status") == "ok" and item.get("book_id")
    ]
    return _digest(
        [
            _coverage_digest(items, blockers),
            _source_digest(conn, books, ledger_id),
        ]
    )


def _changed():
    return AccountingError("导出期间账簿数据已变化，请重新导出", 409)


def _collect_formal_book(posting, conn, actor, book_id, kind, *, account_id, key, value, as_of):
    """Rows for one book, read inside the caller's read-only transaction."""

    header = []
    rows = []
    if kind == "trial_balance":
        data = posting.trial_balance_in_transaction(conn, actor, book_id, as_of)
        header = [
            "科目编码",
            "科目名称",
            "期初余额（借正贷负）",
            "本期借方",
            "本期贷方",
            "期末余额（借正贷负）",
        ]
        for item in data["rows"]:
            rows.append(
                [
                    safe_csv_text(item["code"]),
                    safe_csv_text(item["name"]),
                    *[
                        _money(item[field])
                        for field in (
                            "opening_cents",
                            "debit_cents",
                            "credit_cents",
                            "closing_cents",
                        )
                    ],
                ]
            )
    elif kind in {"account_ledger", "daily_journal", "aux_ledger"}:
        header = [
            "正式凭证ID",
            "日期",
            "凭证字",
            "凭证号",
            "摘要",
            "科目ID",
            "借方",
            "贷方",
            "余额（借正贷负）",
            "辅助项",
        ]
        if kind == "account_ledger":
            if not account_id:
                raise AccountingError("请先选择当前账套科目")
            data = posting.account_ledger_in_transaction(conn, actor, book_id, account_id, as_of)
            entries = data["entries"]
            rows.append(
                [
                    "",
                    "",
                    "",
                    "",
                    "期初余额",
                    safe_csv_text(account_id),
                    "",
                    "",
                    _money(data["opening_cents"]),
                    "",
                ]
            )
        elif kind == "daily_journal":
            entries = posting.daily_journal_in_transaction(conn, actor, book_id)
        else:
            if key not in {"counterparty", "project", "department", "employee"} or not value:
                raise AccountingError("请指定辅助维度及稳定标识")
            data = posting.aux_ledger_in_transaction(conn, actor, book_id, {key: value})
            entries = data["vouchers"]
            for item in data["opening"]:
                rows.append(
                    [
                        "",
                        "",
                        "",
                        "",
                        "辅助期初余额",
                        safe_csv_text(item["account_id"]),
                        "",
                        "",
                        _money(item["opening_cents"]),
                        safe_csv_text(item["aux_json"]),
                    ]
                )
        for item in entries:
            rows.append(
                [
                    safe_csv_text(str(item.get("voucher_id", ""))),
                    item["voucher_date"],
                    safe_csv_text(item["voucher_type"]),
                    item["voucher_no"],
                    safe_csv_text(item["description"]),
                    safe_csv_text(str(item.get("account_id", account_id or ""))),
                    _money(item["debit_cents"]),
                    _money(item["credit_cents"]),
                    _money(item["balance_cents"]) if "balance_cents" in item else "",
                    safe_csv_text(item.get("aux_json", "{}")),
                ]
            )
    else:
        raise AccountingError("不支持的正式账簿类型")
    return header, _check_rows(rows)


def export_formal_book(
    posting, actor, book_id, kind, *, account_id=None, key=None, value=None, as_of=None
):
    # Phase 1: one consistent snapshot, no write lock.
    with posting.db.transaction(actor=actor) as conn:
        book = posting.accounting.access.book(conn, actor, book_id, "export")
        if as_of and kind not in {"trial_balance", "account_ledger"}:
            raise AccountingError("当前账簿不支持截止日期筛选")
        company_id = book["company_id"]
        header, rows = _collect_formal_book(
            posting,
            conn,
            actor,
            book_id,
            kind,
            account_id=account_id,
            key=key,
            value=value,
            as_of=as_of,
        )
        digest = _source_digest(conn, [book_id], _ledger_of(conn, book_id))
        header_rows = [
            ["账套ID", "企业ID", "月份", "币种", "截止日期"],
            [
                safe_csv_text(book_id),
                safe_csv_text(company_id),
                book["period"],
                "CNY",
                as_of or "本期全部",
            ],
            header,
        ]

    # Phase 2: serialize with no connection held.
    content = _render_csv(header_rows, rows)

    # Phase 3: short write transaction — re-authorize, re-check, audit.
    with posting.db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        posting.accounting.access.book(conn, actor, book_id, "export")
        if _source_digest(conn, [book_id], _ledger_of(conn, book_id)) != digest:
            raise _changed()
        posting._audit(
            conn,
            actor,
            book_id,
            "formal_book.export",
            kind,
            {
                "kind": kind,
                "row_count": len(rows),
                "account_id": account_id,
                "as_of": as_of,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    return content


def _range_meta(result, ledger_id, kind):
    blockers = "；".join(f"{item['period']} {item['message']}" for item in result["blockers"])
    return [
        safe_csv_text(ledger_id),
        safe_csv_text(result["scope"]["company_id"]),
        result["start"],
        result["end"],
        "CNY",
        "完整" if result["complete"] else "不完整：" + safe_csv_text(blockers),
        result["fingerprint"],
    ]


def export_range_trial_balance(posting, actor, ledger_id, start, end):
    """Range trial balance as CSV plus the fingerprint the page must show."""

    # Phase 1: coverage, rows and source digest from one read snapshot.
    with posting.db.transaction(actor=actor) as conn:
        coverage = posting._range_coverage(conn, actor, ledger_id, start, end, "export")
        company_id = posting._range_scope(coverage, ledger_id)["company_id"]
        data = posting.range_trial_balance_in_transaction(
            conn, actor, ledger_id, start, end, capability="export"
        )
        header = [
            "科目编码",
            "科目名称",
            "期初余额（借正贷负）",
            "本期借方",
            "本期贷方",
            "期末余额（借正贷负）",
        ]
        rows = _check_rows(
            [
                [
                    safe_csv_text(item["code"]),
                    safe_csv_text(item["name"]),
                    *[
                        _money(item[field])
                        for field in (
                            "opening_cents",
                            "debit_cents",
                            "credit_cents",
                            "closing_cents",
                        )
                    ],
                ]
                for item in data["rows"]
            ]
        )
        digest = _range_digest(conn, data["coverage"], data["blockers"], ledger_id)
        header_rows = [
            ["长期账套ID", "企业ID", "起始期间", "结束期间", "币种", "完整性", "来源指纹"],
            _range_meta(data, ledger_id, "trial_balance"),
            header,
        ]

    content = _render_csv(header_rows, rows)

    with posting.db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        fresh = posting._range_coverage(conn, actor, ledger_id, start, end, "export")
        if (
            _range_digest(conn, posting._coverage_view(fresh), fresh["blockers"], ledger_id)
            != digest
        ):
            raise _changed()
        posting._audit(
            conn,
            actor,
            data["scope"]["book_id"],
            "formal_book.range_export",
            f"trial_balance:{start}:{end}",
            {
                "kind": "trial_balance",
                "start": start,
                "end": end,
                "row_count": len(rows),
                "complete": data["complete"],
                "fingerprint": data["fingerprint"],
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    return content, data["fingerprint"]


def export_range_account_ledger(posting, actor, ledger_id, account_id, start, end):
    """Range account ledger as CSV plus the fingerprint the page must show."""

    with posting.db.transaction(actor=actor) as conn:
        coverage = posting._range_coverage(conn, actor, ledger_id, start, end, "export")
        company_id = posting._range_scope(coverage, ledger_id)["company_id"]
        data = posting.range_account_ledger_in_transaction(
            conn, actor, ledger_id, account_id, start, end, capability="export"
        )
        header = [
            "期间",
            "正式凭证ID",
            "日期",
            "凭证字",
            "凭证号",
            "摘要",
            "科目ID",
            "借方",
            "贷方",
            "余额（借正贷负）",
            "辅助项",
        ]
        rows = _check_rows(
            [
                [
                    item["period"],
                    safe_csv_text(str(item["voucher_id"])),
                    item["voucher_date"],
                    safe_csv_text(item["voucher_type"]),
                    item["voucher_no"],
                    safe_csv_text(item["description"]),
                    safe_csv_text(str(data["account"]["id"])),
                    _money(item["debit_cents"]),
                    _money(item["credit_cents"]),
                    _money(item["balance_cents"]),
                    safe_csv_text(item["aux_json"]),
                ]
                for item in data["entries"]
            ]
        )
        digest = _range_digest(conn, data["coverage"], data["blockers"], ledger_id)
        header_rows = [
            ["长期账套ID", "企业ID", "起始期间", "结束期间", "币种", "完整性", "来源指纹"],
            _range_meta(data, ledger_id, "account_ledger"),
            ["期初余额", _money(data["opening_cents"])],
            header,
        ]

    content = _render_csv(header_rows, rows)

    with posting.db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        fresh = posting._range_coverage(conn, actor, ledger_id, start, end, "export")
        if (
            _range_digest(conn, posting._coverage_view(fresh), fresh["blockers"], ledger_id)
            != digest
        ):
            raise _changed()
        posting._audit(
            conn,
            actor,
            data["scope"]["book_id"],
            "formal_book.range_export",
            f"account_ledger:{start}:{end}",
            {
                "kind": "account_ledger",
                "start": start,
                "end": end,
                "account_id": account_id,
                "row_count": len(rows),
                "complete": data["complete"],
                "fingerprint": data["fingerprint"],
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    return content, data["fingerprint"]
