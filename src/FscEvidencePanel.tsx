import { useEffect, useState } from 'react';

import { api } from './api';
import type { Session } from './types';

type Requirement = {
  action: string;
  business_type: string;
  evidence_kind: string;
  required: boolean;
  max_age_days: number | null;
};

type Policy = {
  id: string;
  company_name: string;
  industry_code: string;
  version: number;
  status: string;
  requirements: Requirement[];
  exceptions: Array<{
    evidence_kind: string;
    reason: string;
    approved_by: string;
    expires_on: string | null;
  }>;
  activated_at: string | null;
};

const ACTION: Record<string, string> = {
  receive: '收件',
  draft: '草稿',
  accrual: '暂估',
  revenue: '收入确认',
  payment: '付款准备',
  review: '独立复核',
  month_close: '月结',
  export: '导出',
};
const EVIDENCE: Record<string, string> = {
  bank_receipt: '银行回单',
  source_original: '来源原件',
  oa_approval: 'OA 审批',
  acceptance: '验收单',
  contract: '合同',
  independent_review: '独立复核记录',
};

export function FscEvidencePanel({ session }: { session: Session }) {
  const [items, setItems] = useState<Policy[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api<{ items: Policy[] }>('/fsc/evidence/active', session)
      .then((body) => {
        if (!alive) return;
        setError('');
        setItems(body.items);
        setLoaded(true);
      })
      .catch((cause) => {
        if (!alive || cause?.name === 'AbortError') return;
        setError('证据政策读取失败');
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
  if (!loaded) return <p className="fsc-org-loading">正在读取证据政策…</p>;
  return (
    <section className="fsc-evidence" aria-label="证据政策">
      <header>
        <div>
          <h2>证据政策</h2>
          <p>按办理动作列出企业已核定证据要求；例外须有依据、审批人和期限，不能当作免检开关。</p>
        </div>
        <strong>{items.length} 份生效政策</strong>
      </header>
      {items.length === 0 ? (
        <p className="fsc-evidence-empty">尚无生效证据政策。未核定政策的办理保持阻断。</p>
      ) : (
        <div className="fsc-evidence-grid">
          {items.map((item) => (
            <article key={item.id}>
              <header>
                <strong>{item.company_name}</strong>
                <span>
                  {item.industry_code} · v{item.version}
                </span>
              </header>
              <ul>
                {item.requirements.map((req, index) => (
                  <li key={`${req.action}-${req.evidence_kind}-${index}`}>
                    <span>
                      {ACTION[req.action] ?? req.action} ·{' '}
                      {EVIDENCE[req.evidence_kind] ?? req.evidence_kind}
                    </span>
                    <small>
                      {req.required ? '必需' : '可选'}
                      {req.max_age_days ? ` · ${req.max_age_days} 天内` : ''}
                    </small>
                  </li>
                ))}
              </ul>
              {item.exceptions.length > 0 && (
                <p>
                  合法例外 {item.exceptions.length} 项：
                  {item.exceptions.map((grant) => grant.reason).join('；')}
                </p>
              )}
              <footer>
                生效{' '}
                {item.activated_at ? item.activated_at.slice(0, 16).replace('T', ' ') : '未记录'}
              </footer>
            </article>
          ))}
        </div>
      )}
      <p className="fsc-evidence-note">
        只列政策，不把附件当作审批通过；实际办理仍由权威服务按对象、期间和当前版本判断。
      </p>
    </section>
  );
}
