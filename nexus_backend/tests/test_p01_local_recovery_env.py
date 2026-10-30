"""The local recovery setup must never adopt another server or a dirty target."""

import pytest

from scripts.p01_local_recovery_env import (
    RecoveryEnvironmentError,
    validate_layout,
    validate_empty_target,
)


def layout():
    return {
        name: {"major": major, "port": port, "kind": kind}
        for name, major, port, kind in (
            ("source16", 16, 25496, "source"),
            ("target16", 16, 25497, "target"),
            ("source17", 17, 25498, "source"),
            ("target17", 17, 25499, "target"),
        )
    }


def test_four_independent_local_clusters_are_required():
    assert validate_layout(layout()) == layout()
    for change in ("missing", "same_port", "wrong_version", "default_port"):
        value = layout()
        if change == "missing":
            del value["target17"]
        elif change == "same_port":
            value["target16"]["port"] = 25496
        elif change == "wrong_version":
            value["source17"]["major"] = 14
        else:
            value["source16"]["port"] = 5432
        with pytest.raises(RecoveryEnvironmentError):
            validate_layout(value)


def test_occupied_ports_are_not_adopted():
    with pytest.raises(RecoveryEnvironmentError, match="occupied"):
        validate_layout(layout(), occupied={25498})


def test_nonempty_target_is_not_reset_or_dropped():
    validate_empty_target(0, 0, 0)
    for counts in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0), (False, 0, 0)):
        with pytest.raises(RecoveryEnvironmentError):
            validate_empty_target(*counts)
