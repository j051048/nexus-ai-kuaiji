"""Build the frozen IMP07 synthetic hard-invoice corpus.

This is a generator for a test fixture, not production data. Thirty synthetic
source documents each receive four deterministic variants (original, rotated,
multi-page, low-contrast rescan), giving 120 items across 30 source groups.
Variants share the source split; no real customer data is read.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "nexus_backend" / "tests" / "fixtures" / "imp07_eval" / "corpus.json"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def build() -> dict:
    sources = []
    items = []
    for n in range(1, 31):
        source_id = f"S{n:03d}"
        split = "dev" if n <= 20 else "holdout"
        firm_id = "firm-a" if n % 2 else "firm-b"
        company_id = "company-a" if n % 2 else "company-b"
        ledger_id = "ledger-a" if n % 2 else "ledger-b"
        book_id = "book-a" if n % 2 else "book-b"
        source_hash = sha(f"imp07-source:{source_id}")
        sources.append(
            {
                "source_id": source_id,
                "split": split,
                "firm_id": firm_id,
                "company_id": company_id,
                "ledger_id": ledger_id,
                "book_id": book_id,
                "period": "2026-10",
                "source_sha256": source_hash,
            }
        )
        invoice = f"{n:04d}"
        day = (n - 1) % 28 + 1
        issued = f"2026-10-{day:02d}"
        net = Decimal(100 + n)
        tax = (net * Decimal("0.06")).quantize(Decimal("0.01"))
        gross = net + tax
        fields = {
            "invoice_number": invoice,
            "issued_on": issued,
            "seller_name": f"合成供应商{n:02d}",
            "seller_tax_id": f"91110000IMP07{n:04d}",
            "buyer_name": f"虚构企业{n:02d}",
            "buyer_tax_id": f"91110000BUY07{n:04d}",
            "gross_amount": money(gross),
            "net_amount": money(net),
            "tax_amount": money(tax),
            "currency": "CNY",
        }
        body = "\n".join(
            [
                "增值税电子普通发票",
                f"发票号码：{invoice}",
                f"开票日期：{issued}",
                f"购买方名称：{fields['buyer_name']}",
                f"购买方纳税人识别号：{fields['buyer_tax_id']}",
                f"销售方名称：{fields['seller_name']}",
                f"销售方纳税人识别号：{fields['seller_tax_id']}",
                f"价税合计（小写）：{fields['gross_amount']}",
                f"金额：{fields['net_amount']}",
                f"税额：{fields['tax_amount']}",
                "币种：CNY",
            ]
        )
        if n % 7 == 0:
            body += "\n人工核对提示：请复核票面金额"
        sources[-1]["source_sha256"] = sha(body)
        variants = [
            ("V1", "standard", body),
            ("V2", "rotated", "扫描件旋转 3.2°\n" + body.replace("：", " ∶ ")),
            (
                "V3",
                "multi_page",
                "边缘裁切；第二页为明细。\n" + body + f"\n明细校验码：IMP07-{n:03d}",
            ),
            ("V4", "low_contrast", "低对比度重扫\n" + body.replace(" ", "  ")),
        ]
        for variant, category, text in variants:
            items.append(
                {
                    "item_id": f"{source_id}-{variant}",
                    "source_id": source_id,
                    "variant": variant,
                    "split": split,
                    "firm_id": firm_id,
                    "company_id": company_id,
                    "ledger_id": ledger_id,
                    "book_id": book_id,
                    "period": "2026-10",
                    "category": category,
                    "capability": "field_extraction",
                    "input_sha256": sha(text),
                    "ocr_text": text,
                    "gold_fields": fields,
                    "risk_required": n % 7 == 0,
                    "abstention_expected": False,
                }
            )
    return {
        "schema": "imp07-eval-v1",
        "dataset_id": "imp07-hard-invoices-v1",
        "dataset_version": 2,
        "data_nature": "synthetic_only",
        "generated_from": "scripts/imp07_build_corpus.py",
        "source_count": len(sources),
        "variant_count": len(items),
        "sources": sources,
        "items": items,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
