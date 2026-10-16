import { lazy, Suspense, type MutableRefObject } from 'react';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Building2,
  CheckCheck,
  ChevronRight,
  ClipboardCheck,
  Download,
  FileCheck2,
  FileText,
  Plus,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { AccountingBookList } from './AccountingBookList';
import { api } from './api';
import { EmptyState, PageControls, Spinner, StatCard } from './AppPrimitives';
import { AccountingBookToolbar, bookPipelineStep } from './AccountingBookToolbar';
import { downloadCsv, formatMoney } from './csv';
import type { Book, BookDetail, Draft, JournalLine, Me, Session, Workbench } from './types';
import type { WorkspaceView } from './workspace-route';

const ProposalTasksPanel = lazy(() =>
  import('./ProposalTasksPanel').then((module) => ({ default: module.ProposalTasksPanel })),
);
const Reviewer = lazy(() =>
  import('./VoucherReviewer').then((module) => ({ default: module.Reviewer })),
);

const statusLabels: Record<Draft['status'], string> = {
  draft: '待审核',
  approved: '已审核',
  exported: '已导出',
};

type Filter = 'all' | 'pending' | 'exception' | 'approved';
type Notice = { kind: 'success' | 'error'; text: string } | null;

export interface BookWorkspaceShellProps {
  session: Session;
  me: Me | null;
  routeView: WorkspaceView;
  filter: Filter;
  navItems: { id: Filter; label: string }[];
  stats: Workbench['stats'] | undefined;
  workbench: Workbench | null;
  filteredBooks: Book[];
  detail: BookDetail | null;
  bookId: string;
  drafts: Draft[];
  activeDraft: Draft | undefined;
  approvedDraftIds: string[];
  selectedIds: string[];
  search: string;
  listTab: 'drafts' | 'sources' | 'exports';
  busy: string;
  operationBusy: string;
  bootLoading: boolean;
  bootError: string;
  bookLoading: boolean;
  bookError: string;
  draftLoading: boolean;
  draftError: string;
  reviewNavigationLoading: boolean;
  reviewDirty: boolean;
  historyLoading: boolean;
  historyError: string;
  exportHistory: { id: string; count: number; filename: string; created_at: string }[];
  focusBookRef: MutableRefObject<string>;
  pendingReview: MutableRefObject<{ bookId: string; draftId: string } | null>;
  initialize: (signal?: AbortSignal, focusId?: string) => unknown;
  refresh: (id: string) => Promise<void>;
  runAction: (name: string, action: () => Promise<void>) => Promise<void>;
  chooseBook: (id: string) => void;
  generateSuggestions: () => unknown;
  saveDraft: (
    draft: Draft,
    description: string,
    lines: JournalLine[],
    correctionReason?: string,
  ) => Promise<void>;
  approveDraft: (draft: Draft) => Promise<void>;
  exportDrafts: () => unknown;
  setSearch: (value: string) => void;
  setBookOffset: (value: number) => void;
  setDraftOffset: (value: number) => void;
  setSourceOffset: (value: number) => void;
  setListTab: (value: 'drafts' | 'sources' | 'exports') => void;
  setDraftId: (value: string) => void;
  setSelectedIds: (value: string[] | ((current: string[]) => string[])) => void;
  setRouteView: (view: WorkspaceView) => void;
  setQueueTarget: (value: string) => void;
  setInlineTarget: (value: { bookId: string; draftId: string } | null) => void;
  setAssistantTarget: (value: { bookId: string; draftId: string; version: number } | null) => void;
  setCaseTarget: (
    value: { eventId?: string; bookId?: string; draftId?: string; title?: string } | undefined,
  ) => void;
  setNotice: (value: Notice) => void;
  changeBusinessBusy: (value: boolean) => void;
  changeReviewDirty: (value: boolean) => void;
  changeIntelligenceBusy: (value: boolean) => void;
  onOpenBookModal: () => void;
  onOpenImport: () => void;
}

