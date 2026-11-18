"""Stable fingerprint for approved report-line mappings."""

import hashlib
import json


def mapping_fingerprint(conn, firm_id: str) -> str:
    rows = conn.execute(
        "SELECT template_id,template_version,version,line_code,line_name,line_kind,"
        "account_prefix,sign FROM report_line_mappings "
        "WHERE firm_id=? AND active=TRUE "
        "ORDER BY template_id,template_version,version,line_code,account_prefix",
        (firm_id,),
    ).fetchall()
    payload = [dict(row) for row in rows]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
