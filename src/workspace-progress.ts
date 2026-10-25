import {
  emptyWorkspaceRoute,
  readWorkspaceRoute,
  workspaceRouteKey,
  workspaceRouteUrl,
  type WorkspaceRoute,
} from './workspace-route';
import {
  taskLabels,
  workspaceSortLabels,
  type TaskKind,
  type WorkspaceDensity,
  type WorkspaceSort,
} from './workspace-types';

export interface HomeProgress {
  bucket: 'action' | 'waiting';
  kind: TaskKind | '';
  companyId: string;
  offset: number;
  sort: WorkspaceSort;
  density: WorkspaceDensity;
  query: string;
  optionsOpen: boolean;
  scrollY?: number;
  taskKey?: string;
}
export interface MonthlyProgress {
  period: string;
  query: string;
  search: string;
  offset: number;
  scrollY?: number;
}
export interface WorkOrigin {
  route: WorkspaceRoute;
  label: string;
}
export interface WorkspaceHistoryVisit {
  key: string;
  action: 'POP' | 'PUSH' | 'REPLACE';
}
const copyTrail = (trail: WorkOrigin[]) =>
  trail.map((origin) => ({ ...origin, route: { ...origin.route } }));
const origins: Partial<Record<WorkspaceRoute['view'], string>> = {
  home: '返回待办',
  monthly: '返回客户月度进度',
  service: '返回本月服务单',
  business: '返回原业务事项',
  review: '返回复核清单',
  accounting: '返回会计工作台',
};
const offset = (value: number) =>
  Number.isInteger(value) && value >= 0 ? Math.min(value, 100000) : 0;
const scroll = (value?: number) =>
  Number.isFinite(value) && value! >= 0 ? Math.min(value!, 10000000) : 0;
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isIdentifier = (value: unknown, allowEmpty = false): value is string =>
  typeof value === 'string' &&
  (allowEmpty || value.length > 0) &&
  value.length <= 200 &&
  !/[\u0000-\u001f\u007f]/.test(value);
const isPeriod = (value: unknown): value is string =>
  typeof value === 'string' && /^20\d{2}-(0[1-9]|1[0-2])$/.test(value);
const queryText = (value: unknown) => (typeof value === 'string' ? value.slice(0, 120) : '');
const focusKey = (value: unknown) => (isIdentifier(value) ? value : undefined);

export function homeProgress(value?: HomeProgress, companyId = ''): HomeProgress {
  const validScope = isIdentifier(companyId, true);
  // Invalid scope must not become a match for the valid, all-authorized-companies scope ''.
  const match = validScope && isRecord(value) && value.companyId === companyId ? value : undefined;
  return {
    bucket: match?.bucket === 'waiting' ? 'waiting' : 'action',
    kind:
      typeof match?.kind === 'string' &&
      Object.prototype.hasOwnProperty.call(taskLabels, match.kind)
        ? match.kind
        : '',
    companyId: validScope ? companyId : '',
    offset: offset(match?.offset ?? 0),
    sort:
      typeof match?.sort === 'string' &&
      Object.prototype.hasOwnProperty.call(workspaceSortLabels, match.sort)
        ? match.sort
        : 'priority',
    density: match?.density === 'table' ? 'table' : 'list',
    query: queryText(match?.query),
    optionsOpen: match?.optionsOpen === true,
    scrollY: scroll(match?.scrollY),
    taskKey: focusKey(match?.taskKey),
  };
}

/** Per-login memory of selections only. Never stores records, attachments, tokens or API responses. */
export class WorkspaceProgress {
  private home?: HomeProgress;
  private monthly?: MonthlyProgress;
  private trail: WorkOrigin[] = [];
  private entries = new Map<string, { routeKey: string; trail: WorkOrigin[] }>();
  constructor(readonly scope: string) {}

  getHome(companyId = '') {
    return homeProgress(this.home, companyId);
  }
  saveHome(value: HomeProgress) {
    if (!isRecord(value) || !isIdentifier(value.companyId, true)) {
      this.home = undefined;
      return;
    }
    const sameCompany = this.home?.companyId === value.companyId;
    this.home = {
      ...homeProgress(value, value.companyId),
      scrollY: sameCompany ? this.home?.scrollY : 0,
      taskKey: sameCompany ? this.home?.taskKey : undefined,
    };
  }
  getMonthly(period: string): MonthlyProgress | undefined {
    return isPeriod(period) && this.monthly?.period === period ? { ...this.monthly } : undefined;
  }
  saveMonthly(value: MonthlyProgress) {
    if (!isRecord(value) || !isPeriod(value.period)) {
      this.monthly = undefined;
      return;
    }
    this.monthly = {
      period: value.period,
      query: queryText(value.query),
      search: queryText(value.search),
      offset: offset(value.offset),
      scrollY: this.monthly?.period === value.period ? this.monthly.scrollY : 0,
    };
  }
  capture(route: WorkspaceRoute, scrollY: number, taskKey?: string) {
    if (route.view === 'home' && this.home)
      this.home = { ...this.home, scrollY: scroll(scrollY), taskKey: focusKey(taskKey) };
    if (route.view === 'monthly' && this.monthly)
      this.monthly = { ...this.monthly, scrollY: scroll(scrollY) };
    const label = origins[route.view];
    if (!label) return;
    const safe = readWorkspaceRoute(
      '/workspace/' + route.view,
      workspaceRouteUrl(route).split('?')[1] ?? '',
    );
    if (
      workspaceRouteKey(this.trail.at(-1)?.route ?? emptyWorkspaceRoute) ===
        workspaceRouteKey(safe) &&
      this.trail.length
    )
      return;
    this.trail = [...this.trail.slice(-5), { route: safe, label }];
  }
  origin() {
    const last = this.trail.at(-1);
    return last ? { ...last, route: { ...last.route } } : undefined;
  }
  takeOrigin() {
    const last = this.origin();
    this.trail.pop();
    return last;
  }
  /** Record only after arrival: capture() already builds the destination's trail. */
  rememberEntry(route: WorkspaceRoute, key: string) {
    if (!isIdentifier(key)) return;
    this.entries.delete(key);
    this.entries.set(key, { routeKey: workspaceRouteKey(route), trail: copyTrail(this.trail) });
    if (this.entries.size > 64) this.entries.delete(this.entries.keys().next().value!);
  }
  arrived(route: WorkspaceRoute, visit?: WorkspaceHistoryVisit) {
    if (visit?.action === 'POP') {
      const entry = this.entries.get(visit.key);
      this.trail = entry?.routeKey === workspaceRouteKey(route) ? copyTrail(entry.trail) : [];
      return;
    }
    let index = -1;
    for (let n = this.trail.length - 1; n >= 0; n--) {
      if (workspaceRouteKey(this.trail[n].route) === workspaceRouteKey(route)) {
        index = n;
        break;
      }
    }
    if (index >= 0) this.trail = this.trail.slice(0, index);
    if (visit) this.rememberEntry(route, visit.key);
  }
  clearTrail() {
    this.trail = [];
  }
}
