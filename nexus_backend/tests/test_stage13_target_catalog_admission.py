"""Current catalog limitations block admission before an irreversible cutover."""
from test_stage13_migration_api import (client, confirmation, detail,
                                        prepare_http, ready)


def test_historical_account_name_outside_current_catalog_is_reported_before_acceptance(client):
    c = prepare_http(client)
    for record in c.package['records']:
        if record['kind'] == 'account' and record['payload']['code'] == '6602.02':
            # Valid historical name, intentionally outside current catalog's
            # 120-character limit. Preserve the original instead of truncating.
            record['payload']['name'] = '合成历史科目名称' * 16
    _, report = ready(c)
    problems = report['report']['issues']
    assert any(item['code'] == 'ACCOUNT_MAPPING_UNSUPPORTED' and item['severity'] == 'blocking' for item in problems)
    assert client.post(c.path + '/accept', headers=c.finance, json=confirmation(c, report)).status_code == 409
    assert detail(c)['project']['status'] == 'needs_attention'
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM migration_cutovers WHERE project_id=?', (c.project,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM accounts WHERE book_id=? AND code='6602.HIST'", (c.book['id'],)).fetchone()[0] == 0