export function BookWorkspaceShell({
  session,
  me,
  routeView,
  filter,
  navItems,
  stats,
  workbench,
  filteredBooks,
  detail,
  bookId,
  drafts,
  activeDraft,
  approvedDraftIds,
  selectedIds,
  search,
  listTab,
  busy,
  operationBusy,
  bootLoading,
  bootError,
  bookLoading,
  bookError,
  draftLoading,
  draftError,
  reviewNavigationLoading,
  reviewDirty,
  historyLoading,
  historyError,
  exportHistory,
  focusBookRef,
  pendingReview,
  initialize,
  refresh,
  runAction,
  chooseBook,
  generateSuggestions,
  saveDraft,
  approveDraft,
  exportDrafts,
  setSearch,
  setBookOffset,
  setDraftOffset,
  setSourceOffset,
  setListTab,
  setDraftId,
  setSelectedIds,
  setRouteView,
  setQueueTarget,
  setInlineTarget,
  setAssistantTarget,
  setCaseTarget,
  setNotice,
  changeBusinessBusy,
  changeReviewDirty,
  changeIntelligenceBusy,
  onOpenBookModal,
  onOpenImport,
}: BookWorkspaceShellProps) {
  return (
    <main>
      <section className="page-heading">
        <div>
          <p className="eyebrow">ACCOUNTING, WITH CLARITY</p>
          <h1>
            {filter === 'all'
              ? '每一笔，都有据可循。'
              : navItems.find((item) => item.id === filter)?.label}
          </h1>
          <p className="page-description">
            多企业协同记账，从原始流水到会计审核，在一个工作台完成。
          </p>
        </div>
        <button
          className="button primary"
          type="button"
          disabled={!!busy || reviewDirty || !me?.permissions.create_book}
          title={!me?.permissions.create_book ? '当前账号没有新建账套权限' : undefined}
          onClick={() => {
            setNotice(null);
            onOpenBookModal();
          }}
        >
          <Plus size={16} />
          新建账套
        </button>
      </section>
      {bootLoading ? (
        <div className="loading-block">
          <Spinner />
          正在载入会计工作空间…
        </div>
      ) : bootError ? (
        <div className="connection-error" role="alert">
          <TriangleAlert size={25} />
          <div>
            <h2>工作空间暂时无法连接</h2>
            <p>{bootError}</p>
            {session.mode === 'local' && <code>后端默认地址：127.0.0.1:8000</code>}
          </div>
          <button className="button secondary" onClick={() => void initialize()}>
            重新连接
          </button>
        </div>
      ) : (
        <>
          <section className="stats-grid" aria-label="事务所账务概览">
            <StatCard
              label="管理账套"
              value={stats?.book_count ?? 0}
              unit="家"
              icon={<Building2 size={19} />}
              hint="当前事务所客户企业"
            />
            <StatCard
              label="待审核凭证"
              value={stats?.pending_count ?? 0}
              unit="张"
              icon={<ClipboardCheck size={19} />}
              hint="等待会计核对与确认"
            />
            <StatCard
              label="待处理异常"
              value={stats?.exception_count ?? 0}
              unit="项"
              icon={<TriangleAlert size={19} />}
              hint="优先处理缺证及校验问题"
              warning
            />
            <StatCard
              label="已审核凭证"
              value={stats?.approved_count ?? 0}
              unit="张"
              icon={<CheckCheck size={19} />}
              hint="含已导出，交付后请核对回执"
            />
          </section>
          <section className="process-strip" aria-label="记账流程">
            <span>
              <span className="step-number">01</span>导入原始流水
            </span>
            <ArrowRight size={15} />
            <span>
              <span className="step-number">02</span>规则与模型建议
            </span>
            <ArrowRight size={15} />
            <span>
              <span className="step-number">03</span>会计复核与导出
            </span>
            <p>
              <ShieldCheck size={14} />
              每一步保留来源
            </p>
          </section>
          <div className="workspace-grid">
            <div className="work-column">
              <AccountingBookList
                books={filteredBooks}
                bookId={bookId}
                search={search}
                filtered={filter !== 'all'}
                busy={!!busy || reviewDirty}
                loading={bootLoading}
                pagination={workbench?.pagination}
                onChoose={chooseBook}
                onSearch={(value) => {
                  focusBookRef.current = '';
                  pendingReview.current = null;
                  setSearch(value);
                }}
                onPage={(value) => {
                  focusBookRef.current = '';
                  pendingReview.current = null;
                  setBookOffset(value);
                }}
              />

              <section className="panel transaction-panel">
                <AccountingBookToolbar
                  book={detail?.book}
                  fallbackName={workbench?.books.find((book) => book.id === bookId)?.name}
                  busy={!!busy}
                  loading={bookLoading}
                  dirty={reviewDirty}
                  activeStep={bookPipelineStep(routeView, listTab)}
                  onImport={() => {
                    setNotice(null);
                    onOpenImport();
                  }}
                  onWorkspace={(view) => setRouteView(view)}
                />
                {detail?.book.permissions.edit && (
                  <Suspense
                    fallback={
                      <div className="loading-block">
                        <Spinner />
                        正在读取后台任务…
                      </div>
                    }
                  >
                    <ProposalTasksPanel
                      key={session.scope_key + ':' + bookId}
                      session={session}
                      bookId={bookId}
                      companyId={detail.book.company_id}
                      paused={reviewDirty || !!busy}
                      onBusyChange={changeBusinessBusy}
                      onChanged={() => refresh(bookId)}
                    />
                  </Suspense>
                )}
                <div className="list-tabs">
                  <div role="tablist" aria-label="账套数据">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={listTab === 'drafts'}
                      className={listTab === 'drafts' ? 'active' : ''}
                      onClick={() => setListTab('drafts')}
                    >
                      凭证队列<span>{drafts.length}</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={listTab === 'sources'}
                      className={listTab === 'sources' ? 'active' : ''}
                      onClick={() => setListTab('sources')}
                    >
                      原始流水
                      <span>{detail?.source_pagination?.total ?? detail?.sources.length ?? 0}</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={listTab === 'exports'}
                      className={listTab === 'exports' ? 'active' : ''}
                      onClick={() => setListTab('exports')}
                    >
                      导出记录
                    </button>
                  </div>
                  <button
                    type="button"
                    className="text-button"
                    disabled={
                      !detail?.book.permissions.edit ||
                      !detail?.sources.length ||
                      !!busy ||
                      bookLoading ||
                      reviewDirty
                    }
                    onClick={() => void generateSuggestions()}
                  >
                    {busy === 'suggest' ? <Spinner /> : <Sparkles size={14} />}生成记账建议
                  </button>
                </div>
                {bookLoading ? (
                  <div className="loading-block">
                    <Spinner />
                    正在载入账套…
                  </div>
                ) : bookError ? (
                  <div className="inline-error" role="alert">
                    {bookError}
                    <button
                      className="text-button"
                      disabled={!!busy}
                      onClick={() => void runAction('refresh', () => refresh(bookId))}
                    >
                      重新加载
                    </button>
                  </div>
                ) : !detail ? (
                  <EmptyState title="选择一个账套开始处理">
                    每家企业的流水、凭证和审核记录独立保存。
                  </EmptyState>
                ) : listTab === 'drafts' ? (
                  <>
                    {drafts.length ? (
                      <div className="transaction-table-wrap">
                        <table className="transaction-table">
                          <thead>
                            <tr>
                              <th className="checkbox-cell">
                                <input
                                  type="checkbox"
                                  aria-label="选择全部可导出凭证"
                                  disabled={
                                    !detail.book.permissions.export ||
                                    !approvedDraftIds.length ||
                                    !!busy
                                  }
                                  checked={
                                    approvedDraftIds.length > 0 &&
                                    approvedDraftIds.every((id) => selectedIds.includes(id))
                                  }
                                  onChange={(event) =>
                                    setSelectedIds(event.target.checked ? approvedDraftIds : [])
                                  }
                                />
                              </th>
                              <th>摘要 / 交易日期</th>
                              <th className="number-cell">金额（元）</th>
                              <th>审核状态</th>
                              <th />
                            </tr>
                          </thead>
                          <tbody>
                            {drafts.map((draft) => {
                              const source = detail.sources.find(
                                (item) => item.id === draft.source_id,
                              );
                              return (
                                <tr
                                  key={draft.id}
                                  className={draft.id === activeDraft?.id ? 'active' : ''}
                                >
                                  <td className="checkbox-cell">
                                    <input
                                      type="checkbox"
                                      aria-label={`选择凭证 ${draft.description}`}
                                      disabled={
                                        !detail.book.permissions.export ||
                                        draft.status !== 'approved' ||
                                        !!busy
                                      }
                                      checked={selectedIds.includes(draft.id)}
                                      onChange={(event) =>
                                        setSelectedIds((ids) =>
                                          event.target.checked
                                            ? [...ids, draft.id]
                                            : ids.filter((id) => id !== draft.id),
                                        )
                                      }
                                    />
                                  </td>
                                  <td>
                                    <button
                                      type="button"
                                      className="draft-title"
                                      disabled={!!busy || reviewDirty}
                                      onClick={() => {
                                        pendingReview.current = null;
                                        setDraftId(draft.id);
                                      }}
                                    >
                                      <strong>
                                        {draft.description || source?.description || '未命名凭证'}
                                      </strong>
                                      <span>
                                        {source?.date ?? '—'}
                                        <span className="inline-dot">·</span>
                                        {source?.counterparty || '未填写交易对方'}
                                      </span>
                                    </button>
                                  </td>
                                  <td className="number-cell">
                                    <span className="amount">
                                      {source ? formatMoney(source.amount) : '—'}
                                    </span>
                                    <span className="direction-label">
                                      {source?.direction === 'income' ? '收入' : '支出'}
                                    </span>
                                  </td>
                                  <td>
                                    <span
                                      className={`badge ${draft.status === 'draft' && draft.issues.length ? 'exception' : draft.status}`}
                                    >
                                      {draft.source_inactive
                                        ? '已替代 / 隔离'
                                        : draft.status === 'draft' && draft.issues.length
                                          ? '需处理'
                                          : statusLabels[draft.status]}
                                    </span>
                                  </td>
                                  <td>
                                    <button
                                      type="button"
                                      className="icon-button review-arrow"
                                      aria-label={`复核 ${draft.description}`}
                                      disabled={!!busy || reviewDirty}
                                      onClick={() => {
                                        pendingReview.current = null;
                                        setDraftId(draft.id);
                                      }}
                                    >
                                      <ChevronRight size={16} />
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <EmptyState
                        title={filter === 'all' ? '还没有凭证草稿' : '当前账套没有符合条件的凭证'}
                        icon={<FileText size={28} />}
                      >
                        {detail.sources.length
                          ? '点击“生成记账建议”，将原始流水转换为可审核草稿。'
                          : '先导入企业流水，再生成记账建议。'}
                      </EmptyState>
                    )}
                    <div className="export-bar">
                      <span>
                        已选 <strong>{selectedIds.length}</strong> 张已审核凭证
                      </span>
                      <button
                        className="button secondary small"
                        disabled={
                          !detail.book.permissions.export ||
                          !selectedIds.length ||
                          !!busy ||
                          reviewDirty
                        }
                        onClick={() => void exportDrafts()}
                      >
                        {busy === 'export' ? <Spinner /> : <Download size={14} />}导出凭证 CSV
                      </button>
                    </div>
                  </>
                ) : listTab === 'exports' ? (
                  historyLoading ? (
                    <div className="loading-block">
                      <Spinner />
                      正在读取导出记录…
                    </div>
                  ) : historyError ? (
                    <div className="inline-error" role="alert">
                      {historyError}
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => {
                          setListTab('drafts');
                        }}
                      >
                        返回凭证队列
                      </button>
                    </div>
                  ) : exportHistory.length ? (
                    <div className="source-list">
                      {exportHistory.map((record) => (
                        <div className="source-row export-history-row" key={record.id}>
                          <span className="source-direction income">
                            <FileCheck2 size={18} />
                          </span>
                          <div>
                            <strong>{record.filename}</strong>
                            <span>
                              {record.count} 张凭证 ·{' '}
                              {new Date(record.created_at).toLocaleString('zh-CN', {
                                timeZone: 'Asia/Shanghai',
                                hour12: false,
                              })}
                            </span>
                          </div>
                          <button
                            type="button"
                            className="button secondary small"
                            disabled={!detail.book.permissions.export || !!busy}
                            aria-label={`重新下载 ${record.filename}`}
                            onClick={() =>
                              void runAction('download', async () => {
                                if (!session) return;
                                const receipt = await api<{
                                  csv: string;
                                  filename: string;
                                }>(
                                  `/books/${encodeURIComponent(bookId)}/exports/${encodeURIComponent(record.id)}`,
                                  session,
                                );
                                downloadCsv(receipt.csv, receipt.filename);
                              })
                            }
                          >
                            {busy === 'download' ? <Spinner /> : <Download size={14} />}
                            重新下载
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState title="还没有导出记录" icon={<Download size={26} />}>
                      审核并导出凭证后，可在这里重新下载历史文件。
                    </EmptyState>
                  )
                ) : detail.sources.length ? (
                  <div className="source-list">
                    {detail.sources
                      .filter(
                        (source) =>
                          !detail.source_page_ids || detail.source_page_ids.includes(source.id),
                      )
                      .map((source) => (
                        <div className="source-row" key={source.id}>
                          <span className={`source-direction ${source.direction}`}>
                            {source.direction === 'income' ? (
                              <ArrowDownLeft size={18} />
                            ) : (
                              <ArrowUpRight size={18} />
                            )}
                          </span>
                          <div>
                            <strong>{source.description}</strong>
                            <span>
                              {source.date} · {source.external_id}
                            </span>
                            <small>
                              {source.evidence_ref
                                ? `凭据引用：${source.evidence_ref}`
                                : '尚未补充凭据引用'}
                            </small>
                          </div>
                          <div className="source-amount">
                            <strong>{formatMoney(source.amount)}</strong>
                            <span>{source.direction === 'income' ? '收入' : '支出'}</span>
                          </div>
                        </div>
                      ))}
                  </div>
                ) : (
                  <EmptyState title="还没有原始流水">
                    支持带表头的 UTF-8 CSV，点击“导入流水”开始。
                  </EmptyState>
                )}
                {detail && listTab !== 'exports' && (
                  <PageControls
                    label={listTab === 'sources' ? '原始流水' : '凭证待办'}
                    page={listTab === 'sources' ? detail.source_pagination : detail.pagination}
                    busy={!!busy || bookLoading || draftLoading || reviewDirty}
                    onPage={(value) => {
                      pendingReview.current = null;
                      setSelectedIds([]);
                      setDraftId('');
                      if (listTab === 'sources') setSourceOffset(value);
                      else setDraftOffset(value);
                    }}
                  />
                )}
              </section>
            </div>

            <aside className="panel review-panel" aria-label="凭证复核">
              <div className="review-panel-header">
                <div className="review-heading">
                  <span className="review-icon">
                    <ClipboardCheck size={18} />
                  </span>
                  <div>
                    <h2>凭证复核</h2>
                    <p>核对来源，再确认入账建议</p>
                  </div>
                </div>
                <span className="review-step">REVIEW</span>
              </div>
              {draftError ? (
                <div className="inline-error" role="alert">
                  {draftError}
                  <button className="text-button" onClick={() => void refresh(bookId)}>
                    重新加载
                  </button>
                </div>
              ) : reviewNavigationLoading ||
                draftLoading ||
                activeDraft?.detail_loaded === false ? (
                <div className="loading-block">
                  <Spinner />
                  正在核对当前凭证…
                </div>
              ) : !bookLoading && detail && activeDraft ? (
                <Suspense
                  fallback={
                    <div className="loading-block">
                      <Spinner />
                      正在打开凭证复核…
                    </div>
                  }
                >
                  <Reviewer
                    key={`${session.scope_key}:${bookId}:${activeDraft.id}`}
                    session={session}
                    draft={activeDraft}
                    detail={detail}
                    busy={busy}
                    externalBusy={!!operationBusy}
                    onAssistant={() =>
                      setAssistantTarget({
                        bookId,
                        draftId: activeDraft.id,
                        version: activeDraft.version,
                      })
                    }
                    onDirtyChange={changeReviewDirty}
                    onIntelligenceBusyChange={changeIntelligenceBusy}
                    onIntelligenceChanged={() => refresh(bookId)}
                    onOpenBatch={(id) => {
                      setQueueTarget(id);
                      setRouteView('queue');
                    }}
                    onOpenCase={(target) => {
                      setCaseTarget(target);
                      setRouteView('cases');
                    }}
                    onSave={saveDraft}
                    onApprove={approveDraft}
                    onBusinessEvidence={() => {
                      if (!reviewDirty) setInlineTarget({ bookId, draftId: activeDraft.id });
                    }}
                    onEvidence={async (evidenceRef) => {
                      if (!session) return;
                      const id = bookId;
                      await runAction('evidence', async () => {
                        await api(
                          `/books/${encodeURIComponent(id)}/sources/${encodeURIComponent(activeDraft.source_id)}/evidence`,
                          session,
                          { method: 'POST', body: { evidence_ref: evidenceRef } },
                        );
                        await refresh(id);
                        setNotice({
                          kind: 'success',
                          text: '凭据引用已补充，请人工核对对应文件的真实性与完整性。',
                        });
                      });
                    }}
                  />
                </Suspense>
              ) : (
                <EmptyState
                  title={bookLoading ? '正在载入凭证' : '选择一张凭证开始复核'}
                  icon={<ClipboardCheck size={30} />}
                >
                  这里将展示原始流水、建议分录和需要处理的问题。
                </EmptyState>
              )}
            </aside>
          </div>
          <footer className="workspace-footer">
            <span>衡账 · AI 辅助记账工作台</span>
            <span>中国大陆企业 / 人民币 / 会计审核后导出</span>
          </footer>
        </>
      )}
    </main>
  );
}
