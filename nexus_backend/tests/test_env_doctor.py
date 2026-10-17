"""环境体检脚本的解析与比较规则。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("env_doctor", ROOT / "scripts" / "env_doctor.py")
env_doctor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(env_doctor)


def test_parse_requirement_handles_comments_extras_and_ranges():
    assert env_doctor.parse_requirement("# 注释") is None
    assert env_doctor.parse_requirement("  ") is None
    assert env_doctor.parse_requirement("-r requirements.txt") is None
    assert env_doctor.parse_requirement("fastapi>=0.133,<0.134  # pin") == (
        "fastapi",
        [(">=", "0.133"), ("<", "0.134")],
    )
    assert env_doctor.parse_requirement("psycopg[binary]>=3.2,<4") == (
        "psycopg",
        [(">=", "3.2"), ("<", "4")],
    )
    assert env_doctor.parse_requirement("defusedxml==0.7.1") == (
        "defusedxml",
        [("==", "0.7.1")],
    )


def test_version_satisfaction_rules():
    assert env_doctor.satisfies("0.133.1", [(">=", "0.133"), ("<", "0.134")])
    assert not env_doctor.satisfies("0.109.0", [(">=", "0.133"), ("<", "0.134")])
    assert env_doctor.satisfies("2.15.1", [(">=", "2.10"), ("<", "3")])
    assert env_doctor.satisfies("1.8.0", [("~=", "1.8")])
    assert not env_doctor.satisfies("1.8.0", [("<", "1.8")])
