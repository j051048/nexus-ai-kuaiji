import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from scripts.access_policy_postgres_gate import MANIFEST, GateError, validate_junit


def xml_report(path: Path, *, skip=False, missing=False):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    for identity in cases[:-1] if missing else cases:
        classname, name = identity.split("::")
        node = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if skip:
            ET.SubElement(node, "skipped")
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("kind", ["skip", "missing", "summary-only-skip"])
def test_gate_rejects_unexecuted_required_cases(tmp_path, kind):
    path = tmp_path / "report.xml"
    xml_report(path, skip=kind == "skip", missing=kind == "missing")
    if kind == "summary-only-skip":
        document = ET.parse(path)
        document.getroot().set("skipped", "1")
        document.write(path)
    with pytest.raises(GateError):
        validate_junit(path)


def test_gate_accepts_only_complete_frozen_manifest(tmp_path):
    path = tmp_path / "report.xml"
    xml_report(path)
    assert validate_junit(path)["passed"] == 90
