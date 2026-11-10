export type EvidenceIssue = {
  id: string;
  code: string;
  message: string;
  severity: 'blocking' | 'warning';
  resolved: boolean;
  evidence_ids: string[];
  owner: string;
  target: 'differences';
  can_handle: boolean;
  next_action: string;
  comparison: {
    state: 'equal' | 'conflict' | 'not_comparable';
    left: string | null;
    right: string | null;
    delta: string | null;
  };
};
export type EvidenceRelationships = {
  version: 1;
  fingerprint: string;
  original_roots: string[];
  nodes: {
    id: string;
    kind: string;
    object_id: string;
    version: string;
    digest: string;
    origin: string;
    period: string;
    currency: string | null;
    basis: string;
    amount: string | null;
  }[];
  edges: {
    kind: 'derived_from' | 'depends_on' | 'same_source' | 'contradicts';
    source: string;
    target: string;
  }[];
  issues: EvidenceIssue[];
};
const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim();
const digest = (v: unknown) => typeof v === 'string' && /^[a-f\d]{64}$/i.test(v);
const money = (v: unknown) => typeof v === 'string' && /^-?\d{1,13}\.\d{2}$/.test(v);
export function assertRelationships(value: unknown, eventId: string): EvidenceRelationships {
  const fail = () => {
    throw new Error('核对依据关联不完整，请重新读取后再办理。');
  };
  if (
    !record(value) ||
    value.version !== 1 ||
    !digest(value.fingerprint) ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    !Array.isArray(value.issues) ||
    !Array.isArray(value.original_roots)
  )
    return fail();
  if (value.nodes.length > 1000 || value.edges.length > 3000 || value.issues.length > 1000)
    return fail();
  const ids = new Set<string>(),
    origins = new Set<string>();
  for (const node of value.nodes) {
    if (
      !record(node) ||
      !text(node.id) ||
      ids.has(node.id) ||
      node.object_id !== eventId ||
      !['event', 'original', 'document', 'aggregate', 'bank'].includes(String(node.kind)) ||
      !text(node.version) ||
      !digest(node.digest) ||
      !text(node.origin) ||
      !text(node.basis) ||
      typeof node.period !== 'string' ||
      !(node.currency === null || text(node.currency)) ||
      !(
        node.amount === null ||
        (typeof node.amount === 'string' && /^\d{1,12}(\.\d{1,2})?$/.test(node.amount))
      )
    )
      return fail();
    ids.add(node.id);
    if (node.kind === 'original') origins.add(node.origin);
  }
  if (
    new Set(value.original_roots).size !== value.original_roots.length ||
    value.original_roots.length !== origins.size ||
    value.original_roots.some((id) => !origins.has(id))
  )
    return fail();
  const dependencies = new Map<string, string[]>();
  for (const edge of value.edges) {
    if (
      !record(edge) ||
      !['derived_from', 'depends_on', 'same_source', 'contradicts'].includes(String(edge.kind)) ||
      !text(edge.source) ||
      !text(edge.target) ||
      !ids.has(edge.source) ||
      !ids.has(edge.target) ||
      edge.source === edge.target
    )
      return fail();
    if (edge.kind === 'derived_from' || edge.kind === 'depends_on')
      dependencies.set(edge.source, [...(dependencies.get(edge.source) ?? []), edge.target]);
  }
  const visited = new Set<string>(),
    active = new Set<string>();
  const visit = (id: string): void => {
    if (active.has(id)) return fail();
    if (visited.has(id)) return;
    active.add(id);
    for (const next of dependencies.get(id) ?? []) visit(next);
    active.delete(id);
    visited.add(id);
  };
  ids.forEach(visit);
  const issueIds = new Set<string>();
  for (const issue of value.issues) {
    if (
      !record(issue) ||
      !digest(issue.id) ||
      issueIds.has(String(issue.id)) ||
      !text(issue.code) ||
      !text(issue.message) ||
      !['blocking', 'warning'].includes(String(issue.severity)) ||
      typeof issue.resolved !== 'boolean' ||
      typeof issue.can_handle !== 'boolean' ||
      !text(issue.owner) ||
      !text(issue.next_action) ||
      issue.target !== 'differences' ||
      !Array.isArray(issue.evidence_ids) ||
      !issue.evidence_ids.length ||
      issue.evidence_ids.some((id) => !ids.has(id)) ||
      !record(issue.comparison)
    )
      return fail();
    const comparison = issue.comparison;
    if (
      comparison.state === 'not_comparable'
        ? [comparison.left, comparison.right, comparison.delta].some((v) => v !== null)
        : !['equal', 'conflict'].includes(String(comparison.state)) ||
          ![comparison.left, comparison.right, comparison.delta].every(money)
    )
      return fail();
    issueIds.add(String(issue.id));
  }
  return value as EvidenceRelationships;
}

/** Disappearing evidence is not a successful approval; report only observed change. */
export function evidenceChange(previous: EvidenceRelationships, current: EvidenceRelationships) {
  const active = previous.issues.filter((issue) => !issue.resolved);
  const remaining = current.issues.filter((issue) => !issue.resolved).length;
  const handled = active.filter((old) =>
    current.issues.some((next) => next.id === old.id && next.resolved),
  ).length;
  const absent = active.filter((old) => !current.issues.some((next) => next.id === old.id)).length;
  return { handled, absent, remaining };
}
