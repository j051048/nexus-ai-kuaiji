"""Human-authorized CSV projection of the existing deterministic read models."""

import csv
import hashlib
import io
from decimal import Decimal

from ..accounting.domain import AccountingError
from ..accounting.service import safe_csv_text


def _money(cents):
    return format(Decimal(int(cents)) / 100, ".2f")


def export_formal_book(
    posting, actor, book_id, kind, *, account_id=None, key=None, value=None, as_of=None
):
    # Resolve the company through live authorization, then use the existing
    # company write gate for a coherent projection plus its append-only audit.
    with posting.db.transaction(actor=actor) as conn:
        book = posting.accounting.access.book(conn, actor, book_id, "export")
        if as_of and kind not in {"trial_balance", "account_ledger"}:
            raise AccountingError("当前账簿不支持截止日期筛选")
        company_id = book["company_id"]
    with posting.db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        book = posting.accounting.access.book(conn, actor, book_id, "export")
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
                data = posting.account_ledger_in_transaction(
                    conn, actor, book_id, account_id, as_of
                )
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
        if len(rows) > 50_000:
            raise AccountingError("账簿超过本地下载上限，请按账期拆分", 413)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["账套ID", "企业ID", "月份", "币种", "截止日期"])
        writer.writerow(
            [
                safe_csv_text(book_id),
                safe_csv_text(company_id),
                book["period"],
                "CNY",
                as_of or "本期全部",
            ]
        )
        writer.writerow(header)
        writer.writerows(rows)
        content = "\ufeff" + output.getvalue()
        if len(content.encode("utf-8")) > 16 * 1024 * 1024:
            raise AccountingError("账簿文件过大，请按账期拆分", 413)
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
