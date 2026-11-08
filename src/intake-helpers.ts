import type { BusinessPayload, BusinessTemplate } from './business-types';
import type { IntakeDraft, IntakeRun } from './intake-types';

/** Presentation only: never starts a provider or confirms original evidence. */
export function intakeNextStep(
  company: string,
  attachmentCount: number,
  runs: IntakeRun[],
  confirmed: boolean,
) {
  if (!company) return { state: 'scope', text: '先选择企业，再拍照或上传原件' };
  if (!attachmentCount)
    return { state: 'upload', text: '先拍照或上传原件，也可按实际业务手工填写' };
  if (runs.some((r) => r.status === 'stale'))
    return { state: 'unknown', text: '识别结果待核查，请联系管理员；不会自动重发' };
  if (runs.some((r) => r.status === 'running'))
    return { state: 'running', text: '识别正在处理，请读取最新草稿，不要重复提交识别' };
  if (runs.some((r) => r.status === 'failed'))
    return {
      state: 'failed',
      text: '识别未完成，可对照原件手工填写；先核查费用和失败原因，再决定是否重试',
    };
  if (runs.some((r) => r.checks.some((c) => c.severity === 'blocking')))
    return { state: 'blocked', text: '先对照原件处理下方票据问题，再核对信息' };
  if (attachmentCount > 1)
    return { state: 'multiple', text: '逐张核对原件；不同业务分别录入，不自动合计' };
  if (confirmed)
    return { state: 'confirmed', text: '已确认字段，核对业务说明后可提交；仍需后续岗位审核' };
  if (runs.some((r) => r.status === 'succeeded'))
    return { state: 'verify', text: '核对金额、日期和交易对方，补充用途后再确认原件' };
  return {
    state: 'manual',
    text: '原件已保存，可点击识别预填，也可手工填写后核对；上传不等于已识别',
  };
}

/** Only the explicitly requested, unchanged single original may prefill untouched facts. */
export function prefillIntake(
  requested: IntakeDraft,
  received: IntakeDraft,
  runId: string,
  current: BusinessPayload,
  edited: ReadonlySet<string>,
): BusinessPayload {
  if (
    requested.id !== received.id ||
    requested.company_id !== received.company_id ||
    requested.version !== received.version ||
    requested.template_id !== received.template_id ||
    requested.attachments.length !== 1 ||
    received.attachments.length !== 1
  )
    return current;
  const original = requested.attachments[0],
    attachment = received.attachments[0];
  if (!original.sha256 || original.id !== attachment.id || original.sha256 !== attachment.sha256)
    return current;
  const run = received.runs.find((item) => item.id === runId && item.attachment_id === original.id);
  if (
    !run ||
    run.result?.documents.length !== 1 ||
    run.checks.some((check) => check.severity === 'blocking')
  )
    return current;
  const next = { ...current };
  for (const candidate of intakeCandidates(run, current.direction)) {
    for (const item of candidate.fields) {
      if (edited.has(item.target) || (item.target !== 'occurred_on' && current[item.target]))
        continue;
      // A date changed through another UI path is also authoritative.
      if (item.target === 'occurred_on' && current.occurred_on !== requested.fields.occurred_on)
        continue;
      next[item.target] = item.value;
    }
  }
  return next;
}

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
