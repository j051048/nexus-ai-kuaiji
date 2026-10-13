import { describe, expect, it } from 'vitest';
import { taskDestination, taskLabels, savedWorkspaceViews, viewStorageKey, formatTaskAmount, taskStateLabel, type WorkspaceTask } from './workspace-types';

const task = (overrides: Partial<WorkspaceTask> = {}): WorkspaceTask => ({ key: 'migration:exact-project', kind: 'migration', ref: 'exact-project', company_id: 'authorized-company', company_name: '合成授权企业', book_id: 'exact-book', event_id: null, draft_id: null, batch_id: null, title: '旧账差异待处理', state: 'needs_attention', priority: 80, due_on: null, updated_at: '2026-10-13T04:00:00Z', bucket: 'action', action_label: '核对迁移差异', responsible: '授权迁移会计', overdue: false, ...overrides });

describe('统一待办精确目标', () => {
  it('旧账迁移筛选有中文标签，目标保留当前项目与账套而不借用业务或凭证 ID', () => {
    expect(taskLabels.migration).toBe('旧账迁移');
    expect(taskDestination(task({ event_id: 'unrelated-event', draft_id: 'unrelated-draft', batch_id: 'unrelated-batch' }))).toEqual({ type: 'migration', bookId: 'exact-book', id: 'exact-project' });
  });
  it('缺少账套引用时保持缺失事实，不能回退其他企业或以公司ID代替账套', () => {
    expect(taskDestination(task({ book_id: null }))).toEqual({ type: 'migration', bookId: null, id: 'exact-project' });
  });
  it('后台等待与人工处理打开同一明确项目，不因状态或列表排序改目标', () => {
    const waiting = task({ state: 'parsing', bucket: 'waiting', key: 'migration:other-project', ref: 'other-project', book_id: 'other-book' });
    expect(taskDestination(waiting)).toEqual({ type: 'migration', bookId: 'other-book', id: 'other-project' });
    expect(taskDestination({ ...waiting, state: 'ready', bucket: 'action' })).toEqual(taskDestination(waiting));
  });
  it('新增迁移类型保留既有复核和导出目标语义', () => {
    expect(taskDestination(task({ kind: 'review', ref: 'source-ref', draft_id: 'actual-draft' }))).toEqual({ type: 'review', bookId: 'exact-book', id: 'actual-draft' });
    expect(taskDestination(task({ kind: 'export', ref: 'actual-export' }))).toEqual({ type: 'export', bookId: 'exact-book', id: 'actual-export' });
  });
});

describe('工作视图只保存过滤条件', () => {
  const view = { bucket: 'action', kind: 'review', company_id: 'exact-company', sort: 'due', density: 'table' };
  it('剔除名称、摘要、搜索词和未许可属性，保留准确企业 ID', () => {
    expect(savedWorkspaceViews(JSON.stringify([{ ...view, company_name: '私有客户', title: '私有事项', q: '私有搜索', admin: true }]))).toEqual([view]);
  });
  it('损坏或越界的筛选不会变成全部企业视图', () => {
    expect(savedWorkspaceViews('not json')).toEqual([]);
    expect(savedWorkspaceViews(JSON.stringify([{ ...view, company_id: null }, { ...view, kind: '__proto__' }, { ...view, sort: 'injected' }]))).toEqual([]);
  });
  it('最多保存三个视图并按稳定用户和事务所隔离', () => {
    expect(savedWorkspaceViews(JSON.stringify([view, view, view, view]))).toHaveLength(3);
    const identity = { mode: 'local' as const, user_id: 'user', firm_id: 'firmA' };
    expect(viewStorageKey(identity)).not.toBe(viewStorageKey({ ...identity, firm_id: 'firmB' }));
    expect(viewStorageKey(identity)).not.toBe(viewStorageKey({ ...identity, user_id: 'other-user' }));
    expect(viewStorageKey(identity)).not.toBe(viewStorageKey({ ...identity, mode: 'supabase' }));
  });
  it('同用户同事务所重新登录或刷新后可读筛选，随机scope和token不进入存储键', () => {
    const first = { mode: 'local' as const, user_id: 'user', firm_id: 'firm', scope_key: 'first-login-scope', token: 'first-private-token' };
    const second = { ...first, scope_key: 'refreshed-login-scope', token: 'second-private-token' };
    const storage = new Map([[viewStorageKey(first), JSON.stringify([view])]]);
    expect(savedWorkspaceViews(storage.get(viewStorageKey(second)) ?? null)).toEqual([view]);
    expect(viewStorageKey(first)).not.toContain('first-login-scope');
    expect(viewStorageKey(first)).not.toContain('first-private-token');
  });
  it('保存企业的访问权失效时仍保留准确过滤ID，不扩展为全部或另一企业', () => {
    const restored = savedWorkspaceViews(JSON.stringify([{ ...view, company_id: 'company-with-revoked-access' }]))[0];
    expect(restored.company_id).toBe('company-with-revoked-access');
    expect(restored.company_id).not.toBe('');
  });
  it('金额使用精确字符串，缺失金额保持未知', () => {
    expect(formatTaskAmount('9007199254740993.07')).toBe('¥9,007,199,254,740,993.07');
    expect(formatTaskAmount('-0.30')).toBe('¥-0.30');
    expect(formatTaskAmount('0.3')).toBe('¥0.30');
    expect(formatTaskAmount(null)).toBe('—');
    expect(formatTaskAmount('0.300')).toBe('—');
  });
  it('状态来自任务事实，未知状态不推断成功', () => {
    expect(taskStateLabel({ kind: 'review', state: 'draft' })).toBe('待会计复核');
    expect(taskStateLabel({ kind: 'ocr', state: 'running' })).toBe('后台处理中');
    expect(taskStateLabel({ kind: 'ocr', state: 'provider_new_state' })).toBe('状态待核实');
  });
});
