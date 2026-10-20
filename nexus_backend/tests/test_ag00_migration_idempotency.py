from scripts.check_migration_idempotency import violations


def test_new_function_must_be_or_replace():
    assert violations("136_x.sql", "CREATE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;")
    assert not violations(
        "136_x.sql", "CREATE OR REPLACE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;"
    )


def test_trigger_must_be_dropped_first():
    assert violations(
        "136_x.sql",
        "CREATE TRIGGER t BEFORE UPDATE ON accounting.x FOR EACH ROW EXECUTE FUNCTION accounting.f();",
    )
    assert not violations(
        "136_x.sql",
        "DROP TRIGGER IF EXISTS t ON accounting.x; CREATE TRIGGER t BEFORE UPDATE ON accounting.x FOR EACH ROW EXECUTE FUNCTION accounting.f();",
    )


def test_functiondef_replace_is_forbidden():
    assert violations(
        "136_x.sql",
        "SELECT pg_get_functiondef('accounting.f'::regproc) AS body; SELECT replace(body, 'a', 'b');",
    )
