import { describe, expect, it } from 'vitest';
import { basisLabel, eligiblePacketIds, packetSelection } from './review-package-helpers';
import type { ReviewPacketSet } from './review-package-types';

describe('同类复核包', () => {
  const value = { items: [{ draft_id: 'ready-b', check_state: 'ready' }, { draft_id: 'stale', check_state: 'stale' }, { draft_id: 'blocked', check_state: 'blocked' }, { draft_id: 'ready-a', check_state: 'ready' }] } as ReviewPacketSet;
  it('阻断或失效条目不能因勾选而进入确认集', () => {
    expect([...eligiblePacketIds(value)]).toEqual(['ready-b', 'ready-a']);
    expect(packetSelection(value, new Set(['ready-a', 'ready-b', 'stale', 'blocked', 'other']))).toEqual(['ready-a', 'ready-b']);
  });
  it('人工修改显式说明，候选不标成已审核规则', () => {
    const basis = { kind: 'manual', rule_id: null, rule_version: null, template_id: null, template_version: null, manual_version: 3 } as const;
    expect(basisLabel(basis)).toBe('会计修正 · 草稿 v3');
    expect(basisLabel({ ...basis, kind: 'candidate' })).toContain('核对业务实质');
  });
});
