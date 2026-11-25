import type { PresentationRole } from './workspace-types';
import type { WorkspaceView } from './workspace-route';

export type PresentationNavItem = { id: string; label: string; hint: string };

export type NavigationCapabilities = {
  accounting: boolean;
  monthly: boolean;
  review: boolean;
  operations: boolean;
  quality: boolean;
  processing: boolean;
  business: boolean;
  admin: boolean;
  access: boolean;
};

type Gate = keyof NavigationCapabilities | 'always';
type Definition = { label: string; hint: string; gate: Gate };

/** Shared menu vocabulary. Gates only project existing capabilities; they never authorize an API. */
const definitions = {
  home: { label: '待办收件箱', hint: '需要我优先处理的事项', gate: 'always' },
  firm: { label: '集团总览', hint: '各子公司进度与分工协同', gate: 'operations' },
  insights: { label: '经营驾驶舱', hint: '全集团收支图表与大屏', gate: 'operations' },
  onboarding: { label: '子公司开通', hint: '新公司建档与业态检查', gate: 'operations' },
  inbox: { label: '异常分派箱', hint: '拦截事项分派和催办', gate: 'operations' },
  accounting: { label: '总账与凭证', hint: '子公司账套、凭证与对账', gate: 'accounting' },
  monthly: { label: '报税与月结', hint: '月度报税底稿与结账锁期', gate: 'monthly' },
  materials: { label: '资料清单', hint: '每月应交票据与对账单', gate: 'always' },
  business: { label: '报销与收支', hint: '核对原办公审批并移交', gate: 'business' },
  cases: { label: '高风险与补件', hint: '拦截单据与退回补件', gate: 'always' },
  review: { label: '批量复核', hint: '绿通放行与凭证同屏复核', gate: 'review' },
  revision: { label: '冲销与调整', hint: '保留轨迹并做红字更正', gate: 'accounting' },
  messages: { label: '消息通知', hint: '退回提醒与协同留言', gate: 'always' },
  briefing: { label: '经营简报', hint: '本公司月度收支一页纸', gate: 'always' },
  admin: { label: '子公司与人员', hint: '子公司建档、岗位与授权', gate: 'admin' },
  access: { label: '分管权限矩阵', hint: '共享中心 5～6 人分管设置', gate: 'access' },
  ingestion: { label: '外部数据接入', hint: '银企或第三方导入状态', gate: 'operations' },
  observability: { label: '系统运行诊断', hint: '队列、锁与接口耗时', gate: 'operations' },
  operations: { label: '智能用量监控', hint: '模型额度与异常告警', gate: 'operations' },
  runs: { label: '后台处理记录', hint: '识别任务追踪与人工接管', gate: 'processing' },
  queue: { label: '票据识别队列', hint: '发票解析进度与重试', gate: 'processing' },
  intelligence: { label: '票据核对异常', hint: '查看识别差异与验真记录', gate: 'processing' },
  quality: { label: '规则效果评测', hint: '验证自动做账准确率', gate: 'quality' },
  portfolio: { label: '人员效率负荷', hint: '共享中心人效与工时分布', gate: 'accounting' },
} as const satisfies Record<string, Definition>;

export type NavigationId = keyof typeof definitions;

const roleEntries: Record<PresentationRole, NavigationId[]> = {
  employee: ['home', 'business', 'materials', 'messages'],
  approver: ['home', 'business', 'materials', 'messages'],
  finance: ['home', 'business', 'materials', 'messages', 'briefing'],
  manager: ['home', 'business', 'materials', 'messages', 'briefing'],
  accountant: ['home', 'accounting', 'business', 'cases', 'monthly'],
  reviewer: ['review', 'cases', 'monthly', 'accounting'],
  firm_owner: ['insights', 'firm', 'accounting', 'monthly', 'cases'],
  admin: ['insights', 'firm', 'accounting', 'monthly', 'cases'],
  viewer: ['home', 'materials', 'briefing'],
};

type Copy = Pick<PresentationNavItem, 'label' | 'hint'>;
const roleCopy: Partial<Record<PresentationRole, Partial<Record<NavigationId, Partial<Copy>>>>> = {
  employee: {
    home: { label: '我的进度', hint: '退回补件与打款进度' },
    business: { label: '我要报销', hint: '3 步提单与补交发票' },
  },
  approver: {
    home: { label: '待我放行', hint: '核对原办公审批一键过' },
    business: { label: '部门单据', hint: '放行、退回与历史记录' },
    materials: { hint: '本部门周期资料' },
  },
  finance: {
    home: { label: '待核验移交', hint: '原审批与票据完整性' },
    business: { label: '收支核验', hint: '核对并移交共享中心' },
    messages: { hint: '与共享中心协同沟通' },
  },
  manager: {
    home: { label: '公司首页', hint: '一键放行与经营速览' },
    business: { label: '待审与单据', hint: '核对原办公审批放行' },
    materials: { hint: '本公司应交资料' },
    messages: { hint: '财务协同通知' },
  },
  accountant: {
    home: { label: '待办收件箱', hint: '所分管子公司今日待办' },
    accounting: { label: '总账与凭证', hint: '做凭证、查账簿、银企对账' },
    business: { label: '子公司单据', hint: '核对报销与原审批凭证' },
    cases: { hint: '高风险拦截与补件跟踪' },
    monthly: { hint: '报税底稿与月末结账' },
  },
  reviewer: {
    review: { label: '复核收件箱', hint: '绿通批量放行与红线严审' },
    cases: { hint: '拦截异常与退回重提' },
    monthly: { hint: '月结把关与锁期发布' },
    accounting: { label: '总账与账簿', hint: '查凭证与科目余额表' },
  },
  firm_owner: {
    insights: { label: '经营驾驶舱', hint: '5 家子公司图表与投大屏' },
    firm: { label: '集团总览', hint: '结账进度与人员分工' },
    accounting: { label: '子公司账套', hint: '穿透查看各公司账簿' },
    cases: { hint: '全集团卡点与异常阻塞' },
  },
  admin: {
    insights: { label: '经营驾驶舱', hint: '5 家子公司图表与投大屏' },
    firm: { label: '集团总览', hint: '结账进度与人员分工' },
    accounting: { label: '子公司账套', hint: '切换查看各公司账簿' },
    cases: { hint: '全集团卡点与异常阻塞' },
  },
  viewer: {
    home: { label: '首页', hint: '当前授权事项进度' },
    materials: { hint: '周期资料清单' },
  },
};

