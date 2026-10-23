import type { ReactNode } from 'react';
import { FileCheck2, FileText, Home, LogOut, MessageSquareText, PieChart } from 'lucide-react';
import type { Session } from './types';
import type { WorkspaceProfile } from './workspace-types';
import { clientPortalNav, presentationRole, roleHome } from './workspace-presentation';
import './client-portal.css';

export type ClientPortalTab = 'home' | 'submit' | 'materials' | 'messages' | 'briefing';

export function ClientPortalShell({
  session,
  profile,
  current,
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
  current: ClientPortalTab;
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
  const home = roleHome(presentationRole(profile));
  return (
    <div className="client-portal">
      <header className="client-portal-header">
        <div>
          <span className="client-portal-mark">衡账</span>
          <span className="client-portal-firm">{session.firm_name}</span>
        </div>
        <div className="client-portal-user">
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
          <span>{session.user_name}</span>
          <small>{home.name}</small>
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
                    会计工作区
                  </button>
                )}
                {profile.can_manage && onAdmin && (
                  <button type="button" disabled={navigationLocked} onClick={onAdmin}>
                    企业与账号
                  </button>
                )}
              </div>
            </details>
          )}
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <nav className="client-portal-tabs" aria-label="客户服务">
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
