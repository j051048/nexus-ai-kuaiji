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

export function homeProgress(value?: HomeProgress, companyId = ''): HomeProgress {
  const match = value?.companyId === companyId ? value : undefined;
  return {
    bucket: match?.bucket === 'waiting' ? 'waiting' : 'action',
    kind:
      match?.kind && Object.prototype.hasOwnProperty.call(taskLabels, match.kind) ? match.kind : '',
    companyId,
    offset: offset(match?.offset ?? 0),
    sort:
      match?.sort && Object.prototype.hasOwnProperty.call(workspaceSortLabels, match.sort)
        ? match.sort
        : 'priority',
    density: match?.density === 'table' ? 'table' : 'list',
    query: match?.query.slice(0, 120) ?? '',
    optionsOpen: !!match?.optionsOpen,
    scrollY: scroll(match?.scrollY),
    taskKey: match?.taskKey,
  };
}

/** Per-login memory of selections only. Never stores records, attachments, tokens or API responses. */
export class WorkspaceProgress {
  private home?: HomeProgress;
  private monthly?: MonthlyProgress;
  private trail: WorkOrigin[] = [];
  constructor(readonly scope: string) {}

  getHome(companyId = '') {
    return homeProgress(this.home, companyId);
  }
  saveHome(value: HomeProgress) {
    const sameCompany = this.home?.companyId === value.companyId;
    this.home = {
      ...homeProgress(value, value.companyId),
      scrollY: sameCompany ? this.home?.scrollY : 0,
      taskKey: sameCompany ? this.home?.taskKey : undefined,
    };
  }
  getMonthly(period: string): MonthlyProgress | undefined {
    return this.monthly?.period === period ? { ...this.monthly } : undefined;
  }
  saveMonthly(value: MonthlyProgress) {
    this.monthly = {
      period: value.period,
      query: value.query.slice(0, 120),
      search: value.search.slice(0, 120),
      offset: offset(value.offset),
      scrollY: this.monthly?.period === value.period ? this.monthly.scrollY : 0,
    };
  }
  capture(route: WorkspaceRoute, scrollY: number, taskKey?: string) {
    if (route.view === 'home' && this.home)
      this.home = { ...this.home, scrollY: scroll(scrollY), taskKey };
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
  arrived(route: WorkspaceRoute) {
    let index = -1;
    for (let n = this.trail.length - 1; n >= 0; n--) {
      if (workspaceRouteKey(this.trail[n].route) === workspaceRouteKey(route)) {
        index = n;
        break;
      }
    }
    if (index >= 0) this.trail = this.trail.slice(0, index);
  }
  clearTrail() {
    this.trail = [];
  }
}