export function navigationItem(id: NavigationId, role?: PresentationRole): PresentationNavItem {
  const { label, hint } = definitions[id];
  return { id, label, hint, ...(role ? roleCopy[role]?.[id] : undefined) };
}

export function roleNavigation(role: PresentationRole): PresentationNavItem[] {
  return roleEntries[role].map((id) => navigationItem(id, role));
}

/** Every route has an explicit navigation owner; adding a route requires choosing its owner. */
export const navigationParents = {
  home: 'home',
  firm: 'firm',
  insights: 'insights',
  onboarding: 'onboarding',
  inbox: 'inbox',
  observability: 'observability',
  runs: 'runs',
  business: 'business',
  accounting: 'accounting',
  review: 'review',
  cases: 'cases',
  learning: 'accounting',
  reconciliation: 'accounting',
  delivery: 'accounting',
  briefing: 'briefing',
  ledger: 'accounting',
  'formal-books': 'accounting',
  integration: 'accounting',
  ingestion: 'ingestion',
  revision: 'revision',
  efficiency: 'accounting',
  closing: 'accounting',
  settlement: 'accounting',
  quality: 'quality',
  migration: 'accounting',
  catalog: 'accounting',
  queue: 'queue',
  intelligence: 'intelligence',
  operations: 'operations',
  monthly: 'monthly',
  balances: 'accounting',
  recurring: 'accounting',
  service: 'monthly',
  materials: 'materials',
  messages: 'messages',
  portfolio: 'portfolio',
} as const satisfies Record<WorkspaceView, NavigationId>;

export function navigationCurrent(view: string, businessOpen = false): NavigationId | null {
  if (view === 'accounting' && businessOpen) return 'business';
  return Object.prototype.hasOwnProperty.call(navigationParents, view)
    ? navigationParents[view as WorkspaceView]
    : null;
}

export function navigationAllowed(item: PresentationNavItem, capabilities: NavigationCapabilities) {
  if (!Object.prototype.hasOwnProperty.call(definitions, item.id)) return false;
  const { gate } = definitions[item.id as NavigationId];
  return gate === 'always' || capabilities[gate];
}

const settingGroups: { id: string; label: string; entries: NavigationId[] }[] = [
  { id: 'people', label: '子公司与分管授权', entries: ['admin', 'access'] },
  { id: 'costs', label: '人效与智能用量', entries: ['operations', 'portfolio'] },
  {
    id: 'processing',
    label: '票据识别与数据接入',
    entries: ['ingestion', 'intelligence', 'queue', 'runs'],
  },
  { id: 'diagnostics', label: '高级运维工具', entries: ['observability', 'quality'] },
];

/** One projection for every professional shell, preserving existing route capabilities. */
export function workspaceNavigation(
  suppliedPrimary: PresentationNavItem[] | undefined,
  capabilities: NavigationCapabilities,
  businessLabel: string = definitions.business.label,
) {
  const settings = settingGroups
    .map((group) => ({
      ...group,
      items: group.entries
        .map((id) => navigationItem(id))
        .filter((item) => navigationAllowed(item, capabilities)),
    }))
    .filter((group) => group.items.length > 0);
  const settingIds = new Set(settings.flatMap((group) => group.items.map((item) => item.id)));
  const primary = (suppliedPrimary ?? [navigationItem('home')]).filter(
    (item) => navigationAllowed(item, capabilities) && !settingIds.has(item.id),
  );
  const used = new Set(primary.map((item) => item.id));
  const accounting = (
    capabilities.accounting ? (['accounting', 'review', 'revision'] as const) : []
  )
    .map((id) => navigationItem(id))
    .filter((item) => navigationAllowed(item, capabilities) && !used.has(item.id));
  accounting.forEach((item) => used.add(item.id));
  const daily = (
    ['home', 'firm', 'onboarding', 'inbox', 'monthly', 'materials', 'business', 'cases'] as const
  )
    .map((id) =>
      id === 'business' ? { ...navigationItem(id), label: businessLabel } : navigationItem(id),
    )
    .filter((item) => navigationAllowed(item, capabilities) && !used.has(item.id));
  return { primary, daily, accounting, settings };
}
