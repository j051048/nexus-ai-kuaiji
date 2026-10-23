"""Entity-bound auxiliary dimensions for newly recorded subledger facts.

Existing immutable history stays readable. This contract deliberately accepts
stable entity IDs only; display labels and old catalog aliases are not IDs.
"""

from collections.abc import Mapping

from ..accounting.database import PostgresConnection
from ..accounting.domain import AccountingError

DIMENSIONS = frozenset({"department", "counterparty", "employee", "project"})


def validate_required_aux(required_aux, account_required=()):
    for dimensions in (required_aux, account_required):
        if not isinstance(dimensions, (list, tuple)) or any(
            not isinstance(value, str) or value not in DIMENSIONS for value in dimensions
        ):
            raise AccountingError("必要辅助维度必须使用受支持的维度名称", 422)
        if len(dimensions) != len(set(dimensions)):
            raise AccountingError("必要辅助维度不能重复", 422)
    if not set(account_required).issubset(required_aux):
        raise AccountingError("控制配置不能遗漏科目要求的必要辅助维度", 422)
    return sorted(required_aux)


def _entity_valid(conn, scope, dimension, value):
    if isinstance(conn, PostgresConnection):
        return bool(
            conn.execute(
                "SELECT accounting.c01_subledger_aux_valid(?,?,?)",
                (scope["id"], dimension, value),
            ).fetchone()[0]
        )
    if dimension in {"counterparty", "project"}:
        return bool(
            conn.execute(
                "SELECT 1 FROM company_master_entities WHERE id=? AND firm_id=? "
                "AND company_id=? AND kind=? AND active=1",
                (value, scope["firm_id"], scope["company_id"], dimension),
            ).fetchone()
        )
    if dimension == "department":
        return bool(
            conn.execute(
                "SELECT 1 FROM departments WHERE id=? AND company_id=?",
                (value, scope["company_id"]),
            ).fetchone()
        )
    if dimension == "employee":
        return bool(
            conn.execute(
                "SELECT 1 FROM company_memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.company_id=? AND m.user_id=? AND u.active=1",
                (scope["company_id"], value),
            ).fetchone()
        )
    return False


def validate_auxiliary_entities(conn, scope, required_aux, aux, counterparty_id):
    """Validate within the caller's authorized, locked write transaction."""
    required = validate_required_aux(required_aux)
    if not isinstance(aux, dict) or set(aux) - DIMENSIONS:
        raise AccountingError("辅助维度字段不受支持", 422)
    if not set(required).issubset(aux):
        raise AccountingError("缺少控制科目要求的必要辅助维度", 422)
    if not isinstance(scope, Mapping) or not all(
        isinstance(scope.get(key), str) and scope[key] for key in ("id", "firm_id", "company_id")
    ):
        raise AccountingError("辅助维度缺少可信企业账套范围", 422)
    actual = conn.execute(
        "SELECT 1 FROM books WHERE id=? AND firm_id=? AND company_id=?",
        (scope["id"], scope["firm_id"], scope["company_id"]),
    ).fetchone()
    if not actual:
        raise AccountingError("账套不存在或不属于当前企业", 404)
    if not isinstance(counterparty_id, str) or not _entity_valid(
        conn, scope, "counterparty", counterparty_id
    ):
        raise AccountingError("往来方不存在、停用或不属于本企业", 404)
    if "counterparty" in aux and aux["counterparty"] != counterparty_id:
        raise AccountingError("往来方辅助维度必须与未结事项往来方一致", 422)
    for dimension, value in aux.items():
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 100
            or not _entity_valid(conn, scope, dimension, value)
        ):
            raise AccountingError("辅助维度实体不存在、类型不符、停用或不属于本企业", 422)
    return dict(sorted(aux.items()))
