"""Authorized, read-only monthly blockers. No new accounting decision engine.

Domain services remain the authority. This projection contains guidance, never
approval or permission to deliver. It is re-read by the worker before sending
and in its fenced completion transaction. Model prose cannot alter this view.
"""

import json
from copy import deepcopy

from .delivery_investigation import DeliveryReads, validate_trace

from app.accounting.access import AccessControl, FIRM_EDITORS
from app.accounting.domain import AccountingError
from app.accounting.models import money_to_cents, cents_to_money
from app.accounting.service import fingerprint


def is_delivery_question(question):
    return bool(
        isinstance(question, str)
        and "交付" in question
        and any(
            word in question for word in ("为什么", "为何", "不能", "卡", "状态", "进度", "还没")
        )
    )


def project_delivery(db, conn, actor, snapshot, *, budget=None, clock=None):
    snapshot = deepcopy(snapshot)
    access = AccessControl()
    company_id = snapshot["company_id"]
    access.company(conn, actor, company_id)
    result = {
        "schema": "assistant-delivery-v2",
        "company_id": company_id,
        "book_id": None,
        "period": None,
        "coverage": "scope_required",
        "can_deliver": None,
        "sources": [],
        "issues": [],
    }

    def finish():
        if len(result["issues"]) > 80:
            raise AccountingError("交付核对事项超出单轮范围，请缩小资料范围", 422)
        reads.finish()
        validate_trace(result)
        result["fingerprint"] = fingerprint(result)
        return result

    # “本月” never guesses the first book, the server's clock, or another month.
    if len(snapshot["books"]) != 1:
        reads = DeliveryReads(
            result, lambda: access.company(conn, actor, company_id), budget=budget, clock=clock
        )
        return finish()
    selected = snapshot["books"][0]
    book = access.book(conn, actor, selected["id"])
    if book["company_id"] != company_id or selected["period"] != book["period"]:
        raise AccountingError("交付核对范围已改变", 409)
    result.update(book_id=book["id"], period=book["period"], coverage="selected_items")
    book_ref = "book:" + book["id"]

    def issue(code, reason, owner, action, refs=None):
        result["issues"].append(
            {
                "code": code,
                "reason": reason,
                "owner_role": owner,
                "next_action": action,
                "evidence_ids": refs or [book_ref],
            }
        )

    staff = access.firm_role(conn, actor) in FIRM_EDITORS and (
        book["permissions"]["edit"] or book["permissions"]["review"]
    )
    if staff:
        result["coverage"] = "selected_book"

    def authorize():
        access.company(conn, actor, company_id)
        current = access.book(conn, actor, selected["id"])
        if current["company_id"] != company_id or current["period"] != selected["period"]:
            raise AccountingError("交付取证范围已改变，请重新选择账套和月份", 409)
        if staff and (
            access.firm_role(conn, actor) not in FIRM_EDITORS
            or not (current["permissions"]["edit"] or current["permissions"]["review"])
        ):
            raise AccountingError("交付取证权限已改变，请联系事务所管理员", 403)

    reads = DeliveryReads(result, authorize, budget=budget, clock=clock)

    def selected_materials():
        # Selected events have already passed the business visibility predicate.
        # No discovery of colleagues' private drafts and no unselected event scan.
        for event in snapshot["events"]:
            ref = f"event:{event['id']}:v{event['fact_version']}"
            if str(event.get("occurred_on", ""))[:7] != book["period"]:
                issue(
                    "event_outside_period",
                    "所选事项不在当前月份，未合并计算",
                    "企业财务",
                    "核对事项日期并选择对应月份",
                    [ref],
                )
                continue
            attachments = [
                row
                for row in snapshot["evidence"]
                if row["kind"] == "attachment" and row.get("event_id") == event["id"]
            ]
            if not attachments:
                issue("missing_original", "缺少原始单据", "企业财务", "上传原件后提交核验", [ref])
            checks = [row for row in event.get("checks", []) if not row["resolved"]]
            documents = event.get("documents", [])
            if any(row["code"] == "document_business_amount" for row in checks):
                reason = "票据与业务事项金额冲突"
                # Only a single independently identified OCR document; never sum
                # duplicated/multiple invoices or invent tax/profit interpretations.
                if (
                    not event.get("documents_truncated")
                    and len(documents) == 1
                    and documents[0].get("currency") == "CNY"
                ):
                    try:
                        delta = abs(
                            money_to_cents(event.get("amount"))
                            - money_to_cents(documents[0].get("gross_amount"))
                        )
                    except ValueError:
                        # Keep the existing conflict and human next step when the
                        # source amount is unknown/invalid; never substitute zero.
                        pass
                    else:
                        reason += "，差额 " + cents_to_money(delta) + " 元（OCR 尚待人工核验）"
                issue(
                    "amount_conflict",
                    reason,
                    "企业财务",
                    "核对原件与事项，按实际原因补件或申请事实修订",
                    [ref, *[row["id"] for row in attachments]],
                )
            elif checks:
                issue(
                    "event_checks_pending",
                    "所选资料还有待人工核验的问题",
                    "企业财务",
                    "打开原件和事项核验提示，逐项处理后重新核对",
                    [ref],
                )

        draft = snapshot.get("draft")
        if draft and draft["status"] not in {"approved", "exported"}:
            issue(
                "draft_review",
                "待会计复核",
                "会计复核员",
                "复核凭证；通过后再按流程记账和导出",
                [f"draft:{draft['id']}:v{draft['version']}"],
            )

    reads.read("selected_materials", selected_materials)

    # Customer visibility is restricted even if their book has edit permission.
    if not staff:
        return finish()
    from app.delivery.monthly_package import MonthlyPackageService
    from app.b04_close.service import CloseService

    packages = MonthlyPackageService(db)

    def month_close():
        evidence = packages._inspect(conn, actor, book)
        plan = evidence["plan"]
        if plan:
            # Bind every plan version change, but do not send names, notes or IDs of
            # unrelated people. The responsible ROLE is supplied by domain guidance.
            result["sources"].append(
                {
                    "kind": "plan",
                    "id": book["id"],
                    "version": plan["version"],
                    "due_on": plan["due_on"],
                }
            )
        for row in evidence["blockers"] + evidence["warnings"]:
            issue(
                row["code"],
                row["message"],
                row["owner"],
                "打开“" + row["entry"] + "”处理后重新核对",
            )
        result["sources"].append(
            {
                "kind": "delivery_readiness",
                "id": book["id"],
                "version": 1,
                "inputs_fingerprint": fingerprint(evidence),
            }
        )
        # Preserve the exact preflight/report versions; never reuse stale checks.
        close = conn.execute(
            "SELECT * FROM internal_close_runs WHERE book_id=? AND period=? ORDER BY version DESC,id DESC LIMIT 1",
            (book["id"], book["period"]),
        ).fetchone()
        if close:
            current, checks = None, []
            if book["period_status"] == "open":
                current = False
                try:
                    checks, dependencies, digest = CloseService(db)._snapshot(
                        conn,
                        actor,
                        book,
                        book["period"],
                        close["report_run_id"],
                    )
                    current = digest == close["inputs_fingerprint"] and dependencies == json.loads(
                        close["dependencies_json"]
                    )
                except AccountingError as error:
                    if error.status not in {404, 409}:
                        raise
            result["sources"].append(
                {
                    "kind": "close",
                    "id": close["id"],
                    "version": close["version"],
                    "inputs_fingerprint": close["inputs_fingerprint"],
                    "current": current,
                    "status": close["status"],
                }
            )
            if not current and book["period_status"] == "open":
                issue(
                    "preflight_stale",
                    "历史月结预检已失效，不能作为本次交付依据",
                    "复核会计",
                    "重新预检并核对当前版本",
                )
            elif current:
                for check in checks:
                    if check["status"] == "blocked":
                        issue(
                            "close_" + check["code"],
                            "月结预检未通过：" + check["code"],
                            check["owner_role"],
                            check["next_action"],
                        )

    reads.read("month_close", month_close)

    def package_receipt():
        package = conn.execute(
            "SELECT * FROM monthly_delivery_packages WHERE book_id=? AND period=? ORDER BY version DESC LIMIT 1",
            (book["id"], book["period"]),
        ).fetchone()
        if package:
            view = packages._view(conn, actor, book, package)
            result["sources"].append(
                {
                    "kind": "package",
                    "id": package["id"],
                    "version": package["version"],
                    "manifest_sha256": package["manifest_sha256"],
                    "customer_receipt_status": view["customer_receipt_status"],
                }
            )
            if view["customer_receipt_status"] != "acknowledged":
                issue(
                    "awaiting_customer_ack",
                    "已有冻结交付包，尚未记录客户签收（不代表外部软件过账）",
                    "客户联系人",
                    "核对对应包版本和文件清单后，由有签收权限的客户确认",
                )

    reads.read("package_receipt", package_receipt)
    return finish()


