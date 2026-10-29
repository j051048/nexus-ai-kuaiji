"""Shared N02/N03 fixture target: explicit isolated runner, never cloud/default PG.

The AK05 configuration contract remains the authority for host, database and
role names. A missing ordinary local environment can be skipped by the caller;
once a runner or any DSN is present, partial/invalid configuration fails closed.
This module performs only read-only validation, never provision/migrate/repair.
"""

from __future__ import annotations

from collections.abc import Mapping
import os

from scripts import ak05_postgres_gate as gate


ENV_KEYS = {
    "admin": "ACCOUNTING_TEST_POSTGRES_URL",
    "app": "ACCOUNTING_TEST_DATABASE_URL",
    "worker": "ACCOUNTING_TEST_WORKER_DATABASE_URL",
    "projection": "ACCOUNTING_PROJECTION_DATABASE_URL",
}
RUNNER_FLAG = "ACCOUNTING_AK05_ISOLATED_TARGET"


def load_suite_target(environment: Mapping[str, str] | None = None) -> dict[str, str] | None:
    environment = os.environ if environment is None else environment
    target = {key: environment.get(name, "") for key, name in ENV_KEYS.items()}
    flag = environment.get(RUNNER_FLAG, "")
    if not any(target.values()) and not flag:
        return None
    if flag != "1" or not all(target.values()):
        raise gate.GateError("N02/N03 require the explicit isolated runner and all four identities")
    return gate.validate_config(target)


def verify_suite_target(target: Mapping[str, str]) -> dict[str, str]:
    """Recheck actual PG major, durability and exact minimum role membership."""
    config = gate.validate_config(target)
    try:
        with gate.local_connection(config["admin"]) as connection:
            major = int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000
        if major not in {16, 17}:
            raise gate.GateError("N02/N03 require actual PostgreSQL 16 or 17")
        gate.assert_server_and_roles(config, major)
    except gate.GateError:
        raise
    except Exception:
        # Connection errors can contain passwords or connection strings. Callers
        # receive a controlled diagnostic, not raw driver text or exception chains.
        raise gate.GateError(
            "N02/N03 isolated server or restricted roles could not be verified"
        ) from None
    return config
