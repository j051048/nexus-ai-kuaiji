from scripts.migrate_accounting import classify_migrations


def test_status_distinguishes_missing_from_checksum_mismatch():
    files = [(1, "a"), (2, "b"), (3, "c")]
    assert classify_migrations({1: "a", 2: "old"}, files) == [
        (1, "已应用"),
        (2, "校验不符"),
        (3, "缺失"),
    ]
