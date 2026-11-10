import { assertRelationships, type EvidenceRelationships } from './business-evidence';
export type BusinessContextTarget = {
  firmId: string;
  companyId: string;
  eventId: string;
  period: string;
  version: number;
  originals?: { id: string; sha256: string; superseded?: boolean }[];
};
export type BusinessContextCard = {
  mode: 'evidence_summary';
  scope: {
    firm_id: string;
    company_id: string;
    event_id: string;
    period: string;
    event_version: number;
    fact_version: number;
  };
  fingerprint: string | null;
  coverage: string;
  relationships: EvidenceRelationships;
  evidence: { attachment_id: string; filename: string; sha256: string }[];
  differences: { code: string; severity: 'blocking' | 'warning'; message: string }[];
  next_step: { owner: string; label: string; detail: string; target: 'actions' | null };
};
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
const integer = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0;
const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);

/** Accept all evidence and actions together; never render a partially matching reply. */
export function assertBusinessContext(
  value: unknown,
  target: BusinessContextTarget,
): BusinessContextCard {
  const fail = () => {
    throw new Error('办理依据与当前事项不符，请重新读取；仍有问题请联系企业财务。');
  };
  if (!record(value) || !record(value.scope) || !record(value.next_step)) return fail();
  const scope = value.scope,
    step = value.next_step;
  if (
    value.mode !== 'evidence_summary' ||
    scope.firm_id !== target.firmId ||
    scope.company_id !== target.companyId ||
    scope.event_id !== target.eventId ||
    scope.period !== target.period ||
    scope.event_version !== target.version ||
    !integer(scope.event_version) ||
    !integer(scope.fact_version) ||
    !text(value.coverage) ||
    !(value.fingerprint === null || digest(value.fingerprint)) ||
    !Array.isArray(value.evidence) ||
    !Array.isArray(value.differences) ||
    !text(step.owner) ||
    !text(step.label) ||
    !text(step.detail) ||
    !['actions', null].includes(step.target as string | null)
  )
    return fail();
  const ids = new Set<string>();
  for (const item of value.evidence) {
    if (
      !record(item) ||
      !text(item.attachment_id) ||
      !text(item.filename) ||
      !digest(item.sha256) ||
      ids.has(item.attachment_id)
    )
      return fail();
    ids.add(item.attachment_id);
    if (
      target.originals &&
      !target.originals.some(
        (row) => !row.superseded && row.id === item.attachment_id && row.sha256 === item.sha256,
      )
    )
      return fail();
  }
  if (target.originals && ids.size !== target.originals.filter((row) => !row.superseded).length)
    return fail();
  for (const item of value.differences) {
    if (
      !record(item) ||
      !text(item.code) ||
      !text(item.message) ||
      !['blocking', 'warning'].includes(String(item.severity))
    )
      return fail();
  }
  const relationships = assertRelationships(value.relationships, target.eventId);
  const eventNode = relationships.nodes.find((node) => node.kind === 'event');
  const originalNodes = relationships.nodes.filter((node) => node.kind === 'original');
  const originals = value.evidence;
  if (
    !eventNode ||
    relationships.nodes.filter((node) => node.kind === 'event').length !== 1 ||
    eventNode.id !== `event:${target.eventId}` ||
    eventNode.version !== String(target.version) ||
    eventNode.period !== target.period ||
    originalNodes.length !== value.evidence.length ||
    originalNodes.some(
      (node) =>
        !originals.some(
          (item) =>
            record(item) &&
            node.id === `original:${item.attachment_id}` &&
            node.digest === item.sha256 &&
            node.version === item.sha256 &&
            node.origin === `sha256:${item.sha256}`,
        ),
    )
  )
    return fail();
  const active = relationships.issues.filter((issue) => !issue.resolved);
  const differences = value.differences;
  if (
    active.length !== value.differences.length ||
    active.some(
      (issue) =>
        !differences.some(
          (item) =>
            record(item) &&
            item.code === issue.code &&
            item.message === issue.message &&
            item.severity === issue.severity,
        ),
    )
  )
    return fail();
  return value as BusinessContextCard;
}
