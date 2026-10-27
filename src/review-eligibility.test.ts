import { describe, expect, it } from 'vitest';
import { reviewBlockers, reviewEligibility } from './review-eligibility';

const knownIssues = [
  '该会计期间已关闭，不可审核',
  '申请人不能审核本人业务事项的凭证',
  '已关联银行流水存在尚未关闭的异常或补件，须先处理后复核业务凭证',
  '已关联银行流水存在待核验的相似交易，须先核验独立交易，禁止重复记账',
  '缺少凭证证据引用，请补充资料后审核',
  '未形成完整分录，请会计补充对应科目',
  '借贷不平衡',
  '借贷合计必须分别等于原始流水金额',
  '每行必须且只能有一侧非零金额',
  '银行存款分录的金额或方向与原始流水不符',
  '科目 6602.01 不存在或已停用',
];

const snapshot = (issues: string[], ready = issues.length === 0) => ({
  issues,
  ready,
  fact_version: 3,
  expected_version: 3,
});

describe('review blockers', () => {
  it('classifies every blocker the review service can return', () => {
    const blockers = reviewBlockers(knownIssues);
    expect(blockers.map((item) => item.code)).toEqual([
      'period_closed',
      'self_prepared',
      'open_case',
      'duplicate_pending',
      'missing_evidence',
      'incomplete_lines',
      'unbalanced',
      'amount_mismatch',
      'line_side_error',
      'bank_direction',
      'account_inactive',
    ]);
  });

  it('names the next owner and next entry for each blocker instead of only quoting it', () => {
    for (const blocker of reviewBlockers(knownIssues)) {
      expect(blocker.owner.length).toBeGreaterThan(1);
      expect(blocker.entry.length).toBeGreaterThan(1);
      expect(blocker.reason.length).toBeGreaterThan(1);
      expect(blocker.source.length).toBeGreaterThan(1);
    }
  });

  it('tells a preparer to hand the voucher to an independent reviewer', () => {
    const [selfPrepared] = reviewBlockers(['申请人不能审核本人业务事项的凭证']);
    expect(selfPrepared.code).toBe('self_prepared');
    expect(selfPrepared.owner).toContain('独立复核');
    expect(selfPrepared.entry).toContain('移交给');
  });

  it('sends a closed period to a manager instead of the preparer', () => {
    const [closed] = reviewBlockers(['该会计期间已关闭，不可审核']);
    expect(closed.owner).toContain('会计主管');
  });

  it('keeps an unrecognised issue as a blocker without echoing it as the reason', () => {
    const [unknown] = reviewBlockers(['内部校验码 X-9137 未定义']);
    expect(unknown.code).toBe('unknown');
    expect(unknown.reason).not.toContain('X-9137');
    expect(unknown.source).toContain('X-9137');
  });

  it('keeps one blocker per code when several issues repeat a cause', () => {
    const blockers = reviewBlockers(['借贷不平衡', '借贷不平衡', '借贷不平衡']);
    expect(blockers).toHaveLength(1);
  });
});

describe('review eligibility', () => {
  it('allows confirmation only for a ready snapshot with a granted book', () => {
    const value = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot([]),
    });
    expect(value.state).toBe('ready');
    expect(value.canConfirm).toBe(true);
    expect(value.blockers).toEqual([]);
  });

  it('never enables a high-risk action while the authorization is unknown', () => {
    const value = reviewEligibility({ authorization: 'unknown', snapshot: snapshot([]) });
    expect(value.state).toBe('unknown');
    expect(value.canConfirm).toBe(false);
    expect(value.headline).toContain('尚未确认');
  });

  it('denies a book that returned 403 even when the account can review elsewhere', () => {
    const value = reviewEligibility({ authorization: 'denied', snapshot: snapshot([]) });
    expect(value.state).toBe('denied');
    expect(value.canConfirm).toBe(false);
    expect(value.nextOwner).toContain('管理员');
  });

  it('blocks confirmation while a server issue is unresolved', () => {
    const value = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot(['借贷不平衡'], false),
    });
    expect(value.state).toBe('blocked');
    expect(value.canConfirm).toBe(false);
    expect(value.blockers[0].code).toBe('unbalanced');
    expect(value.headline).toContain('1');
  });

  it('blocks confirmation when the preview has expired', () => {
    const value = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot([]),
      previewExpired: true,
    });
    expect(value.state).toBe('expired');
    expect(value.canConfirm).toBe(false);
    expect(value.nextEntry).toContain('重新预览');
  });

  it('blocks confirmation when the draft changed after the user loaded it', () => {
    const value = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot([]),
      knownVersion: 2,
    });
    expect(value.state).toBe('stale');
    expect(value.canConfirm).toBe(false);
    expect(value.nextEntry).toContain('重新读取');
  });

  it('reports an unreadable snapshot as unknown rather than as ready', () => {
    const value = reviewEligibility({ authorization: 'granted', snapshot: null });
    expect(value.state).toBe('unknown');
    expect(value.canConfirm).toBe(false);
  });
});

describe('复核资格按账套判定', () => {
  it('同一账号在 A 账套可复核、在 B 账套只读，判定不来自全局角色', () => {
    const inA = reviewEligibility({ authorization: 'granted', snapshot: snapshot([]) });
    const inB = reviewEligibility({ authorization: 'denied', snapshot: snapshot([]) });
    expect(inA.state).toBe('ready');
    expect(inA.canConfirm).toBe(true);
    expect(inB.state).toBe('denied');
    expect(inB.canConfirm).toBe(false);
    expect(inB.headline).not.toContain('可确认');
  });

  it('权限未知时不显示可执行的高风险操作', () => {
    const value = reviewEligibility({ authorization: 'unknown', snapshot: snapshot([]) });
    expect(value.state).toBe('unknown');
    expect(value.canConfirm).toBe(false);
    expect(value.nextEntry).toContain('重新读取');
  });

  it('本人制单与已锁期都给出下一位负责人，而不是只回显服务端原文', () => {
    const self = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot(['申请人不能审核本人业务事项的凭证']),
    });
    const closed = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot(['该会计期间已关闭，不可审核']),
    });
    expect(self.nextOwner).toContain('独立复核');
    expect(self.blockers[0].reason).not.toBe('申请人不能审核本人业务事项的凭证');
    expect(self.blockers[0].source).toBe('申请人不能审核本人业务事项的凭证');
    expect(closed.nextOwner).toContain('会计主管');
    expect(closed.nextEntry).toContain('期间');
  });

  it('预览过期发生在阻断检查之前，避免把过期预览当成可确认', () => {
    const value = reviewEligibility({
      authorization: 'granted',
      snapshot: snapshot([]),
      previewExpired: true,
    });
    expect(value.state).toBe('expired');
    expect(value.canConfirm).toBe(false);
  });
});
