import { describe, expect, it } from 'vitest';
import {
  assistantAllowedProposals,
  assistantCitations,
  assistantDetailMatches,
  assistantPlanConfirmable,
  assistantPlanMatches,
  assistantQuestion,
  assistantReceiptProgress,
  assistantTurnActive,
} from './assistant-helpers';
import type {
  AssistantActionPlan,
  AssistantConversation,
  AssistantConversationDetail,
  AssistantReply,
} from './assistant-types';

const conversation: AssistantConversation = {
  id: 'c1',
  company_id: 'company1',
  title: '月末核对',
  book_ids: ['b1'],
  event_ids: ['e1'],
  scope_version: 1,
  created_at: '',
  updated_at: '',
};
const plan: AssistantActionPlan = {
  id: 'p1',
  kind: 'ocr_batch',
  state: 'ready',
  fingerprint: 'a'.repeat(64),
  expires_at: '2026-10-14T00:00:00Z',
  scope: { company_id: 'company1', book_ids: ['b1'], event_ids: ['e1'] },
  count: 1,
  items: [{ title: '票据', event_id: 'e1' }],
  blocked: [],
  reason: '核对票据',
  requires_confirmation: true,
};
describe('全局会计助手边界', () => {
  it('去除提问空白，拒绝超长或空提问', () => {
    expect(assistantQuestion(' 核对当前事项 ')).toBe('核对当前事项');
    expect(assistantQuestion(' ')).toBeNull();
    expect(assistantQuestion('问'.repeat(4001))).toBeNull();
  });
  it('只显示当前企业、当前会话及授权资料的回复', () => {
    const detail: AssistantConversationDetail = {
      conversation,
      turns: [],
      context: {
        company_id: 'company1',
        company_name: '企业一',
        books: [{ id: 'b1', name: '账套', period: '2026-10' }],
        events: [{ id: 'e1', status: 'submitted' }],
        evidence: [],
        input_fingerprint: 'a',
      },
    };
    expect(assistantDetailMatches(detail, 'company1', 'c1')).toBe(true);
    expect(assistantDetailMatches(detail, 'company2', 'c1')).toBe(false);
    expect(
      assistantDetailMatches(
        {
          ...detail,
          context: {
            ...detail.context,
            books: [{ id: 'other', name: '他人账套', period: '2026-10' }],
          },
        },
        'company1',
        'c1',
      ),
    ).toBe(false);
  });
  it('模型引用不能制造资料或跳转地址', () => {
    const reply: AssistantReply = {
      answer: '核对',
      citation_ids: ['known', 'unknown', 'known'],
      proposals: [],
    };
    expect(
      assistantCitations(reply, [{ id: 'known', kind: 'source', label: '流水', detail: '已授权' }]),
    ).toHaveLength(1);
  });
  it('不展示审核、导出或模型传入的其他工具', () => {
    const reply = {
      answer: '',
      citation_ids: [],
      proposals: [
        { kind: 'ocr_batch', reason: '核对' },
        { kind: 'approve', reason: '直接通过' },
        { kind: 'bank_drafts', reason: '' },
      ],
    } as unknown as AssistantReply;
    expect(assistantAllowedProposals(reply)).toEqual([{ kind: 'ocr_batch', reason: '核对' }]);
  });
  it('跨企业、越界账套或事项的预览不能确认', () => {
    expect(assistantPlanMatches(plan, conversation)).toBe(true);
    expect(
      assistantPlanMatches(
        { ...plan, scope: { ...plan.scope, company_id: 'other' } },
        conversation,
      ),
    ).toBe(false);
    expect(
      assistantPlanMatches({ ...plan, items: [{ title: '越界', book_id: 'b2' }] }, conversation),
    ).toBe(false);
    expect(
      assistantPlanMatches({ ...plan, scope: { ...plan.scope, event_ids: ['e2'] } }, conversation),
    ).toBe(false);
  });
  it('选定账套允许其映射事项，单独选定事项不会扩大到整个账套', () => {
    const booksOnly = { ...conversation, event_ids: [] };
    expect(
      assistantPlanMatches(
        {
          ...plan,
          scope: { ...plan.scope, event_ids: [] },
          items: [{ title: '账套事项', event_id: 'derived', book_id: 'b1' }],
        },
        booksOnly,
      ),
    ).toBe(true);
    expect(
      assistantPlanMatches(
        {
          ...plan,
          scope: { ...plan.scope, event_ids: [] },
          items: [{ title: '未知事项', event_id: 'derived' }],
        },
        booksOnly,
      ),
    ).toBe(false);
    const eventsOnly = { ...conversation, book_ids: [] };
    expect(
      assistantPlanMatches(
        {
          ...plan,
          scope: { ...plan.scope, book_ids: [] },
          items: [{ title: '所选事项', event_id: 'e1', book_id: 'mapping' }],
        },
        eventsOnly,
      ),
    ).toBe(true);
    expect(
      assistantPlanMatches(
        {
          ...plan,
          scope: { ...plan.scope, book_ids: [] },
          items: [{ title: '其他事项', event_id: 'other', book_id: 'mapping' }],
        },
        eventsOnly,
      ),
    ).toBe(false);
  });
  it('过期、空、已执行或损坏的预览不能确认', () => {
    const now = Date.parse('2026-10-13T00:00:00Z');
    expect(assistantPlanConfirmable(plan, conversation, now)).toBe(true);
    expect(assistantPlanConfirmable(plan, conversation, Date.parse(plan.expires_at))).toBe(false);
    expect(assistantPlanConfirmable({ ...plan, state: 'executed' }, conversation, now)).toBe(false);
    expect(assistantPlanConfirmable({ ...plan, count: 0 }, conversation, now)).toBe(false);
    expect(assistantPlanConfirmable({ ...plan, fingerprint: 'invalid' }, conversation, now)).toBe(
      false,
    );
    expect(assistantPlanConfirmable({ ...plan, expires_at: 'invalid' }, conversation, now)).toBe(
      false,
    );
    expect(assistantPlanConfirmable({ ...plan, count: 2 }, conversation, now)).toBe(false);
    expect(
      assistantPlanMatches(
        { ...plan, scope: { ...plan.scope, draft_id: 'other' } },
        { ...conversation, draft_id: 'd1' },
      ),
    ).toBe(false);
  });
  it('终态不显示持续执行或伪造进度', () => {
    expect(assistantTurnActive('queued')).toBe(true);
    expect(assistantTurnActive('running')).toBe(true);
    expect(assistantTurnActive('interrupted')).toBe(false);
    expect(assistantTurnActive('succeeded')).toBe(false);
  });
  it('任务进度只使用当前回执已核实的真实状态', () => {
    expect(
      assistantReceiptProgress({
        id: 'r',
        plan_id: 'p',
        kind: 'ocr_batch',
        count: 2,
        jobs: [
          { id: 'j1', event_id: 'e', status: 'succeeded' },
          { id: 'j2', event_id: 'e', status: 'scope_changed' },
        ],
        draft_ids: [],
        created: 0,
        exceptions: 0,
        created_at: '',
      }),
    ).toBe('1 / 2 项完成 · 1 项需处理');
  });
});
