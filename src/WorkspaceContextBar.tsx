import { useEffect, useState } from 'react';
import { api } from './api';
import { confirmLeaveIfDirty, registerUnsavedGuard } from './unsaved-guard';
import { beginScope, isCurrentScope, scopedController } from './request-scope';
import type { Session } from './types';
import './workspace-context-bar.css';

interface CloseRow {
  id: string;
  period: string;
  status: string;
  version: number;
}

const roleLabels: Record<string, string> = {
  owner: '共享中心负责人',
  admin: '共享中心管理员',
  reviewer: '总账复核主管',
  operator: '分管核算会计',
  finance: '子公司财务',
  viewer: '只读查阅成员',
  employee: '子公司员工',
  client_employee: '子公司员工',
  client_member: '子公司成员',
};

/**
 * 全局「共享中心 / 当前子公司 / 岗位身份 / 会计期间 / 智能与纯手工模式」常驻切换条。
 * 切换子公司或会计期间时同步触发全局请求防串台护栏与当前视图联动，不越权造假。
 */
export function WorkspaceContextBar({
  session,
  companyName,
  companies,
  companyId,
  period,
  bookId,
  unsavedLabels,
  manualMode = false,
  dutySummary,
  onToggleManualMode,
  onPeriodChange,
  onCompanyChange,
  onOpenBooks,
}: {
  session: Session;
  companyName?: string;
  companies?: { id: string; name: string }[];
  companyId?: string;
  period: string;
  bookId?: string;
  /** 工作区里当前有未保存编辑的来源标签（由 App 汇总，纯字符串，避免把守卫模块打进首屏）。 */
  unsavedLabels?: string[];
  /** 是否处于离线/纯手工办理模式 */
  manualMode?: boolean;
  /** 首屏一句大白话岗位职责摘要 */
  dutySummary?: string;
  onToggleManualMode?: () => void;
  onPeriodChange: (period: string) => void;
  onCompanyChange?: (companyId: string) => void;
  onOpenBooks: () => void;
}) {
  const [close, setClose] = useState<CloseRow | null>(null);
  const [error, setError] = useState('');
  const labelsKey = (unsavedLabels ?? []).join('|');

  useEffect(() => {
    const disposers = labelsKey
      ? labelsKey.split('|').map((label, index) => registerUnsavedGuard(`ctx-${index}`, label))
      : [];
    return () => disposers.forEach((off) => off());
  }, [labelsKey]);

  useEffect(() => {
    if (!bookId || !/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) {
      setClose(null);
      return;
    }
    let active = true;
    const scope = scopedController();
    const query = new URLSearchParams({ book_id: bookId, period });
    void api<{ closes: CloseRow[] }>(`/b04-close?${query}`, session, {
      signal: scope.signal,
    })
      .then((data) => {
        if (!active || !isCurrentScope(scope.epoch)) return;
        setError('');
        setClose(data.closes.find((row) => row.period === period) ?? null);
      })
      .catch(() => {
        if (!active || !isCurrentScope(scope.epoch)) return;
        setClose(null);
        setError('锁期状态待核实');
      })
      .finally(() => scope.release());
    return () => {
      active = false;
      scope.abort();
    };
  }, [bookId, period, session]);

  const lockLabel = !bookId
    ? '未选择账套'
    : error
      ? '锁期状态待核实'
      : close?.status === 'locked' || close?.status === 'published'
        ? '已锁期'
        : '未锁期';

  const hasMultiCompanies = !!companies && companies.length > 0 && !!onCompanyChange;

  return (
    <section className="workspace-context-bar" aria-label="当前子公司、岗位与会计期间">
      <div className="workspace-context-row">
        <div className="workspace-context-item workspace-context-firm">
          <span className="workspace-context-label">中心</span>
          <strong>{session.firm_name || '未载入'}</strong>
        </div>
        <div className="workspace-context-item workspace-context-company">
          <span className="workspace-context-label">子公司</span>
          {hasMultiCompanies ? (
            <select
              aria-label="当前办理子公司"
              value={companyId ?? ''}
              onChange={(event) => {
                const next = event.target.value;
                if (next === (companyId ?? '')) return;
                const nextName =
                  next === ''
                    ? '全部子公司'
                    : companies.find((item) => item.id === next)?.name;
                void (async () => {
                  const proceed = await confirmLeaveIfDirty({
                    title: '切换当前办理子公司？',
                    description: '切换子公司会同步更新本页数据、账套与右侧财税助手范围',
                    company: nextName ?? (next || undefined),
                  });
                  if (!proceed) return;
                  beginScope();
                  onCompanyChange(next);
                })();
              }}
            >
              <option value="">
                {companies.length > 1 ? `全部子公司（${companies.length}家）` : '未选择'}
              </option>
              {companies.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </select>
          ) : (
            <strong>{companyName ?? '未选择'}</strong>
          )}
          <button
            type="button"
            className="text-button"
            onClick={() => {
              void (async () => {
                const proceed = await confirmLeaveIfDirty({
                  title: '前往子公司账套列表？',
                  description: '进入总账与凭证中心可切换该公司的具体账套',
                });
                if (!proceed) return;
                beginScope();
                onOpenBooks();
              })();
            }}
          >
            账套
          </button>
        </div>
        <div className="workspace-context-item workspace-context-role">
          <span className="workspace-context-label">岗位</span>
          <strong>{roleLabels[session.role ?? ''] ?? session.role ?? '未载入'}</strong>
        </div>
        <label className="workspace-context-item workspace-context-period">
          <span className="workspace-context-label">期间</span>
          <input
            type="month"
            aria-label="当前会计期间"
            min="2000-01"
            max="2099-12"
            value={period}
            onChange={(event) => {
              const next = event.target.value;
              if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(next) || next === period) return;
              void (async () => {
                const proceed = await confirmLeaveIfDirty({
                  title: '切换会计期间？',
                  description: '切换会计期间会重新加载该期间的账务与报税进度',
                  period: next,
                });
                if (!proceed) return;
                beginScope();
                onPeriodChange(next);
              })();
            }}
          />
        </label>
        <div className="workspace-context-status-group">
          {onToggleManualMode && (
            <button
              type="button"
              className={'workspace-mode-pill' + (manualMode ? ' manual' : '')}
              aria-pressed={manualMode}
              title={
                manualMode
                  ? '当前已切为纯手工办理模式，点击恢复智能辅助'
                  : '智能辅助在线；断网或需纯手工操作时可一键切换'
              }
              onClick={onToggleManualMode}
            >
              <span className="workspace-mode-dot" aria-hidden="true" />
              {manualMode ? '纯手工模式' : '智能在线'}
            </button>
          )}
          <span
            className={
              'workspace-context-lock' +
              (lockLabel === '已锁期' ? ' locked' : '') +
              (lockLabel === '锁期状态待核实' ? ' unknown' : '')
            }
          >
            {lockLabel}
          </span>
        </div>
      </div>
    </section>
  );
}
