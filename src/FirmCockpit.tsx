import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Building2, CalendarDays, RefreshCw, ShieldAlert, Users } from 'lucide-react';
import { useMonthlyData } from './useMonthlyData';
import { parseFirmSnapshot } from './firm-cockpit-contract';
import { EmptyState, Spinner } from './AppPrimitives';
import type { Session } from './types';
import {
  coordinationReasons,
  dueLabel,
  firmStageLabels,
  selectCoordinationClients,
  type FirmClient,
  type FirmSnapshot,
} from './firm-cockpit-focus';
import './cockpit.css';
import './workbench-focus.css';

type Actions = {
  onOpenBook: (target: { bookId: string; companyId: string; period: string }) => void;
  onOpenInbox: () => void;
  onMonthly?: (period: string) => void;
  onTeam?: () => void;
  onOnboarding?: (period: string) => void;
};

function ClientProgress({
  row,
  onOpenBook,
  onOnboarding,
  onMonthly,
}: { row: FirmClient } & Pick<Actions, 'onOpenBook' | 'onOnboarding' | 'onMonthly'>) {
  const reasons = coordinationReasons(row);
  return (
    <article className={`cockpit-client${reasons.length ? ' needs-coordination' : ''}`}>
      <div className="cockpit-client-main">
        <strong>{row.company_name}</strong>
        <span className={'cockpit-stage ' + row.stage}>{firmStageLabels[row.stage]}</span>
      </div>
      <p className="cockpit-client-owners">
        <span>{row.period || '所选月份'} 服务单</span>
        <span>主办：{row.lead_name || '主办会计待安排'}</span>
        <span>期限：{dueLabel(row.due_on)}</span>
      </p>
      {reasons.length > 0 && (
        <p className="cockpit-client-reason">
          <strong>需协调：</strong>
          {reasons.join('；')}
        </p>
      )}
      {row.book_id ? (
        <button
          className="text-button"
          type="button"
          onClick={() =>
            onOpenBook({ bookId: row.book_id!, companyId: row.company_id, period: row.period })
          }
          aria-label={`查看${row.company_name}本月工作`}
        >
          查看本月工作 <ArrowRight size={14} />
        </button>
      ) : (row.book_count ?? 0) > 1 ? (
        onMonthly ? (
          <button className="text-button" type="button" onClick={() => onMonthly(row.period)}>
            选择本月账套
          </button>
        ) : (
          <span className="field-help">请在客户进度中选择账套</span>
        )
      ) : onOnboarding ? (
        <button className="text-button" type="button" onClick={() => onOnboarding(row.period)}>
          安排客户开通 <ArrowRight size={14} />
        </button>
      ) : (
        <span className="field-help">请管理员完成本月开通</span>
      )}
    </article>
  );
}

