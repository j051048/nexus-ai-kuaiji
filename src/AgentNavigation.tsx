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
import { workspaceNavigation, type PresentationNavItem } from './workspace-navigation';

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
  canBusiness = true,
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
  canBusiness?: boolean;
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
  const {
    primary: mainItems,
    daily: dailyItems,
    accounting: accountingItems,
    settings,
  } = workspaceNavigation(
    presentationNav,
    {
      accounting: canAccounting,
      monthly: canMonthly,
      review: canReview,
      operations: canOperations,
      quality: canQuality,
      processing: canProcessing,
      business: canBusiness,
      admin: Boolean(onAdmin),
      access: Boolean(onAccess),
    },
    businessLabel,
  );
  const settingsItems = settings.flatMap((group) => group.items);
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
              <nav aria-label="设置入口">
                {settings.map((group) => (
                  <section
                    key={group.id}
                    className="agent-settings-section"
                    aria-label={group.label}
                  >
                    <p className="agent-nav-context">{group.label}</p>
                    {group.items.map((item) => renderEntry(item))}
                  </section>
                ))}
              </nav>
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
