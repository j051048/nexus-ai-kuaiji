import pytest
from tools.capacity import firm_operations


def test_mixed_capacity_requires_unique_reviewed_outputs():
    rows = [
        {"company_id": "a", "draft_id": "d1", "export_id": "e1"},
        {"company_id": "b", "draft_id": "d2", "export_id": "e2"},
    ]
    assert firm_operations.validate_mixed_results(rows, {"a", "b"})["count"] == 2
    for bad in (
        [],
        [rows[0], rows[0]],
        [{**rows[0], "company_id": "other"}],
        [{**rows[0], "export_id": ""}],
    ):
        with pytest.raises(ValueError):
            firm_operations.validate_mixed_results(bad, {"a", "b"})
