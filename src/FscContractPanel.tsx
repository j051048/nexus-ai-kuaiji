import { useEffect, useState } from 'react';

import { api } from './api';
import type { Session } from './types';

type Contract = {
  id: string;
  company_id: string;
  company_name?: string;
  industry_code: string;
  version: number;
  status: string;
  processor_code: string;
  capabilities: string[];
  evidence_policy: Record<string, unknown>;
  activated_at: string | null;
};

type Body = { items: Contract[] };
type CapabilityRow = {
  company_id: string;
  company_name: string;
  industry: string | null;
  profile_version: number | null;
  contract_version: number | null;
  processor_code: string | null;
  enabled: string[];
  unsupported: string[];
  reason: string;
};
type Matrix = {
  catalog: string[];
  items: CapabilityRow[];
  summary: { companies: number; with_confirmed_profile: number; with_active_contract: number };
};

const CAPABILITY: Record<string, string> = {
  taxi_fee_import: '规费导入',
  taxi_bank_allocation: '到账核销',
  advertising_project_import: '广告项目导入',
  advertising_revenue_draft: '广告收入草稿',
  property_service_billing: '物业分期',
  trade_inventory_match: '库存匹配',
  hospital_department_summary: '医院科室汇总',
};

export function FscContractPanel({ session }: { session: Session }) {
  const [items, setItems] = useState<Contract[]>([]);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    Promise.all([
      api<Body>('/fsc/contracts/active', session),
      api<Matrix>('/fsc/contracts/matrix', session),
    ])
      .then(([body, capability]) => {
        if (!alive) return;
        setError('');
        setItems(body.items);
        setMatrix(capability);
        setLoaded(true);
      })
      .catch((cause) => {
        if (!alive || cause?.name === 'AbortError') return;
        setError('行业契约读取失败');
      });
    return () => {
      alive = false;
    };
  }, [session]);

  if (error)
    return (
      <p className="fsc-org-error" role="alert">
        {error}
      </p>
    );
  if (!loaded) return <p className="fsc-org-loading">正在读取行业契约…</p>;
  return (
    <section className="fsc-contract" aria-label="行业契约">
      <header>
        <div>
          <h2>行业契约</h2>
          <p>
            只列已生效配置：说明允许的源、处理器和证据政策，不执行任意代码；变更须重新独立确认。
          </p>
        </div>
        <strong>{items.length} 份生效契约</strong>
      </header>
      {items.length === 0 ? (
        <p className="fsc-contract-empty">尚无已生效的行业契约。未确认的草稿不会驱动业务。</p>
      ) : (
        <div className="fsc-contract-table">
          <div className="fsc-contract-head">
            <span>企业</span>
            <span>行业 / 版本</span>
            <span>处理器</span>
            <span>已启用能力</span>
            <span>生效时间</span>
          </div>
          {items.map((item) => (
            <div className="fsc-contract-row" key={item.id}>
              <strong>{item.company_name ?? item.company_id}</strong>
              <span>
                {item.industry_code} · v{item.version}
              </span>
              <span>{item.processor_code}</span>
              <span>
                {item.capabilities.map((code) => CAPABILITY[code] ?? code).join('、') ||
                  '仅登记，未启用'}
              </span>
              <span>
                {item.activated_at ? item.activated_at.slice(0, 16).replace('T', ' ') : '未记录'}
              </span>
            </div>
          ))}
        </div>
      )}
      {matrix && (
        <div className="fsc-capability-grid" aria-label="五行业能力矩阵">
          {matrix.items.map((item) => (
            <article key={item.company_id}>
              <header>
                <strong>{item.company_name}</strong>
                <span>
                  {item.industry ?? '行业未登记'} ·{' '}
                  {item.contract_version ? `契约 v${item.contract_version}` : '未生效'}
                </span>
              </header>
              <div className="fsc-capability-chips">
                {matrix.catalog.map((code) => {
                  const on = item.enabled.includes(code);
                  return (
                    <span
                      className={on ? 'on' : 'off'}
                      key={code}
                      title={on ? '已生效契约启用' : item.reason || '未在生效契约中'}
                    >
                      {CAPABILITY[code] ?? code}
                      {on ? '' : ' · 未启用'}
                    </span>
                  );
                })}
              </div>
              <p>{item.reason || `处理器 ${item.processor_code}；未列出的能力保持关闭。`}</p>
            </article>
          ))}
        </div>
      )}
      <p className="fsc-contract-note">
        契约是配置证据，不是执行脚本；未在清单中的能力保持未启用。企业档案未确认或契约未生效时，能力不会被推断为可用。
      </p>
    </section>
  );
}
