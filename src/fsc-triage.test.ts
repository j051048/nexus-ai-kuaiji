import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TRIAGE_POLICY,
  attachOaRef,
  classifyReviewDraft,
  classifyTaskLane,
  employeeProgressSteps,
  parseOaRef,
} from './fsc-triage';

describe('第三期：绿通合规分流、高风险红线拦截与原办公审批衔接', () => {
  it('小额无异常报销与复核单进入绿通合规包', () => {
    const decision = classifyTaskLane({
      kind: 'review',
      state: 'draft',
      bucket: 'action',
      overdue: false,
      amount: '1280.00',
      title: '车队常规办公耗材采购',
    });
    expect(decision.lane).toBe('green_pass');
    expect(decision.badge).toBe('绿通合规包');
  });

  it('超过金额红线、逾期或命中跨期/首次账号关键词的任务强制进入高风险拦截', () => {
    const overLimit = classifyTaskLane(
      {
        kind: 'review',
        state: 'draft',
        bucket: 'action',
        overdue: false,
        amount: '18500.00',
        title: '媒介投放首付款',
      },
      { ...DEFAULT_TRIAGE_POLICY, greenMaxYuan: 5000 },
    );
    expect(overLimit.lane).toBe('high_risk');
    expect(overLimit.reasons[0]).toContain('超过绿通上限');

    const crossPeriod = classifyTaskLane({
      kind: 'business',
      state: 'submitted',
      bucket: 'action',
      overdue: false,
      amount: '600.00',
      title: '去年跨期发票补报',
    });
    expect(crossPeriod.lane).toBe('high_risk');
    expect(crossPeriod.reasons.some((r) => r.includes('跨期'))).toBe(true);
  });

  it('批量复核台精准识别绿通可批审凭证与红线拦截凭证', () => {
    expect(
      classifyReviewDraft({
        amount: '860.00',
        issue_count: 0,
        risk_level: 'low',
        description: '日常维修费',
        has_evidence: true,
      }).lane,
    ).toBe('green_pass');

    const blocked = classifyReviewDraft({
      amount: '860.00',
      issue_count: 2,
      risk_level: 'high',
      description: '缺失原件凭证',
      has_evidence: false,
    });
    expect(blocked.lane).toBe('high_risk');
    expect(blocked.reasons.length).toBeGreaterThanOrEqual(2);
  });

  it('金额缺失或格式非法时严禁进入绿通，合法零值、正常金额与超额分别归类准确', () => {
    const base = {
      kind: 'review' as const,
      state: 'draft',
      bucket: 'action' as const,
      overdue: false,
      title: '常规费用核对',
    };
    const missing = classifyTaskLane({ ...base, amount: null });
    expect(missing.lane).not.toBe('green_pass');
    expect(missing.badge).toBe('金额待核对');
    expect(missing.reasons[0]).toContain('打开原件');

    const emptyStr = classifyTaskLane({ ...base, amount: '   ' });
    expect(emptyStr.lane).not.toBe('green_pass');
    expect(emptyStr.badge).toBe('金额待核对');

    const invalid = classifyTaskLane({ ...base, amount: '未知' });
    expect(invalid.lane).not.toBe('green_pass');
    expect(invalid.badge).toBe('金额待核对');

    const zero = classifyTaskLane({ ...base, amount: '0.00' });
    expect(zero.lane).toBe('green_pass');

    const small = classifyTaskLane({ ...base, amount: '12' });
    expect(small.lane).toBe('green_pass');

    const over = classifyTaskLane({ ...base, amount: '5,001' });
    expect(over.lane).toBe('high_risk');

    const draftUnknown = classifyReviewDraft({
      amount: '未知',
      issue_count: 0,
      risk_level: 'low',
      description: '常规凭证',
      has_evidence: true,
    });
    expect(draftUnknown.lane).not.toBe('green_pass');
    expect(draftUnknown.badge).toBe('金额待核对');
  });

  it('原办公审批单号可无损附加与提取', () => {
    const attached = attachOaRef('给车队采购防冻液', 'OA-202611-0091');
    expect(attached).toBe('[原办公审批单号: OA-202611-0091] 给车队采购防冻液');
    expect(parseOaRef(attached)).toEqual({
      oaRef: 'OA-202611-0091',
      cleanText: '给车队采购防冻液',
    });
    // 覆盖旧单号不重复叠加
    const replaced = attachOaRef(attached, 'OA-202611-0099');
    expect(parseOaRef(replaced).oaRef).toBe('OA-202611-0099');
    expect(parseOaRef(replaced).cleanText).toBe('给车队采购防冻液');
  });

  it('快递物流式进度条准确表达正常流转与退回补件状态', () => {
    const returned = employeeProgressSteps('returned');
    expect(returned[1]).toEqual({ label: '退回待补件', status: 'returned' });
    const handed = employeeProgressSteps('handed_off');
    expect(handed[2].status).toBe('current');
    const paid = employeeProgressSteps('exported');
    expect(paid.every((s) => s.status === 'done')).toBe(true);
  });
});
