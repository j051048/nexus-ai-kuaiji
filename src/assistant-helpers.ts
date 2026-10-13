import type { AssistantActionPlan, AssistantActionReceipt, AssistantConversation, AssistantConversationDetail, AssistantEvidence, AssistantReply, AssistantTaskKind, AssistantTurnStatus } from './assistant-types';

export const assistantTaskLabels: Record<AssistantTaskKind, string> = { ocr_batch: '识别所选票据', proposal_batch: '生成分录草稿', bank_drafts: '生成流水草稿' };
export const assistantTurnLabels: Record<AssistantTurnStatus, string> = { queued: '等待模型处理', running: '模型请求处理中', succeeded: '回复已保存', failed: '本次处理失败', interrupted: '请求中断，需核对', stale: '资料已更新，请重新提问', cancelled: '本次处理已取消' };
export function assistantQuestion(value: string): string | null {
  const text = value.trim(); return text.length > 0 && text.length <= 4000 ? text : null;
}
export function assistantTurnActive(status: AssistantTurnStatus): boolean { return status === 'queued' || status === 'running'; }
export function assistantDetailMatches(detail: AssistantConversationDetail, companyId: string, conversationId: string): boolean {
  return detail.conversation.id === conversationId && detail.conversation.company_id === companyId
    && detail.context.company_id === companyId && detail.conversation.scope_version === 1
    && detail.context.books.every(book => detail.conversation.book_ids.includes(book.id))
    && detail.context.events.every(event => detail.conversation.event_ids.includes(event.id));
}
/** Citations are only navigable when returned by the current authorized evidence snapshot. */
export function assistantCitations(reply: AssistantReply, evidence: AssistantEvidence[]): AssistantEvidence[] {
  const allowed = new Map(evidence.map(item => [item.id, item]));
  return [...new Set(reply.citation_ids)].flatMap(id => allowed.has(id) ? [allowed.get(id)!] : []);
}
export function assistantAllowedProposals(reply: AssistantReply): AssistantReply['proposals'] {
  return reply.proposals.filter(proposal => Object.prototype.hasOwnProperty.call(assistantTaskLabels, proposal.kind) && typeof proposal.reason === 'string' && proposal.reason.trim().length > 0).slice(0, 3);
}
/** A server preview may narrow the selected scope; it cannot introduce another enterprise or ledger. */
export function assistantPlanMatches(plan: AssistantActionPlan, conversation: AssistantConversation): boolean {
  return plan.scope.company_id === conversation.company_id && plan.requires_confirmation === true
    && (plan.scope.draft_id ?? null) === (conversation.draft_id ?? null)
    && plan.scope.book_ids.every(id => conversation.book_ids.includes(id))
    && plan.scope.event_ids.every(id => conversation.event_ids.includes(id))
    && plan.items.every(item => (!conversation.draft_id || !item.draft_id || item.draft_id === conversation.draft_id) && (conversation.event_ids.length > 0
      ? !!item.event_id && conversation.event_ids.includes(item.event_id) && (!conversation.book_ids.length || !item.book_id || conversation.book_ids.includes(item.book_id))
      : !!item.book_id && conversation.book_ids.includes(item.book_id)))
    && /^[a-f0-9]{64}$/.test(plan.fingerprint) && Number.isSafeInteger(plan.count) && plan.count === plan.items.length && plan.count >= 0 && plan.count <= 100;
}
export function assistantBusinessStatus(status: string): string {
  return ({ draft: '填写中', submitted: '待部门审批', department_approved: '待财务审批', finance_approved: '待会计移交', handed_off: '已移交会计', returned: '待补充资料' } as Record<string, string>)[status] ?? '状态待核对';
}
export function assistantReceiptProgress(receipt: AssistantActionReceipt): string {
  if (!receipt.jobs.length) return `${receipt.created} 份草稿已生成${receipt.exceptions ? ` · ${receipt.exceptions} 份需要人工处理` : ''}`;
  const succeeded = receipt.jobs.filter(job => job.status === 'succeeded').length;
  const attention = receipt.jobs.filter(job => ['failed', 'interrupted', 'stale', 'needs_attention', 'scope_changed'].includes(job.status)).length;
  const active = receipt.jobs.filter(job => ['queued', 'running', 'retry_wait', 'waiting'].includes(job.status)).length;
  const cancelled = receipt.jobs.filter(job => job.status === 'cancelled').length;
  return [`${succeeded} / ${receipt.jobs.length} 项完成`, active ? `${active} 项等待或处理中` : '', attention ? `${attention} 项需处理` : '', cancelled ? `${cancelled} 项已取消` : ''].filter(Boolean).join(' · ');
}
export function assistantPlanConfirmable(plan: AssistantActionPlan, conversation: AssistantConversation, now = Date.now()): boolean {
  return assistantPlanMatches(plan, conversation) && plan.state === 'ready' && plan.count > 0
    && Number.isFinite(Date.parse(plan.expires_at)) && Date.parse(plan.expires_at) > now;
}
export function assistantErrorCodeLabel(code: string | null | undefined): string {
  return ({ model_unavailable: '会计对话模型尚未配置，请联系管理员配置兼容接口。', budget_exceeded: '企业模型预算已达上限，请联系管理员调整或核定费用。', quota_exceeded: '企业模型配额已达上限，请联系管理员处理。', interrupted: '模型请求结果未确认，请先核对请求与费用记录。', stale: '业务事实已更新，请按最新资料重新提问。' } as Record<string, string>)[code ?? ''] ?? '';
}