export function FirmCockpitContent({
  data,
  onOpenBook,
  onOpenInbox,
  onMonthly,
  onTeam,
  onOnboarding,
  pageControls,
  searchControls,
  detailsOpen,
  onDetailsChange,
}: {
  data: FirmSnapshot;
  pageControls?: ReactNode;
  searchControls?: ReactNode;
  detailsOpen?: boolean;
  onDetailsChange?: (value: boolean) => void;
} & Actions) {
  const coordination = data.coordination ?? selectCoordinationClients(data.companies);
  return (
    <>
      <section className="cockpit-summary" aria-label="全所运营概览">
        <header>
          <div>
            <h2>本月服务进度</h2>
            <p>先确认每家客户的进度，再协调负责人和阻塞事项。</p>
          </div>
          {onMonthly && (
            <button
              className={`button ${data.summary.clients ? 'primary' : 'secondary'}`}
              type="button"
              onClick={() => onMonthly(data.period)}
            >
              客户进度 <ArrowRight size={15} />
            </button>
          )}
        </header>
        <div className="cockpit-summary-values">
          <article>
            <span>服务客户</span>
            <strong>{data.summary.clients}</strong>
            <small>{data.summary.opened} 家已开通本月账套</small>
          </article>
          <article>
            <span>待完成月结</span>
            <strong>{Math.max(0, data.summary.clients - data.summary.closed)}</strong>
            <small>按当月账套状态统计</small>
          </article>
          <article>
            <span>已完成月结</span>
            <strong>{data.summary.closed}</strong>
            <small>交付结果请在客户进度中确认</small>
          </article>
        </div>
      </section>

      {!data.summary.clients ? (
        <section className="cockpit-panel cockpit-first-client">
          <Building2 size={28} aria-hidden="true" />
          <h2>从第一家客户开始</h2>
          <p>完成企业开通后，再安排主办会计、复核人和本月期限。</p>
          {onOnboarding ? (
            <button
              className="button primary"
              type="button"
              onClick={() => onOnboarding(data.period)}
            >
              新增客户 <ArrowRight size={15} />
            </button>
          ) : (
            <p className="field-help">请联系管理员完成客户开通。</p>
          )}
        </section>
      ) : (
        <div className="cockpit-grid">
          <section className="cockpit-panel cockpit-coordination">
            <header>
              <div>
                <h2>需要你协调</h2>
                <p>企业当前未结事项，可能跨月；逾期、异常和分工待安排的客户优先。</p>
              </div>
              <ShieldAlert size={18} aria-hidden="true" />
            </header>
            {coordination.length ? (
              <div className="cockpit-client-list">
                {coordination.slice(0, 3).map((row) => (
                  <ClientProgress
                    key={row.company_id}
                    row={row}
                    onOpenBook={onOpenBook}
                    onOnboarding={onOnboarding}
                    onMonthly={onMonthly}
                  />
                ))}
                {coordination.length > 3 && (
                  <details className="cockpit-more-team">
                    <summary>其余 {coordination.length - 3} 家需协调客户</summary>
                    {coordination.slice(3).map((row) => (
                      <ClientProgress
                        key={row.company_id}
                        row={row}
                        onOpenBook={onOpenBook}
                        onOnboarding={onOnboarding}
                        onMonthly={onMonthly}
                      />
                    ))}
                  </details>
                )}
              </div>
            ) : (
              <EmptyState title="目前没有需要你协调的事项">
                可以查看客户进度，了解各家企业本月的办理情况。
              </EmptyState>
            )}
            <footer className="cockpit-panel-footer">
              <button className="text-button" type="button" onClick={onOpenInbox}>
                查看待处理问题 <ArrowRight size={14} />
              </button>
            </footer>
          </section>

          <section className="cockpit-panel cockpit-team">
            <header>
              <div>
                <h2>人员安排</h2>
                <p>按所选月份主办会计归组，显示客户合计待办；可能跨月，不是个人指派量。</p>
              </div>
              <Users size={18} aria-hidden="true" />
            </header>
            <div className="cockpit-workload">
              {data.workload.slice(0, 5).map((row) => (
                <article key={row.user_id ?? 'unassigned'}>
                  <strong>{row.user_id ? row.name : '主办会计待安排'}</strong>
                  <span>
                    {row.clients} 家客户 · 客户合计待办 {row.pending} 项
                  </span>
                  {row.overdue > 0 && <small className="danger">{row.overdue} 项待办已逾期</small>}
                </article>
              ))}
              {!data.workload.length && (
                <p className="cockpit-panel-help">尚未确认人员分工，请先为客户安排主办会计。</p>
              )}
            </div>
            {onTeam && (
              <footer className="cockpit-panel-footer">
                <button className="text-button" type="button" onClick={onTeam}>
                  人员管理 <ArrowRight size={14} />
                </button>
              </footer>
            )}
            {data.workload.length > 5 && (
              <details className="cockpit-more-team">
                <summary>查看其余人员分工</summary>
                <div className="cockpit-workload">
                  {data.workload.slice(5).map((row) => (
                    <article key={row.user_id ?? 'unassigned'}>
                      <strong>{row.user_id ? row.name : '主办会计待安排'}</strong>
                      <span>
                        {row.clients} 家客户 · 客户合计待办 {row.pending} 项
                      </span>
                    </article>
                  ))}
                </div>
              </details>
            )}
            {data.assignments && (
              <details className="cockpit-more-team">
                <summary>实际任务指派 · {data.assignments.total} 项</summary>
                <p className="field-help">
                  仅统计仍在待办中的指派，不代表已处理，也不授予操作权限。
                  {data.assignments.stale ? '部分待办正在更新，请进入待办确认。' : ''}
                </p>
                <div className="cockpit-workload">
                  {data.assignments.people.map((row) => (
                    <article key={row.user_id}>
                      <strong>{row.name}</strong>
                      <span>
                        已指派待办 {row.pending} 项 · 逾期 {row.overdue} 项
                      </span>
                    </article>
                  ))}
                </div>
                {data.assignments.people_total > 20 && (
                  <p>展示任务最多的 20 人，其余请在待办中查看。</p>
                )}
              </details>
            )}
          </section>
        </div>
      )}

      <section className="cockpit-directory" aria-label="客户查找与进度">
        {searchControls}
        <details
          className="cockpit-panel cockpit-client-details"
          open={detailsOpen}
          onToggle={(event) => onDetailsChange?.(event.currentTarget.open)}
        >
          <summary>全部客户与阶段明细</summary>
          {pageControls}
          <div className="cockpit-client-list">
            {data.companies.map((row) => (
              <ClientProgress
                key={row.company_id}
                row={row}
                onOpenBook={onOpenBook}
                onOnboarding={onOnboarding}
                onMonthly={onMonthly}
              />
            ))}
            {!data.companies.length && (
              <p className="cockpit-panel-help">
                当前月份和搜索条件下没有匹配客户。请调整搜索或月份。
              </p>
            )}
          </div>
        </details>
      </section>
      <details className="cockpit-diagnostics">
        <summary>数据更新说明</summary>
        <p>
          负责人和期限取自所选月份的服务单。待办按企业当前未结事项汇总，待办未按月份拆分，也不是主办会计个人的任务指派数。请进入客户核对具体事项、期间和实际负责人。
        </p>
        <p>统计按当前授权的服务客户汇总。待办数量用于安排工作，不代表业务已处理或凭证已交付。</p>
        {data.summary.projection_stale > 0 ? (
          <p>部分企业待办正在更新。请刷新进度，或进入客户查看最新记录。</p>
        ) : (
          <p>待办汇总目前无待更新企业。</p>
        )}
      </details>
    </>
  );
}

