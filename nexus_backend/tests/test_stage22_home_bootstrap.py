"""Home bootstrap preserves current grants and never performs accounting actions."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import FirmCreate, FirmMemberGrant
from app.accounting.telemetry import tag_authorized_firm, transaction_actor
from app.workspace.bootstrap import home_bootstrap
from test_business import business, create  # noqa: F401
from test_stage12_workspace import workspace
from test_stage8_cases_api import client  # noqa: F401
from test_business_api import auth


def identity(c, actor, **changes):
    with c.db.transaction(actor=actor) as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (actor.user_id,)).fetchone()
    return SimpleNamespace(user_id=actor.user_id, display_name=row['name'], email=row['email'], **changes)


@pytest.mark.parametrize('persona', ['employee', 'department', 'finance', 'accountant'])
def test_http_bootstrap_matches_three_private_endpoints(client, persona):
    headers = auth(client, persona)
    expected = {key: client.get(path, headers=headers).json() for key, path in (
        ('me', '/api/me'), ('profile', '/api/workspace/profile'), ('tasks', '/api/workspace/tasks'))}
    response = client.get('/api/workspace/bootstrap', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert 'no-store' in response.headers['cache-control']


def test_http_bootstrap_requires_auth_and_rejects_ungranted_firm(client):
    assert client.get('/api/workspace/bootstrap').status_code == 401
    headers = {**auth(client, 'employee'), 'X-Firm-Id': 'ungranted-firm'}
    response = client.get('/api/workspace/bootstrap', headers=headers)
    assert response.status_code == 403
    assert 'tasks' not in response.json()


def test_unchanged_verified_identity_uses_one_read_transaction(business, monkeypatch):
    c, service = business, workspace(business)
    current = c.actors['employee']
    verified = identity(c, current)
    expected = {'me': c.directory.me(current.user_id), 'profile': service.profile(current), 'tasks': service.list(current)}
    transactions, original = [], c.db.transaction

    @contextmanager
    def tracked(*args, **kwargs):
        transactions.append(kwargs)
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(c.db, 'transaction', tracked)
    assert home_bootstrap(c.directory, service, verified) == expected
    assert len(transactions) == 1
    assert not transactions[0].get('write') and not transactions[0].get('system')
    assert transactions[0]['actor'] == Actor(current.user_id, '')


def test_new_user_has_no_firm_and_changed_identity_syncs_without_new_grants(business):
    c, service = business, workspace(business)
    newcomer = SimpleNamespace(user_id='new-home-user', display_name='新账号', email='new-home@example.test')
    value = home_bootstrap(c.directory, service, newcomer)
    assert value['me']['firms'] == [] and value['me']['current_firm_id'] is None
    assert value['profile'] is None and value['tasks'] is None
    newcomer.display_name = '新的显示名称'
    assert home_bootstrap(c.directory, service, newcomer)['me']['user']['name'] == newcomer.display_name
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM memberships WHERE user_id=?', (newcomer.user_id,)).fetchone()[0] == 0


def test_disabled_identity_is_not_registered_or_reactivated(business, monkeypatch):
    c, service = business, workspace(business)
    verified = identity(c, c.actors['employee'])
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute('UPDATE users SET active=0 WHERE id=?', (verified.user_id,))
    calls = []
    monkeypatch.setattr(c.directory, 'register_identity', lambda _: calls.append(True))
    with pytest.raises(AccountingError, match='停用'):
        home_bootstrap(c.directory, service, verified)
    assert calls == []


def test_disable_during_identity_change_remains_blocked(business, monkeypatch):
    c, service = business, workspace(business)
    verified = identity(c, c.actors['employee'])
    verified.display_name = '已修改的姓名'
    original = c.directory.register_identity

    def disable_before_register(value):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('UPDATE users SET active=0 WHERE id=?', (value.user_id,))
        original(value)

    monkeypatch.setattr(c.directory, 'register_identity', disable_before_register)
    with pytest.raises(AccountingError, match='停用'):
        home_bootstrap(c.directory, service, verified)


def test_explicit_firm_selection_and_revocation_never_fall_back(business):
    c, service = business, workspace(business)
    verified = identity(c, c.manager)
    second_owner = SimpleNamespace(user_id='second-home-owner', display_name='另一负责人', email='second-home@example.test')
    c.directory.register_identity(second_owner)
    other = c.directory.create_firm(second_owner.user_id, FirmCreate(name='另一事务所'))
    c.directory.grant_firm_member(Actor(second_owner.user_id, other['id']), FirmMemberGrant(email=verified.email, role='viewer'))
    value = home_bootstrap(c.directory, service, verified, other['id'])
    assert value['me']['current_firm_id'] == other['id']
    assert value['profile']['companies'] == [] and value['tasks']['tasks'] == []
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute('DELETE FROM memberships WHERE firm_id=? AND user_id=?', (other['id'], verified.user_id))
    with pytest.raises(AccountingError, match='授权'):
        home_bootstrap(c.directory, service, verified, other['id'])
    assert home_bootstrap(c.directory, service, verified)['me']['current_firm_id'] == c.manager.firm_id


def test_home_read_never_changes_business_review_or_exports(business):
    c, service = business, workspace(business)
    owned, hidden = create(c), create(c, 'peer')

    def snapshot():
        with c.db.transaction(actor=c.manager) as conn:
            return [conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
                    for table in ('exports', 'audit_events', 'business_actions', 'drafts')]

    before = snapshot()
    value = home_bootstrap(c.directory, service, identity(c, c.actors['employee']))
    assert owned['id'] in str(value['tasks']['tasks'])
    assert hidden['id'] not in str(value)
    assert snapshot() == before


def test_home_metrics_use_authorized_firm_and_reset_after_failure(business, monkeypatch):
    c, service = business, workspace(business)
    verified = identity(c, c.actors['employee'])
    before = c.db.metrics.snapshot(firm_id=c.manager.firm_id)['count']
    home_bootstrap(c.directory, service, verified)
    assert c.db.metrics.snapshot(firm_id=c.manager.firm_id)['count'] == before + 1
    assert transaction_actor.get() is None

    def fail(_conn, _actor):
        raise RuntimeError('合成读取故障')

    monkeypatch.setattr(service, 'profile_in_connection', fail)
    with pytest.raises(RuntimeError, match='合成'):
        home_bootstrap(c.directory, service, verified)
    snapshot = c.db.metrics.snapshot(firm_id=c.manager.firm_id)
    assert snapshot['count'] == before + 2 and snapshot['rollbacks'] == 1
    assert transaction_actor.get() is None
    with pytest.raises(AccountingError):
        home_bootstrap(c.directory, service, verified, 'not-authorized')
    assert c.db.metrics.snapshot(firm_id=c.manager.firm_id)['count'] == before + 2
    assert transaction_actor.get() is None


def test_metric_tag_cannot_change_transaction_user_or_existing_firm(business):
    c = business
    with c.db.transaction(actor=c.manager):
        for actor in (Actor('another-user', c.manager.firm_id), Actor(c.manager.user_id, 'another-firm')):
            with pytest.raises(ValueError, match='事务'):
                tag_authorized_firm(actor)
            assert transaction_actor.get() == c.manager
    assert transaction_actor.get() is None
