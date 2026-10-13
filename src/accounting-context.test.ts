import { describe, expect, it } from 'vitest';
import { contextActions, contextQuestion, evidencePage, matchesAccountingScope, type AccountingContextAction, type AccountingContextScope } from './accounting-context';

const scope: AccountingContextScope = { book_id: 'b1', company_id: 'c1', company_name: '企业一', book_name: '账套', period: '2026-10', period_status: 'open', draft_id: 'd1', draft_version: 3, fact_version: 2 };
describe('会计上下文边界', () => {
  it('账套、凭证或事实版本变化后不能呈现旧回复', () => {
    expect(matchesAccountingScope(scope, 'b1', 'd1', 3)).toBe(true);
    expect(matchesAccountingScope(scope, 'b2', 'd1', 3)).toBe(false);
    expect(matchesAccountingScope(scope, 'b1', 'd2', 3)).toBe(false);
    expect(matchesAccountingScope(scope, 'b1', 'd1', 4)).toBe(false);
    expect(matchesAccountingScope({ ...scope, draft_version: Number.NaN }, 'b1', 'd1')).toBe(false);
  });
  it('提问去除空白并拒绝空内容及超长输入', () => {
    expect(contextQuestion('  核对借贷金额  ')).toBe('核对借贷金额');
    expect(contextQuestion('   ')).toBeNull();
    expect(contextQuestion('问'.repeat(1000))).toHaveLength(1000);
    expect(contextQuestion('问'.repeat(1001))).toBeNull();
  });
  it('不允许助手导航到回复中夹带的其他企业凭证', () => {
    const actions: AccountingContextAction[] = [
      { type: 'review', label: '查看审核', book_id: 'b1', draft_id: 'd1', enabled: true },
      { type: 'edit', label: '编辑', book_id: 'other', draft_id: 'd1', enabled: true },
      { type: 'history', label: '查看历史', book_id: 'b1', draft_id: 'other', enabled: true },
    ];
    expect(contextActions(actions, scope)).toEqual([actions[0]]);
  });
  it('只接受真实正整数页码，不推断缺失的字段位置', () => {
    expect(evidencePage(undefined)).toBeUndefined();
    expect(evidencePage(0)).toBeUndefined();
    expect(evidencePage(-2)).toBeUndefined();
    expect(evidencePage(1.5)).toBeUndefined();
    expect(evidencePage(10001)).toBeUndefined();
    expect(evidencePage(3)).toBe(3);
  });
});
