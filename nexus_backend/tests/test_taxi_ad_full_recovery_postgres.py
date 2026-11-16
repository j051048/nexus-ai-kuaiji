"""Add populated taxi evidence to the existing isolated joint recovery drill.

The original drill still runs all identity, object, RLS, hold and resumed-workflow
assertions. Only its seed and verification hooks are extended for this test.
"""

import os

import pytest

import test_c01_full_recovery_postgres as joint
from taxi_ad_recovery_business import (
    seed_taxi_recovery,
    taxi_snapshot,
    verify_taxi_recovery_http,
)


pytestmark = pytest.mark.skipif(
    os.getenv("C01_FULL_RECOVERY_ACCEPT") != "1",
    reason="必须显式启用本机新集群出租车联合恢复，禁止读取生产连接",
)


@pytest.mark.parametrize("major", [16, 17])
def test_taxi_populated_database_originals_identity_permissions_and_hold(
    major, tmp_path, monkeypatch
):
    seeded = {}
    expected = None
    inventory_checks = 0
    http_checks = 0
    original_seed = joint.seed_latest_recovery_business
    original_inventory = joint.pending_inventory
    original_verify = joint.verify_latest_business_http

    def seed(*args, **kwargs):
        value = original_seed(*args, **kwargs)
        seeded.update(
            seed_taxi_recovery(args[0], kwargs["owner"], kwargs["reviewer"], value["company_id"])
        )
        value["taxi_ad"] = seeded
        return value

    def inventory(conn):
        nonlocal expected, inventory_checks
        value = original_inventory(conn)
        actual = taxi_snapshot(conn, seeded, postgres=True)
        if expected is None:
            expected = actual
        else:
            assert actual == expected, "恢复、解除hold或只读核验改变了出租车冻结证据"
        inventory_checks += 1
        return value

    def verify(client, headers, evidence, **kwargs):
        nonlocal http_checks
        value = original_verify(client, headers, evidence, **kwargs)
        value.update(verify_taxi_recovery_http(client, headers, evidence["taxi_ad"]))
        http_checks += 1
        return value

    monkeypatch.setattr(joint, "seed_latest_recovery_business", seed)
    monkeypatch.setattr(joint, "pending_inventory", inventory)
    monkeypatch.setattr(joint, "verify_latest_business_http", verify)
    joint.test_fresh_cluster_database_objects_signed_identity_and_http_recovery(
        major, tmp_path, monkeypatch
    )
    assert inventory_checks >= 3, "必须验证源库、恢复hold期间及恢复后业务状态"
    assert http_checks == 1, "必须通过恢复身份从真实API读取出租车证据"
