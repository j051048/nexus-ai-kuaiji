"""Pure semantic keys; identity resolution itself always needs a scoped transaction."""

from ..accounting.service import fingerprint


def semantic_definition(definition):
    value = definition.model_dump() if hasattr(definition, "model_dump") else definition
    conditions = dict(value["conditions"])
    if conditions.get("counterparty_id"):
        conditions.pop("counterparty", None)
    else:
        conditions.pop("counterparty_id", None)
    return {
        "source_kind": value["source_kind"],
        "conditions": conditions,
        "lines": sorted(
            value["lines"],
            key=lambda line: (line["account_code"], line["side"], line["amount_field"]),
        ),
    }


def semantic_fingerprint(definition):
    return fingerprint(semantic_definition(definition))
