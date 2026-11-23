export type WorkflowCondition = {
  amount_cents_gte?: number;
  amount_cents_lt?: number;
};

export type WorkflowEdge = {
  to: string;
  condition?: WorkflowCondition;
};

export type WorkflowNode = {
  id: string;
  label: string;
  role: string;
  approval?: boolean;
  terminal?: boolean;
  irreversible?: boolean;
  external_locked?: boolean;
  start?: boolean;
  edges: WorkflowEdge[];
};

export type WorkflowRouting = {
  auto_approve_below_cents: number;
  dual_approval_from_cents: number;
  escalate_after_hours: number;
};

export type WorkflowDefinition = {
  id: string;
  company_id: string;
  process_kind: string;
  process_label: string;
  version: number;
  status: string;
  nodes: WorkflowNode[];
  routing: WorkflowRouting;
  content_fingerprint: string;
  note: string;
  rollback_from_version: number | null;
  created_by: string;
  created_at: string;
  published_by: string | null;
  published_at: string | null;
};

export type WorkflowDiffField = { before: unknown; after: unknown };

export type WorkflowDiff = {
  added_nodes: string[];
  removed_nodes: string[];
  changed_nodes: { id: string; fields: Record<string, WorkflowDiffField> }[];
  routing: Record<string, WorkflowDiffField>;
  start_changed: boolean;
  identical: boolean;
  from?: { id: string; version: number; status: string; published_at: string | null };
  to?: { id: string; version: number; status: string; published_at: string | null };
};

export type WorkflowStepLog = {
  node_id: string;
  action: string;
  from_node: string;
  to_node: string;
  actor_user_id: string;
  reason: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type WorkflowInstance = {
  id: string;
  company_id: string;
  book_id: string;
  definition_id: string;
  definition_version: number;
  process_kind: string;
  process_label: string;
  amount_cents: number;
  current_node: string;
  path: string[];
  status: string;
  suspended_reason: string;
  terminal_external_locked: boolean;
  irreversible_reached: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  logs?: WorkflowStepLog[];
};

export type WorkflowProcess = {
  process_kind: string;
  label: string;
  summary: string;
  template: { nodes: WorkflowNode[]; routing: WorkflowRouting };
  active: WorkflowDefinition | null;
  versions: WorkflowDefinition[];
  instance_counts: Record<string, number>;
};

export type WorkflowOverview = {
  company_id: string;
  company_name: string;
  can_manage: boolean;
  processes: WorkflowProcess[];
  routing_rule_version: string;
};

export type WorkflowInstanceList = {
  company_id: string;
  status: string;
  items: WorkflowInstance[];
  total: number;
};

export const BOX_WIDTH = 176;
export const BOX_HEIGHT = 64;
const GAP_X = 88;
const GAP_Y = 96;
const PADDING = 24;

const ROLE_LABELS: Record<string, string> = {
  creator: '发起人',
  reviewer: '复核人',
  manager: '主管',
  finance: '财务',
  cashier: '出纳',
};

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  active: '生效中',
  archived: '已归档',
  running: '办理中',
  suspended: '已挂起',
  completed: '已完成',
  rejected: '已驳回',
  escalated: '已升级主管',
};

const ACTION_LABELS: Record<string, string> = {
  start: '发起',
  approve: '审批通过',
  reject: '驳回',
  suspend: '挂起',
  resume: '恢复',
  rollback: '回退',
  escalate_timeout: '超时升级',
  auto_approve: '低风险直通',
  external_submit: '提交外部系统',
};

