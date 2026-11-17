"""Read-only fleet contribution and driver receivable views.

Revenue is limited to reviewed fee and recognized lease plans. Costs are limited
to reviewed repair/insurance/OA batches. Shared costs without a vehicle stay in
``unallocated_cost_cents`` and are never silently spread over vehicles.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from ..accounting.domain import AccountingError
from ..accounting.service import AccountingService


def _money(cents):
    return f"{Decimal(int(cents)) / 100:.2f}"


def _age_days(value, today):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0, (today - moment.astimezone(timezone.utc)).days)
    except (TypeError, ValueError):
        return None


class FleetMetricsService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id):
        book = self.accounting.access.book(conn, actor, book_id, "read")
        return book

    def overview(self, actor, book_id, period):
        if len(period) != 7 or period[4] != "-":
            raise AccountingError("期间格式应为 YYYY-MM", 422)
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            vehicles = {
                row["id"]: {
                    "id": row["id"],
                    "external_id": row["external_id"],
                    "name": row["display_name"],
                    "active": bool(row["active"]),
                    "revenue_cents": 0,
                    "assigned_cost_cents": 0,
                    "cost_kind_cents": {"repair": 0, "insurance": 0, "oa": 0},
                    "fee_batch_ids": set(),
                    "lease_contract_ids": set(),
                    "cost_batch_ids": set(),
                    "cost_source_ids": set(),
                }
                for row in conn.execute(
                    "SELECT s.id,s.external_id,s.display_name,s.active FROM transport_subjects s "
                    "JOIN books b ON b.id=? AND b.company_id=s.company_id AND b.firm_id=s.firm_id "
                    "WHERE s.kind='vehicle' ORDER BY s.display_name,s.id",
                    (book_id,),
                )
            }
            by_external = {v["external_id"]: v for v in vehicles.values()}
            fee_rows = conn.execute(
                "SELECT r.driver_id,r.vehicle_id,r.net_cents,b.id AS batch_id "
                "FROM tax_fee_batch_rows r JOIN tax_fee_batches b ON b.id=r.batch_id "
                "WHERE b.book_id=? AND b.period=? AND b.status='reviewed' AND r.status='adopted'",
                (book_id, period),
            ).fetchall()
            for row in fee_rows:
                vehicle = vehicles.get(row["vehicle_id"])
                if vehicle:
                    vehicle["revenue_cents"] += int(row["net_cents"])
                    vehicle["fee_batch_ids"].add(row["batch_id"])
            lease_rows = conn.execute(
                "SELECT p.amount_cents,c.id AS contract_id,c.vehicle_external_id "
                "FROM lease_plan_rows p JOIN lease_contracts c ON c.id=p.contract_id "
                "WHERE c.book_id=? AND p.period=? AND p.status='recognized' "
                "AND p.kind='revenue' AND c.status IN ('active','terminated')",
                (book_id, period),
            ).fetchall()
            for row in lease_rows:
                vehicle = by_external.get(row["vehicle_external_id"])
                if vehicle:
                    vehicle["revenue_cents"] += int(row["amount_cents"])
                    vehicle["lease_contract_ids"].add(row["contract_id"])
            unallocated = 0
            cost_rows = conn.execute(
                "SELECT r.amount_cents,b.id AS batch_id,b.kind,s.id AS source_id,s.payload_json "
                "FROM tax_cost_batch_rows r "
                "JOIN tax_cost_batches b ON b.id=r.batch_id "
                "JOIN tax_cost_sources s ON s.id=r.source_id "
                "WHERE b.book_id=? AND b.period=? AND b.status='reviewed' AND r.status='adopted'",
                (book_id, period),
            ).fetchall()
            for row in cost_rows:
                payload = {}
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError):
                    payload = {}
                external_id = str(payload.get("vehicle_external_id") or "").strip()
                vehicle = by_external.get(external_id)
                if vehicle:
                    vehicle["assigned_cost_cents"] += int(row["amount_cents"])
                    if row["kind"] in vehicle["cost_kind_cents"]:
                        vehicle["cost_kind_cents"][row["kind"]] += int(row["amount_cents"])
                    vehicle["cost_batch_ids"].add(row["batch_id"])
                    vehicle["cost_source_ids"].add(row["source_id"])
                else:
                    unallocated += int(row["amount_cents"])
            if not vehicles and (fee_rows or lease_rows or cost_rows):
                raise AccountingError("没有车辆主数据，不能归属到车辆", 409)
            total_revenue = sum(v["revenue_cents"] for v in vehicles.values())
            total_cost = sum(v["assigned_cost_cents"] for v in vehicles.values())
            total_fee = sum(int(row["net_cents"]) for row in fee_rows)
            total_lease = sum(int(row["amount_cents"]) for row in lease_rows)
            if total_revenue != total_fee + total_lease:
                raise AccountingError("车辆收入合计与来源合计不一致", 500)
            lines = []
            for vehicle in sorted(vehicles.values(), key=lambda v: (v["name"], v["id"])):
                lines.append(
                    {
                        **{
                            key: value for key, value in vehicle.items() if key != "cost_kind_cents"
                        },
                        "fee_batch_ids": sorted(vehicle["fee_batch_ids"]),
                        "lease_contract_ids": sorted(vehicle["lease_contract_ids"]),
                        "cost_batch_ids": sorted(vehicle["cost_batch_ids"]),
                        "cost_source_ids": sorted(vehicle["cost_source_ids"]),
                        "revenue": _money(vehicle["revenue_cents"]),
                        "assigned_cost": _money(vehicle["assigned_cost_cents"]),
                        "repair_cost_cents": vehicle["cost_kind_cents"]["repair"],
                        "insurance_cost_cents": vehicle["cost_kind_cents"]["insurance"],
                        "oa_cost_cents": vehicle["cost_kind_cents"]["oa"],
                        "contribution_cents": vehicle["revenue_cents"]
                        - vehicle["assigned_cost_cents"],
                        "contribution": _money(
                            vehicle["revenue_cents"] - vehicle["assigned_cost_cents"]
                        ),
                    }
                )
            today = datetime.now(timezone.utc)
            aging = defaultdict(
                lambda: {"zero_30": 0, "days_31_60": 0, "days_61_90": 0, "over_90": 0}
            )
            driver_rows = conn.execute(
                "SELECT s.id AS driver_id,s.external_id,s.display_name,"
                "i.id AS item_id,i.counterparty_id,i.amount_cents,i.created_at "
                "FROM settlement_items i JOIN transport_subjects s "
                "ON s.external_id=i.counterparty_id AND s.kind='driver' "
                "WHERE i.book_id=? AND i.direction='income' AND i.clearing_account='1122'",
                (book_id,),
            ).fetchall()
            drivers = {}
            for driver_id, external_id, name in conn.execute(
                "SELECT s.id,s.external_id,s.display_name FROM transport_subjects s "
                "JOIN books b ON b.id=? AND b.company_id=s.company_id AND b.firm_id=s.firm_id "
                "WHERE s.kind='driver' ORDER BY s.display_name,s.id",
                (book_id,),
            ):
                drivers[driver_id] = {
                    "id": driver_id,
                    "external_id": external_id,
                    "name": name,
                    "billed_cents": 0,
                    "outstanding_cents": None,
                    "collection_progress": None,
                    "aging_known": False,
                    "aging": None,
                    "fee_batch_ids": [],
                }
            fee_batch_ids = defaultdict(set)
            for row in fee_rows:
                driver = drivers.get(row["driver_id"])
                if driver:
                    driver["billed_cents"] += int(row["net_cents"])
                    fee_batch_ids[row["driver_id"]].add(row["batch_id"])

            for row in driver_rows:
                item_id = row["item_id"]
                used = conn.execute(
                    "SELECT COALESCE(SUM(a.amount_cents),0) AS used FROM settlement_allocations a "
                    "WHERE a.item_id=? AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)",
                    (item_id,),
                ).fetchone()["used"]
                outstanding = int(row["amount_cents"]) - int(used)
                if outstanding <= 0:
                    continue
                driver_id = row["driver_id"]
                driver = drivers.get(driver_id)
                if not driver:
                    continue
                driver["outstanding_cents"] = int(driver["outstanding_cents"] or 0) + outstanding
                driver["aging_known"] = True
                days = _age_days(row["created_at"], today)
                bucket = (
                    "over_90"
                    if days is None or days > 90
                    else "days_61_90"
                    if days > 60
                    else "days_31_60"
                    if days > 30
                    else "zero_30"
                )
                aging[driver_id][bucket] += outstanding
            for driver_id, driver in drivers.items():
                driver["fee_batch_ids"] = sorted(fee_batch_ids[driver_id])
                if not driver["aging_known"]:
                    continue
                driver["aging"] = dict(aging[driver_id])
                if driver["billed_cents"] > 0:
                    driver["collection_progress"] = round(
                        (driver["billed_cents"] - int(driver["outstanding_cents"]))
                        / driver["billed_cents"],
                        4,
                    )
            total_driver_billed = sum(d["billed_cents"] for d in drivers.values())
            total_outstanding = sum(int(d["outstanding_cents"] or 0) for d in drivers.values())
            return {
                "book_id": book_id,
                "period": period,
                "vehicles": lines,
                "drivers": sorted(drivers.values(), key=lambda d: (d["name"], d["id"])),
                "totals": {
                    "revenue_cents": total_revenue,
                    "assigned_cost_cents": total_cost,
                    "contribution_cents": total_revenue - total_cost,
                    "unallocated_cost_cents": unallocated,
                    "net_profit_cents": None,
                    "net_profit_known": False,
                    "net_profit_reason": "共用费用未分配，不能把车辆贡献称为净利润",
                    "driver_billed_cents": total_driver_billed,
                    "driver_outstanding_cents": total_outstanding,
                },
                "checks": {
                    "revenue_matches_sources": total_revenue == total_fee + total_lease,
                    "assigned_cost_not_negative": total_cost >= 0,
                    "unallocated_cost_cents": unallocated,
                    "aging_known_driver_count": sum(
                        1 for d in drivers.values() if d["aging_known"]
                    ),
                },
            }
