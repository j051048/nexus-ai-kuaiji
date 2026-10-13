import { expect, it } from 'vitest';
import { hasNewProposalDraft, proposalOperationReason } from './proposal-task-helpers';
import type { ProposalTask } from './proposal-task-helpers';

it('任务结果版本变化才提示重新读取账务', () => {
  const job = { id: 'a', status: 'succeeded', result_draft_version: 3 } as ProposalTask;
  expect(hasNewProposalDraft(new Map(), [job])).toBe(true);
  expect(hasNewProposalDraft(new Map([['a', 3]]), [job])).toBe(false);
  expect(hasNewProposalDraft(new Map([['a', 2]]), [job])).toBe(true);
  expect(hasNewProposalDraft(new Map(), [{ ...job, status: 'running' }])).toBe(false);
});
it('恢复处理必须填写明确依据', () => {
  expect(proposalOperationReason('  ')).toBe('请填写处理依据。');
  expect(proposalOperationReason('依据'.repeat(501))).toContain('1000');
  expect(proposalOperationReason('已修正客户科目，重新核对')).toBeNull();
});
