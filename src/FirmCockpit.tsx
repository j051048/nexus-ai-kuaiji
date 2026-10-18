import { useEffect, useState } from 'react';
import { ArrowRight, Building2, RefreshCw, ShieldAlert, Users } from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, Spinner } from './AppPrimitives';
import type { Session } from './types';
import './cockpit.css';

type CompanyRow = {
  company_id: string;
  company_name: string;
  book_id: string | null;
  period: string;
  period_status: string;
  stage: 'unopened' | 'intake' | 'accounting' | 'review' | 'delivery' | 'closed';
  tasks: { intake: number; review: number; exception: number; delivery: number; overdue: number };
  lead_user_id: string | null;
  lead_name: string;
  reviewer_name: string;
  due_on: string;
  risk: number;
};

type Cockpit = {
  period: string;
  summary: {
    clients: number;
    opened: number;
    closed: number;
    overdue: number;
    exceptions: number;
    risk_clients: number;
    projection_stale: number;
  };
  stages: Record<string, number>;
  companies: CompanyRow[];
  workload: {
    user_id: string | null;
    name: string;
    clients: number;
    pending: number;
    exceptions: number;
    overdue: number;
    risk_clients: number;
  }[];
  risks: CompanyRow[];
};

const stageLabels: Record<CompanyRow['stage'], string> = {
  unopened: '未建账',
  intake: '资料与流水',
  accounting: '做账中',
  review: '复核异常',
  delivery: '交付核验',
  closed: '已锁期',
};

