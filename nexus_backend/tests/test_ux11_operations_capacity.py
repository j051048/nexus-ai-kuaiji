import pytest
from tools.capacity.firm_operations import validate_results


def result(company, output):
    return {"ok": True, "company_id": company, "output_id": output}


def test_capacity_acceptance_requires_exact_company_conservation():
    assert validate_results([result("a", "1"), result("b", "2")], {"a", "b"}) == 2
    for rows in (
        [result("a", "1")],
        [result("a", "1"), result("a", "2")],
        [result("a", "1"), result("foreign", "2")],
        [result("a", "1"), result("b", "1")],
        [result("a", "1"), {**result("b", "2"), "ok": False}],
    ):
        with pytest.raises(ValueError):
            validate_results(rows, {"a", "b"})
