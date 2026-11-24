import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import type { Session } from './types';
import './fleet-metrics.css';

type Vehicle = {
  id: string;
  external_id: string;
  name: string;
  revenue_cents: number;
  assigned_cost_cents: number;
  contribution_cents: number;
  repair_cost_cents?: number;
  insurance_cost_cents?: number;
  oa_cost_cents?: number;
  fee_batch_ids: string[];
  lease_contract_ids: string[];
  cost_batch_ids: string[];
};
type Driver = {
  id: string;
  external_id: string;
  name: string;
  billed_cents: number;
  outstanding_cents: number | null;
  collection_progress: number | null;
  aging_known: boolean;
  aging: { zero_30: number; days_31_60: number; days_61_90: number; over_90: number } | null;
};
type Fleet = {
  book_id: string;
  period: string;
  vehicles: Vehicle[];
  drivers: Driver[];
  totals: {
    revenue_cents: number;
    assigned_cost_cents: number;
    contribution_cents: number;
    repair_cost_cents?: number;
    insurance_cost_cents?: number;
    oa_cost_cents?: number;
    unallocated_cost_cents: number;
    net_profit_cents: number | null;
    net_profit_known: boolean;
    net_profit_reason: string;
    driver_billed_cents: number;
    driver_outstanding_cents: number;
  };
  checks: {
    revenue_matches_sources: boolean;
    assigned_cost_not_negative: boolean;
    aging_known_driver_count: number;
  };
};

const money = (cents: number | null) =>
  cents === null
    ? '未知'
    : `¥${(Math.abs(cents) / 100).toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
const percent = (value: number | null) =>
  value === null ? '未知' : `${(value * 100).toFixed(1)}%`;

export function FleetMetricsPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const [data, setData] = useState<Fleet | null>(null);
  const [failure, setFailure] = useState('');
  const [loading, setLoading] = useState(false);
  const loadSequence = useRef(0);

  async function load(signal?: AbortSignal) {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setFailure('');
    try {
      const result = await scheduleSubpanel(
        () =>
          api<Fleet>(
            `/reports/fleet?book_id=${encodeURIComponent(bookId)}&period=${encodeURIComponent(period)}`,
            session,
            { signal },
          ),
        signal,
        'front',
      );
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setData(result);
    } catch (error) {
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setFailure(error instanceof ApiError ? error.message : '单车贡献与驾驶员欠款读取失败。');
    } finally {
      if (sequence === loadSequence.current && !signal?.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      loadSequence.current += 1;
      controller.abort();
    };
  }, [bookId, period]);

  return (
    <section className="fleet-metrics" aria-label="单车贡献与驾驶员欠款">
      <header>
        <div>
          <p className="fleet-eyebrow">车队经营 · 单车贡献与收款</p>
          <h4>单车贡献、未分配成本与驾驶员欠款</h4>
          <p>
            收入只取已复核规费/已确认租赁；成本只取已复核车辆费用。共用费用未分配前不计算净利润。
          </p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? '读取中…' : '刷新'}
        </button>
      </header>
      {failure ? (
        <p className="fleet-error" role="alert">
          {failure}
        </p>
      ) : null}
      {!data ? (
        <p className="fleet-empty">{failure ? '没有可显示的数据。' : '读取单车数据…'}</p>
      ) : (
        <>
          <div className="fleet-summary">
            <div>
              <span>车辆收入</span>
              <strong>{money(data.totals.revenue_cents)}</strong>
            </div>
            <div>
              <span>已归属成本</span>
              <strong>{money(data.totals.assigned_cost_cents)}</strong>
            </div>
            <div>
              <span>车辆贡献</span>
              <strong>{money(data.totals.contribution_cents)}</strong>
            </div>
            <div>
              <span>未分配共用成本</span>
              <strong>{money(data.totals.unallocated_cost_cents)}</strong>
            </div>
            <div>
              <span>净利润</span>
              <strong>{money(data.totals.net_profit_cents)}</strong>
            </div>
          </div>
          <p className="fleet-note">{data.totals.net_profit_reason}</p>
          <div className="fleet-table-wrap">
            <table>
              <caption>车辆贡献（{data.period}）</caption>
              <thead>
                <tr>
                  <th>车辆</th>
                  <th>收入</th>
                  <th>已归属成本</th>
                  <th>贡献</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {data.vehicles.map((vehicle) => (
                  <tr key={vehicle.id}>
                    <th scope="row">
                      {vehicle.name}
                      <small>{vehicle.external_id}</small>
                    </th>
                    <td>{money(vehicle.revenue_cents)}</td>
                    <td>
                      {money(vehicle.assigned_cost_cents)}
                      <small className="fleet-cost-split">
                        维修 {money(vehicle.repair_cost_cents ?? 0)} · 保险{' '}
                        {money(vehicle.insurance_cost_cents ?? 0)} · OA{' '}
                        {money(vehicle.oa_cost_cents ?? 0)}
                      </small>
                    </td>
                    <td>{money(vehicle.contribution_cents)}</td>
                    <td>
                      规费 {vehicle.fee_batch_ids.length} · 租赁 {vehicle.lease_contract_ids.length}{' '}
                      · 成本 {vehicle.cost_batch_ids.length}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="fleet-table-wrap">
            <table>
              <caption>驾驶员欠款（{data.period}）</caption>
              <thead>
                <tr>
                  <th>驾驶员</th>
                  <th>应收</th>
                  <th>未收</th>
                  <th>收款进度</th>
                  <th>账龄</th>
                </tr>
              </thead>
              <tbody>
                {data.drivers.map((driver) => (
                  <tr key={driver.id}>
                    <th scope="row">
                      {driver.name}
                      <small>{driver.external_id}</small>
                    </th>
                    <td>{money(driver.billed_cents)}</td>
                    <td>{driver.aging_known ? money(driver.outstanding_cents) : '未知'}</td>
                    <td>{percent(driver.collection_progress)}</td>
                    <td>
                      {driver.aging
                        ? `0–30 ${money(driver.aging.zero_30)} · 31–60 ${money(driver.aging.days_31_60)} · 61–90 ${money(driver.aging.days_61_90)} · 90+ ${money(driver.aging.over_90)}`
                        : '没有已核定欠款明细'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="fleet-note">
            收入与来源合计{data.checks.revenue_matches_sources ? '一致' : '不一致'}； 已归属成本
            {data.checks.assigned_cost_not_negative ? '非负' : '异常'}； 可核账龄驾驶员{' '}
            {data.checks.aging_known_driver_count} 人。未知不写 0。
          </p>
        </>
      )}
    </section>
  );
}
