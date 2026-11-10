"""Bound evidence relationships over existing reconciliation, without model voting."""

import re
from decimal import Decimal

from app.accounting.service import fingerprint


def projection_findings(assessment):
    """Collapse identical display findings; never turn any unresolved copy green."""
    unique = {}
    for row in (assessment or {}).get("findings", []):
        key = fingerprint({name: row[name] for name in ("code", "severity", "payload")})
        if key not in unique:
            unique[key] = dict(row)
        else:
            unique[key]["resolved"] = bool(unique[key].get("resolved") and row.get("resolved"))
    return list(unique.values())


def compare_amounts(left, right):
    """Only identical business semantics permit an amount comparison."""
    unknown = {"state": "not_comparable", "left": None, "right": None, "delta": None}
    if any(
        not left.get(key) or left.get(key) != right.get(key)
        for key in ("object_id", "period", "currency", "basis")
    ):
        return unknown
    values = [row.get("value") for row in (left, right)]
    if any(
        not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?", value)
        for value in values
    ):
        return unknown
    a, b = map(Decimal, values)
    return {
        "state": "equal" if a == b else "conflict",
        "left": f"{a:.2f}",
        "right": f"{b:.2f}",
        "delta": f"{a - b:.2f}",
    }


def relationships(event, assessment, owner, actionable):
    """No new authority: findings remain those of the existing deterministic checks.

    Only derived_from/depends_on edges form a DAG. same_source and contradicts
    are associations, never votes or extra independent original evidence.
    """
    period = event["payload"]["occurred_on"][:7]
    event_node = "event:" + event["id"]
    nodes, edges, roots, original_ids, documents = [], [], {}, {}, []

    def node(
        identifier, kind, version, digest, origin, basis, amount=None, currency=None, month=period
    ):
        nodes.append(
            dict(
                id=identifier,
                kind=kind,
                object_id=event["id"],
                version=str(version),
                digest=digest,
                origin=origin,
                period=month,
                currency=currency,
                basis=basis,
                amount=amount,
            )
        )

    node(
        event_node,
        "event",
        event["version"],
        fingerprint(event["payload"]),
        event_node,
        "business_gross",
        event["payload"]["amount"],
        "CNY",
    )
    for original in event["attachments"]:
        if original.get("superseded"):
            continue
        identifier = "original:" + original["id"]
        origin = "sha256:" + original["sha256"]
        node(identifier, "original", original["sha256"], original["sha256"], origin, "original")
        original_ids[original["id"]] = identifier
        if origin in roots:
            edges.append(dict(kind="same_source", source=identifier, target=roots[origin]))
        else:
            roots[origin] = identifier
    for document in (assessment or {}).get("document_evidence", []):
        original = original_ids.get(document["attachment_id"])
        if original is None:
            continue
        identifier = f"document:{document['extraction_id']}:{document['document_index']}"
        origin = next(row["origin"] for row in nodes if row["id"] == original)
        node(
            identifier,
            "document",
            document["extraction_id"],
            fingerprint(document),
            origin,
            "document_gross",
            document.get("gross_amount"),
            document.get("currency"),
            (document.get("issued_on") or "")[:7],
        )
        edges.append(dict(kind="derived_from", source=identifier, target=original))
        documents.append((identifier, document, origin))
    bank_ids = []
    for bank in (assessment or {}).get("links", []):
        identifier = "bank:" + bank["id"]
        bank_ids.append(identifier)
        source_digest = fingerprint(bank)
        node(
            identifier,
            "bank",
            source_digest,
            source_digest,
            identifier,
            "payment",
            f"{Decimal(bank['amount_cents']) / 100:.2f}",
            "CNY",
            bank["date"][:7],
        )
    issues = []
    findings = projection_findings(assessment)
    selected = [row for row in documents if row[1]["document_type"] == "invoice"] or [
        row for row in documents if row[1]["document_type"] == "receipt"
    ]
    for finding in findings:
        payload, code = finding["payload"], finding["code"]
        references = [event_node]
        attachment_id = payload.get("attachment_id")
        if attachment_id in original_ids:
            references.append(original_ids[attachment_id])
        elif isinstance(payload.get("document_index"), int):
            references.extend(
                identifier
                for identifier, document, _ in documents
                if document["document_index"] == payload["document_index"]
            )
        else:
            references.extend(original_ids.values())
        comparison = {"state": "not_comparable", "left": None, "right": None, "delta": None}
        next_action = "核对相关原件；在核对区记录依据，必要时退回申请人补正。"
        if code == "document_business_amount":
            # Cross-month, mixed-currency or repeated origins require explicit
            # human reconciliation; the existing blocking finding is retained.
            comparable = bool(selected) and all(
                doc.get("currency") == "CNY" and (doc.get("issued_on") or "")[:7] == period
                for _, doc, _ in selected
            )
            comparable = comparable and len({origin for _, _, origin in selected}) == len(selected)
            if comparable:
                semantics = dict(
                    object_id=event["id"],
                    period=period,
                    currency="CNY",
                    basis="document_business_gross",
                )
                comparison = compare_amounts(
                    {**semantics, "value": payload.get("business_amount")},
                    {**semantics, "value": payload.get("document_amount")},
                )
            references.extend(identifier for identifier, _, _ in selected)
            if comparison["state"] == "conflict":
                aggregate = "total:" + fingerprint(
                    sorted(identifier for identifier, _, _ in selected)
                )
                node(
                    aggregate,
                    "aggregate",
                    event["version"],
                    fingerprint(payload),
                    aggregate,
                    "document_business_gross",
                    comparison["right"],
                    "CNY",
                )
                edges.extend(
                    dict(kind="depends_on", source=aggregate, target=identifier)
                    for identifier, _, _ in selected
                )
                edges.append(dict(kind="contradicts", source=event_node, target=aggregate))
                references.append(aggregate)
            else:
                next_action = (
                    "先确认票据月份、币种、同源重复与金额口径，再核对原件；不可直接比较或自动改数。"
                )
        elif code == "bank_link_amount":
            references.extend(bank_ids)
            next_action = "先核对是否分次付款、预付款或跨期收付，再查看已关联流水；不能直接把流水合计当作票据总额。"
        issues.append(
            dict(
                id=fingerprint({"code": code, "severity": finding["severity"], "payload": payload}),
                code=code,
                message=payload["message"],
                severity=finding["severity"],
                resolved=bool(finding.get("resolved")),
                evidence_ids=list(dict.fromkeys(references)),
                owner=owner,
                target="differences",
                can_handle=actionable,
                next_action=next_action,
                comparison=comparison,
            )
        )
    result = dict(version=1, nodes=nodes, edges=edges, original_roots=sorted(roots), issues=issues)
    return {**result, "fingerprint": fingerprint(result)}
