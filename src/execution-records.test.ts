import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setApiScope } from './api';
import { ExecutionRecordPanel } from './ExecutionRecordPanel';
import {
  executionHandoffIntent,
  executionInitialState,
  executionScopeKey,
  executionStateReducer,
  type ExecutionRecord,
} from './execution-types';
import type { Session } from './types';

const session: Session = {
  token: 'synthetic-local-token',
  mode: 'local',
  firm_id: 'firm',
  user_id: 'accountant',
  firm_name: '合成事务所',
  user_name: '合成会计',
  role: 'reviewer',
  scope_key: 'synthetic-scope',
};
function record(): ExecutionRecord {
  return {
    schema: 'ak04-execution-v1',
    execution_id: 'ocr:run',
    kind: 'ocr',
    status: 'failed',
    accounting_approved: false,
    validation: { code: 'not_checked', status: 'not_checked', finding_codes: [] },
    failure: {
      category: 'provider_or_transport',
      code: 'timeout',
      message: '请核对处理状态',
      outcome_unknown: true,
      automatic_retry: false,
    },
    handoff: {
      required: true,
      reason_code: 'timeout',
      action: 'inspect_execution',
      automatic_retry: false,
      supported: true,
      can_create: true,
      fingerprint: 'a'.repeat(64),
      message: '可创建人工核对任务',
    },
    timeline: [{ stage: 'created', at: '2026-10-21T00:00:00Z', source: 'execution' }],
  };
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setApiScope(null);
});

