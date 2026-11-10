import { describe, expect, it } from 'vitest';
import { assertBusinessContext } from './business-context';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { WorkflowGuide } from './WorkflowGuide';

const target = {
  firmId: 'firm-a',
  companyId: 'company-a',
  eventId: 'event-a',
  period: '2026-10',
  version: 3,
};
const card = () => ({
  mode: 'evidence_summary',
  scope: {
    firm_id: 'firm-a',
    company_id: 'company-a',
    period: '2026-10',
    event_id: 'event-a',
    event_version: 3,
    fact_version: 2,
  },
  fingerprint: null,
  coverage: '未经人工核验',
  relationships: {
    version: 1,
    fingerprint: 'a'.repeat(64),
    nodes: [
      {
        id: 'event:event-a',
        kind: 'event',
        object_id: 'event-a',
        version: '3',
        digest: 'b'.repeat(64),
        origin: 'event:event-a',
        period: '2026-10',
        currency: 'CNY',
        basis: 'business_gross',
        amount: '1.00',
      },
      {
        id: 'original:original-a',
        kind: 'original',
        object_id: 'event-a',
        version: 'a'.repeat(64),
        digest: 'a'.repeat(64),
        origin: `sha256:${'a'.repeat(64)}`,
        period: '2026-10',
        currency: null,
        basis: 'original',
        amount: null,
      },
    ],
    edges: [],
    original_roots: [`sha256:${'a'.repeat(64)}`],
    issues: [],
  },
  evidence: [{ attachment_id: 'original-a', filename: '原件.png', sha256: 'a'.repeat(64) }],
  differences: [],
  next_step: {
    owner: '企业财务',
    label: '核对后办理',
    detail: '核对原件后再确认',
    target: 'actions',
  },
});
describe('current business context acceptance', () => {
  it('never folds a blocking workflow warning', () => {
    const html = renderToStaticMarkup(
      createElement(WorkflowGuide, {
        label: '事项办理引导',
        compact: true,
        collapsed: true,
        guide: {
          title: '交接异常',
          status: '缺少关联',
          next: '请授权会计核对',
          tone: 'warning',
          steps: [],
        },
      }),
    );
    expect(html).toMatch(/^<section/);
    expect(html).not.toContain('business-extra-guide');
    expect(html).toContain('请授权会计核对');
  });
  it('accepts the entire matching contract atomically', () =>
    expect(assertBusinessContext(card(), target)).toEqual(card()));
  for (const [field, value] of Object.entries({
    firm_id: 'other',
    company_id: 'other',
    event_id: 'other',
    period: '2026-11',
    event_version: 2,
    fact_version: true,
  })) {
    it(`rejects wrong ${field}`, () => {
      const valueCard = card();
      Object.assign(valueCard.scope, { [field]: value });
      expect(() => assertBusinessContext(valueCard, target)).toThrow();
    });
  }
  it('rejects arbitrary action routes and partially valid evidence', () => {
    const value = card();
    value.next_step.target = 'https://invalid';
    expect(() => assertBusinessContext(value, target)).toThrow();
    const bad = card();
    bad.evidence[0].sha256 = '';
    expect(() => assertBusinessContext(bad, target)).toThrow();
  });
  it('rejects incomplete findings instead of claiming zero differences', () => {
    expect(() => assertBusinessContext({ ...card(), differences: null }, target)).toThrow();
    expect(() =>
      assertBusinessContext(
        { ...card(), differences: [{ code: 'mismatch', severity: 'blocking' }] },
        target,
      ),
    ).toThrow();
  });
  it('rejects a graph from another original or task revision atomically', () => {
    const old = card();
    old.relationships.nodes[0].version = '2';
    expect(() => assertBusinessContext(old, target)).toThrow();
    const other = card();
    other.relationships.nodes[1].digest = 'c'.repeat(64);
    expect(() => assertBusinessContext(other, target)).toThrow();
  });
});
