"""Complete permission and human review evidence required before recovery release."""

TABLE_FIELDS = {
    "staff_access_assignments": "id firm_id user_id template company_id valid_from valid_to state reason created_by created_at",
    "delivery_contact_access": "book_id user_id firm_id company_id valid_to state created_by reason created_at",
    "review_authority_snapshots": "subject_kind subject_id reviewed_version book_id firm_id reviewer_user_id role_snapshot proof_kind recorded_at",
    "ledger_review_policies": "ledger_id version firm_id company_id mode reason confirmed_by created_at",
    "draft_preparations": "draft_id book_id version maker_user_id content_revision updated_at version_transaction",
    "draft_preparation_history": "draft_id content_revision book_id version maker_user_id changed_at transaction_id",
    "draft_review_receipts": "draft_id reviewed_version book_id maker_user_id reviewer_user_id policy_version mode independent reason reviewed_at content_revision content_fingerprint",
    "legacy_review_preparations": "id kind subject_id book_id source_version reviewed_version content_revision content_fingerprint policy_version mode maker_user_id reason created_at",
    "legacy_review_confirmations": "request_id book_id reviewer_user_id role_snapshot reason reviewed_at",
}