def validate_projection(value, company_id, books, citation_ids):
    if not isinstance(value, dict) or value.get("schema") not in {
        "assistant-delivery-v1",
        "assistant-delivery-v2",
    }:
        raise ValueError("delivery projection schema invalid")
    if value.get("company_id") != company_id or value.get("can_deliver") is not None:
        raise ValueError("delivery projection scope/decision invalid")
    if fingerprint({k: v for k, v in value.items() if k != "fingerprint"}) != value.get(
        "fingerprint"
    ):
        raise ValueError("delivery projection fingerprint invalid")
    if value["coverage"] == "scope_required":
        if (
            value["book_id"] is not None
            or value["period"] is not None
            or value["issues"]
            or value["sources"]
        ):
            raise ValueError("ambiguous scope cannot contain a diagnosis")
    elif value["coverage"] not in {"selected_items", "selected_book"} or books != [
        {"book_id": value["book_id"], "period": value["period"]}
    ]:
        raise ValueError("delivery period/book mismatch")
    if len(value["issues"]) > 80:
        raise ValueError("delivery issues unbounded")
    for row in value["issues"]:
        if not row["evidence_ids"] or not set(row["evidence_ids"]) <= set(citation_ids):
            raise ValueError("delivery evidence outside current scope")
        if any(
            not isinstance(row[key], str) or not 1 <= len(row[key]) <= 500
            for key in ("code", "reason", "owner_role", "next_action")
        ):
            raise ValueError("delivery guidance invalid")
    if value["schema"] == "assistant-delivery-v2":
        validate_trace(value)
    return value


