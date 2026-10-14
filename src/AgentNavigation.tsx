import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Activity,
  ArrowUpRight,
  Building2,
  CalendarDays,
  CheckCheck,
  ClipboardCheck,
  Command,
  FileCheck2,
  FileSearch,
  FileText,
  Layers3,
  LayoutDashboard,
  Menu,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react';
import type { Session } from './types';

export type AgentNavigationView =
  | 'home'
  | 'business'
  | 'accounting'
  | 'review'
  | 'cases'
  | 'runs'
  | 'queue'
  | 'intelligence'
  | 'operations'
  | 'revision'
  | 'monthly';

export function AgentNavigation({
  session,
  current,
  canAccounting,
  canMonthly = canAccounting,
  canReview,
  canOperations,
  locked,
  firmSelector,
  footer,
  onNavigate,
  onAccountingFilter,
  onCommand,
  onAssistant,
}: {
  session: Session;
  current: string;
  canAccounting: boolean;
  canMonthly?: boolean;
  canReview: boolean;
  canOperations: boolean;
  locked: boolean;
  firmSelector: ReactNode;
  footer: ReactNode;
  onNavigate: (view: AgentNavigationView) => void;
  onAccountingFilter: (filter: 'pending' | 'approved') => void;
  onCommand: () => void;
  onAssistant?: () => void;
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
  const workActive = ['home', 'business', 'review', 'cases'].includes(current);
  function go(view: AgentNavigationView) {
    if (locked) return;
    onNavigate(view);
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
        <button
          className="agent-command-launch"
          disabled={locked}
          onClick={() => {
            setMobileOpen(false);
            onCommand();
          }}
        >
          <Search size={16} />
          <span>查找企业与任务</span>
          <kbd>
            <Command size={11} /> K
          </kbd>
        </button>
        {onAssistant && (
          <button
            className="agent-assistant-launch"
            disabled={locked}
            onClick={() => {
              setMobileOpen(false);
              onAssistant();
            }}
          >
            <Sparkles size={16} />
            <span>
              会计助手<small>按企业提问与执行任务</small>
            </span>
          </button>
        )}
        <nav aria-label="主导航" className="agent-primary-nav">
          <button
            aria-label="我的待办"
            className={'nav-item agent-primary-item ' + (workActive ? 'active' : '')}
            disabled={locked}
            onClick={() => go('home')}
            aria-current={workActive ? 'page' : undefined}
          >
            <LayoutDashboard size={19} />
            <span>
              我的待办<small>我的工作</small>
            </span>
          </button>
          {canAccounting && (
            <button
              aria-label="客户企业"
              className={
                'nav-item agent-primary-item ' + (current === 'accounting' ? 'active' : '')
              }
              disabled={locked}
              onClick={() => go('accounting')}
              aria-current={current === 'accounting' ? 'page' : undefined}
            >
              <Building2 size={19} />
              <span>
                客户企业<small>账套与交付</small>
              </span>
            </button>
          )}
          {canMonthly && (
            <button
              aria-label="客户月度进度"
              className={'nav-item agent-primary-item ' + (current === 'monthly' ? 'active' : '')}
              disabled={locked}
              onClick={() => go('monthly')}
              aria-current={current === 'monthly' ? 'page' : undefined}
            >
              <CalendarDays size={19} />
              <span>
                客户月度进度<small>跨企业处理矩阵</small>
              </span>
            </button>
          )}
          <button
            aria-label="后台运行"
            className={'nav-item agent-primary-item ' + (current === 'runs' ? 'active' : '')}
            disabled={locked}
            onClick={() => go('runs')}
            aria-current={current === 'runs' ? 'page' : undefined}
          >
            <Activity size={19} />
            <span>
              后台运行<small>执行进度与接管</small>
            </span>
          </button>
          <div className="agent-work-shortcuts" aria-label="工作快捷入口">
            <button
              className={'nav-item ' + (current === 'business' ? 'selected' : '')}
              disabled={locked}
              onClick={() => go('business')}
            >
              <FileText size={16} />
              <span>业务事项</span>
            </button>
            <button
              className={'nav-item ' + (current === 'cases' ? 'selected' : '')}
              disabled={locked}
              onClick={() => go('cases')}
            >
              <ClipboardCheck size={16} />
              <span>异常与补件</span>
            </button>
            {canReview && (
              <button
                className={'nav-item ' + (current === 'review' ? 'selected' : '')}
                disabled={locked}
                onClick={() => go('review')}
              >
                <CheckCheck size={16} />
                <span>跨企业批量复核</span>
              </button>
            )}
          </div>
        </nav>
        {canAccounting && (
          <details className="nav-group" open={current === 'accounting'}>
            <summary>会计工作区</summary>
            <nav aria-label="会计协作">
              <button className="nav-item" disabled={locked} onClick={() => go('accounting')}>
                <LayoutDashboard size={16} />
                <span>会计工作台</span>
              </button>
              <button
                className="nav-item"
                disabled={locked}
                onClick={() => {
                  if (!locked) {
                    onAccountingFilter('pending');
                    setMobileOpen(false);
                  }
                }}
              >
                <CheckCheck size={16} />
                <span>待审核凭证</span>
              </button>
              <button
                className="nav-item"
                disabled={locked}
                onClick={() => {
                  if (!locked) {
                    onAccountingFilter('approved');
                    setMobileOpen(false);
                  }
                }}
              >
                <FileCheck2 size={16} />
                <span>审核与导出</span>
              </button>
              <button className="nav-item" disabled={locked} onClick={() => go('revision')}>
                <FileCheck2 size={16} />
                <span>事实修订与调整</span>
              </button>
            </nav>
          </details>
        )}
        <details className="nav-group agent-management-group">
          <summary>处理记录与管理</summary>
          <nav aria-label="处理记录与管理">
            <button className="nav-item" disabled={locked} onClick={() => go('intelligence')}>
              <FileSearch size={16} />
              <span>票据核对异常</span>
            </button>
            <button className="nav-item" disabled={locked} onClick={() => go('queue')}>
              <Layers3 size={16} />
              <span>票据处理任务</span>
            </button>
            {canOperations && (
              <button className="nav-item" disabled={locked} onClick={() => go('operations')}>
                <Activity size={16} />
                <span>费用与运行监控</span>
              </button>
            )}
          </nav>
        </details>
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
