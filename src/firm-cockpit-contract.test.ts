import { describe, expect, it } from 'vitest';
import { parseFirmSnapshot } from './firm-cockpit-contract';

const scope = {
  firm_id: 'firm-a',
  user_id: 'owner-a',
  period: '2026-10',
  search: '',
  limit: 20,
  offset: 0,
};
const data = () => ({
  scope: { firm_id: scope.firm_id, user_id: scope.user_id, period: scope.period, search: '' },
  period: scope.period,
  summary: {
    clients: 0,
    opened: 0,
    closed: 0,
    overdue: 0,
    exceptions: 0,
    risk_clients: 0,
    projection_stale: 0,
  },
  companies: [],
  coordination: [],
  risks: [],
  stages: {},
  workload: [],
  pagination: { total: 0, limit: 20, offset: 0, has_more: false },
  assignments: {
    basis: 'active_task_assignments',
    total: 0,
    people_total: 0,
    stale: false,
    people: [],
  },
});

describe('全所进度仅接纳同一身份、月份和分页的完整响应', () => {
  it('接纳显式零和空页', () => expect(parseFirmSnapshot(data(), scope)).not.toBeNull());
  it.each(['firm_id', 'user_id', 'period', 'search'] as const)('拒绝错 %s 的响应', (field) => {
    const value = data();
    value.scope[field] = 'wrong';
    expect(parseFirmSnapshot(value, scope)).toBeNull();
  });
  it.each([true, -1, 0.5, null, '0'])('不把 %s 当作有效数量', (count) => {
    expect(
      parseFirmSnapshot({ ...data(), summary: { ...data().summary, clients: count } }, scope),
    ).toBeNull();
  });
  it('拒绝错页、缺字段和自相矛盾的下一页', () => {
    expect(
      parseFirmSnapshot({ ...data(), pagination: { ...data().pagination, offset: 20 } }, scope),
    ).toBeNull();
    expect(
      parseFirmSnapshot({ ...data(), pagination: { ...data().pagination, has_more: true } }, scope),
    ).toBeNull();
    expect(parseFirmSnapshot({ ...data(), coordination: undefined }, scope)).toBeNull();
  });
});