export function FirmCockpit({
  session,
  initialPeriod,
  onOpenBook,
  onOpenInbox,
  onMonthly,
  onTeam,
  onOnboarding,
}: { session: Session; initialPeriod?: string } & Actions) {
  const [period, setPeriod] = useState(initialPeriod || new Date().toISOString().slice(0, 7));
  const [revision, setRevision] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const root = useRef<HTMLElement>(null);
  const pendingSearch = useRef(false);
  const [search, setSearch] = useState(''),
    [query, setQuery] = useState(''),
    [offset, setOffset] = useState(0);
  const limit = 20;
  const result = useMonthlyData<unknown>(
    session,
    '/firm/cockpit?' +
      new URLSearchParams({ period, search: query, offset: String(offset), limit: String(limit) }),
    revision,
  );
  const data = useMemo(
    () =>
      result.data === null
        ? null
        : parseFirmSnapshot(result.data, {
            firm_id: session.firm_id ?? '',
            user_id: session.user_id,
            period,
            search: query,
            limit,
            offset,
          }),
    [result.data, session.firm_id, session.user_id, period, query, offset],
  );
  const loading = result.loading;
  const error =
    result.error ||
    (result.data !== null && !data ? '返回的企业范围或数据格式不符，请重新读取。' : '');

  useEffect(() => {
    if (!pendingSearch.current || loading || !data || error) return;
    pendingSearch.current = false;
    const frame = requestAnimationFrame(() => {
      const summary = root.current?.querySelector<HTMLElement>('.cockpit-client-details > summary');
      summary?.focus({ preventScroll: true });
      summary?.scrollIntoView({ block: 'nearest' });
    });
    return () => cancelAnimationFrame(frame);
  }, [data, loading, error]);

  const searchControls = (
    <form
      className="cockpit-client-search"
      onSubmit={(event) => {
        event.preventDefault();
        setQuery(search.trim());
        setOffset(0);
        setDetailsOpen(true);
        pendingSearch.current = true;
      }}
    >
      <label>
        查找客户
        <input
          aria-label="搜索服务客户"
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="输入企业名称"
        />
      </label>
      <button className="button secondary" type="submit">
        查找
      </button>
      <small>搜索只筛选客户明细，全所汇总与需协调客户保持不变。</small>
    </form>
  );

  return (
    <main className="cockpit" ref={root}>
      <header className="cockpit-heading">
        <div>
          <p className="eyebrow">事务所管理</p>
          <h1>全所工作台</h1>
          <p>客户进度、待协调事项和人员安排，在这里一目了然。</p>
        </div>
        <div className="cockpit-actions">
          <label>
            <span>
              <CalendarDays size={13} aria-hidden="true" /> 服务月份
            </span>
            <input
              type="month"
              value={period}
              onChange={(event) => {
                if (event.target.value) {
                  setPeriod(event.target.value);
                  setOffset(0);
                }
              }}
            />
          </label>
          <button
            className="button secondary"
            type="button"
            disabled={loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新进度
          </button>
          {onOnboarding && (
            <button className="button secondary" type="button" onClick={() => onOnboarding(period)}>
              新增客户
            </button>
          )}
        </div>
      </header>
      {error ? (
        <div className="inline-error" role="alert">
          <p>本月进度暂时无法加载：{error}</p>
          <button
            className="button secondary"
            type="button"
            onClick={() => setRevision((value) => value + 1)}
          >
            重新加载
          </button>
        </div>
      ) : loading || !data ? (
        <div className="loading-block" role="status">
          <Spinner />
          正在汇总客户进度和分工，请稍候…
        </div>
      ) : (
        <FirmCockpitContent
          data={data}
          searchControls={searchControls}
          detailsOpen={detailsOpen}
          onDetailsChange={setDetailsOpen}
          pageControls={
            data.pagination && (
              <nav className="cockpit-pages" aria-label="客户明细分页">
                <span>
                  共 {data.pagination.total} 家 · 第 {Math.floor(offset / limit) + 1} 页
                </span>
                <button
                  className="button secondary"
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                >
                  上一页
                </button>
                <button
                  className="button secondary"
                  type="button"
                  disabled={!data.pagination.has_more}
                  onClick={() => setOffset(offset + limit)}
                >
                  下一页
                </button>
              </nav>
            )
          }
          onOpenBook={onOpenBook}
          onOpenInbox={onOpenInbox}
          onMonthly={onMonthly}
          onTeam={onTeam}
          onOnboarding={onOnboarding}
        />
      )}
    </main>
  );
}
