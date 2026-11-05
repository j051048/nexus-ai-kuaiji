import { describe, expect, it } from 'vitest';
import { initialBusinessPayload } from './business-forms';
import { prefillIntake } from './intake-helpers';
import type { IntakeDraft, IntakeRun } from './intake-types';

const fields = initialBusinessPayload('reimbursement');
const run = {
  id: 'r',
  attachment_id: 'a',
  status: 'succeeded',
  checks: [],
  error_code: '',
  error_detail: '',
  result: {
    documents: [
      {
        document_type: 'receipt',
        currency: 'CNY',
        gross_amount: '128.50',
        issued_on: '2026-11-01',
        seller_name: '商店',
        evidence: [
          { field: 'gross_amount', page: 1, quote: '128.50' },
          { field: 'issued_on', page: 1, quote: '2026-11-01' },
          { field: 'seller_name', page: 1, quote: '商店' },
        ],
      },
    ],
  },
} as unknown as IntakeRun;
const draft = {
  id: 'd',
  company_id: 'c',
  version: 2,
  fields,
  template_id: 'reimbursement',
  attachments: [{ id: 'a', sha256: 'hash' }],
  runs: [run],
} as IntakeDraft;

describe('录入预填不覆盖人工事实', () => {
  it('单原件、单据、同版本只填未编辑的字段，仍需人工确认', () => {
    expect(prefillIntake(draft, draft, run.id, fields, new Set())).toMatchObject({
      amount: '128.50',
      occurred_on: '2026-11-01',
      counterparty: '商店',
    });
  });
  it('迟到结果保留人工金额及主动清空的对方', () => {
    expect(
      prefillIntake(
        draft,
        draft,
        run.id,
        { ...fields, amount: '120' },
        new Set(['amount', 'counterparty']),
      ),
    ).toMatchObject({ amount: '120', counterparty: '' });
  });
  it.each(['id', 'company_id', 'version', 'template_id'] as const)('拒绝变化的 %s', (key) => {
    const changed = { ...draft, [key]: key === 'version' ? 3 : 'changed' } as IntakeDraft;
    expect(prefillIntake(draft, changed, run.id, fields, new Set())).toEqual(fields);
  });
  it('拒绝原件变更、多附件、多票、风险结果和不属于本次的运行', () => {
    for (const changed of [
      { ...draft, attachments: [{ ...draft.attachments[0], sha256: 'changed' }] },
      { ...draft, attachments: [...draft.attachments, { ...draft.attachments[0], id: 'b' }] },
      {
        ...draft,
        runs: [
          {
            ...run,
            result: {
              ...run.result!,
              documents: [...run.result!.documents, ...run.result!.documents],
            },
          },
        ],
      },
      {
        ...draft,
        runs: [
          {
            ...run,
            checks: [{ code: 'risk', severity: 'blocking', payload: { message: '冲突' } }],
          },
        ],
      },
      { ...draft, runs: [{ ...run, attachment_id: 'other' }] },
    ])
      expect(prefillIntake(draft, changed, run.id, fields, new Set())).toEqual(fields);
  });
  it('恢复草稿的已填字段不被识别覆盖', () => {
    const saved = { ...fields, amount: '55.00', occurred_on: '2026-10-31', counterparty: '原对方' };
    expect(prefillIntake(draft, draft, run.id, saved, new Set(Object.keys(saved)))).toEqual(saved);
  });
});
