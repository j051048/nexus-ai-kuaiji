import type { ReviewBasis, ReviewPacketSet } from './review-package-types';

export function eligiblePacketIds(value: ReviewPacketSet): Set<string> {
  return new Set(value.items.filter(item => item.check_state === 'ready').map(item => item.draft_id));
}
export function packetSelection(value: ReviewPacketSet, selected: Set<string>): string[] {
  return [...eligiblePacketIds(value)].filter(id => selected.has(id)).sort();
}
export function basisLabel(basis: ReviewBasis): string {
  if (basis.kind === 'manual') return `会计修正 · 草稿 v${basis.manual_version}`;
  if (basis.kind === 'company_rule') return `企业规则 v${basis.rule_version ?? '待核验'}`;
  if (basis.kind === 'template') return `固定模板 v${basis.template_version ?? '待核验'}`;
  return '候选分录 · 核对业务实质';
}
