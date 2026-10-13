"""Latest, bounded replay summaries; no cross-book or inferred human effort."""

import json

MODES = ("rules", "templates", "stored_model", "stored_ocr")
STATUSES = ("passed", "different", "uncovered", "stale")


def latest_runs_sql(dialect):
    mode = (
        "r.settings_json::jsonb->>'mode'"
        if dialect == "postgres"
        else "json_extract(r.settings_json,'$.mode')"
    )
    return f"""WITH authorized_runs AS (
      SELECT r.*,ROW_NUMBER() OVER(PARTITION BY {mode} ORDER BY r.created_at DESC,r.id DESC) AS mode_rank
      FROM quality_runs r WHERE r.book_id=? AND r.firm_id=?
       AND {mode} IN ('rules','templates','stored_model','stored_ocr')
    ) SELECT * FROM authorized_runs WHERE mode_rank=1 ORDER BY created_at DESC,id DESC LIMIT 4"""


def summarize_run(row, context_stale):
    settings, report = json.loads(row["settings_json"]), json.loads(row["report_json"])
    groups = {}
    for item in report.get("items", [])[:100]:
        business_type = item["business_type"]
        counts = groups.setdefault(business_type, {status: 0 for status in STATUSES})
        if item["status"] in counts:
            counts[item["status"]] += 1
    result = []
    for business_type, counts in sorted(groups.items()):
        measured = counts["passed"] + counts["different"]
        result.append(
            {
                "business_type": business_type,
                "mode": settings["mode"],
                "run_id": row["id"],
                "created_at": row["created_at"],
                "context_stale": context_stale,
                "sample_count": sum(counts.values()),
                "counts": counts,
                "measured_sample_count": None if context_stale else measured,
                "measured_match_rate": counts["passed"] / measured
                if not context_stale and measured
                else None,
                "human_minutes": None,
                "supplement_wait_seconds": None,
                "delivery_differences": None,
            }
        )
    return result


def report_groups(conn, actor, book_id, dialect, current_run):
    """Caller must check current book review permission before issuing this SQL."""
    result = []
    for row in conn.execute(latest_runs_sql(dialect), (book_id, actor.firm_id)):
        current = current_run(row)
        result.extend(summarize_run(row, current["stale"]))
    return result
