import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { OperationsBudgetSummary, OperationsPricingRecord } from './OperationsWorkbench';
import {
  budgetPercentage,
  canReconcileOperatingCost,
  currentOperationsPeriod,
  formatOperatingCost,
  formatTokenCount,
  normalizeOperatingAmount,
  normalizePolicyRequest,
  policyFromOverview,
  validateCostReconciliation,
  validatePolicyInput,
} from './operations-helpers';
import type { OperationsCost, OperationsOverview, OperationsPolicy } from './operations-types';

const policy: OperationsPolicy = {
  quota_enabled: true,
  monthly_limit: '100.00',
  ocr_reserve: '1.00',
  proposal_reserve: '2.00',
  failure_threshold: 3,
  failure_window_minutes: 15,
  backlog_threshold: 20,
  backlog_age_minutes: 30,
  queue_wait_minutes: 20,
  worker_stale_seconds: 60,
  version: 4,
  can_manage: true,
};
const cost: OperationsCost = {
  id: 'cost-a',
  kind: 'ocr',
  model: 'configured-model',
  state: 'uncertain',
  reserved_cost: '1.000000',
  estimated_cost: null,
  confirmed_cost: null,
  input_tokens: null,
  output_tokens: null,
  created_at: '2026-10-12T01:00:00Z',
  version: 2,
  reason: null,
  can_reconcile: true,
};
const overview: OperationsOverview = {
  company: { id: 'company-a', name: '企业甲' },
  period: '2026-10',
  currency: 'CNY',
  policy,
  pricing: {
    ocr: { configured: false, input_per_million: null, output_per_million: null, model: '' },
    proposal: { configured: false, input_per_million: null, output_per_million: null, model: '' },
  },
  usage: {
    requests: 4,
    input_tokens: null,
    output_tokens: null,
    reported_requests: 2,
    uncertain_requests: 1,
    reserved_cost: '1.000000',
    carried_reserved_cost: '0.000000',
    estimated_cost: '0.123456',
    confirmed_cost: null,
    exposure: '1.123456',
    available: '98.876544',
    overrun_count: 0,
  },
  queue: {
    queued: 0,
    running: 0,
    retry_wait: 0,
    succeeded: 2,
    failed: 1,
    interrupted: 1,
    stale: 0,
    cancelled: 0,
    recent_failures: 1,
    oldest_pending_at: null,
  },
  exceptions: { events: 0, blocking: 0, warning: 0, oldest_opened_at: null, updated_at: null },
  workers: { available: false, last_heartbeat_at: null },
  alerts: [],
  costs: [cost],
  can_manage_budget: true,
};
describe('模型费用精度与未知用量', () => {
  it.each([null, undefined, 'NaN', '1e-6', '1.1234567'])(
    '未知或不合约值 %s 显示待核定而非零',
    (value) => expect(formatOperatingCost(value)).toBe('待核定'),
  );
  it('完整保留六位小数与大额低位，不经过浮点计算', () => {
    expect(formatOperatingCost('0.000001')).toBe('¥ 0.000001');
    expect(formatOperatingCost('999999999999.123456')).toBe('¥ 999,999,999,999.123456');
    expect(formatOperatingCost('0.00')).toBe('¥ 0.000000');
    expect(formatOperatingCost('-0.000001')).toBe('¥ -0.000001');
  });
  it('供应商缺少用量不作零，明确的零值正常显示', () => {
    expect(formatTokenCount(null)).toBe('未返回');
    expect(formatTokenCount(undefined)).toBe('未返回');
    expect(formatTokenCount(0)).toBe('0');
    expect(formatTokenCount(123456)).toBe('123,456');
    expect(formatTokenCount(Number.NaN)).toBe('未返回');
    expect(formatTokenCount(Number.MAX_SAFE_INTEGER + 1)).toBe('未返回');
  });
  it('费用概览分别列示估算、核定和预留，不宣称账单已结算', () => {
    const html = renderToStaticMarkup(createElement(OperationsBudgetSummary, { overview }));
    expect(html).toContain('¥ 0.123456');
    expect(html).toContain('账单人工核定');
    expect(html).toContain('待核定');
    expect(html).toContain('预留不代表实际结算金额');
    expect(html).not.toContain('账单已结算');
  });
  it('报价缺失保持等待管理员配置，不提供假定价格或汇率', () => {
    const html = renderToStaticMarkup(
      createElement(OperationsPricingRecord, {
        pricing: overview.pricing.ocr,
        label: '票据识别模型',
      }),
    );
    expect(html).toContain('等待管理员配置报价');
    expect(html).toContain('待核定');
    expect(html).not.toContain('¥ 0.000000');
    expect(html).not.toContain('USD');
  });
});
describe('额度策略与版本', () => {
  it('金额占用通过精确整数计算，额度关闭或未知占用不显示零进度', () => {
    expect(
      budgetPercentage({
        policy: { ...policy, monthly_limit: '0.30' },
        usage: { ...overview.usage, exposure: '0.29' },
      }),
    ).toBe(96.66);
    expect(
      budgetPercentage({ policy: { ...policy, quota_enabled: false }, usage: overview.usage }),
    ).toBe(null);
    expect(budgetPercentage({ policy, usage: { ...overview.usage, exposure: null } })).toBe(null);
    expect(
      budgetPercentage({ policy: { ...policy, monthly_limit: '0.00' }, usage: overview.usage }),
    ).toBe(null);
  });
  it('保留超额含义，进度条限制100而不是把费用算成已核定', () => {
    const exceeded = {
      ...overview,
      usage: { ...overview.usage, exposure: '100.000001', overrun_count: 1 },
    };
    expect(budgetPercentage(exceeded)).toBe(100);
    const html = renderToStaticMarkup(
      createElement(OperationsBudgetSummary, {
        overview: { ...exceeded, usage: { ...exceeded.usage, exposure: '200.000000' } },
      }),
    );
    expect(html).toContain('1 次超额记录');
    expect(html).toContain('value="100"');
  });
  it('表单转换保留expected_version，六位金额仅移除确切的尾零', () => {
    const input = policyFromOverview({
      ...policy,
      monthly_limit: '100.000000',
      ocr_reserve: '0.010000',
    });
    expect(input.expected_version).toBe(4);
    expect(input.monthly_limit).toBe('100.00');
    expect(input.ocr_reserve).toBe('0.01');
    expect(input).not.toHaveProperty('can_manage');
  });
  it('请求按两位金额契约规范化，保留分而不做浮点四舍五入', () => {
    expect(normalizeOperatingAmount('0.8')).toBe('0.80');
    expect(normalizeOperatingAmount('01')).toBe('1.00');
    expect(normalizeOperatingAmount('999999999999.99')).toBe('999999999999.99');
    expect(
      normalizePolicyRequest({ ...policyFromOverview(policy), monthly_limit: '100' }).monthly_limit,
    ).toBe('100.00');
    expect(() => normalizeOperatingAmount('0.001')).toThrow();
  });
  it.each(['monthly_limit', 'ocr_reserve', 'proposal_reserve'] as const)(
    '开启额度后 %s 不能是零',
    (key) => {
      expect(validatePolicyInput({ ...policyFromOverview(policy), [key]: '0.00' })).toContain(
        '必须大于零',
      );
      expect(
        validatePolicyInput({ ...policyFromOverview(policy), quota_enabled: false, [key]: '0.00' }),
      ).toBe('');
    },
  );
  it('拒绝过精度、负金额以及无效阈值，金额单价不从模型输出填写', () => {
    expect(
      validatePolicyInput({ ...policyFromOverview(policy), monthly_limit: '1.001' }),
    ).toContain('两位小数');
    expect(validatePolicyInput({ ...policyFromOverview(policy), ocr_reserve: '-1.00' })).toContain(
      '非负金额',
    );
    expect(validatePolicyInput({ ...policyFromOverview(policy), failure_threshold: 0 })).toContain(
      '大于零的整数',
    );
    expect(
      validatePolicyInput({ ...policyFromOverview(policy), worker_stale_seconds: Number.NaN }),
    ).toContain('大于零的整数');
    expect(
      validatePolicyInput({ ...policyFromOverview(policy), worker_stale_seconds: 29 }),
    ).toContain('30–3600');
    expect(
      validatePolicyInput({ ...policyFromOverview(policy), backlog_threshold: 1001 }),
    ).toContain('1–1000');
  });
});
describe('费用核定与权限', () => {
  it.each(['reserved', 'sent', 'released', 'unknown'])(
    '禁止提前或越状态核定 %s，避免释放正在处理的预留',
    (state) => expect(canReconcileOperatingCost({ ...cost, state }, true)).toBe(false),
  );
  it.each(['uncertain', 'estimated', 'reconciled'])(
    '只允许有明确管理权限与行级核定权限的人处理 %s',
    (state) => {
      expect(canReconcileOperatingCost({ ...cost, state }, true)).toBe(true);
      expect(canReconcileOperatingCost({ ...cost, state }, false)).toBe(false);
      expect(canReconcileOperatingCost({ ...cost, state, can_reconcile: false }, true)).toBe(false);
      expect(canReconcileOperatingCost({ ...cost, state, can_reconcile: undefined }, true)).toBe(
        false,
      );
    },
  );
  it('允许账单明确零费用，但未知、不带依据和超过两位精度不能保存', () => {
    expect(validateCostReconciliation('0.00', '供应商账单明确未收费')).toBe('');
    expect(validateCostReconciliation('', '账单待核对')).toContain('非负金额');
    expect(validateCostReconciliation('1.25', ' ')).toContain('核对依据');
    expect(validateCostReconciliation('1.001', '账单已核对')).toContain('两位小数');
  });
  it('月份按上海时区确定，月末不使用宿主的UTC月份', () => {
    expect(currentOperationsPeriod(new Date('2026-09-30T16:30:00Z'))).toBe('2026-10');
  });
});
