import { expect, it } from 'vitest';
import {
  assertRelationships,
  evidenceChange,
  type EvidenceRelationships,
} from './business-evidence';

const graph = (): EvidenceRelationships => ({
  version: 1,
  fingerprint: 'a'.repeat(64),
  original_roots: [],
  nodes: [
    {
      id: 'event:a',
      kind: 'event',
      object_id: 'a',
      version: '1',
      digest: 'b'.repeat(64),
      origin: 'event:a',
      period: '2026-10',
      currency: 'CNY',
      basis: 'business_gross',
      amount: '0.30',
    },
  ],
  edges: [],
  issues: [
    {
      id: 'c'.repeat(64),
      code: 'amount',
      message: '金额待核对',
      severity: 'blocking',
      resolved: false,
      evidence_ids: ['event:a'],
      owner: '企业财务',
      target: 'differences',
      can_handle: true,
      next_action: '核对原件',
      comparison: { state: 'not_comparable', left: null, right: null, delta: null },
    },
  ],
});
it('rejects foreign facts, broken references, unknown routes and duplicate findings', () => {
  const broken = graph();
  broken.nodes[0].object_id = 'other';
  expect(() => assertRelationships(broken, 'a')).toThrow();
  const absent = graph();
  absent.issues[0].evidence_ids = ['missing'];
  expect(() => assertRelationships(absent, 'a')).toThrow();
  expect(() => assertRelationships({ ...graph(), original_roots: ['invented'] }, 'a')).toThrow();
  const duplicated = graph();
  duplicated.issues.push(duplicated.issues[0]);
  expect(() => assertRelationships(duplicated, 'a')).toThrow();
  expect(() =>
    assertRelationships(
      { ...graph(), issues: [{ ...graph().issues[0], target: 'https://evil' }] },
      'a',
    ),
  ).toThrow();
});
it('rejects dependency cycles while association edges do not imply derivation', () => {
  const cycle = graph();
  cycle.nodes.push({ ...cycle.nodes[0], id: 'event:b' });
  cycle.edges = [
    { kind: 'derived_from', source: 'event:a', target: 'event:b' },
    { kind: 'depends_on', source: 'event:b', target: 'event:a' },
  ];
  expect(() => assertRelationships(cycle, 'a')).toThrow();
  cycle.edges[1].kind = 'contradicts';
  expect(assertRelationships(cycle, 'a')).toEqual(cycle);
});
it('distinguishes a recorded human check from a finding that disappeared', () => {
  const before = graph(),
    current = graph();
  current.issues[0].resolved = true;
  expect(evidenceChange(before, current)).toEqual({ handled: 1, absent: 0, remaining: 0 });
  current.issues = [];
  expect(evidenceChange(before, current)).toEqual({ handled: 0, absent: 1, remaining: 0 });
  expect(evidenceChange(before, before)).toEqual({ handled: 0, absent: 0, remaining: 1 });
});
