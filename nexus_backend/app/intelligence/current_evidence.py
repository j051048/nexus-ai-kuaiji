"""Only explicitly accepted supersessions change the current OCR evidence set."""


def superseded_ids(db, conn, event):
    if db.is_postgres:
        # Current event visibility is checked inside the capability. It returns
        # only original attachment IDs, never case notes or internal case IDs.
        return {
            row[0]
            for row in conn.execute(
                "SELECT * FROM accounting.event_superseded_attachments(?)", (event["id"],)
            )
        }
    return {
        row["old_attachment_id"]
        for row in conn.execute(
            "SELECT old_attachment_id FROM case_supersessions WHERE event_id=? AND firm_id=?",
            (event["id"], event["firm_id"]),
        )
    }


def current_attachments(db, conn, event, attachments):
    excluded = superseded_ids(db, conn, event)
    return [item for item in attachments if item["id"] not in excluded]