export function FirmCockpit({
  session,
  initialPeriod,
  onOpenBook,
  onOpenInbox,
}: {
  session: Session;
  initialPeriod?: string;
  onOpenBook: (target: { bookId: string; companyId: string; period: string }) => void;
  onOpenInbox: () => void;
}) {
  const [period, setPeriod] = useState(initialPeriod || new Date().toISOString().slice(0, 7));
  const [data, setData] = useState<Cockpit | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<Cockpit>('/firm/cockpit?' + new URLSearchParams({ period }).toString(), session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [session, period, revision]);

  return (
    <main className="cockpit">
      <header className="cockpit-heading">
        <div>
          <p className="eyebrow">FIRM OPERATIONS / ONE TRUTH</p>
          <h1>事务所运营驾驶舱</h1>
          <p>先看风险、负载和交付阻塞，再进入具体客户账套。</p>
        </div>
        <div className="cockpit-actions">
          <button className="button primary" type="button" onClick={onOpenInbox}>
            <ShieldAlert size={15} />
            打开异常收件箱
          </button>
          <label>
            <span>会计期间</span>
            <input
              type="month"
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
            />
          </label>
          <button
            className="button secondary"
            type="button"
            disabled={loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>

      {error ? (
        <div className="inline-error" role="alert">
          {error}
        </div>
      ) : loading && !data ? (
        <div className="loading-block">
          <Spinner />
          正在汇总全所客户进度…
        </div>
      ) : data ? (
        <>
          <section className="cockpit-summary" aria-label="全所运营概览">
            <article>
              <Building2 size={18} />
              <span>客户企业</span>
              <strong>{data.summary.clients}</strong>
              <small>{data.summary.opened} 家已建当月账套</small>
            </article>
            <article>
              <ShieldAlert size={18} />
              <span>风险客户</span>
              <strong>{data.summary.risk_clients}</strong>
              <small>{data.summary.exceptions} 项异常待处理</small>
            </article>
            <article>
              <span>逾期任务</span>
              <strong>{data.summary.overdue}</strong>
              <small>按截止日期实时汇总</small>
            </article>
            <article>
              <span>已完成锁期</span>
              <strong>{data.summary.closed}</strong>
              <small>当前期间已完成月结</small>
            </article>
          </section>
          {data.summary.projection_stale > 0 && (
            <p className="cockpit-notice">
              当前有 {data.summary.projection_stale}{' '}
              家企业的待办投影仍在刷新，任务数量可能短暂回退到实时口径。
            </p>
          )}
          <section className="cockpit-stage-strip" aria-label="客户阶段分布">
            {Object.entries(stageLabels).map(([key, label]) => (
              <div key={key}>
                <span>{label}</span>
                <strong>{data.stages[key] ?? 0}</strong>
              </div>
            ))}
          </section>

          <div className="cockpit-grid">
            <section className="cockpit-panel cockpit-matrix">
              <header>
                <div>
                  <h2>客户进度与风险</h2>
                  <p>按风险从高到低展示，直接进入账套处理。</p>
                </div>
              </header>
              <div className="cockpit-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>客户企业</th>
                      <th>当前阶段</th>
                      <th>待办</th>
                      <th>会计</th>
                      <th>风险</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {data.companies
                      .slice()
                      .sort((left, right) => right.risk - left.risk)
                      .map((row) => (
                        <tr key={row.company_id}>
                          <td>
                            <strong>{row.company_name}</strong>
                            <span>
                              {row.period || period} · {row.period_status}
                            </span>
                          </td>
                          <td>
                            <span className={'cockpit-stage ' + row.stage}>
                              {stageLabels[row.stage]}
                            </span>
                          </td>
                          <td>
                            {row.tasks.intake +
                              row.tasks.review +
                              row.tasks.exception +
                              row.tasks.delivery}
                            {row.tasks.overdue > 0 && (
                              <small className="danger">逾期 {row.tasks.overdue}</small>
                            )}
                          </td>
                          <td>{row.lead_name || '未分配'}</td>
                          <td>
                            <span className={row.risk >= 4 ? 'danger' : ''}>{row.risk}</span>
                          </td>
                          <td>
                            <button
                              className="text-button"
                              type="button"
                              disabled={!row.book_id}
                              onClick={() =>
                                row.book_id &&
                                onOpenBook({
                                  bookId: row.book_id,
                                  companyId: row.company_id,
                                  period: row.period,
                                })
                              }
                            >
                              进入账套
                              <ArrowRight size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {!data.companies.length && (
                  <EmptyState title="当前期间没有客户">请先完成客户开通和账套创建。</EmptyState>
                )}
              </div>
            </section>

            <aside className="cockpit-side">
              <section className="cockpit-panel">
                <header>
                  <div>
                    <h2>人员负载</h2>
                    <p>按主办会计聚合当前客户和未结任务。</p>
                  </div>
                  <Users size={18} />
                </header>
                <div className="cockpit-workload">
                  {data.workload.map((row) => (
                    <article key={row.user_id ?? 'unassigned'}>
                      <strong>{row.name}</strong>
                      <span>
                        {row.clients} 家客户 · {row.pending} 项待办
                      </span>
                      <small>
                        {row.exceptions} 项异常 · {row.overdue} 项逾期 · {row.risk_clients} 家风险
                      </small>
                    </article>
                  ))}
                </div>
              </section>
              <section className="cockpit-panel cockpit-risks">
                <header>
                  <div>
                    <h2>优先关注</h2>
                    <p>异常、逾期和未建账客户优先。</p>
                  </div>
                </header>
                {data.risks
                  .filter((row) => row.risk > 0)
                  .slice(0, 8)
                  .map((row) => (
                    <article key={row.company_id}>
                      <strong>{row.company_name}</strong>
                      <span>
                        {stageLabels[row.stage]} · 风险分 {row.risk}
                      </span>
                      <small>
                        {row.tasks.exception} 项异常 · {row.tasks.overdue} 项逾期
                      </small>
                    </article>
                  ))}
                {!data.risks.some((row) => row.risk > 0) && (
                  <p className="field-help">当前没有需要特别关注的客户。</p>
                )}
              </section>
            </aside>
          </div>
        </>
      ) : null}
    </main>
  );
}
