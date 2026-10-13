import { describe, expect, it } from 'vitest';
import { changeIntakeTemplate, intakeCandidates } from './intake-helpers';
import { initialBusinessPayload } from './business-forms';
import type { IntakeRun } from './intake-types';

describe('员工录入候选与模板', () => {
  it('切模板保留填写内容，仅调整模板限定的收付状态', () => {
    const payload = {
      ...initialBusinessPayload('reimbursement'),
      amount: '80.25',
      counterparty: '供应商',
      description: '办公采购',
    };
    expect(changeIntakeTemplate(payload, 'daily_cashflow')).toMatchObject({
      amount: '80.25',
      counterparty: '供应商',
      description: '办公采购',
      payment_status: 'paid',
    });
    expect(
      changeIntakeTemplate({ ...payload, direction: 'income' }, 'reimbursement'),
    ).toMatchObject({ direction: 'expense', payment_status: 'unpaid' });
  });
  it('多张单据保持独立候选，不合计金额，不采用证据失败或外币金额', () => {
    const document = {
      document_type: 'receipt',
      gross_amount: '10.00',
      currency: 'CNY',
      issued_on: '2026-10-13',
      seller_name: '商家',
      evidence: [{ field: 'gross_amount', page: 1, quote: '10.00' }],
    };
    const run = {
      status: 'succeeded',
      checks: [],
      result: { documents: [document, { ...document, gross_amount: '20.00' }] },
    } as unknown as IntakeRun;
    expect(intakeCandidates(run, 'expense').map((row) => row.fields[0].value)).toEqual([
      '10.00',
      '20.00',
    ]);
    run.checks = [
      {
        code: 'document_1_evidence_gross_amount',
        severity: 'blocking',
        payload: { message: '证据错误', document_index: 0, field: 'gross_amount' },
      },
    ];
    expect(intakeCandidates(run, 'expense')[0].fields).toEqual([]);
    run.result!.documents[1].currency = 'USD';
    expect(intakeCandidates(run, 'expense')[1].fields).toEqual([]);
  });
});
