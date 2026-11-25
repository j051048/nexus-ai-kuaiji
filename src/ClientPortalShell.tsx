import type { ReactNode } from 'react';
import {
  FileCheck2,
  FileText,
  Home,
  LogOut,
  MessageSquareText,
  PieChart,
  Smartphone,
  Sparkles,
} from 'lucide-react';
import type { Me, Session } from './types';
import type { WorkspaceProfile } from './workspace-types';
import { clientPortalNav, presentationRole } from './workspace-presentation';
import { fscRoleProfile } from './fsc-shell';
import { isStandalonePwa, openPwaInstallGuide } from './pwa-install-trigger';
import { AccountIdentity } from './AccountIdentity';
import './client-portal.css';

export type ClientPortalTab = 'home' | 'submit' | 'materials' | 'messages' | 'briefing';

export function ClientPortalShell({
  session,
  profile,
  me,
  identityUnavailable = false,
  current,
  companyId,
  onCompanyChange,
  manualMode = false,
  onToggleManualMode,
  onOpenAssistant,
  assistantOpen = false,
  onNavigate,
  onBriefing,
  onLogout,
  onAccounting,
  onAdmin,
  navigationLocked = false,
  persona,
  personas,
  onPersonaChange,
  children,
}: {
  session: Session;
  profile: WorkspaceProfile;
  me?: Me | null;
  identityUnavailable?: boolean;
  current: ClientPortalTab;
  companyId?: string;
  onCompanyChange?: (companyId: string) => void;
  manualMode?: boolean;
  onToggleManualMode?: () => void;
  onOpenAssistant?: () => void;
  assistantOpen?: boolean;
  onNavigate: (tab: ClientPortalTab) => void;
  onBriefing: () => void;
  onLogout: () => void;
  onAccounting?: () => void;
  onAdmin?: () => void;
  navigationLocked?: boolean;
  persona?: string;
  personas?: { id: string; label: string }[];
  onPersonaChange?: (value: string) => void;
  children: ReactNode;
}) {
  const icons = {
    home: Home,
    submit: FileText,
    materials: FileCheck2,
    messages: MessageSquareText,
    briefing: PieChart,
  };
  const tabs = clientPortalNav(profile);
  const role = presentationRole(profile);
  const activeCompanyName =
    profile.companies.find((item) => item.id === companyId)?.name ??
    (profile.companies.length === 1 ? profile.companies[0].name : undefined);
  const roleMeta = fscRoleProfile(role, {
    companyName: activeCompanyName,
    companyCount: profile.companies.length,
  });

  return (
    <div className="client-portal">
      <header className="client-portal-header">
        <div>
          <span className="client-portal-mark">衡账</span>
          <span className="client-portal-firm">
            {session.firm_name} · {roleMeta.title}
          </span>
        </div>
        <div className="client-portal-user">
          {profile.companies.length > 1 && onCompanyChange && (
            <select
              aria-label="当前子公司"
              value={companyId ?? ''}
              disabled={navigationLocked}
              onChange={(event) => onCompanyChange(event.target.value)}
            >
              <option value="">全部已授权子公司</option>
              {profile.companies.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </select>
          )}
          {onOpenAssistant && (
            <button
              type="button"
              className={'client-portal-assistant-btn' + (assistantOpen ? ' active' : '')}
              aria-label="打开财税智能副驾"
              disabled={navigationLocked}
              onClick={onOpenAssistant}
            >
              <Sparkles size={15} aria-hidden="true" />
              <span>财税助手</span>
            </button>
          )}
          {onToggleManualMode && (
            <button
              type="button"
              className={'client-portal-mode-btn' + (manualMode ? ' manual' : '')}
              aria-pressed={manualMode}
              title={
                manualMode
                  ? '当前为纯手工填单模式，点击恢复智能辅助'
                  : '智能辅助在线，断网或需纯手工时可随时切换'
              }
              onClick={onToggleManualMode}
            >
              {manualMode ? '纯手工' : '智能在线'}
            </button>
          )}
          {!!personas?.length && onPersonaChange && (
            <select
              aria-label="切换验证身份"
              value={persona}
              disabled={navigationLocked}
              onChange={(event) => onPersonaChange(event.target.value)}
            >
              {personas.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          )}
          {((profile.can_accounting && onAccounting) || (profile.can_manage && onAdmin)) && (
            <details className="client-portal-settings">
              <summary
                aria-disabled={navigationLocked}
                onClick={(event) => {
                  if (navigationLocked) event.preventDefault();
                }}
              >
                设置
              </summary>
              <div>
                {profile.can_accounting && onAccounting && (
                  <button type="button" disabled={navigationLocked} onClick={onAccounting}>
                    共享财务中台
                  </button>
                )}
                {profile.can_manage && onAdmin && (
                  <button type="button" disabled={navigationLocked} onClick={onAdmin}>
                    子公司与人员
                  </button>
                )}
              </div>
            </details>
          )}
          {!isStandalonePwa() && (
            <button
              type="button"
              aria-label="添加到手机桌面"
              title="添加到手机桌面"
              onClick={openPwaInstallGuide}
            >
              <Smartphone size={16} aria-hidden="true" />
            </button>
          )}
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <AccountIdentity
        session={session}
        profile={profile}
        me={me}
        unavailable={identityUnavailable}
      />
      <p className="client-portal-duty-tip">
        <span className="client-portal-duty-badge">本岗指引</span>
        <span>{roleMeta.dutySummary}</span>
      </p>
      <nav className="client-portal-tabs" aria-label="子公司财税服务">
        {tabs.map((tab) => {
          const Icon = icons[tab.id];
          return (
            <button
              key={tab.id}
              type="button"
              className={current === tab.id ? 'active' : ''}
              aria-current={current === tab.id ? 'page' : undefined}
              disabled={navigationLocked}
              onClick={() => (tab.id === 'briefing' ? onBriefing() : onNavigate(tab.id))}
            >
              <Icon size={18} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="client-portal-body">{children}</div>
    </div>
  );
}
