import { describe, expect, it } from 'vitest';
import { currentReviewError, reviewFailureNotice } from './review-operation-feedback';

describe('复核失败提示只绑定发起操作的会话、账套与凭证', () => {
  const target = { scopeKey: 'firm-a:user-a:session-a', bookId: 'book-a', draftId: 'draft-a' };
  it('保留真实失败消息并绑定目标，不因全局 toast 推断其它草稿失败', () => {
    const notice = reviewFailureNotice('版本已变化，请重新读取', target);
    expect(currentReviewError(notice, target)).toBe('版本已变化，请重新读取');
    expect(currentReviewError({ kind: 'error', text: '其它操作失败' }, target)).toBe('');
    expect(currentReviewError({ kind: 'success', text: '已保存' }, target)).toBe('');
    expect(currentReviewError(null, target)).toBe('');
  });
  it.each(['scopeKey', 'bookId', 'draftId'] as const)('%s 改变后不展示旧错误', (key) => {
    const notice = reviewFailureNotice('企业 A 的处理错误', target);
    expect(currentReviewError(notice, { ...target, [key]: 'other' })).toBe('');
  });
});
