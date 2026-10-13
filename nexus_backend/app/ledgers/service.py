"""Stable organization identity over immutable monthly physical book partitions.

No historical record is moved or rewritten. Permission to a parent never gives
access to an old month. Inherited rules contain formulas only, never samples.
"""
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint, now
from ..learning.models import RuleDefinition
from ..learning.service import LearningService


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class LedgersService:
    def __init__(self, db):
        self.db, self.access, self.learning = db, AccessControl(), LearningService(db)

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, value):
        conn.execute('INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)',
                     (actor.firm_id, book_id, actor.user_id, action, object_id, encode(value), now()))

    def ensure_book_in_transaction(self, conn, actor, book):
        book = self.access.book(conn, actor, book['id'], 'review')
        prior = conn.execute('SELECT ledger_id FROM ledger_periods WHERE book_id=?', (book['id'],)).fetchone()
        if prior:
            return prior['ledger_id']
        self.access.company(conn, actor, book['company_id'], manage=True)
        ledger_id, stamp = 'ledger_' + book['id'], now()
        conn.execute('INSERT INTO ledgers(id,firm_id,company_id,name,created_by,created_at) VALUES(?,?,?,?,?,?)',
                     (ledger_id, actor.firm_id, book['company_id'], book['name'], actor.user_id, stamp))
        conn.execute('INSERT INTO ledger_managers VALUES(?,?,?,?,?)', (ledger_id, actor.user_id, actor.user_id, '创建时明确授权管理', stamp))
        conn.execute('INSERT INTO ledger_periods VALUES(?,?,?,?,?,?,?)',
                     (ledger_id, book['id'], actor.firm_id, book['company_id'], book['period'], actor.user_id, stamp))
        return ledger_id

    def _ledger(self, conn, actor, book_id, manage=False):
        book = self.access.book(conn, actor, book_id, 'review' if manage else 'read')
        row = conn.execute('SELECT l.* FROM ledgers l JOIN ledger_periods p ON p.ledger_id=l.id AND p.firm_id=l.firm_id AND p.company_id=l.company_id WHERE p.book_id=? AND l.firm_id=?',
                           (book_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError('账套尚未完成长期账套映射，请管理员升级初始化', 409)
        if manage and not conn.execute('SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?', (row['id'], actor.user_id)).fetchone():
            raise AccountingError('需要明确的长期账套管理授权及当前来源期间复核权限', 403)
        return book, row

    def _visible_periods(self, conn, actor, ledger_id):
        result = []
        for row in conn.execute('SELECT p.*,b.name,s.status FROM ledger_periods p JOIN books b ON b.id=p.book_id JOIN periods s ON s.book_id=b.id AND s.period=b.period WHERE p.ledger_id=? ORDER BY p.period', (ledger_id,)):
            try:
                book = self.access.book(conn, actor, row['book_id'])
                result.append({'book_id': row['book_id'], 'period': row['period'], 'name': row['name'], 'status': row['status'], 'permissions': book['permissions']})
            except AccountingError:
                pass
        return result

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id)
            manager = bool(conn.execute('SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?', (ledger['id'], actor.user_id)).fetchone())
            members = []
            if manager and book['permissions']['review']:
                choices = conn.execute('SELECT * FROM accounting.ledger_member_choices(?)', (book_id,)) if self.db.is_postgres else conn.execute('SELECT a.user_id,a.role,u.name FROM book_access a JOIN users u ON u.id=a.user_id AND u.active=1 WHERE a.book_id=? ORDER BY u.name,a.user_id', (book_id,))
                for row in choices:
                    if self._member_current(conn, actor, book, row):
                        members.append(dict(row))
            published = [{'id': row['id'], 'version': row['current_version'], 'name': json.loads(row['definition_json'])['name']}
                         for row in conn.execute("SELECT r.id,r.current_version,v.definition_json FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=? AND r.status='published' ORDER BY r.id LIMIT 100", (book_id,))]
            candidates = [dict(row) | {'definition': json.loads(row['definition_json'])} for row in conn.execute('SELECT * FROM ledger_rule_candidates WHERE target_book_id=? ORDER BY created_at,id LIMIT 100', (book_id,))]
            entities = [dict(row) | {'active': bool(row['active'])} for row in conn.execute('SELECT id,kind,code,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY kind,code LIMIT 500', (actor.firm_id, book['company_id']))]
            aliases = [dict(row) for row in conn.execute('SELECT id,kind,entity_id,original,status,version,created_by,reviewed_by,reason,review_reason FROM company_master_aliases WHERE firm_id=? AND company_id=? ORDER BY created_at DESC,id LIMIT 500', (actor.firm_id, book['company_id']))]
            return {'id': ledger['id'], 'name': ledger['name'], 'company_id': book['company_id'], 'book_id': book_id, 'period': book['period'],
                    'can_manage': manager and book['permissions']['review'], 'can_grant_manager': book['permissions']['manage'] and book['permissions']['review'],
                    'can_review': book['permissions']['review'], 'periods': self._visible_periods(conn, actor, ledger['id']),
                    'members': members, 'published_rules': published, 'candidates': candidates, 'entities': entities, 'aliases': aliases}

    def grant_manager(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id)
            self.access.book(conn, actor, book_id, 'review')
            self.access.company(conn, actor, book['company_id'], manage=True)
            self.access.require_user(conn, request.user_id)
            firm = conn.execute('SELECT role FROM memberships WHERE firm_id=? AND user_id=?', (actor.firm_id, request.user_id)).fetchone()
            company = conn.execute('SELECT role FROM company_memberships WHERE company_id=? AND user_id=?', (book['company_id'], request.user_id)).fetchone()
            if not ((firm and firm['role'] in {'owner', 'admin', 'reviewer'}) or (not firm and company and company['role'] == 'finance')):
                raise AccountingError('长期账套管理人须为当前事务所管理员、复核人员或本企业财务', 403)
            conn.execute('INSERT OR IGNORE INTO ledger_managers VALUES(?,?,?,?,?)', (ledger['id'], request.user_id, actor.user_id, request.reason, now()))
            self._audit(conn, actor, book_id, 'ledger.manager.grant', ledger['id'], request.model_dump())
            return {'ledger_id': ledger['id'], 'user_id': request.user_id}

    def _member_current(self, conn, actor, book, row):
        if self.db.is_postgres:
            return bool(conn.execute('SELECT accounting.ledger_member_current(?,?,?)', (book['id'], row['user_id'], row['role'])).fetchone()[0])
        active = conn.execute('SELECT 1 FROM users WHERE id=? AND active=1', (row['user_id'],)).fetchone()
        firm = conn.execute('SELECT role FROM memberships WHERE firm_id=? AND user_id=?', (actor.firm_id, row['user_id'])).fetchone()
        company = conn.execute('SELECT role FROM company_memberships WHERE company_id=? AND user_id=?', (book['company_id'], row['user_id'])).fetchone()
        return bool(active and self.access.permissions(firm['role'] if firm else None, row['role'], company['role'] if company else None)['read'])

    def create_period(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id, True)
            source = self.access.book(conn, actor, request.source_book_id, 'review')
            mapping = conn.execute('SELECT ledger_id FROM ledger_periods WHERE book_id=?', (source['id'],)).fetchone()
            if not mapping or mapping['ledger_id'] != ledger['id']:
                raise AccountingError('继承来源必须是同一长期账套的明确期间', 409)
            digest = fingerprint(request.model_dump())
            prior = conn.execute('SELECT * FROM ledger_commands WHERE ledger_id=? AND request_key=?', (ledger['id'], request.idempotency_key)).fetchone()
            if prior:
                if prior['request_fingerprint'] != digest:
                    raise AccountingError('创建期间幂等键已用于不同参数', 409)
                self.access.book(conn, actor, prior['book_id'], 'read')
                return {'ledger_id': ledger['id'], 'book_id': prior['book_id'], 'replayed': True}
            if conn.execute('SELECT 1 FROM ledger_periods WHERE ledger_id=? AND period=?', (ledger['id'], request.period)).fetchone():
                raise AccountingError('长期账套已存在该期间，不能重复创建', 409)
            inherited = []
            native_choices = {row['user_id']: row for row in conn.execute('SELECT * FROM accounting.ledger_member_choices(?)', (source['id'],))} if self.db.is_postgres else None
            for user_id in request.inherit_user_ids:
                grant = native_choices.get(user_id) if native_choices is not None else conn.execute('SELECT user_id,role FROM book_access WHERE book_id=? AND user_id=?', (source['id'], user_id)).fetchone()
                if not grant or not self._member_current(conn, actor, source, grant):
                    raise AccountingError('选定人员已停用或不再具有来源账套权限，请刷新后重新选择', 403)
                inherited.append(dict(grant))
            rules = []
            for rule_id in request.inherit_rule_ids:
                row = self.learning._rule(conn, actor, source['id'], rule_id)
                if row['status'] != 'published':
                    raise AccountingError('只能继承当前已发布的企业规则定义', 409)
                rules.append(row)
            target, stamp = uuid4().hex, now()
            if self.db.is_postgres:
                conn.execute('SELECT accounting.ledger_create_period(?,?,?,?,?,?::jsonb)', (ledger['id'], target, source['id'], request.period, request.name, encode(request.inherit_user_ids)))
            else:
                conn.execute('INSERT INTO books(id,firm_id,company_id,name,period) VALUES(?,?,?,?,?)', (target, actor.firm_id, book['company_id'], request.name, request.period))
                conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')", (target, actor.firm_id, actor.user_id))
                for member in inherited:
                    if member['user_id'] != actor.user_id:
                        conn.execute('INSERT INTO book_access VALUES(?,?,?,?)', (target, actor.firm_id, member['user_id'], member['role']))
                conn.execute("INSERT INTO periods VALUES(?,?,'open')", (target, request.period))
                conn.execute('INSERT INTO ledger_periods VALUES(?,?,?,?,?,?,?)', (ledger['id'], target, actor.firm_id, book['company_id'], request.period, actor.user_id, stamp))
            conn.execute('INSERT INTO accounts(book_id,code,name,active,category,normal_balance,auxiliary_requirements,version) SELECT ?,code,name,active,category,normal_balance,auxiliary_requirements,1 FROM accounts WHERE book_id=?', (target, source['id']))
            for row in conn.execute('SELECT t.*,v.definition,v.definition_fingerprint FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=? ORDER BY t.id', (source['id'],)).fetchall():
                template_id = uuid4().hex
                conn.execute('INSERT INTO journal_templates VALUES(?,?,?,1,?,?)', (template_id, target, row['active'], actor.user_id, stamp))
                conn.execute('INSERT INTO journal_template_versions VALUES(?,?,1,?,?,?,?)', (template_id, target, row['definition'], row['definition_fingerprint'], actor.user_id, stamp))
            for rule in rules:
                conn.execute('INSERT INTO ledger_rule_candidates(id,ledger_id,source_book_id,target_book_id,source_rule_id,source_rule_version,definition_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                             (uuid4().hex, ledger['id'], source['id'], target, rule['id'], rule['current_version'], rule['definition_json'], actor.user_id, stamp))
            conn.execute('INSERT INTO ledger_commands VALUES(?,?,?,?,?,?)', (ledger['id'], request.idempotency_key, digest, target, actor.user_id, stamp))
            self._audit(conn, actor, target, 'ledger.period.create', target, {'ledger_id': ledger['id'], 'source_book_id': source['id'], 'inherit_user_ids': request.inherit_user_ids, 'inherit_rule_ids': request.inherit_rule_ids, 'catalog_copied': True, 'rules_published': False})
            return {'ledger_id': ledger['id'], 'book_id': target, 'replayed': False}

    def link_period(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id, True)
            source = self.access.book(conn, actor, request.source_book_id, 'review')
            self.access.company(conn, actor, source['company_id'], manage=True)
            if source['firm_id'] != actor.firm_id or source['company_id'] != book['company_id']:
                raise AccountingError('不得关联其他企业或事务所的历史账套', 404)
            mapping = conn.execute('SELECT * FROM ledger_periods WHERE book_id=?', (source['id'],)).fetchone()
            if not mapping or mapping['ledger_id'] != request.expected_ledger_id:
                raise AccountingError('历史账套映射已变化，请刷新后核对', 409)
            if conn.execute('SELECT 1 FROM ledger_periods WHERE ledger_id=? AND period=? AND book_id<>?', (ledger['id'], source['period'], source['id'])).fetchone():
                raise AccountingError('目标长期账套已存在同月期间，不能自动合并', 409)
            conn.execute('UPDATE ledger_periods SET ledger_id=?,linked_by=?,linked_at=? WHERE book_id=? AND ledger_id=?', (ledger['id'], actor.user_id, now(), source['id'], request.expected_ledger_id))
            self._audit(conn, actor, source['id'], 'ledger.period.link', source['id'], {'from': request.expected_ledger_id, 'to': ledger['id'], 'reason': request.reason, 'physical_book_unchanged': True})
            return {'ledger_id': ledger['id'], 'book_id': source['id']}

    def activate_candidate(self, actor, book_id, candidate_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.learning._book(conn, actor, book_id, True)
            row = conn.execute('SELECT * FROM ledger_rule_candidates WHERE id=? AND target_book_id=?', (candidate_id, book_id)).fetchone()
            if not row:
                raise AccountingError('继承候选不存在或无权访问', 404)
            if row['status'] != 'pending':
                raise AccountingError('此候选已形成待发布规则，请在审核样例与规则中继续', 409)
            definition = RuleDefinition.model_validate_json(row['definition_json'])
            rule = self.learning._save_in_transaction(conn, actor, book, definition, request.sample_ids, request.reason, None)
            stored = self.learning._rule(conn, actor, book_id, rule['id'], True)
            report = self.learning._evaluation(conn, actor, book_id, stored, request.negative_sample_ids)
            if not report['passed']:
                raise AccountingError('新期间正反例评估未通过，继承候选未启用', 409)
            evaluation_id = uuid4().hex
            conn.execute('INSERT INTO learning_rule_evaluations(id,rule_id,book_id,rule_version,negative_sample_ids_json,input_fingerprint,report_json,created_by,created_at) VALUES(?,?,?,1,?,?,?,?,?)',
                         (evaluation_id, rule['id'], book_id, encode(request.negative_sample_ids), report['input_fingerprint'], encode(report), actor.user_id, now()))
            conn.execute("UPDATE ledger_rule_candidates SET status='evaluated',target_rule_id=? WHERE id=? AND status='pending'", (rule['id'], candidate_id))
            self._audit(conn, actor, book_id, 'ledger.rule.inheritance.evaluate', candidate_id, {'rule_id': rule['id'], 'evaluation_id': evaluation_id, 'published': False})
            return {'rule': rule, 'evaluation_id': evaluation_id, 'report': report, 'published': False}

    def _history(self, conn, actor, book, object_id, action, value):
        conn.execute('INSERT INTO company_master_history VALUES(?,?,?,?,?,?,?,?,?)', (uuid4().hex, actor.firm_id, book['company_id'], book['id'], object_id, action, encode(value), actor.user_id, now()))
        self._audit(conn, actor, book['id'], action, object_id, value)

    def create_entity(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            if conn.execute('SELECT 1 FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind=? AND code=?', (actor.firm_id, book['company_id'], request.kind, request.code)).fetchone():
                raise AccountingError('本企业该类型主数据编码已存在', 409)
            entity_id = uuid4().hex
            conn.execute('INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,recorded_book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (entity_id, actor.firm_id, book['company_id'], request.kind, request.code, request.name, book_id, actor.user_id, now()))
            self._history(conn, actor, book, entity_id, 'master.entity.create', request.model_dump())
            return {'id': entity_id, 'version': 1, **request.model_dump()}

    def update_entity(self, actor, book_id, entity_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            row = conn.execute('SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=?', (entity_id, actor.firm_id, book['company_id'])).fetchone()
            if not row:
                raise AccountingError('主数据不存在或无权访问', 404)
            self.access.book(conn, actor, row['recorded_book_id'], 'review')
            if row['version'] != request.expected_version:
                raise AccountingError('主数据版本已变化', 409)
            conn.execute('UPDATE company_master_entities SET name=?,active=?,version=version+1 WHERE id=? AND version=?', (request.name, int(request.active), entity_id, request.expected_version))
            self._history(conn, actor, book, entity_id, 'master.entity.update', {'before': dict(row), 'after': request.model_dump()})
            return {'id': entity_id, 'version': row['version'] + 1}

    def create_alias(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            entity = conn.execute('SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND active=1', (request.entity_id, actor.firm_id, book['company_id'])).fetchone()
            if not entity:
                raise AccountingError('主数据不存在或已停用', 404)
            if conn.execute("SELECT 1 FROM company_master_aliases WHERE firm_id=? AND company_id=? AND kind=? AND original=? AND status IN ('pending','approved')", (actor.firm_id, book['company_id'], entity['kind'], request.original)).fetchone():
                raise AccountingError('该原始名称已有待审或已批准映射，请先处置原版本', 409)
            alias_id = uuid4().hex
            conn.execute('INSERT INTO company_master_aliases(id,firm_id,company_id,kind,entity_id,original,reason,recorded_book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (alias_id, actor.firm_id, book['company_id'], entity['kind'], entity['id'], request.original, request.reason, book_id, actor.user_id, now()))
            self._history(conn, actor, book, alias_id, 'master.alias.request', request.model_dump())
            return {'id': alias_id, 'version': 1, 'status': 'pending'}

    def decide_alias(self, actor, book_id, alias_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            row = conn.execute('SELECT * FROM company_master_aliases WHERE id=? AND firm_id=? AND company_id=?', (alias_id, actor.firm_id, book['company_id'])).fetchone()
            if not row:
                raise AccountingError('别名映射不存在或无权访问', 404)
            self.access.book(conn, actor, row['recorded_book_id'], 'review')
            if row['created_by'] == actor.user_id:
                raise AccountingError('别名映射须由另一名会计独立复核', 403)
            if row['version'] != request.expected_version or (request.decision in {'approve', 'reject'} and row['status'] != 'pending') or (request.decision == 'retire' and row['status'] != 'approved'):
                raise AccountingError('别名版本或审核状态已变化', 409)
            status = {'approve': 'approved', 'reject': 'rejected', 'retire': 'retired'}[request.decision]
            if status == 'approved' and conn.execute("SELECT 1 FROM company_master_aliases WHERE firm_id=? AND company_id=? AND kind=? AND original=? AND status='approved'", (actor.firm_id, book['company_id'], row['kind'], row['original'])).fetchone():
                raise AccountingError('同一原始名称已有生效映射，不能覆盖', 409)
            conn.execute('UPDATE company_master_aliases SET status=?,version=version+1,reviewed_by=?,review_reason=?,reviewed_at=? WHERE id=? AND version=?', (status, actor.user_id, request.reason, now(), alias_id, request.expected_version))
            self._history(conn, actor, book, alias_id, 'master.alias.' + request.decision, {'before': dict(row), 'decision': request.model_dump()})
            return {'id': alias_id, 'version': row['version'] + 1, 'status': status}

    def resolve_in_transaction(self, conn, actor, company_id, kind, original, book_id=None):
        if kind not in {'counterparty', 'project', 'bank_account'} or not isinstance(original, str) or not 1 <= len(original) <= 200:
            return None
        self.access.company(conn, actor, company_id)
        if book_id:
            book = self.access.book(conn, actor, book_id)
            if book['company_id'] != company_id:
                raise AccountingError('主数据与账套企业不一致', 404)
        elif kind == 'bank_account':
            raise AccountingError('银行账户映射需要明确账套授权', 403)
        if self.db.is_postgres:
            row = conn.execute('SELECT * FROM accounting.master_resolve(?,?,?)', (company_id, kind, original)).fetchone()
        else:
            row = conn.execute("SELECT e.id AS entity_id,e.name AS canonical_value,e.version AS entity_version,a.version AS alias_version FROM company_master_aliases a JOIN company_master_entities e ON e.id=a.entity_id WHERE a.firm_id=? AND a.company_id=? AND a.kind=? AND a.original=? AND a.status='approved' AND e.active=1", (actor.firm_id, company_id, kind, original)).fetchone()
            if not row:
                rows = conn.execute('SELECT id AS entity_id,name AS canonical_value,version AS entity_version,0 AS alias_version FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind=? AND name=? AND active=1', (actor.firm_id, company_id, kind, original)).fetchall()
                row = rows[0] if len(rows) == 1 else None
        return None if not row else dict(row) | {'original': original, 'version': f"{row['entity_version']}:{row['alias_version']}"}

    def policy_fingerprint_in_transaction(self, conn, actor, book_id):
        book = self.access.book(conn, actor, book_id)
        entities = [dict(row) for row in conn.execute('SELECT id,kind,code,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY id', (actor.firm_id, book['company_id']))]
        aliases = [dict(row) for row in conn.execute("SELECT id,entity_id,kind,original,status,version FROM company_master_aliases WHERE firm_id=? AND company_id=? ORDER BY id", (actor.firm_id, book['company_id']))]
        return fingerprint({'entities': entities, 'aliases': aliases})

    def context_in_transaction(self, conn, actor, company_id, payload, book_id=None):
        result = {}
        for kind in ('counterparty', 'project'):
            value = self.resolve_in_transaction(conn, actor, company_id, kind, payload.get(kind, ''), book_id)
            if value is not None:
                result[kind] = value
        return result
