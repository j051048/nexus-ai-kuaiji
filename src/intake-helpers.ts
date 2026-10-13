import type { BusinessPayload, BusinessTemplate } from './business-types';
import type { IntakeRun } from './intake-types';

export function changeIntakeTemplate(
  payload: BusinessPayload,
  template: BusinessTemplate,
): BusinessPayload {
  return {
    ...payload,
    direction: template === 'daily_cashflow' ? payload.direction : 'expense',
    payment_status:
      template === 'daily_cashflow'
        ? 'paid'
        : template === 'reimbursement'
          ? 'unpaid'
          : payload.payment_status,
  };
}

export function intakeCandidates(run: IntakeRun, direction: 'income' | 'expense') {
  if (run.status !== 'succeeded' || !run.result) return [];
  return run.result.documents.map((document, index) => {
    const seller = direction === 'income' ? 'buyer_name' : 'seller_name';
    const fields = [
      ['amount', 'gross_amount', '票面总金额'],
      ['occurred_on', 'issued_on', '票据日期（请核对发生日）'],
      ['counterparty', seller, '交易对方'],
    ] as const;
    return {
      document,
      index,
      fields: fields.flatMap(([target, source, label]) => {
        const value = document[source];
        const evidence = document.evidence.find((row) => row.field === source);
        const blocked = run.checks.some(
          (check) =>
            (check.payload.document_index === index && check.payload.field === source) ||
            check.code === `document_${index + 1}_evidence_${source}` ||
            check.code === `document_${index + 1}_pages`,
        );
        if (
          !value ||
          !evidence ||
          blocked ||
          (source === 'gross_amount' && document.currency !== 'CNY') ||
          document.document_type === 'unreadable' ||
          document.document_type === 'other'
        )
          return [];
        return [{ target, value, label, page: evidence.page, quote: evidence.quote }];
      }),
    };
  });
}
