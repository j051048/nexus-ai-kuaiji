import type { PresentationRole } from './workspace-types';

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
  home: { label: '我的待办', hint: '需要我处理的工作', gate: 'always' },
  firm: { label: '全所概览', hint: '交付进度与人员安排', gate: 'operations' },
  onboarding: { label: '客户开通', hint: '准备资料并完成检查', gate: 'operations' },
  inbox: { label: '异常收件箱', hint: '分派和催办', gate: 'operations' },
  accounting: { label: '会计工作台', hint: '账套与凭证', gate: 'accounting' },
  monthly: { label: '月度交付', hint: '负责人、期限与进度', gate: 'monthly' },
  materials: { label: '资料清单', hint: '周期资料', gate: 'always' },
  business: { label: '业务事项', hint: '提交、核验与移交', gate: 'business' },
  cases: { label: '异常与补件', hint: '处理业务阻塞', gate: 'always' },
  review: { label: '跨企业批量复核', hint: '原件与凭证一起核对', gate: 'review' },
  revision: { label: '事实修订与调整', hint: '保留历史并更正事实', gate: 'accounting' },
  messages: { label: '消息', hint: '通知与沟通', gate: 'always' },
  briefing: { label: '报告', hint: '获授权结果', gate: 'always' },
  admin: { label: '企业与人员管理', hint: '开户、权限与企业资料', gate: 'admin' },
  access: { label: '成员与账套授权', hint: '事务所成员与账套职责', gate: 'access' },
  ingestion: { label: '数据接入', hint: '连接与导入失败', gate: 'operations' },
  observability: { label: '运行可观测性', hint: '队列、锁与耗时', gate: 'operations' },
  operations: { label: '费用与运行监控', hint: '用量、额度与告警', gate: 'operations' },
  runs: { label: '后台运行', hint: '处理记录与接管', gate: 'processing' },
  queue: { label: '票据处理任务', hint: '进度与重试', gate: 'processing' },
  intelligence: { label: '票据核对异常', hint: '查看识别与核对记录', gate: 'processing' },
  quality: { label: '试点评测', hint: '验证处理效果', gate: 'quality' },
  portfolio: { label: '客户人工投入', hint: '查看人工处理时间', gate: 'accounting' },
} as const satisfies Record<string, Definition>;

export type NavigationId = keyof typeof definitions;

const roleEntries: Record<PresentationRole, NavigationId[]> = {
  employee: ['home', 'business', 'materials', 'messages'],
  approver: ['home', 'business', 'materials', 'messages'],
  finance: ['home', 'business', 'materials', 'messages', 'briefing'],
  manager: ['home', 'materials', 'messages', 'briefing'],
  accountant: ['home', 'review', 'business', 'cases', 'monthly'],
  reviewer: ['review', 'cases', 'monthly', 'materials'],
  firm_owner: ['firm', 'accounting', 'monthly', 'cases'],
  admin: ['firm', 'accounting', 'monthly', 'cases'],
  viewer: ['home', 'materials', 'briefing'],
};

type Copy = Pick<PresentationNavItem, 'label' | 'hint'>;
const roleCopy: Partial<Record<PresentationRole, Partial<Record<NavigationId, Partial<Copy>>>>> = {
  employee: {
    home: { label: '首页', hint: '我的退回与进度' },
    business: { label: '我的事项', hint: '提交与补件' },
  },
  approver: {
    home: { label: '待审批', hint: '本部门事项' },
    business: { label: '部门事项', hint: '审批与退回' },
    materials: { hint: '部门资料' },
  },
  finance: {
    home: { label: '待核验', hint: '资料完整性' },
    business: { hint: '核验与移交' },
    messages: { hint: '客户沟通' },
  },
  manager: {
    home: { label: '企业首页', hint: '进度与待确认' },
    materials: { hint: '服务资料' },
    messages: { hint: '沟通记录' },
  },
  accountant: {
    home: { label: '今日工作', hint: '我的异常与草稿' },
    review: { hint: '待复核凭证与差异' },
    business: { label: '客户事项', hint: '待处理业务' },
    cases: { hint: '阻塞与退回' },
    monthly: { hint: '客户期间进度' },
  },
  reviewer: {
    review: { hint: '待复核凭证与差异' },
    cases: { hint: '退回重提' },
    monthly: { hint: '复核与交付' },
    materials: { hint: '证据查看' },
  },
  firm_owner: {
    accounting: { label: '客户企业', hint: '选择客户并继续办理' },
    cases: { hint: '交付阻塞' },
  },
  admin: {
    accounting: { label: '客户企业', hint: '选择客户并继续办理' },
    cases: { hint: '交付阻塞' },
  },
  viewer: {
    home: { label: '首页', hint: '获授权事项' },
    materials: { hint: '获授权资料' },
  },
};

export function navigationItem(id: NavigationId, role?: PresentationRole): PresentationNavItem {
  const { label, hint } = definitions[id];
  return { id, label, hint, ...(role ? roleCopy[role]?.[id] : undefined) };
}

export function roleNavigation(role: PresentationRole): PresentationNavItem[] {
  return roleEntries[role].map((id) => navigationItem(id, role));
}

/** Route aliases and registered pages share the same current-item resolver. */
export function navigationCurrent(view: string, businessOpen = false): NavigationId {
  if (view === 'service') return 'monthly';
  if (view === 'accounting' && businessOpen) return 'business';
  return Object.prototype.hasOwnProperty.call(definitions, view)
    ? (view as NavigationId)
    : 'accounting';
}

export function navigationAllowed(item: PresentationNavItem, capabilities: NavigationCapabilities) {
  if (!Object.prototype.hasOwnProperty.call(definitions, item.id)) return false;
  const { gate } = definitions[item.id as NavigationId];
  return gate === 'always' || capabilities[gate];
}

const settingGroups: { id: string; label: string; entries: NavigationId[] }[] = [
  { id: 'people', label: '人员与授权', entries: ['admin', 'access'] },
  { id: 'costs', label: '费用与人工投入', entries: ['operations', 'portfolio'] },
  {
    id: 'processing',
    label: '资料处理与接入',
    entries: ['ingestion', 'intelligence', 'queue', 'runs'],
  },
  { id: 'diagnostics', label: '技术诊断', entries: ['observability', 'quality'] },
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
