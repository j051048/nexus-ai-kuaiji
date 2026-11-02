"""DCP06-A：跨企业批量准备复核资料。

只汇总现有对象与阻断，不批准、不过账、不导出。每个企业一行，绑定账套、
期间、逐张凭证的 id 与版本，并给出可核对的清单指纹（manifest）。
清单只对调用者有权读取的账套展开；越权或不存在一律不暴露对象名。
"""

from __future__ import annotations

import hashlib

from ..accounting.domain import AccountingError, Actor
from ..accounting.service import AccountingService

MAX_COMPANIES = 200
MAX_DRAFTS = 200


def _digest(*parts):
    joined = "|".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ReviewPackService:
    def __init__(self, db, review):
        self.db, self.review = db, review
        self.access = AccountingService(db).access

    def build(self, actor: Actor, company_ids, period):
        if (
            not period
            or len(period) != 7
            or period[4] != "-"
            or not period[:4].isdigit()
            or not 1 <= int(period[5:]) <= 12
        ):
            raise AccountingError("期间格式应为 YYYY-MM", 422)
        unique = list(dict.fromkeys(company_ids))
        if not unique:
            raise AccountingError("请选择至少一家企业", 422)
        if len(unique) > MAX_COMPANIES:
            raise AccountingError(f"单次最多 {MAX_COMPANIES} 家企业", 422)
        # 批量准备复核资料是事务所侧动作：客户财务没有事务所复核岗位，不能打包。
        with self.db.transaction(actor=actor) as conn:
            if self.access.firm_role(conn, actor) not in {"owner", "admin", "reviewer"}:
                raise AccountingError("批量准备复核资料需要事务所复核岗位", 403)
        entries = [self._company(actor, company_id, period) for company_id in unique]
        manifest = _digest(
            *(
                f"{entry['company_id']}:{entry.get('book_id')}:{entry.get('manifest', '')}"
                for entry in entries
            )
        )
        return {
            "period": period,
            "manifest": manifest,
            "ready": sum(1 for entry in entries if entry["status"] == "ready"),
            "blocked": sum(1 for entry in entries if entry["status"] != "ready"),
            "items": entries,
        }

    def _company(self, actor, company_id, period):
        with self.db.transaction(actor=actor) as conn:
            book = conn.execute(
                "SELECT id,name FROM books WHERE company_id=? AND period=?",
                (company_id, period),
            ).fetchone()
            if not book:
                return {"company_id": company_id, "status": "blocked", "blockers": ["no_book"]}
            try:
                self.access.book(conn, actor, book["id"], "read")
            except AccountingError:
                return {"company_id": company_id, "status": "blocked", "blockers": ["no_access"]}
            book_id, book_name = book["id"], book["name"]
            status = conn.execute(
                "SELECT status FROM periods WHERE book_id=? AND period=?", (book_id, period)
            ).fetchone()
        page = self.review.list_drafts(
            actor, company_id=company_id, period=period, book_id=book_id, limit=100
        )
        total = page["pagination"]["total"]
        items = list(page["items"])
        while page["pagination"]["has_more"] and len(items) < MAX_DRAFTS:
            page = self.review.list_drafts(
                actor,
                company_id=company_id,
                period=period,
                book_id=book_id,
                limit=100,
                offset=len(items),
            )
            items.extend(page["items"])
        pinned = [
            {"draft_id": item["draft_id"], "version": item["expected_version"]} for item in items
        ]
        entry = {
            "company_id": company_id,
            "book_id": book_id,
            "book_name": book_name,
            "period": period,
            "draft_count": total,
            "drafts": pinned,
        }
        if total == 0:
            return {**entry, "status": "blocked", "blockers": ["no_drafts"]}
        if total > len(pinned):
            # 不静默截断：超额让调用者缩小范围，而不是只导一部分。
            return {
                **entry,
                "status": "blocked",
                "blockers": ["draft_limit"],
                "drafts": [],
                "draft_count": total,
            }
        if status and status["status"] != "open":
            return {**entry, "status": "blocked", "blockers": ["period_locked"]}
        entry["manifest"] = _digest(
            *(f"{item['draft_id']}:{item['version']}" for item in packed(pinned))
        )
        entry["status"] = "ready"
        entry["blockers"] = []
        return entry


def packed(items):
    return sorted(items, key=lambda item: (item["draft_id"], item["version"]))