def render_delivery(pack):
    facts = {row["key"]: row for row in pack.facts}
    row = facts.get("delivery_task")
    if row is None:
        return "", []
    value = validate_projection(
        row["value"], pack.scope["company_id"], pack.scope["books"], facts["citation_ids"]["value"]
    )
    if value["coverage"] == "scope_required":
        return "交付核对需要先选择一个账套和月份；未根据其他企业或月份推断。", []
    lines = [value["period"] + " 月交付核对（只读，不代表会计复核或交付授权）："]
    if value["coverage"] == "selected_items":
        lines.append("仅覆盖当前有权查看的已选事项；完整月结与交付条件需由服务会计核对。")
    if value["schema"] == "assistant-delivery-v2":
        labels = {
            "selected_materials": "已选资料",
            "month_close": "月结条件",
            "package_receipt": "交付签收",
        }
        lines.append(
            "取证范围："
            + " → ".join(labels[step["tool"]] for step in value["investigation"]["steps"])
        )
        lines.append("人工接手清单（按负责岗位归类；尚未实际指派，仍须会计复核后导出）：")
    citations = []
    for source in value["sources"]:
        if source["kind"] == "plan":
            lines.append(f"服务单版本 {source['version']}；期限 {source['due_on']}。")
        if source["kind"] == "package":
            lines.append(
                f"交付包 {source['id']} · 版本 {source['version']}；签收与外部过账分别核验。"
            )
    items = value["issues"]
    if value["schema"] == "assistant-delivery-v2":
        items = [
            value["issues"][i]
            for group in value["handoff"]["groups"]
            for i in group["issue_indices"]
        ]
    for index, item in enumerate(items):
        line = (
            f"• {item['reason']}。负责岗位：{item['owner_role']}；下一步：{item['next_action']}。"
        )
        refs = list(dict.fromkeys([*citations, *item["evidence_ids"]]))
        if sum(len(part) + 1 for part in lines) + len(line) > 6000 or len(refs) > 90:
            lines.append(
                f"另有 {len(value['issues']) - index} 项未展示，请缩小资料范围或打开交付核验；不能据此判断已具备交付条件。"
            )
            break
        lines.append(line)
        citations = refs
    if not value["issues"]:
        lines.append(
            "本次已核对范围未发现上述阻断；不能据此认定已完成复核、交付或客户签收。请由服务会计确认当前版本。"
        )
    return "\n".join(lines), citations
