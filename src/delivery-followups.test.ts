import { describe, expect, it } from 'vitest';
import {
  followupRows,
  followupSummary,
  validateFollowupFeed,
  type DeliveryFollowupFeed,
} from './delivery-followups';

const feed: DeliveryFollowupFeed = {
  count: 3,
  items: [
    {
      book_id: 'b1',
      book_name: '杭州示例科技有限公司',
      period: '2026-10',
      reasons: ['recipient_missing', 'signature_missing'],
      next_step: 'configure_recipient',
      owner: 'firm_manager',
      package_id: 'p1',
      recipient_user_id: null,
    },
    {
      book_id: 'b2',
      book_name: '宁波示例贸易有限公司',
      period: '2026-09',
      reasons: ['period_not_closed'],
      next_step: 'close_period',
      owner: 'accountant',
      package_id: null,
      recipient_user_id: null,
    },
    {
      book_id: 'b3',
      book_name: '苏州示例服务有限公司',
      period: '2026-08',
      reasons: ['signature_missing'],
      next_step: 'await_customer_signature',
      owner: 'customer',
      package_id: 'p3',
      recipient_user_id: 'u-customer',
    },
  ],
};

describe('delivery followups', () => {
  it('routes unknown sends to a human instead of asking the customer to wait', () => {
    const rows = followupRows({
      count: 1,
      items: [
        {
          ...feed.items[0],
          reasons: ['notification_unknown'],
          next_step: 'reconcile_notification',
          owner: 'firm_manager',
        },
      ],
    });
    expect(rows[0]).toMatchObject({
      reasonText: '通知发送结果未知',
      nextStepText: '人工核对发送结果，勿重复发送',
      tone: 'action',
    });
  });
  it('turns server codes into operator language', () => {
    const rows = followupRows(feed);
    expect(rows[0]).toMatchObject({
      key: 'b1:2026-10',
      reasonText: '未配置签收人 · 客户未签收',
      nextStepText: '配置签收人',
      ownerText: '事务所管理员',
      tone: 'action',
    });
    expect(rows[2].tone).toBe('waiting');
  });

  it('keeps unknown codes verbatim instead of inventing a step', () => {
    const rows = followupRows({
      count: 1,
      items: [
        {
          book_id: 'b9',
          book_name: '',
          period: '2026-10',
          reasons: ['brand_new_reason'],
          next_step: 'brand_new_step',
          owner: 'auditor',
          package_id: null,
          recipient_user_id: null,
        },
      ],
    });
    expect(rows[0]).toMatchObject({
      bookName: 'b9',
      reasonText: 'brand_new_reason',
      nextStepText: 'brand_new_step',
      ownerText: 'auditor',
      tone: 'action',
    });
  });

  it('caps the rows shown and degrades empty feeds to no rows', () => {
    expect(followupRows(feed, 2)).toHaveLength(2);
    expect(followupRows(null)).toEqual([]);
    expect(followupRows({ count: 0, items: [] })).toEqual([]);
  });

  it('counts customer-waiting versus firm-action rows', () => {
    expect(followupSummary(followupRows(feed))).toEqual({ total: 3, waiting: 1, needsFirm: 2 });
  });

  it('rejects feeds that do not match the server contract', () => {
    expect(validateFollowupFeed(feed)).toBe(true);
    expect(validateFollowupFeed({ items: [{ book_id: 'b1', period: '2026-13' }] })).toBe(false);
    expect(validateFollowupFeed({ items: 'nope' })).toBe(false);
    expect(validateFollowupFeed(null)).toBe(false);
  });
});
