import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Activity,
  ArrowUpRight,
  Building2,
  CalendarDays,
  Cable,
  CheckCheck,
  ClipboardCheck,
  FileCheck2,
  FileText,
  LayoutDashboard,
  Menu,
  ShieldCheck,
  X,
} from 'lucide-react';
import type { Session } from './types';
import type { PresentationNavItem } from './workspace-presentation';

export type AgentNavigationView =
  | 'home'
  | 'firm'
  | 'onboarding'
  | 'inbox'
  | 'observability'
  | 'ingestion'
  | 'messages'
  | 'business'
  | 'accounting'
  | 'review'
  | 'cases'
  | 'runs'
  | 'queue'
  | 'intelligence'
  | 'operations'
  | 'efficiency'
  | 'quality'
  | 'revision'
  | 'monthly'
  | 'materials'
  | 'portfolio'
  | 'briefing';

export function AgentNavigation({
  session,
  current,
  canAccounting,
  canMonthly = canAccounting,
  canReview,
  canOperations,
  canQuality = false,
  canProcessing = false,
  businessLabel = '业务事项',
  locked,
  firmSelector,
  footer,
  presentationNav,
  presentationLabel,
  onAdmin,
  onAccess,
  onNavigate,
  onAccountingFilter,
}: {
  session: Session;
  current: string;
  presentationNav?: PresentationNavItem[];
  presentationLabel?: string;
  onAdmin?: () => void;
  onAccess?: () => void;
  canAccounting: boolean;
  canMonthly?: boolean;
  canReview: boolean;
  canOperations: boolean;
  canQuality?: boolean;
  canProcessing?: boolean;
  businessLabel?: string;
  locked: boolean;
  firmSelector: ReactNode;
  footer: ReactNode;
  onNavigate: (view: AgentNavigationView) => void;
  onAccountingFilter: (filter: 'pending' | 'approved') => void;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null),
    panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setMobileOpen(false);
  }, [session.scope_key, current]);
  useEffect(() => {
    if (!mobileOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusBefore = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMobileOpen(false);
      }
      if (event.key !== 'Tab' || !panel.current) return;
      const items = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],select:not(:disabled),summary',
        ),
      ).filter((item) => item.getClientRects().length);
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) {
        event.preventDefault();
        items[items.length - 1]?.focus();
      } else if (!event.shiftKey && document.activeElement === items[items.length - 1]) {
        event.preventDefault();
        items[0].focus();
      }
    };
    document.addEventListener('keydown', keyboard);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener('keydown', keyboard);
      if (focusBefore?.isConnected) focusBefore.focus();
      else opener.current?.focus();
    };
  }, [mobileOpen]);
  function go(view: AgentNavigationView) {
    if (locked) return;
    onNavigate(view);
    setMobileOpen(false);
  }
  const businessItems: PresentationNavItem[] = [
    { id: 'home', label: '我的待办', hint: '需要我处理的工作' },
    ...(canOperations
      ? [
          { id: 'firm', label: '全所概览', hint: '交付进度与人员安排' },
          { id: 'onboarding', label: '客户开通', hint: '准备资料并完成检查' },
          { id: 'inbox', label: '异常收件箱', hint: '分派和催办' },
        ]
      : []),
    ...(canAccounting ? [{ id: 'accounting', label: '客户企业', hint: '选择客户并继续办理' }] : []),
    ...(canMonthly ? [{ id: 'monthly', label: '月度交付', hint: '负责人、期限与进度' }] : []),
    { id: 'materials', label: '资料清单', hint: '收集和补充资料' },
    { id: 'business', label: businessLabel, hint: '提交、核验与移交' },
    { id: 'cases', label: '异常与补件', hint: '处理业务阻塞' },
  ];
  const settingsItems: PresentationNavItem[] = [
    ...(onAdmin ? [{ id: 'admin', label: '企业与人员管理', hint: '开户、权限与企业资料' }] : []),
    ...(onAccess ? [{ id: 'access', label: '成员与账套授权', hint: '事务所成员与账套职责' }] : []),
    ...(canOperations
      ? [
          { id: 'ingestion', label: '数据接入', hint: '连接与导入失败' },
          { id: 'observability', label: '运行可观测性', hint: '队列、锁与耗时' },
          { id: 'operations', label: '费用与运行监控', hint: '用量、额度与告警' },
        ]
      : []),
    ...(canProcessing
      ? [
          { id: 'runs', label: '后台运行', hint: '处理记录与接管' },
          { id: 'queue', label: '票据处理任务', hint: '进度与重试' },
          { id: 'intelligence', label: '票据核对异常', hint: '查看识别与核对记录' },
        ]
      : []),
    ...(canQuality ? [{ id: 'quality', label: '试点评测', hint: '验证处理效果' }] : []),
    ...(canAccounting
      ? [{ id: 'portfolio', label: '客户人工投入', hint: '查看人工处理时间' }]
      : []),
  ];
  const canShow = (item: PresentationNavItem) => {
    if (item.id === 'admin') return Boolean(onAdmin);
    if (
      ['firm', 'onboarding', 'inbox', 'ingestion', 'observability', 'operations'].includes(item.id)
    )
      return canOperations;
    if (item.id === 'accounting' || item.id === 'revision') return canAccounting;
    if (item.id === 'monthly') return canMonthly;
    if (item.id === 'review') return canReview;
    if (item.id === 'quality') return canQuality;
    if (['runs', 'queue', 'intelligence'].includes(item.id)) return canProcessing;
    return true;
  };
  const settingIds = new Set(settingsItems.map((item) => item.id));
  const mainItems = (presentationNav?.length ? presentationNav : businessItems.slice(0, 1)).filter(
    (item) => canShow(item) && !settingIds.has(item.id),
  );
  const mainIds = new Set(mainItems.map((item) => item.id));
  const accountingItems = [
    { id: 'accounting', label: '会计工作台', hint: '账套与凭证' },
    ...(canReview ? [{ id: 'review', label: '跨企业批量复核', hint: '原件与凭证一起核对' }] : []),
    { id: 'revision', label: '事实修订与调整', hint: '保留历史并更正事实' },
  ].filter((item) => !mainIds.has(item.id));
  const secondaryIds = new Set(accountingItems.map((item) => item.id));
  const dailyItems = businessItems.filter(
    (item) => !mainIds.has(item.id) && !secondaryIds.has(item.id),
  );
  function renderEntry(item: PresentationNavItem, primary = false) {
    const Icon = navigationIcon(item.id);
    const active = current === item.id;
    return (
      <button
        key={item.id}
        aria-label={item.label}
        className={'nav-item ' + (primary ? 'agent-primary-item ' : '') + (active ? 'active' : '')}
        disabled={locked || (item.id === 'admin' && !onAdmin)}
        aria-current={active ? 'page' : undefined}
        onClick={() => {
          if (locked) return;
          if (item.id === 'admin') {
            onAdmin?.();
            setMobileOpen(false);
          } else if (item.id === 'access') {
            onAccess?.();
            setMobileOpen(false);
          } else go(item.id as AgentNavigationView);
        }}
      >
        <Icon size={primary ? 19 : 16} />
        <span>
          {item.label}
          {primary && <small>{item.hint}</small>}
        </span>
      </button>
    );
  }
  function filter(value: 'pending' | 'approved') {
    if (locked) return;
    onAccountingFilter(value);
    setMobileOpen(false);
  }
  return (
    <aside className={'sidebar agent-sidebar' + (mobileOpen ? ' mobile-open' : '')}>
      <div className="agent-brand-row">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            go('home');
          }}
          aria-label="衡账，返回工作台"
        >
          <span className="brand-mark">
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">
            衡账<span>ACCOUNTING AGENT</span>
          </span>
        </a>
        <button
          ref={opener}
          className="icon-button agent-mobile-menu"
          aria-label="打开工作空间导航"
          aria-expanded={mobileOpen}
          aria-controls="agent-navigation-panel"
          onClick={() => setMobileOpen(true)}
        >
          <Menu size={21} />
        </button>
      </div>
      {mobileOpen && (
        <button
          className="agent-nav-scrim"
          aria-label="关闭工作空间导航"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <div
        ref={panel}
        id="agent-navigation-panel"
        className="agent-nav-panel"
        role={mobileOpen ? 'dialog' : undefined}
        aria-modal={mobileOpen || undefined}
        aria-label={mobileOpen ? '工作空间导航' : undefined}
      >
        <div className="agent-mobile-panel-header">
          <strong>工作空间</strong>
          <button
            className="icon-button"
            onClick={() => setMobileOpen(false)}
            aria-label="关闭导航"
          >
            <X size={20} />
          </button>
        </div>
        <div className="firm-block">
          <span className="firm-icon">
            <Building2 size={17} />
          </span>
          <div>
            {firmSelector}
            <span>会计专用工作空间</span>
          </div>
        </div>
        <nav aria-label="主导航" className="agent-primary-nav">
          {presentationLabel && (
            <p className="agent-nav-context" aria-label="当前工作视角">
              {presentationLabel}
            </p>
          )}
          {mainItems.map((item) => renderEntry(item, true))}
          {dailyItems.length > 0 && (
            <details className="nav-group" open={dailyItems.some((item) => item.id === current)}>
              <summary>日常业务</summary>
              {dailyItems.map((item) => renderEntry(item))}
            </details>
          )}
          {canAccounting && (
            <details
              className="nav-group"
              open={['accounting', 'review', 'revision'].includes(current)}
            >
              <summary>会计工作区</summary>
              {accountingItems.map((item) => renderEntry(item))}
              <button className="nav-item" disabled={locked} onClick={() => filter('pending')}>
                <CheckCheck size={16} />
                <span>待审核凭证</span>
              </button>
              <button className="nav-item" disabled={locked} onClick={() => filter('approved')}>
                <FileCheck2 size={16} />
                <span>审核与导出</span>
              </button>
            </details>
          )}
          {settingsItems.length > 0 && (
            <details
              className="nav-group agent-management-group"
              aria-label="设置"
              open={settingsItems.some((item) => item.id === current)}
            >
              <summary>设置</summary>
              <nav aria-label="设置入口">{settingsItems.map((item) => renderEntry(item))}</nav>
            </details>
          )}
        </nav>
        <div className="agent-sidebar-note">
          <ShieldCheck size={14} />
          <span>原件留存 · 会计复核后导出</span>
          <ArrowUpRight size={13} />
        </div>
        <div className="sidebar-bottom">{footer}</div>
      </div>
    </aside>
  );
}

function navigationIcon(id: string) {
  if (id === 'review') return CheckCheck;
  if (['cases', 'business', 'inbox'].includes(id)) return ClipboardCheck;
  if (id === 'materials') return FileCheck2;
  if (['monthly', 'portfolio'].includes(id)) return CalendarDays;
  if (['firm', 'operations', 'observability', 'runs'].includes(id)) return Activity;
  if (id === 'accounting') return Building2;
  if (id === 'ingestion') return Cable;
  if (id === 'admin' || id === 'quality') return ShieldCheck;
  if (id === 'messages') return FileText;
  return LayoutDashboard;
}
