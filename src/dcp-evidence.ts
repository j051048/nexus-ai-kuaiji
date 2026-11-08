export type InvestigationConclusion = 'accepted_by_channel' | 'not_sent' | 'unresolved';
export const conclusionLabels: Record<InvestigationConclusion, string> = {
  accepted_by_channel: '人工核查：通道接受（不等于客户签收）',
  not_sent: '人工核查：未发送（仍不自动重发）',
  unresolved: '人工核查：证据不足，继续核对',
};
export const investigationTools = ['voucher_status', 'close_blockers', 'delivery_status'] as const;
export const investigationLabels: Record<string, string> = {
  voucher_status: '凭证处理情况',
  close_blockers: '月结阻断',
  delivery_status: '交付与通知情况',
};
export interface InvestigationEvidence {
  book_id: string;
  period: string;
  status: 'complete' | 'blocked' | 'partial';
  model_calls: number;
  coverage: { read: number; of: number };
  findings: Record<string, Record<string, unknown>>;
  missing: string[];
  steps: unknown[];
  evidence: { firm_id: string; company_id: string; fingerprint: string } | null;
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('证据格式不完整，请重新核对');
  return value as Record<string, unknown>;
}
function integer(value: unknown, min = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min)
    throw new Error('证据版本或数量无效');
  return value;
}
function hash(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) throw new Error('证据摘要缺失');
  return value;
}
export function readInvestigation(
  value: unknown,
  book: string,
  period: string,
  company: string,
  firm: string,
): InvestigationEvidence {
  const v = object(value),
    coverage = object(v.coverage),
    facts = object(v.findings);
  const names = Object.keys(facts),
    missing = v.missing;
  if (
    v.book_id !== book ||
    v.period !== period ||
    v.model_calls !== 0 ||
    !['complete', 'blocked', 'partial'].includes(String(v.status)) ||
    integer(coverage.read) !== names.length ||
    coverage.of !== 3 ||
    !Array.isArray(missing) ||
    !Array.isArray(v.steps) ||
    new Set([...names, ...missing]).size !== 3 ||
    names.length + missing.length !== 3 ||
    [...names, ...missing].some(
      (n) => !investigationTools.includes(n as (typeof investigationTools)[number]),
    ) ||
    (v.status === 'complete' && missing.length > 0)
  )
    throw new Error('证据范围变化，请重新调查');
  names.forEach((n) => object(facts[n]));
  const steps = v.steps.map(object);
  if (
    steps.length > investigationTools.length ||
    steps.some((s, i) => s.tool !== investigationTools[i] || typeof s.ok !== 'boolean') ||
    names.some((n) => !steps.some((s) => s.tool === n && s.ok === true))
  )
    throw new Error('调查步骤与证据不一致，请重新核对');
  if (names.length) {
    const evidence = object(v.evidence);
    if (evidence.company_id !== company || !firm || evidence.firm_id !== firm)
      throw new Error('企业范围不一致');
    hash(evidence.fingerprint);
  } else if (v.evidence !== null) throw new Error('空结果不得携带旧证据');
  return v as unknown as InvestigationEvidence;
}
export function investigationCommand(
  value: unknown,
  conclusion: InvestigationConclusion,
  evidence_ref: string,
  reason: string,
  key: string,
) {
  const item = object(value),
    pkg = object(item.package),
    recipient = object(item.recipient);
  if (
    item.state !== 'unknown' ||
    typeof recipient.user_id !== 'string' ||
    !recipient.user_id ||
    !conclusionLabels[conclusion] ||
    evidence_ref.trim().length < 3 ||
    reason.trim().length < 3
  )
    throw new Error('请核对未知通知并填写证据与原因');
  return {
    expected_revision: item.investigation ? integer(object(item.investigation).revision, 1) : 0,
    expected_attempts: integer(item.attempts),
    expected_package_version: integer(pkg.version, 1),
    expected_manifest_sha256: hash(pkg.manifest_sha256),
    expected_recipient_user_id: recipient.user_id,
    conclusion,
    evidence_ref: evidence_ref.trim(),
    reason: reason.trim(),
    idempotency_key: key,
  };
}
