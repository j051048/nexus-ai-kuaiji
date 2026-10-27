import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { parseManualReviewList, manualCanReview } from './manual-review-types';
import { ManualVoucherReviewPanel } from './ManualVoucherReviewPanel';
import type { Session } from './types';
const data = {
  scope: { book_id: 'book-a', company_id: 'company-a', firm_id: 'firm-a', period: '2026-10' },
  requests: [
    {
      id: 'request-a',
      book_id: 'book-a',
      version: 1,
      status: 'pending',
      prepared_by: 'maker-a',
      prepared_name: '制单甲',
      policy_version: 1,
      mode: 'independent',
      independent: null,
      reviewed_by: null,
      reviewed_name: null,
      review_reason: null,
      content: {
        book_id: 'book-a',
        command_key: 'command-a',
        voucher_date: '2026-10-01',
        voucher_type: '记',
        description: '虚构手工凭证',
        reason: '本地测试',
        lines: [
          {
            account_id: 'account-a',
            account_version: 1,
            debit: '100.00',
            credit: '0.00',
            auxiliary_values: {},
            source_ref: '',
          },
          {
            account_id: 'account-b',
            account_version: 1,
            debit: '0.00',
            credit: '100.00',
            auxiliary_values: {},
            source_ref: '',
          },
        ],
      },
    },
  ],
  accounts: [{ id: 'account-a', version: 1, code: '1002', name: '银行存款' }],
  can_prepare: true,
  can_review: true,
};
const scope = { bookId: 'book-a', firmId: 'firm-a', companyId: 'company-a' };
describe('manual voucher review UI boundaries', () => {
  it('requires matching company, book, firm and nested request identity', () => {
    expect(parseManualReviewList(data, scope).requests).toHaveLength(1);
    for (const expected of [
      { ...scope, bookId: 'book-b' },
      { ...scope, firmId: 'firm-b' },
      { ...scope, companyId: 'company-b' },
    ])
      expect(() => parseManualReviewList(data, expected)).toThrow();
    expect(() =>
      parseManualReviewList(
        { ...data, requests: [{ ...data.requests[0], book_id: 'book-b' }] },
        scope,
      ),
    ).toThrow();
    expect(() =>
      parseManualReviewList({ ...data, requests: [{ ...data.requests[0], version: true }] }, scope),
    ).toThrow();
  });
  it('does not derive review permission from edit permission or allow independent self review', () => {
    const list = parseManualReviewList(data, scope);
    expect(manualCanReview(list, list.requests[0], 'maker-a')).toBe(false);
    expect(manualCanReview({ ...list, can_prepare: false }, list.requests[0], 'reviewer-b')).toBe(
      true,
    );
    expect(manualCanReview({ ...list, can_review: false }, list.requests[0], 'reviewer-b')).toBe(
      false,
    );
  });
  it('a withdrawn pending request remains visible but cannot be reviewed', () => {
    const list = parseManualReviewList(
      {
        ...data,
        requests: [
          { ...data.requests[0], withdrawn: true, withdrawal_reason: '凭证摘要录错后重新准备' },
        ],
      },
      scope,
    );
    expect(list.requests).toHaveLength(1);
    expect(manualCanReview(list, list.requests[0], 'reviewer-b')).toBe(false);
  });
  it('blocks malformed original command keys and contradictory approved responsibility', () => {
    for (const item of [
      { ...data.requests[0], status: 'approved', independent: false, reviewed_by: 'reviewer-b' },
      { ...data.requests[0], content: { ...data.requests[0].content, command_key: null } },
    ])
      expect(() => parseManualReviewList({ ...data, requests: [item] }, scope)).toThrow();
  });
  it('starts read-only until authorized account and responsibility data are loaded', () => {
    const session: Session = {
      token: 'secret',
      mode: 'local',
      firm_name: '虚构事务所',
      user_name: '会计',
      user_id: 'maker-a',
      firm_id: 'firm-a',
      role: 'owner',
      scope_key: 'scope-a',
    };
    const html = renderToStaticMarkup(
      createElement(ManualVoucherReviewPanel, {
        session,
        bookId: 'book-a',
        companyId: 'company-a',
      }),
    );
    expect(html).toContain('提交待复核');
    expect(html).toContain('会计审核');
    expect(html).toContain('正式记账');
    expect(html).not.toContain('>直接记账<');
    expect(html).not.toContain(session.token);
    expect(html).not.toContain('<form');
  });
});