const ROUTING_LABELS: Record<string, string> = {
  auto_approve_below_cents: '低风险直通门槛',
  dual_approval_from_cents: '双人审批门槛',
  escalate_after_hours: '超时升级时限',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function routingLabel(key: string): string {
  return ROUTING_LABELS[key] ?? key;
}

/** 整数分 → 「1,234.56」；全程整数运算，不做浮点解析。 */
export function formatCents(cents: number): string {
  const value = Math.trunc(cents);
  const negative = value < 0;
  const absolute = Math.abs(value);
  const yuan = String(Math.floor(absolute / 100));
  const fraction = String(absolute % 100).padStart(2, '0');
  const grouped = yuan.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${grouped}.${fraction}`;
}

export function amountLabel(cents: number): string {
  return `${formatCents(cents)} 元`;
}

/** 「1234.5」→ 123450；非法输入返回 null。用整数字符串拼接，不用 parseFloat。 */
export function centsFromYuanInput(text: string): number | null {
  const value = text.trim();
  if (!/^\d{1,12}(\.\d{0,2})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0') || '0');
}

export function yuanInputFromCents(cents: number): string {
  const value = Math.trunc(cents);
  const whole = Math.floor(value / 100);
  const fraction = value % 100;
  return fraction ? `${whole}.${String(fraction).padStart(2, '0')}` : String(whole);
}

export function conditionLabel(condition?: WorkflowCondition): string {
  if (!condition) return '';
  const parts: string[] = [];
  if (typeof condition.amount_cents_gte === 'number')
    parts.push(`金额 ≥ ${formatCents(condition.amount_cents_gte)} 元`);
  if (typeof condition.amount_cents_lt === 'number')
    parts.push(`金额 < ${formatCents(condition.amount_cents_lt)} 元`);
  return parts.join(' 且 ');
}

export function shortFingerprint(value: string): string {
  return value.slice(0, 12);
}

export function clip(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

/** 积压实例能否安全回退；返回阻止原因（可直接展示），可回退时返回 null。 */
export function rollbackBlockReason(instance: WorkflowInstance): string | null {
  if (instance.terminal_external_locked)
    return '该流程已过账或已提交银行/税局，红字冲销后才能调整，不能回退';
  if (instance.irreversible_reached) return '该流程已到达不可逆节点，只能走冲销流程，不能回退';
  if (instance.status === 'completed' || instance.status === 'rejected')
    return '流程已结束，不能回退';
  return null;
}

export function routingIssues(routing: WorkflowRouting): string[] {
  const issues: string[] = [];
  if (routing.auto_approve_below_cents > routing.dual_approval_from_cents)
    issues.push('低风险直通门槛不能高于双人审批门槛');
  if (routing.escalate_after_hours < 1 || routing.escalate_after_hours > 720)
    issues.push('超时升级时限必须在 1 到 720 小时之间');
  return issues;
}

export type WorkflowBox = {
  id: string;
  label: string;
  role: string;
  x: number;
  y: number;
  approval: boolean;
  terminal: boolean;
  irreversible: boolean;
  external_locked: boolean;
  start: boolean;
};

export type WorkflowLink = {
  from: string;
  to: string;
  label: string;
  path: string;
  labelX: number;
  labelY: number;
  fallback: boolean;
};

export type WorkflowLayout = {
  width: number;
  height: number;
  boxes: WorkflowBox[];
  links: WorkflowLink[];
  startId: string;
};

/** 纯函数分层布局：按最长路径分列，同列纵向排列，供原生 SVG 渲染（零第三方图形库）。 */
export function layoutWorkflow(nodes: WorkflowNode[]): WorkflowLayout {
  const known = new Set(nodes.map((node) => node.id));
  const depth = new Map<string, number>(nodes.map((node) => [node.id, 0]));
  for (let round = 0; round < nodes.length; round++) {
    let changed = false;
    for (const node of nodes) {
      for (const edge of node.edges ?? []) {
        if (!known.has(edge.to)) continue;
        const next = (depth.get(node.id) ?? 0) + 1;
        if (next > (depth.get(edge.to) ?? 0)) {
          depth.set(edge.to, next);
          changed = true;
        }
      }
    }
    if (!changed) break;
  }
  const columns = new Map<number, WorkflowNode[]>();
  for (const node of nodes) {
    const column = depth.get(node.id) ?? 0;
    const bucket = columns.get(column) ?? [];
    bucket.push(node);
    columns.set(column, bucket);
  }
  const order: string[] = [];
  for (const [, bucket] of [...columns.entries()].sort((left, right) => left[0] - right[0]))
    for (const node of bucket) order.push(node.id);

  const position = new Map<string, { x: number; y: number }>();
  const boxes: WorkflowBox[] = [];
  const columnCount = Math.max(1, columns.size);
  let tallest = 0;
  for (const [column, bucket] of [...columns.entries()].sort((left, right) => left[0] - right[0])) {
    tallest = Math.max(tallest, bucket.length);
    bucket.forEach((node, index) => {
      const x = PADDING + column * (BOX_WIDTH + GAP_X);
      const y = PADDING + index * (BOX_HEIGHT + GAP_Y);
      position.set(node.id, { x, y });
      boxes.push({
        id: node.id,
        label: clip(node.label, 12),
        role: roleLabel(node.role),
        x,
        y,
        approval: node.approval === true,
        terminal: node.terminal === true,
        irreversible: node.irreversible === true,
        external_locked: node.external_locked === true,
        start: node.start === true,
      });
    });
  }
  const links: WorkflowLink[] = [];
  for (const node of nodes) {
    const source = position.get(node.id);
    if (!source) continue;
    for (const edge of node.edges ?? []) {
      const target = position.get(edge.to);
      if (!source || !target) continue;
      const x1 = source.x + BOX_WIDTH;
      const y1 = source.y + BOX_HEIGHT / 2;
      const x2 = target.x;
      const y2 = target.y + BOX_HEIGHT / 2;
      const bend = Math.max(40, Math.abs(x2 - x1) / 2);
      const multiple = (node.edges ?? []).length > 1;
      links.push({
        from: node.id,
        to: edge.to,
        label: edge.condition ? conditionLabel(edge.condition) : multiple ? '其他情形' : '',
        fallback: !edge.condition && multiple,
        labelX: (x1 + x2) / 2 - 12,
        labelY: (y1 + y2) / 2 - 8,
        path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
      });
    }
  }
  const width = PADDING * 2 + columnCount * BOX_WIDTH + Math.max(0, columnCount - 1) * GAP_X;
  const height = PADDING * 2 + tallest * BOX_HEIGHT + Math.max(0, tallest - 1) * GAP_Y;
  const startId = nodes.find((node) => node.start)?.id ?? order[0] ?? '';
  return { width, height, boxes, links, startId };
}

export function nodeLabel(nodes: WorkflowNode[], id: string): string {
  return nodes.find((node) => node.id === id)?.label ?? id;
}

/** 版本差异转成可直接展示的中文行，避免前端自造口径。 */
export function diffLines(diff: WorkflowDiff, nodes: WorkflowNode[] = []): string[] {
  const lines: string[] = [];
  for (const [key, field] of Object.entries(diff.routing ?? {})) {
    const before = typeof field.before === 'number' ? field.before : null;
    const after = typeof field.after === 'number' ? field.after : null;
    const render = (value: number | null) =>
      key === 'escalate_after_hours'
        ? `${value ?? '—'} 小时`
        : `${value === null ? '—' : formatCents(value)} 元`;
    lines.push(`${routingLabel(key)}：${render(before)} → ${render(after)}`);
  }
  for (const id of diff.added_nodes ?? []) lines.push(`新增节点：${id}`);
  for (const id of diff.removed_nodes ?? []) lines.push(`移除节点：${id}`);
  for (const node of diff.changed_nodes ?? []) {
    const fields = Object.keys(node.fields ?? {}).join('、');
    lines.push(`调整节点：${nodeLabel(nodes, node.id)}（${fields}）`);
  }
  if (diff.start_changed) lines.push('起点节点已更换');
  if (!lines.length) lines.push('两个版本规则与指纹一致，没有差异');
  return lines;
}
