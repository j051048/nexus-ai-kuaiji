"""H01 offline invoice-field replay.

This module deliberately has no FastAPI, database, worker, network or provider
imports. It consumes frozen OCR text, executes two declarative policies,
validates evidence against that text, and delegates field scoring to the
existing pure evaluator.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_ocr_fields import FIELDS, evaluate  # noqa: E402
from .vendor.harness2_runmark import (  # noqa: E402
    fingerprint as harness2_fingerprint,
    is_complete,
    mark_complete,
    why_not,
)
from .vendor.harness2_tree import normalize_rel  # noqa: E402

SCHEMA = "ocr-replay-v1"
MAX_MANIFEST = 2 * 1024 * 1024
MAX_SAMPLES = 500
MAX_PAGE_TEXT = 30_000
MAX_FILE = 2 * 1024 * 1024
DISCLAIMER = "本报告仅用于离线比较，不授权规则发布、自动审核、记账或导出。"


class ReplayError(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractionInput:
    """The only object an extractor may receive; gold is deliberately absent."""

    sample_id: str
    firm_id: str
    company_id: str
    ledger_id: str
    book_id: str
    period: str
    category: str
    ocr_sha256: str
    pages: tuple


@dataclass(frozen=True)
class Policy:
    name: str
    version: str
    prompt: str
    gross_pattern: str
    strip_invoice_zeros: bool = False


POLICIES = {
    "baseline": Policy(
        name="baseline",
        version="h01-baseline-v1",
        prompt=(
            "从冻结 OCR 文本提取票据字段；金额保留十进制文本；无证据不补零；"
            "价税合计仅识别“价税合计”标签。"
        ),
        gross_pattern=r"价税合计\s*[：:]\s*([^\s，,]+)",
    ),
    "candidate": Policy(
        name="candidate",
        version="h01-candidate-gross-label-v1",
        prompt=(
            "从冻结 OCR 文本提取票据字段；金额保留十进制文本；无证据不补零；"
            "价税合计同时识别“价税合计（小写）”；不猜测未知字段。"
        ),
        gross_pattern=r"价税合计(?:（小写）)?\s*[：:]\s*([^\s，,]+)",
        strip_invoice_zeros=True,
    ),
}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReplayError("DUPLICATE_KEY", f"JSON 重复键: {key}")
        result[key] = value
    return result


def _json(text, code="INVALID_JSON"):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(
            ReplayError("NON_FINITE", f"不允许 {value}")
        ))
    except ReplayError:
        raise
    except (ValueError, TypeError) as exc:
        raise ReplayError(code, "JSON 无效") from exc


def _read_text(path: Path, limit=MAX_FILE):
    if path.stat().st_size > limit:
        raise ReplayError("FILE_TOO_LARGE", f"文件超过 {limit} 字节")
    return path.read_text(encoding="utf-8-sig")


def read_json(path: Path, limit=MAX_FILE):
    return _json(_read_text(path, limit))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _safe(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ReplayError("UNSAFE_PATH", "路径必须是 manifest 目录内的相对路径")
    try:
        relative = normalize_rel(relative)
    except ValueError as exc:
        raise ReplayError("UNSAFE_PATH", "路径越出数据目录") from exc
    if not relative:
        raise ReplayError("UNSAFE_PATH", "路径不能为空")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ReplayError("UNSAFE_PATH", "路径越出数据目录") from exc
    if not candidate.is_file():
        raise ReplayError("MISSING_FILE", f"文件不存在: {relative}")
    return candidate


def load_manifest(path: Path):
    root = path.resolve().parent
    data = read_json(path, MAX_MANIFEST)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise ReplayError("MANIFEST_SCHEMA", "manifest schema 不匹配")
    if data.get("data_nature") not in {"synthetic", "authorized_private_test"}:
        raise ReplayError("DATA_NATURE", "数据性质必须是 synthetic 或 authorized_private_test")
    firms = data.get("firms")
    samples = data.get("samples")
    if not isinstance(firms, list) or not firms or not isinstance(samples, list):
        raise ReplayError("MANIFEST_SHAPE", "firms/samples 必须是非空数组")
    if not 1 <= len(samples) <= MAX_SAMPLES:
        raise ReplayError("SAMPLE_LIMIT", "样本数必须在 1–500")
    firm_ids = set()
    firm_scope = {}
    for firm in firms:
        if not isinstance(firm, dict) or not all(
            isinstance(firm.get(key), str) and firm[key]
            for key in ("firm_id", "company_id", "ledger_id", "book_id", "period")
        ):
            raise ReplayError("FIRM_SHAPE", "企业范围字段无效")
        if firm["firm_id"] in firm_ids:
            raise ReplayError("FIRM_DUPLICATE", "firm_id 重复")
        firm_ids.add(firm["firm_id"])
        firm_scope[firm["firm_id"]] = firm
    seen, sources, splits = set(), set(), {}
    normalized = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ReplayError("SAMPLE_SHAPE", "样本必须是对象")
        required = (
            "sample_id", "firm_id", "company_id", "ledger_id", "book_id", "period",
            "category", "split", "source_hash", "ocr_path", "gold_path",
            "ocr_sha256", "gold_sha256",
        )
        if not all(isinstance(sample.get(key), str) and sample[key] for key in required):
            raise ReplayError("SAMPLE_SHAPE", "样本范围/路径字段不完整")
        if sample["sample_id"] in seen:
            raise ReplayError("SAMPLE_DUPLICATE", "sample_id 重复")
        seen.add(sample["sample_id"])
        if sample["firm_id"] not in firm_ids:
            raise ReplayError("SAMPLE_FIRM", "样本引用了未声明企业")
        scope = firm_scope[sample["firm_id"]]
        for key in ("company_id", "ledger_id", "book_id", "period"):
            if sample[key] != scope[key]:
                raise ReplayError("SAMPLE_SCOPE", f"{sample['sample_id']} 的 {key} 不属于声明企业")
        if sample["split"] not in {"dev", "validation"}:
            raise ReplayError("SPLIT", "split 必须是 dev/validation")
        if sample["source_hash"] in sources:
            raise ReplayError("SOURCE_DUPLICATE", "同一来源出现在多个样本或划分")
        sources.add(sample["source_hash"])
        splits.setdefault(sample["source_hash"], sample["split"])
        if sample["category"].startswith("unsupported") and sample["split"] != "validation":
            raise ReplayError("UNSUPPORTED_SPLIT", "范围外样本只能放在 validation")
        ocr_path, gold_path = _safe(root, sample["ocr_path"]), _safe(root, sample["gold_path"])
        if sample.get("ocr_sha256") and sha256_path(ocr_path).lower() != sample["ocr_sha256"].lower():
            raise ReplayError("OCR_HASH", f"OCR hash 不匹配: {sample['sample_id']}")
        if sample.get("gold_sha256") and sha256_path(gold_path).lower() != sample["gold_sha256"].lower():
            raise ReplayError("GOLD_HASH", f"金标 hash 不匹配: {sample['sample_id']}")
        ocr = read_json(ocr_path)
        gold = read_json(gold_path)
        pages = ocr.get("pages") if isinstance(ocr, dict) else None
        if not isinstance(pages, list) or not 1 <= len(pages) <= 6:
            raise ReplayError("PAGES", "OCR 必须包含 1–6 页")
        page_numbers = []
        for page in pages:
            if not isinstance(page, dict):
                raise ReplayError("PAGE_SHAPE", "页面必须是对象")
            number, text = page.get("page"), page.get("raw_text")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise ReplayError("PAGE_NUMBER", "页码必须是正整数")
            if not isinstance(text, str) or len(text) > MAX_PAGE_TEXT:
                raise ReplayError("PAGE_TEXT", "页面文本缺失或超长")
            page_numbers.append(number)
        if len(set(page_numbers)) != len(page_numbers):
            raise ReplayError("PAGE_DUPLICATE", "页码重复")
        gfields = gold.get("fields") if isinstance(gold, dict) else None
        if not isinstance(gfields, dict) or not gfields or set(gfields) - set(FIELDS):
            raise ReplayError("GOLD_FIELDS", "金标字段为空或包含未知字段")
        normalized.append({
            **sample,
            "ocr_path": str(ocr_path),
            "gold_path": str(gold_path),
            "ocr_pages": pages,
            "gold_fields": gfields,
        })
    return {**data, "firms": firms, "samples": normalized, "manifest_sha256": sha256_path(path)}


def to_extraction_input(sample):
    """Strip scoring-only fields before the extractor is called."""
    return ExtractionInput(
        sample_id=sample["sample_id"],
        firm_id=sample["firm_id"],
        company_id=sample["company_id"],
        ledger_id=sample["ledger_id"],
        book_id=sample["book_id"],
        period=sample["period"],
        category=sample["category"],
        ocr_sha256=sample["ocr_sha256"].lower(),
        pages=tuple({"page": p["page"], "raw_text": p["raw_text"]} for p in sample["ocr_pages"]),
    )


def _line_evidence(pages, selector):
    for page in pages:
        text = page["raw_text"]
        match = selector(text)
        if match:
            quote = match.group(0).strip()
            return quote, page["page"], text
    return None, None, None


def _find(pages, pattern, field, evidence):
    for page in pages:
        text = page["raw_text"]
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            value = (match.group(1) if match.lastindex else match.group(0)).strip()
            evidence.append({"field": field, "page": page["page"], "quote": match.group(0).strip()})
            return value
    return None


def _money(value):
    if value is None:
        return None
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?", value):
        raise ReplayError("INVALID_AMOUNT", "金额必须是非负十进制文本，最多两位小数")
    return value


def _date(value):
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ReplayError("INVALID_DATE", "日期必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ReplayError("INVALID_DATE", "日期不是有效日历日") from exc
    return value


def _result_identity(inp, policy):
    return {
        "schema_version": SCHEMA,
        "sample_id": inp.sample_id,
        "firm_id": inp.firm_id,
        "company_id": inp.company_id,
        "ledger_id": inp.ledger_id,
        "book_id": inp.book_id,
        "period": inp.period,
        "ocr_sha256": inp.ocr_sha256,
        "policy": policy.name,
        "policy_version": policy.version,
        "policy_prompt": policy.prompt,
        "request_fingerprint": sha256_bytes(json.dumps({
            "sample": inp.sample_id,
            "ocr": inp.ocr_sha256,
            "policy": policy.version,
            "prompt": policy.prompt,
        }, ensure_ascii=False, sort_keys=True).encode()),
        "input_hash": inp.ocr_sha256,
        "duration_ms": 0.0,
        "cost_cny": None,
    }


def extract(inp: ExtractionInput, policy: Policy):
    """Run one policy against a gold-free ExtractionInput."""
    if not isinstance(inp, ExtractionInput):
        raise ReplayError("INPUT_CONTRACT", "提取器只接受 ExtractionInput")
    if not isinstance(policy, Policy):
        raise ReplayError("POLICY", "策略必须是固定 Policy")
    pages = list(inp.pages)
    fields = {}
    evidence = []
    fields["invoice_number"] = _find(pages, r"发票号码\s*[：:]\s*([A-Za-z0-9-]{1,100})", "invoice_number", evidence)
    if fields["invoice_number"] and policy.strip_invoice_zeros:
        fields["invoice_number"] = fields["invoice_number"].lstrip("0") or "0"
    fields["issued_on"] = _find(pages, r"开票日期\s*[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", "issued_on", evidence)
    fields["seller_name"] = _find(pages, r"销售方名称\s*[：:]\s*([^\n]{1,200})", "seller_name", evidence)
    fields["seller_tax_id"] = _find(pages, r"销售方纳税人识别号\s*[：:]\s*([A-Za-z0-9]{5,50})", "seller_tax_id", evidence)
    fields["buyer_name"] = _find(pages, r"购买方名称\s*[：:]\s*([^\n]{1,200})", "buyer_name", evidence)
    fields["buyer_tax_id"] = _find(pages, r"购买方纳税人识别号\s*[：:]\s*([A-Za-z0-9]{5,50})", "buyer_tax_id", evidence)
    fields["gross_amount"] = _find(pages, policy.gross_pattern, "gross_amount", evidence)
    fields["net_amount"] = _find(pages, r"(?<!价税合计)金额\s*[：:]\s*([^\s，,]+)", "net_amount", evidence)
    fields["tax_amount"] = _find(pages, r"税额\s*[：:]\s*([^\s，,]+)", "tax_amount", evidence)
    fields["currency"] = _find(pages, r"币种\s*[：:]\s*([A-Za-z]{3})", "currency", evidence)
    for name in ("gross_amount", "net_amount", "tax_amount"):
        try:
            fields[name] = _money(fields[name])
        except ReplayError:
            return _failed(inp, policy, "INVALID_AMOUNT", fields, evidence, "金额格式无效")
    try:
        fields["issued_on"] = _date(fields["issued_on"])
    except ReplayError:
        return _failed(inp, policy, "INVALID_DATE", fields, evidence, "日期格式无效")
    # Deterministic checks are computed before any gold is read.
    deterministic_errors = []
    if fields["currency"] and fields["currency"].upper() != "CNY":
        deterministic_errors.append("UNSUPPORTED_CURRENCY")
    if all(fields[name] is not None for name in ("gross_amount", "net_amount", "tax_amount")):
        if Decimal(fields["gross_amount"]) != Decimal(fields["net_amount"]) + Decimal(fields["tax_amount"]):
            deterministic_errors.append("AMOUNT_MISMATCH")
    missing = [name for name, value in fields.items() if value is None]
    review = bool(missing) or bool(deterministic_errors) or inp.category.startswith("unsupported")
    error = deterministic_errors[0] if deterministic_errors else ("REVIEW_REQUIRED" if review else None)
    result = _result_identity(inp, policy)
    result.update({
        "status": "succeeded",
        "fields": fields,
        "evidence": evidence,
        "review_required": review,
        "error_code": error,
        "deterministic_errors": deterministic_errors,
        "missing_fields": missing,
    })
    result["evidence_errors"] = validate_evidence(evidence, pages, fields)
    return result


def _failed(inp, policy, code, fields, evidence, message):
    result = _result_identity(inp, policy)
    result.update({
        "status": "failed",
        "fields": fields,
        "evidence": evidence,
        "review_required": True,
        "error_code": code,
        "error_message": message,
    })
    result["evidence_errors"] = validate_evidence(evidence, list(inp.pages), fields)
    return result


def validate_evidence(evidence, pages, fields=None):
    """Recompute evidence validity from frozen text; never trust result flags."""
    errors = []
    if not isinstance(evidence, list):
        return ["evidence-not-list"]
    page_map = {page["page"]: page["raw_text"] for page in pages}
    for item in evidence:
        if not isinstance(item, dict):
            errors.append("evidence-not-object")
            continue
        field, page, quote = item.get("field"), item.get("page"), item.get("quote")
        if field not in FIELDS:
            errors.append(f"unknown-field:{field}")
        if type(page) is not int or page not in page_map:
            errors.append(f"bad-page:{page}")
        elif not isinstance(quote, str) or not quote or quote not in page_map[page]:
            errors.append(f"quote-not-in-text:{field}")
        elif fields is not None and fields.get(field) is not None and not _quote_supports(field, fields[field], quote):
            errors.append(f"evidence-does-not-support-value:{field}")
    if fields is not None:
        for field, value in fields.items():
            if value is not None and not any(
                isinstance(item, dict) and item.get("field") == field for item in evidence
            ):
                errors.append(f"missing-evidence:{field}")
    return errors


def _quote_supports(field, value, quote):
    text = unicodedata.normalize("NFKC", quote)
    wanted = unicodedata.normalize("NFKC", str(value))
    if field.endswith("_amount"):
        try:
            target = Decimal(wanted)
        except InvalidOperation:
            return False
        return any(
            Decimal(token) == target
            for token in re.findall(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", text)
            if _is_decimal(token)
        )
    return wanted in text


def _is_decimal(token):
    try:
        Decimal(token)
        return True
    except InvalidOperation:
        return False


def _atomic_json(path: Path, value):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(manifest, policy, mode="mock", archived_sha256=None):
    return sha256_bytes(json.dumps({
        "manifest": manifest["manifest_sha256"],
        "policy": policy.version,
        "policy_config": {
            "prompt": policy.prompt,
            "gross_pattern": policy.gross_pattern,
            "strip_invoice_zeros": policy.strip_invoice_zeros,
        },
        "schema": SCHEMA,
        "mode": mode,
        "archived_sha256": archived_sha256,
        "harness": harness2_fingerprint(Path(__file__).parent),
    }, ensure_ascii=False, sort_keys=True).encode())


def _archive_digest(path: Path):
    path = Path(path).resolve()
    target = path / "results.jsonl" if path.is_dir() else path
    if not target.is_file():
        raise ReplayError("ARCHIVED_MISSING", "归档结果文件不存在")
    return sha256_path(target)


def _result_digest(result):
    return sha256_bytes(json.dumps(result, ensure_ascii=False, sort_keys=True).encode())


def expected_request_fingerprint(sample, policy):
    return sha256_bytes(json.dumps({
        "sample": sample["sample_id"],
        "ocr": sample["ocr_sha256"].lower(),
        "policy": policy.version,
        "prompt": policy.prompt,
    }, ensure_ascii=False, sort_keys=True).encode())


def validate_result_identity(manifest, sample, result, policy=None):
    """Shared validation for new, archived, resumed and compared results."""
    if not isinstance(result, dict):
        raise ReplayError("RESULT_SHAPE", f"{sample['sample_id']} 结果不是对象")
    if result.get("schema_version") != SCHEMA:
        raise ReplayError("RESULT_SCHEMA", f"{sample['sample_id']} schema 不匹配")
    for key in ("firm_id", "company_id", "ledger_id", "book_id", "period"):
        if result.get(key) != sample[key]:
            raise ReplayError("RESULT_SCOPE", f"{sample['sample_id']} {key} 与 manifest 不一致")
    if result.get("sample_id") != sample["sample_id"]:
        raise ReplayError("RESULT_SAMPLE", "sample_id 不一致")
    if result.get("ocr_sha256") != sample["ocr_sha256"].lower():
        raise ReplayError("RESULT_INPUT", f"{sample['sample_id']} OCR 摘要不一致")
    if result.get("input_hash") != sample["ocr_sha256"].lower():
        raise ReplayError("RESULT_INPUT", f"{sample['sample_id']} 输入摘要不一致")
    if result.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ReplayError("RESULT_DATASET", f"{sample['sample_id']} 数据集摘要不一致")
    if not result.get("request_fingerprint"):
        raise ReplayError("RESULT_SOURCE", f"{sample['sample_id']} 缺少请求来源指纹")
    if policy is not None:
        if result.get("policy_version") != policy.version:
            raise ReplayError("RESULT_POLICY", f"{sample['sample_id']} 策略版本不一致")
        if result.get("policy_prompt") != policy.prompt:
            raise ReplayError("RESULT_POLICY", f"{sample['sample_id']} 策略提示词不一致")
        if result["request_fingerprint"] != expected_request_fingerprint(sample, policy):
            raise ReplayError("RESULT_SOURCE", f"{sample['sample_id']} 请求来源指纹不一致")
    if result.get("status") not in {"succeeded", "failed", "unknown"}:
        raise ReplayError("RESULT_STATUS", f"{sample['sample_id']} 状态无效")
    if type(result.get("review_required")) is not bool:
        raise ReplayError("RESULT_REVIEW", f"{sample['sample_id']} 人工标记无效")
    if not isinstance(result.get("fields"), dict) or not isinstance(result.get("evidence", []), list):
        raise ReplayError("RESULT_FIELDS", f"{sample['sample_id']} 字段/证据结构无效")
    if result.get("status") == "unknown":
        raise ReplayError("RESULT_UNKNOWN", f"{sample['sample_id']} 结果未知，不能作为有效比较")
    return result


def _ensure_not_input(out: Path, manifest_path: Path):
    out = out.resolve()
    manifest_root = manifest_path.resolve().parent
    if out == manifest_root or manifest_root in out.parents:
        raise ReplayError("OUTPUT_PROTECTED", "输出目录不能位于输入数据目录内")
    if out == manifest_path.resolve():
        raise ReplayError("OUTPUT_PROTECTED", "输出不能覆盖 manifest")


def run_manifest(
    manifest_path: Path,
    policy_name: str,
    out_dir: Path,
    resume=False,
    mode="mock",
    archived=None,
):
    manifest = load_manifest(manifest_path)
    if policy_name not in POLICIES:
        raise ReplayError("POLICY", f"未知策略: {policy_name}")
    if mode not in {"mock", "archived_results"}:
        raise ReplayError("MODE", "仅支持 mock 或 archived_results")
    if mode == "archived_results" and archived is None:
        raise ReplayError("ARCHIVED_REQUIRED", "archived_results 需提供归档结果")
    policy = POLICIES[policy_name]
    archived_sha256 = _archive_digest(archived) if mode == "archived_results" else None
    fingerprint = _fingerprint(manifest, policy, mode, archived_sha256)
    _ensure_not_input(out_dir, manifest_path)
    out_dir = out_dir.resolve()
    if out_dir.exists() and not resume:
        raise ReplayError("OUTPUT_EXISTS", "输出目录已存在；使用 --resume 或换目录")
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        if state.get("fingerprint") != fingerprint:
            raise ReplayError("INCOMPATIBLE_RESUME", "配置或数据包与旧运行不一致")
        if state.get("status") == "completed":
            if not is_complete(out_dir, harness_dir=Path(__file__).parent):
                raise ReplayError(
                    "INCOMPLETE",
                    "运行状态为 completed 但缺少有效完成标记；" + why_not(
                        out_dir, harness_dir=Path(__file__).parent
                    ),
                )
            if not (out_dir / "results.jsonl").is_file():
                raise ReplayError("INCOMPLETE", "完成标记存在但结果文件缺失")
            persisted = {row.get("sample_id"): row for row in _load_results(out_dir)}
            for sample in manifest["samples"]:
                sid = sample["sample_id"]
                stored = state["samples"].get(sid, {})
                if stored.get("result_sha256") != _result_digest(persisted.get(sid)):
                    raise ReplayError("RESULT_CORRUPT", f"样本 {sid} 持久化结果被修改")
                validate_result_identity(manifest, sample, persisted.get(sid))
    else:
        state = {
            "schema_version": SCHEMA,
            "fingerprint": fingerprint,
            "manifest_sha256": manifest["manifest_sha256"],
            "policy": policy.name,
            "policy_version": policy.version,
            "mode": mode,
            "archived_sha256": archived_sha256,
            "status": "running",
            "samples": {},
        }
        _atomic_json(state_path, state)
    archived_rows = None
    if mode == "archived_results":
        archived_rows = {}
        archived_path = Path(archived).resolve()
        archived_source = (
            _load_results(archived_path)
            if archived_path.is_dir()
            else [_json(line) for line in _read_text(archived_path).splitlines() if line.strip()]
        )
        for row in archived_source:
            key = row.get("sample_id") if isinstance(row, dict) else None
            if key in archived_rows:
                raise ReplayError("DUPLICATE_RESULT", f"归档结果重复: {key}")
            if key not in {s["sample_id"] for s in manifest["samples"]}:
                raise ReplayError("RESULT_SET", f"归档结果含未知样本: {key}")
            if row.get("status") not in {"succeeded", "failed"} or type(row.get("review_required")) is not bool:
                raise ReplayError("ARCHIVED_SHAPE", "归档结果状态/人工标记无效")
            archived_rows[key] = row
        if len(archived_rows) != len(manifest["samples"]):
            raise ReplayError("RESULT_SET", "归档结果缺少样本")
    results_path = out_dir / "results.jsonl"
    completed = 0
    for sample in manifest["samples"]:
        sample_id = sample["sample_id"]
        old = state["samples"].get(sample_id)
        if old and old.get("status") == "completed" and old.get("fingerprint") == fingerprint:
            if old.get("result_sha256") != _result_digest(old.get("result")):
                raise ReplayError("RESULT_CORRUPT", f"样本 {sample_id} 的完成结果被修改")
            validate_result_identity(manifest, sample, old.get("result"))
            completed += 1
            continue
        if old and old.get("status") == "unknown":
            raise ReplayError("UNKNOWN_NOT_RETRIED", f"样本 {sample_id} 上次结果未知，禁止自动重发")
        started = time.perf_counter()
        if archived_rows is not None:
            source = dict(archived_rows[sample_id])
            validate_result_identity(manifest, sample, source, policy=policy)
            result = dict(source)
            result.setdefault("duration_ms", None)
            result.setdefault("cost_cny", None)
            # Preserve the original source; the import wrapper is separate.
            result["imported_mode"] = "archived_results"
            result["imported_from_manifest_sha256"] = source.get("manifest_sha256")
            result["imported_from_policy_version"] = source.get("policy_version")
        else:
            result = extract(to_extraction_input(sample), policy)
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 6)
            result["manifest_sha256"] = manifest["manifest_sha256"]
            result["mode"] = mode
            validate_result_identity(manifest, sample, result)
        state["samples"][sample_id] = {
            "status": "completed",
            "fingerprint": fingerprint,
            "result_sha256": _result_digest(result),
            "result": result,
        }
        _atomic_json(state_path, state)
        completed += 1
    tmp_results = results_path.with_name(results_path.name + ".tmp")
    with tmp_results.open("w", encoding="utf-8") as handle:
        for sample in manifest["samples"]:
            handle.write(json.dumps(state["samples"][sample["sample_id"]]["result"], ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp_results, results_path)
    _atomic_json(out_dir / "run.json", {
        "schema_version": SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "policy": policy.name,
        "policy_version": policy.version,
        "policy_prompt": policy.prompt,
        "policy_config": {
            "gross_pattern": policy.gross_pattern,
            "strip_invoice_zeros": policy.strip_invoice_zeros,
        },
        "mode": mode,
        "archived_sha256": archived_sha256,
        "model": "none",
        "model_verified": False,
        "disclaimer": DISCLAIMER,
    })
    state["status"] = "completed"
    _atomic_json(state_path, state)
    mark_complete(
        out_dir,
        run_id=fingerprint,
        harness_dir=Path(__file__).parent,
        extra={"dataset": manifest["manifest_sha256"], "policy": policy.version, "mode": mode},
    )
    return {"run_dir": str(out_dir), "completed": completed, "results": results_path}


def _load_results(directory: Path):
    results = []
    path = directory / "results.jsonl"
    if not path.is_file():
        raise ReplayError("RESULTS_MISSING", f"缺少 {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            results.append(_json(line))
    return results


def compare(manifest_path: Path, baseline_dir: Path, candidate_dir: Path, out_json: Path | None = None):
    manifest = load_manifest(manifest_path)
    if out_json:
        _ensure_not_input(out_json, manifest_path)
        if out_json.exists():
            raise ReplayError("OUTPUT_EXISTS", "报告已存在，拒绝静默覆盖")
    def run_meta(directory):
        path = Path(directory) / "run.json"
        if not path.is_file():
            raise ReplayError("RUN_METADATA", f"缺少运行元数据: {directory}")
        meta = read_json(path)
        if meta.get("manifest_sha256") != manifest["manifest_sha256"]:
            raise ReplayError("RUN_METADATA", "运行元数据与 manifest 不一致")
        return meta
    baseline_meta, candidate_meta = run_meta(baseline_dir), run_meta(candidate_dir)
    if baseline_meta.get("mode") != candidate_meta.get("mode"):
        raise ReplayError("MODE_MISMATCH", "基线与候选运行模式不一致")
    baseline_mode = baseline_meta.get("mode", "mock")
    baseline_rows, candidate_rows = _load_results(baseline_dir), _load_results(candidate_dir)
    from types import SimpleNamespace
    ids = [sample["sample_id"] for sample in manifest["samples"]]
    by_id = {sample["sample_id"]: sample for sample in manifest["samples"]}

    def index(rows, label, meta):
        found = {}
        policy = SimpleNamespace(version=meta.get("policy_version"), prompt=meta.get("policy_prompt"))
        for row in rows:
            key = row.get("sample_id")
            if key in found:
                raise ReplayError("DUPLICATE_RESULT", f"{label} 重复结果: {key}")
            if key not in by_id:
                raise ReplayError("RESULT_SET", f"{label} 含未知样本: {key}")
            validate_result_identity(manifest, by_id[key], row, policy=policy)
            found[key] = row
        if set(found) != set(ids):
            raise ReplayError("RESULT_SET", f"{label} 结果集与固定样本不一致")
        return found

    base = index(baseline_rows, "baseline", baseline_meta)
    cand = index(candidate_rows, "candidate", candidate_meta)
    supported = [s for s in manifest["samples"] if not s["category"].startswith("unsupported")]
    unsupported = [s for s in manifest["samples"] if s["category"].startswith("unsupported")]
    if not supported:
        raise ReplayError("NO_SUPPORTED", "没有受支持样本，无法比较")
    labels = [{"sample_id": s["sample_id"], "category": s["category"], "fields": s["gold_fields"]} for s in supported]
    def predictions(rows):
        return [{k: row[k] for k in ("sample_id", "status", "review_required", "fields") if k in row} | {
            "duration_ms": row.get("duration_ms"), "cost_cny": row.get("cost_cny")
        } for row in rows if row["sample_id"] in {s["sample_id"] for s in supported}]
    base_report = evaluate(labels, predictions(baseline_rows))
    cand_report = evaluate(labels, predictions(candidate_rows))
    evidence = {}
    for label, rows in (("baseline", baseline_rows), ("candidate", candidate_rows)):
        evidence[label] = {
            row["sample_id"]: validate_evidence(row.get("evidence", []), by_id[row["sample_id"]]["ocr_pages"], row.get("fields", {}))
            for row in rows
        }
    base_evidence = sum(1 for errors in evidence["baseline"].values() if not errors)
    cand_evidence = sum(1 for errors in evidence["candidate"].values() if not errors)
    candidate_evidence_errors = {k: v for k, v in evidence["candidate"].items() if v}
    fixes, regressions, fixed_fields, regressed_fields = [], [], [], []
    for sample in supported:
        sid, truth = sample["sample_id"], sample["gold_fields"]
        fields_base, fields_cand = base[sid].get("fields", {}), cand[sid].get("fields", {})
        b_ok = base[sid].get("status") == "succeeded" and all(f in fields_base and _same(f, truth.get(f), fields_base.get(f)) for f in truth)
        c_ok = cand[sid].get("status") == "succeeded" and all(f in fields_cand and _same(f, truth.get(f), fields_cand.get(f)) for f in truth)
        if not b_ok and c_ok:
            fixes.append(sid)
        if b_ok and not c_ok:
            regressions.append(sid)
        for field in truth:
            b_field = (
                base[sid].get("status") == "succeeded"
                and field in fields_base
                and _same(field, truth.get(field), fields_base.get(field))
            )
            c_field = (
                cand[sid].get("status") == "succeeded"
                and field in fields_cand
                and _same(field, truth.get(field), fields_cand.get(field))
            )
            if not b_field and c_field:
                fixed_fields.append({"sample_id": sid, "field": field})
            if b_field and not c_field:
                regressed_fields.append({"sample_id": sid, "field": field})
    critical_fields = {"gross_amount", "net_amount", "tax_amount", "invoice_number", "seller_tax_id", "buyer_tax_id"}
    critical = sorted({item["sample_id"] for item in regressed_fields if item["field"] in critical_fields})
    failed = sum(1 for row in baseline_rows + candidate_rows if row.get("status") in {"failed", "unknown"})
    split_stats = {}
    for split in ("dev", "validation"):
        members = [s for s in supported if s["split"] == split]
        if not members:
            continue
        split_stats[split] = {
            "samples": len(members),
            "baseline_exact": sum(1 for s in members if all(f in base[s["sample_id"]].get("fields", {}) and _same(f, s["gold_fields"].get(f), base[s["sample_id"]].get("fields", {}).get(f)) for f in s["gold_fields"]) and base[s["sample_id"]].get("status") == "succeeded"),
            "candidate_exact": sum(1 for s in members if all(f in cand[s["sample_id"]].get("fields", {}) and _same(f, s["gold_fields"].get(f), cand[s["sample_id"]].get("fields", {}).get(f)) for f in s["gold_fields"]) and cand[s["sample_id"]].get("status") == "succeeded"),
        }
    if failed or base_report["missing_results"] or cand_report["missing_results"]:
        status = "incomplete"
    elif critical or candidate_evidence_errors:
        status = "regression"
    elif cand_report["all_labeled_fields_exact"] > base_report["all_labeled_fields_exact"]:
        status = "promising_offline"
    else:
        status = "no_improvement"
    report = {
        "schema_version": "ocr-replay-report-v1",
        "dataset_id": manifest.get("dataset_id"),
        "dataset_version": manifest.get("dataset_version"),
        "data_nature": manifest.get("data_nature"),
        "manifest_sha256": manifest["manifest_sha256"],
        "mode": baseline_mode,
        "model": "none",
        "model_verified": False,
        "disclaimer": DISCLAIMER,
        "samples": len(ids),
        "supported": len(supported),
        "unsupported": len(unsupported),
        "unsupported_samples": [s["sample_id"] for s in unsupported],
        "failed_or_unknown": failed,
        "baseline": base_report,
        "candidate": cand_report,
        "splits": split_stats,
        "baseline_evidence_legal": base_evidence,
        "candidate_evidence_legal": cand_evidence,
        "candidate_evidence_errors": candidate_evidence_errors,
        "fixed_samples": fixes,
        "regressed_samples": regressions,
        "fixed_fields": fixed_fields,
        "regressed_fields": regressed_fields,
        "critical_regressions": critical,
        "status": status,
        "scope_note": "仅比较冻结 OCR 文本到结构化字段；不验证图片识别、真实模型效果、会计复核或生产接入。",
    }
    if out_json:
        _atomic_json(out_json, report)
    return report


def _same(field, expected, actual):
    if expected is None:
        return actual is None
    if not isinstance(actual, str):
        return False
    if field.endswith("_amount"):
        try:
            return Decimal(expected) == Decimal(actual)
        except (InvalidOperation, ValueError):
            return False
    return unicodedata.normalize("NFKC", expected).strip() == unicodedata.normalize("NFKC", actual).strip()


def render_markdown(report):
    lines = [
        "# H01 票据字段离线回放报告",
        "",
        f"- 数据集：{report['dataset_id']} / {report['dataset_version']}",
        f"- 运行模式：{report['mode']}；模型：{report['model']}（未验证）",
        f"- 固定样本：{report['samples']}；范围外：{report['unsupported']}",
        f"- 状态：**{report['status']}**",
        f"- 基线全字段一致：{report['baseline']['all_labeled_fields_exact']}",
        f"- 候选全字段一致：{report['candidate']['all_labeled_fields_exact']}",
        f"- 候选修复样本：{', '.join(report['fixed_samples']) or '无'}",
        f"- 候选退步样本：{', '.join(report['regressed_samples']) or '无'}",
        f"- 字段修复：{', '.join(item['sample_id'] + '/' + item['field'] for item in report['fixed_fields']) or '无'}",
        f"- 字段退步：{', '.join(item['sample_id'] + '/' + item['field'] for item in report['regressed_fields']) or '无'}",
        f"- 关键退步：{', '.join(report['critical_regressions']) or '无'}",
        f"- 基线与候选证据合法数：{report['baseline_evidence_legal']} / {report['candidate_evidence_legal']}",
        "",
        "## 字段准确率",
        "",
        "| 字段 | 基线 | 候选 | 标注数 |",
        "|---|---|---|---|",
    ]
    for field in FIELDS:
        b, c = report["baseline"]["fields"][field], report["candidate"]["fields"][field]
        lines.append(f"| {field} | {_pct(b['accuracy'])} | {_pct(c['accuracy'])} | {b['expected']} |")
    lines += ["", "## 限制与声明", "", report["scope_note"], "", DISCLAIMER, ""]
    return "\n".join(lines)


def _pct(value):
    return "未评测" if value is None else f"{value * 100:.1f}%"


def main(argv=None):
    parser = argparse.ArgumentParser(description="H01 冻结 OCR 文本字段离线回放")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    for name in ("run",):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True, type=Path)
        command.add_argument("--policy", choices=sorted(POLICIES), required=True)
        command.add_argument("--out", required=True, type=Path)
        command.add_argument("--resume", action="store_true")
        command.add_argument("--mode", choices=["mock", "archived_results"], default="mock")
        command.add_argument("--archived", type=Path)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--manifest", required=True, type=Path)
    compare_parser.add_argument("--baseline", required=True, type=Path)
    compare_parser.add_argument("--candidate", required=True, type=Path)
    compare_parser.add_argument("--out", type=Path)
    demo = sub.add_parser("demo")
    demo.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            manifest = load_manifest(args.manifest)
            print(f"数据包有效：{len(manifest['samples'])} 个样本，{len(manifest['firms'])} 个企业")
        elif args.command == "run":
            result = run_manifest(
                args.manifest, args.policy, args.out, args.resume, args.mode, args.archived
            )
            print(f"运行完成：{result['completed']} 个样本；{result['run_dir']}")
        elif args.command == "compare":
            report = compare(args.manifest, args.baseline, args.candidate, args.out)
            print(render_markdown(report))
        elif args.command == "demo":
            fixture = ROOT / "nexus_backend/tests/fixtures/ocr_replay/manifest.json"
            base = run_manifest(fixture, "baseline", args.out / "baseline")
            cand = run_manifest(fixture, "candidate", args.out / "candidate")
            report = compare(fixture, Path(base["run_dir"]), Path(cand["run_dir"]), args.out / "report.json")
            (args.out / "report.md").write_text(render_markdown(report), encoding="utf-8")
            print(f"演示完成：{args.out}")
    except ReplayError as exc:
        print(f"拒绝: {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
