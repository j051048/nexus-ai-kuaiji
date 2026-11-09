"""Finance resume inventory; requires PG 222 / SQLite 139 or later.

These are operational records, not original files. Back up them with the
database and bind their contents before releasing a restored environment.
"""

TABLE_FIELDS = {
    "periodic_assets": "id book_id kind name cost_cents residual_cents life_months start_period accumulated_cents expense_account_id expense_account_version accum_account_id accum_account_version status disposed_period created_by created_at",
    "periodic_runs": "id book_id period kind run_key asset_id input_fingerprint status draft_id voucher_id created_by created_at reconciliation_json",
    "periodic_drafts": "id run_id book_id period kind description status version created_by created_at approved_by approved_at posted_voucher_id",
    "periodic_draft_lines": "id draft_id line_no account_id account_version debit_cents credit_cents aux_json source_ref",
    "subledger_items": "id firm_id company_id ledger_id book_id period kind counterparty_id control_account_id control_account_version original_date due_date amount_cents source_kind source_id source_version status aux_json created_by created_at",
    "subledger_allocations": "id firm_id company_id book_id item_id voucher_id amount_cents allocated_on idempotency_key request_fingerprint created_by created_at",
    "subledger_allocation_requests": "id firm_id company_id book_id voucher_id idempotency_key request_fingerprint total_cents created_by created_at",
    "subledger_allocation_reversals": "id firm_id company_id book_id allocation_id reason idempotency_key request_fingerprint created_by created_at",
}
