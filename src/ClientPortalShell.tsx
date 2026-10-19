import type { ReactNode } from 'react';
import { FileCheck2, FileText, Home, LogOut, MessageSquareText, PieChart } from 'lucide-react';
import type { Session } from './types';
import type { WorkspaceProfile } from './workspace-types';
import { clientPortalTabs, presentationRole, roleHome } from './workspace-presentation';
import './client-portal.css';

export type ClientPortalTab = 'home' | 'submit' | 'materials' | 'messages' | 'briefing';

export function ClientPortalShell({
  session,
  profile,
  current,
  onNavigate,
  onBriefing,
  onLogout,
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
  persona?: string;
  personas?: { id: string; label: string }[];
  onPersonaChange?: (value: string) => void;
  children: ReactNode;
}) {
  const allTabs: { id: ClientPortalTab; label: string; icon: typeof Home }[] = [
    { id: 'home', label: '待办', icon: Home },
    { id: 'submit', label: '提交', icon: FileText },
    { id: 'materials', label: '资料清单', icon: FileCheck2 },
    { id: 'messages', label: '消息', icon: MessageSquareText },
    { id: 'briefing', label: '本月简报', icon: PieChart },
  ];
  const allowed = new Set(clientPortalTabs(profile));
  const tabs = allTabs.filter((tab) => allowed.has(tab.id));
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
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <nav className="client-portal-tabs" aria-label="客户服务">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              className={current === tab.id ? 'active' : ''}
              aria-current={current === tab.id ? 'page' : undefined}
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
