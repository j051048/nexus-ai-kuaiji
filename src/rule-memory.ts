export type RuleMemoryScope = {
  firm_id: string;
  company_id: string;
  book_id: string;
  period: string;
  rule_id: string;
  version: number;
  state_version: number;
};
export type RuleMemory = {
  state: 'candidate' | 'active' | 'blocked' | 'retired';
  auto_apply: false;
  sources: {
    sample_id: string;
    draft_id: string;
    reviewed_version: number;
    reviewed_by: string;
    source_fingerprint: string;
    original_hashes: string[];
  }[];
  unavailable: string[];
  boundary: string;
  next_action: string;
  fingerprint: string;
};
function object(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v))
    throw new Error('规则依据不完整，请重新读取');
  return v as Record<string, unknown>;
}
function text(v: unknown): v is string {
  return typeof v === 'string' && v.length > 0;
}
function hash(v: unknown) {
  return typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
}
export function readRuleMemory(raw: unknown, expected: RuleMemoryScope): RuleMemory {
  const v = object(raw),
    scope = object(v.scope);
  if (
    v.schema !== 'enterprise-rule-memory-v1' ||
    v.auto_apply !== false ||
    !['candidate', 'active', 'blocked', 'retired'].includes(String(v.state)) ||
    ['firm_id', 'company_id', 'book_id', 'period'].some(
      (k) => scope[k] !== expected[k as keyof RuleMemoryScope],
    ) ||
    ['rule_id', 'version', 'state_version'].some(
      (k) => v[k] !== expected[k as keyof RuleMemoryScope],
    ) ||
    !Number.isSafeInteger(v.version) ||
    !Number.isSafeInteger(v.state_version) ||
    !Array.isArray(v.sources) ||
    v.sources.length > 100 ||
    !Array.isArray(v.unavailable) ||
    !v.unavailable.every(text) ||
    !text(v.boundary) ||
    !text(v.next_action) ||
    [
      'fingerprint',
      'policy_fingerprint',
      'catalog_fingerprint',
      'master_fingerprint',
      'definition_fingerprint',
    ].some((k) => !hash(v[k]))
  )
    throw new Error('规则范围或版本已变化，请重新打开当前规则');
  const sources = v.sources.map(object);
  if (
    new Set(sources.map((s) => s.sample_id)).size !== sources.length ||
    sources.some(
      (s) =>
        !text(s.sample_id) ||
        !text(s.draft_id) ||
        !text(s.reviewed_by) ||
        typeof s.reviewed_version !== 'number' ||
        !Number.isSafeInteger(s.reviewed_version) ||
        s.reviewed_version < 1 ||
        !hash(s.source_fingerprint) ||
        !Array.isArray(s.original_hashes) ||
        !s.original_hashes.every(hash),
    ) ||
    (['candidate', 'active'].includes(String(v.state)) &&
      (!sources.length || v.unavailable.length)) ||
    (v.state === 'blocked' && !v.unavailable.length)
  )
    throw new Error('规则审核来源已失效，请人工核对');
  return v as unknown as RuleMemory;
}