describe('AK04 execution disclosure and handoff', () => {
  it('renders a collapsed explicit entry without issuing a request', () => {
    const request = vi.spyOn(globalThis, 'fetch');
    const html = renderToStaticMarkup(
      createElement(ExecutionRecordPanel, {
        session,
        companyId: 'company',
        kind: 'ocr',
        runId: 'run',
      }),
    );
    expect(html).toContain('查看执行记录');
    expect(html).not.toContain('人工处理依据');
    expect(request).not.toHaveBeenCalled();
  });

  it('changes identity for every user, firm, role, company and execution boundary', () => {
    const original = executionScopeKey(session, 'company', 'ocr', 'run');
    for (const change of [
      { user_id: 'other' },
      { firm_id: 'other' },
      { role: 'admin' as const },
      { scope_key: 'other' },
    ]) {
      expect(executionScopeKey({ ...session, ...change }, 'company', 'ocr', 'run')).not.toBe(
        original,
      );
    }
    expect(executionScopeKey(session, 'other', 'ocr', 'run')).not.toBe(original);
    expect(executionScopeKey(session, 'company', 'journal', 'run')).not.toBe(original);
    expect(executionScopeKey(session, 'company', 'ocr', 'other')).not.toBe(original);
  });

  it('ignores an old read even when a transport ignores cancellation', () => {
    let state = executionInitialState('target', 'ocr:run');
    state = executionStateReducer(state, {
      type: 'begin',
      target: 'target',
      epoch: 2,
      operation: 'read',
    });
    expect(
      executionStateReducer(state, {
        type: 'loaded',
        target: 'target',
        epoch: 1,
        record: record(),
      }),
    ).toBe(state);
    expect(
      executionStateReducer(state, { type: 'loaded', target: 'other', epoch: 2, record: record() }),
    ).toBe(state);
    const wrong = { ...record(), execution_id: 'ocr:other' };
    expect(
      executionStateReducer(state, { type: 'loaded', target: 'target', epoch: 2, record: wrong })
        .record,
    ).toBeNull();
    state = executionStateReducer(state, {
      type: 'loaded',
      target: 'target',
      epoch: 2,
      record: record(),
    });
    expect(state.record?.execution_id).toBe('ocr:run');
  });

  it.each([409, 503])(
    'invalidates the old fingerprint on %s while preserving the local reason',
    (status) => {
      let state = executionInitialState('target', 'ocr:run');
      state = executionStateReducer(state, { type: 'reason', value: '请核对原件与调用结果' });
      state = executionStateReducer(state, {
        type: 'begin',
        target: 'target',
        epoch: 1,
        operation: 'read',
      });
      state = executionStateReducer(state, {
        type: 'loaded',
        target: 'target',
        epoch: 1,
        record: record(),
      });
      state = executionStateReducer(state, {
        type: 'begin',
        target: 'target',
        epoch: 2,
        operation: 'submit',
      });
      state = executionStateReducer(state, { type: 'failed', target: 'target', epoch: 2, status });
      expect(state.reason).toBe('请核对原件与调用结果');
      expect(state.record).toBeNull();
      expect(state.error).toContain('重新查看');
      expect(executionHandoffIntent(state.record, state.reason, null, () => 'new-key')).toBeNull();
    },
  );

  it('uses one key for a retried body and a new key when the confirmed body changes', () => {
    const first = executionHandoffIntent(record(), ' 核对原始票据 ', null, () => 'first-key');
    expect(first?.body).toEqual({
      expected_fingerprint: 'a'.repeat(64),
      reason: '核对原始票据',
      idempotency_key: 'first-key',
    });
    const duplicate = executionHandoffIntent(
      record(),
      '核对原始票据',
      first,
      () => 'must-not-change',
    );
    expect(duplicate).toBe(first);
    const changed = executionHandoffIntent(
      record(),
      '核对费用与原始票据',
      first,
      () => 'changed-key',
    );
    expect(changed?.body.idempotency_key).toBe('changed-key');
    const refreshed = record();
    refreshed.handoff.fingerprint = 'b'.repeat(64);
    expect(
      executionHandoffIntent(refreshed, '核对原始票据', first, () => 'fresh-key')?.body
        .idempotency_key,
    ).toBe('fresh-key');
  });

  it('cannot manufacture case permissions or a valid fingerprint', () => {
    for (const change of [
      { can_create: false },
      { supported: false },
      { fingerprint: null },
      { fingerprint: 'provider-private-text' },
    ]) {
      const preview = record();
      Object.assign(preview.handoff, change);
      expect(executionHandoffIntent(preview, '核对原件', null, () => 'unused-key')).toBeNull();
    }
  });

  it.each([401, 403, 404])(
    'clears private reasons and receipts when a refreshed source returns %s',
    (status) => {
      let state = executionInitialState('target', 'ocr:run');
      state = executionStateReducer(state, { type: 'reason', value: 'PRIVATE_REASON' });
      state = executionStateReducer(state, {
        type: 'begin',
        target: 'target',
        epoch: 1,
        operation: 'submit',
      });
      state = executionStateReducer(state, {
        type: 'completed',
        target: 'target',
        epoch: 1,
        caseId: 'PRIVATE_CASE',
      });
      state = executionStateReducer(state, {
        type: 'begin',
        target: 'target',
        epoch: 2,
        operation: 'read',
      });
      expect(state.caseId).toBeNull();
      state = executionStateReducer(state, { type: 'failed', target: 'target', epoch: 2, status });
      expect(state.reason).toBe('');
      expect(state.caseId).toBeNull();
      expect(state.record).toBeNull();
    },
  );

  it('keeps the existing global 403 event and sends exactly the explicit POST', async () => {
    const events: string[] = [];
    vi.stubGlobal('window', {
      dispatchEvent: (event: Event) => {
        events.push(event.type);
        return true;
      },
    });
    setApiScope(session);
    const request = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ detail: '拒绝' }), { status: 403 }));
    const intent = executionHandoffIntent(record(), '核对原件', null, () => 'handoff-same-key')!;
    await expect(
      api('/executions/ocr/run/handoff', session, { method: 'POST', body: intent.body }),
    ).rejects.toMatchObject({ status: 403 });
    expect(events).toEqual(['accounting:forbidden']);
    expect(request).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual(intent.body);
  });
});
