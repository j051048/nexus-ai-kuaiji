"""B08 switch preflight/package generator. It never executes a switch.

The output can be ready for an authorized human decision, but this tool does not
mark a switch approved and does not modify a ledger, mode or production system.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REQUIRED_GATES = {
    "b01_security": "B01 安全与权限",
    "b02_opening": "B02 核算契约与期初",
    "b03_posting": "B03 正式凭证与账簿",
    "b04_close_reports": "B04 子账月结与报表",
    "b05_ai_scope": "B05 AI 启用范围",
    "b06_user_paths": "B06 实际用户路径",
    "b07_deployment_recovery": "B07 部署与恢复",
    "two_periods": "两个完整连续账期",
    "permissions": "企业/账套/部门/本人权限",
    "recovery": "恢复与未知结果核对",
    "performance": "性能与效率门槛",
    "accountant_scope": "会计负责人确认政策与范围",
    "user_acceptance": "实际人员操作验收",
}
ALLOWED = {"passed", "partial", "failed", "missing", "not_applicable"}


class InputError(ValueError):
    pass


def _load(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"无法读取或解析切换清单：{path}") from error
    if not isinstance(value, dict):
        raise InputError("切换清单必须是 JSON 对象")
    return value


def evaluate(manifest):
    scope = manifest.get("scope") or {}
    for field in ("company_id", "ledger_id", "start_period", "mode"):
        if not scope.get(field):
            raise InputError(f"切换清单 scope 缺少 {field}")
    gates = manifest.get("gates")
    if not isinstance(gates, dict):
        raise InputError("切换清单缺少 gates")
    evidence = manifest.get("evidence") or []
    if not isinstance(evidence, list):
        raise InputError("evidence 必须是数组")
    issues = []
    normalized = {}
    evidence_gates = {item.get("gate") for item in evidence if isinstance(item, dict)}
    for key, label in REQUIRED_GATES.items():
        status = gates.get(key, "missing")
        if status not in ALLOWED:
            issues.append(
                {"gate": key, "label": label, "message": "未知门槛状态", "status": status}
            )
            status = "missing"
        if status == "not_applicable":
            reason = (manifest.get("not_applicable") or {}).get(key, "")
            if not reason or len(str(reason).strip()) < 3:
                issues.append(
                    {
                        "gate": key,
                        "label": label,
                        "message": "不适用必须写明试点范围依据",
                        "status": status,
                    }
                )
            else:
                status = "passed"
        if status != "passed":
            issues.append(
                {"gate": key, "label": label, "message": f"门槛未通过：{status}", "status": status}
            )
        if key not in evidence_gates:
            issues.append(
                {"gate": key, "label": label, "message": "缺少对应证据引用", "status": "missing"}
            )
        normalized[key] = {"label": label, "status": status}
    for item in evidence:
        if not isinstance(item, dict) or not item.get("ref") or not item.get("sha256"):
            issues.append(
                {
                    "gate": "evidence",
                    "label": "证据",
                    "message": "证据必须有 ref 和 sha256",
                    "status": "missing",
                }
            )
            continue
        digest = str(item["sha256"]).lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            issues.append(
                {
                    "gate": "evidence",
                    "label": "证据",
                    "message": "证据 sha256 格式无效",
                    "status": "failed",
                }
            )
    freeze = manifest.get("freeze") or {}
    authorization = manifest.get("authorization") or {}
    for field in ("data_boundary", "freeze_at", "program_version", "migration_version"):
        if not freeze.get(field):
            issues.append(
                {
                    "gate": "freeze",
                    "label": "冻结范围",
                    "message": f"缺少 {field}",
                    "status": "missing",
                }
            )
    # This tool never grants approval. A human authorization record only changes
    # the package to "ready_for_authorization"; the caller must still sign outside.
    if not authorization.get("approved_by") or not authorization.get("approved_at"):
        status = "ready_for_authorization"
    else:
        status = "ready_for_authorization"
        issues.append(
            {
                "gate": "authorization",
                "label": "最终授权",
                "message": "B08 工具不验证签名，不能将记录当作已批准切换",
                "status": "pending_external_signature",
            }
        )
    if any(item["gate"] != "authorization" for item in issues):
        status = "blocked"
    package = {
        "format": "b08-switch-package-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scope": scope,
        "gates": normalized,
        "issues": issues,
        "freeze": freeze,
        "data_boundary": freeze.get("data_boundary"),
        "steps": [
            "最终备份并验证原件/数据库完整性",
            "停止旧系统新增并冻结精确水位",
            "最终增量核对与差异签收",
            "仅启用获批企业/账套/期间/模式",
            "首批业务与权限观察",
            "观察期达到停止阈值则按中止方案处理",
        ],
        "stop_conditions": [
            "出现越权、借贷不平、重复记账或未解释期初差异",
            "存在未知外部结果或未核对回执",
            "恢复/权限/关键用户路径未通过",
            "会计负责人或授权人未签收",
        ],
        "note": "只读预检与切换包；不执行切换、不修改账本、不代表已批准。",
    }
    package["package_fingerprint"] = hashlib.sha256(
        json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return package


def markdown(package):
    lines = [
        "# B08 正式切换预检包",
        "",
        f"- 状态：**{package['status']}**",
        f"- 企业：{package['scope']['company_id']}",
        f"- 账套：{package['scope']['ledger_id']}",
        f"- 起始期间：{package['scope']['start_period']}",
        f"- 模式：{package['scope']['mode']}",
        f"- 包指纹：`{package['package_fingerprint']}`",
        "",
        "## 门槛",
        "",
    ]
    for item in package["issues"]:
        lines.append(f"- `{item['gate']}`：{item['message']}")
    if not package["issues"]:
        lines.append("全部门槛有证据，仍需外部授权人签收。")
    lines.extend(["", "## 中止条件", ""])
    lines.extend(f"- {item}" for item in package["stop_conditions"])
    lines.extend(["", "> 本包不执行切换，不替代会计签收或最终授权。"])
    return "\n".join(lines) + "\n"


def _write(path, text, force):
    target = Path(path)
    if target.exists() and not force:
        raise InputError(f"输出文件已存在，拒绝覆盖：{path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="B08 切换只读预检/切换包生成器")
    parser.add_argument("manifest")
    parser.add_argument("--json-out")
    parser.add_argument("--markdown-out")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        package = evaluate(_load(args.manifest))
        if args.json_out:
            _write(args.json_out, json.dumps(package, ensure_ascii=False, indent=2), args.force)
        if args.markdown_out:
            _write(args.markdown_out, markdown(package), args.force)
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0 if package["status"] == "ready_for_authorization" else 2


if __name__ == "__main__":
    raise SystemExit(main())
