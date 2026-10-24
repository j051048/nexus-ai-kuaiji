import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BusinessRecordLinks, type BusinessMappedBook } from './BusinessRecordLinks';
import type { BusinessEvent } from './business-types';
import { RevisionWorkbench } from './RevisionWorkbench';
import { BusinessOriginalPreview } from './BusinessOriginalPreview';
import type { Session } from './types';

const event: BusinessEvent = {
  id: 'event-a',
  firm_id: 'firm-a',
  company_id: 'company-a',
  department_id: null,
  applicant_id: 'employee-a',
  template_id: 'reimbursement',
  template_version: 1,
  version: 3,
  status: 'handed_off',
  payload: {
    occurred_on: '2026-10-24',
    description: '虚构差旅事项',
    amount: '120.00',
    counterparty: '虚构供方',
    direction: 'expense',
    payment_status: 'paid',
    category: 'travel',
  },
  attachments: [],
  actions: [],
  versions: [],
  mapping: { book_id: 'book-a', source_id: 'source-a', draft_id: 'draft-a' },
  permissions: {
    edit: false,
    submit: false,
    department_approve: false,
    finance_approve: false,
    return: false,
    handoff: false,
  },
};
const book: BusinessMappedBook = {
  id: 'book-a',
  company_id: 'company-a',
  period: '2026-10',
  period_status: 'open',
  permissions: { edit: true, review: true, export: true, manage: false },
};
const callbacks = { onOpenDraft: () => {}, onOpenRevision: () => {} };
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'accountant-a',
  user_name: '虚构会计',
  role: 'operator',
  scope_key: 'offline-a',
};
function render(value: BusinessMappedBook | undefined = book, busy = false) {
  return renderToStaticMarkup(
    createElement(BusinessRecordLinks, {
      event,
      book: value,
      busy,
      ...callbacks,
    }),
  );
}

describe('V06 原记录附近的关联动作遵循当前账套与权限', () => {
  it('修订页从原事项进入时有明确的返回入口', () => {
    const session: Session = {
      token: 'offline',
      mode: 'local',
      firm_id: 'firm-a',
      firm_name: '虚构事务所',
      user_id: 'accountant-a',
      user_name: '虚构会计',
      role: 'operator',
      scope_key: 'offline-a',
    };
    const props = { session, initialEventId: event.id, onBack: () => {} };
    const html = renderToStaticMarkup(createElement(RevisionWorkbench, props));
    expect(html).toContain('返回原业务事项');
  });
  it('只读用户可看关联凭证，不显示可写事实修订入口', () => {
    const html = render({
      ...book,
      permissions: { edit: false, review: false, export: false, manage: false },
    });
    expect(html).not.toContain('申请事实修订');
    expect(html).toContain('查看关联凭证');
    expect(html).not.toContain('打开会计复核');
  });
  it('开放期间授权编辑者可以提出修订，后续仍须独立审批', () => {
    const html = render(book);
    expect(html).toContain('申请事实修订');
    expect(html).toContain('打开会计复核');
    expect(html).toContain('独立审批');
  });
  it('已关期间保留只读凭证入口，不能新提交事实修订', () => {
    const html = render({ ...book, period_status: 'closed' });
    expect(html).not.toContain('申请事实修订');
    expect(html).toContain('查看关联凭证');
  });
  it.each([
    { ...book, company_id: 'company-b' },
    { ...book, id: 'book-b' },
    { ...book, period: '2026-09' },
  ])('他企业、错账套或错月份不得沿旧映射打开', (other) => {
    expect(render(other)).toBe('');
  });
  it('授权账套未读取时不从事件映射自行授权', () => {
    const html = renderToStaticMarkup(
      createElement(BusinessRecordLinks, {
        event,
        book: undefined,
        ...callbacks,
      }),
    );
    expect(html).toBe('');
  });
  it('处理中关联操作一起禁用', () => {
    expect(render(book, true).match(/disabled=""/g)).toHaveLength(2);
  });
  it('没有映射时不制造凭证或事实修订入口', () => {
    expect(
      renderToStaticMarkup(
        createElement(BusinessRecordLinks, {
          event: { ...event, mapping: null },
          book,
          ...callbacks,
        }),
      ),
    ).toBe('');
  });
});

describe('V06 原件预览按需打开，不默认加载所有业务原件', () => {
  const withOriginal = {
    ...event,
    attachments: [
      {
        id: 'file-a',
        filename: '虚构原件.png',
        media_type: 'image/png',
        sha256: 'a'.repeat(64),
        size_bytes: 1024,
      },
    ],
  };
  it('初始仅显示预览入口，不将原件内容嵌入每一条事项', () => {
    const html = renderToStaticMarkup(
      createElement(BusinessOriginalPreview, { event: withOriginal, session }),
    );
    expect(html).toContain('预览原件');
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<iframe');
  });
  it('缺少附件时不显示不可用的预览入口', () => {
    expect(renderToStaticMarkup(createElement(BusinessOriginalPreview, { event, session }))).toBe(
      '',
    );
  });
  it('业务写操作期间预览入口保留忙碌锁', () => {
    const html = renderToStaticMarkup(
      createElement(BusinessOriginalPreview, { event: withOriginal, session, busy: true }),
    );
    expect(html).toContain('disabled=""');
  });
});
