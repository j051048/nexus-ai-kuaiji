"""Fresh, request-scoped home data without repeated identity/actor lookups."""

from app.accounting.domain import Actor, AccountingError
from app.accounting.telemetry import tag_authorized_firm


def home_bootstrap(directory, workspace, identity, firm_id=None, mode="supabase"):
    # Both runtime roles and all settings remain transaction-local. No shared
    # permission or home-response cache exists; the next request sees revocation.
    for attempt in range(2):
        with workspace.db.transaction(actor=Actor(identity.user_id, "")) as conn:
            me = directory.me_in_connection(
                conn,
                identity.user_id,
                firm_id,
                mode,
                identity=identity if mode == "supabase" else None,
            )
            if me is not None:
                selected = me["current_firm_id"]
                if selected is None:
                    return {"me": me, "profile": None, "tasks": None}
                actor = Actor(identity.user_id, selected)
                if workspace.db.is_postgres:
                    # Complete the scope switch before issuing any domain SQL.
                    # Never rely on evaluation order in one SELECT statement.
                    conn.execute("SELECT set_config('app.firm_id',?,true)", (selected,))
                tag_authorized_firm(actor)
                return {
                    "me": me,
                    "profile": workspace.profile_in_connection(conn, actor),
                    "tasks": workspace.list_in_connection(conn, actor),
                }
        if attempt == 0:
            # Existing register_identity rechecks activation under its write
            # lock. Never attempt writes inside the home read transaction.
            directory.register_identity(identity)
    raise AccountingError("账号资料发生变化，请重新登录后重试", 409)
