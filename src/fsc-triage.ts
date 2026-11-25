import type { WorkspaceTask } from './workspace-types';

export type TriageLane = 'green_pass' | 'high_risk' | 'waiting' | 'standard';

export interface FscTriagePolicy {
  /** 单笔绿通合规包最高金额（元），超过即进入高风险人工复核红线 */
  greenMaxYuan: number;
  /** 是否强制拦截首次出现的收款账号/往来户 */
  blockNewCounterparty: boolean;
  /** 是否强制拦截跨期发票或缺失发票异常 */
  blockCrossPeriod: boolean;
  /** 审批流转时是否醒目提醒核对原办公审批单号 */
  requireOaRefHint: boolean;
}

export const DEFAULT_TRIAGE_POLICY: FscTriagePolicy = {
  greenMaxYuan: 5000,
  blockNewCounterparty: true,
  blockCrossPeriod: true,
  requireOaRefHint: true,
};

const POLICY_STORAGE_KEY = 'hengzhang:fsc:triage-policy:v1';

export function readTriagePolicy(): FscTriagePolicy {
  try {
    const raw = window.localStorage.getItem(POLICY_STORAGE_KEY);
    if (!raw) return DEFAULT_TRIAGE_POLICY;
    const parsed = JSON.parse(raw) as Partial<FscTriagePolicy>;
    return {
      greenMaxYuan:
        typeof parsed.greenMaxYuan === 'number' &&
        Number.isFinite(parsed.greenMaxYuan) &&
        parsed.greenMaxYuan >= 100 &&
        parsed.greenMaxYuan <= 500_000
          ? Math.round(parsed.greenMaxYuan)
          : DEFAULT_TRIAGE_POLICY.greenMaxYuan,
      blockNewCounterparty:
        typeof parsed.blockNewCounterparty === 'boolean'
          ? parsed.blockNewCounterparty
          : DEFAULT_TRIAGE_POLICY.blockNewCounterparty,
      blockCrossPeriod:
        typeof parsed.blockCrossPeriod === 'boolean'
          ? parsed.blockCrossPeriod
          : DEFAULT_TRIAGE_POLICY.blockCrossPeriod,
      requireOaRefHint:
        typeof parsed.requireOaRefHint === 'boolean'
          ? parsed.requireOaRefHint
          : DEFAULT_TRIAGE_POLICY.requireOaRefHint,
    };
  } catch {
    return DEFAULT_TRIAGE_POLICY;
  }
}

export function saveTriagePolicy(policy: FscTriagePolicy): void {
  try {
    window.localStorage.setItem(POLICY_STORAGE_KEY, JSON.stringify(policy));
  } catch {
    // 忽略无存储环境
  }
}

const STRICT_AMOUNT_PATTERN = /^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/;

export function parseAmountYuan(raw: string | null | undefined): number | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const cleaned = trimmed.replace(/^[¥￥]\s*/, '').replace(/,/g, '');
  if (!STRICT_AMOUNT_PATTERN.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? Math.abs(n) : null;
}

export interface TriageDecision {
  lane: TriageLane;
  badge: string;
  reasons: string[];
}

const HIGH_RISK_KINDS = new Set(['case', 'alert', 'fee_variance', 'bridge_return']);
const HIGH_RISK_STATES = new Set(['failed', 'exception', 'returned', 'blocked', 'rejected']);
const WAITING_STATES = new Set(['awaiting_info', 'waiting', 'pending_material']);

export function classifyTaskLane(
  task: Pick<WorkspaceTask, 'kind' | 'state' | 'bucket' | 'overdue' | 'amount' | 'title'>,
  policy: FscTriagePolicy = DEFAULT_TRIAGE_POLICY,
): TriageDecision {
  if (task.bucket === 'waiting' || WAITING_STATES.has(task.state)) {
    return {
      lane: 'waiting',
      badge: '待子公司补件',
      reasons: ['正在等待子公司补充票据或确认说明'],
    };
  }

  const reasons: string[] = [];
  const yuan = parseAmountYuan(task.amount);

  if (yuan !== null && yuan > policy.greenMaxYuan) {
    reasons.push(
      `单笔金额 ¥${yuan.toLocaleString('zh-CN')} 超过绿通上限（¥${policy.greenMaxYuan.toLocaleString('zh-CN')}），须人工严审`,
    );
  }
  if (task.overdue) {
    reasons.push('已超目标办理期限，请优先排查卡点');
  }
  if (HIGH_RISK_KINDS.has(task.kind) || HIGH_RISK_STATES.has(task.state)) {
    reasons.push('命中异常或差异红线，须对照原始票据逐项核实');
  }
  if (policy.blockCrossPeriod && /跨期|无票|白条|重复|重号/.test(task.title)) {
    reasons.push('摘要命中跨期、缺票或疑似重报关键词，禁止自动绿通放行');
  }
  if (policy.blockNewCounterparty && /首次|新账号|变更账号/.test(task.title)) {
    reasons.push('涉及首次出现的收款账号，请核对合同公章与银行开户信息');
  }

  if (reasons.length > 0) {
    if (yuan === null) {
      reasons.unshift('金额缺失或格式待确认，请打开原件核对');
    }
    return {
      lane: 'high_risk',
      badge: '高风险拦截',
      reasons,
    };
  }

  if (yuan === null) {
    return {
      lane: 'standard',
      badge: '金额待核对',
      reasons: ['金额缺失或格式待确认，请打开原件核对，不可直接绿通放行'],
    };
  }

  if (['review', 'business', 'approval', 'finance', 'handoff', 'intake'].includes(task.kind)) {
    return {
      lane: 'green_pass',
      badge: '绿通合规包',
      reasons: [
        `金额在 ¥${policy.greenMaxYuan.toLocaleString('zh-CN')} 绿通范围内，无逾期及红线异常，建议快速放行`,
      ],
    };
  }

  return {
    lane: 'standard',
    badge: '常规待办',
    reasons: ['请按岗位职责核对后处理'],
  };
}

export function classifyReviewDraft(
  row: {
    amount?: string | null;
    issue_count?: number;
    risk_level?: string | null;
    description?: string;
    has_evidence?: boolean;
  },
  policy: FscTriagePolicy = DEFAULT_TRIAGE_POLICY,
): TriageDecision {
  const reasons: string[] = [];
  const yuan = parseAmountYuan(row.amount);

  if ((row.issue_count ?? 0) > 0) {
    reasons.push(`存在 ${row.issue_count} 项校验差异，须人工逐笔核对`);
  }
  if (row.risk_level === 'high' || row.risk_level === 'blocked') {
    reasons.push('系统标记为高风险凭证，严禁批量跳过');
  }
  if (yuan !== null && yuan > policy.greenMaxYuan) {
    reasons.push(
      `金额 ¥${yuan.toLocaleString('zh-CN')} 超过绿通批量上限（¥${policy.greenMaxYuan.toLocaleString('zh-CN')}）`,
    );
  }
  if (row.has_evidence === false) {
    reasons.push('缺少原始票据或原办公审批附件');
  }
  if (policy.blockCrossPeriod && row.description && /跨期|无票|白条|重复/.test(row.description)) {
    reasons.push('摘要命中跨期或无票红线关键词');
  }

  if (reasons.length > 0) {
    if (yuan === null) {
      reasons.unshift('金额缺失或格式待确认，请打开原件核对');
    }
    return { lane: 'high_risk', badge: '红线拦截', reasons };
  }
  if (yuan === null) {
    return {
      lane: 'standard',
      badge: '金额待核对',
      reasons: ['金额缺失或格式待确认，请打开原件核对，不可直接绿通批审'],
    };
  }
  return {
    lane: 'green_pass',
    badge: '绿通可批审',
    reasons: ['符合绿通合规条件，可勾选一键批量复核入账'],
  };
}

const OA_REF_PATTERN = /\[原办公审批单号[:：]\s*([^\]]+)\]/;

export function parseOaRef(text: string | null | undefined): {
  oaRef: string;
  cleanText: string;
} {
  if (!text) return { oaRef: '', cleanText: '' };
  const match = text.match(OA_REF_PATTERN);
  if (!match) return { oaRef: '', cleanText: text.trim() };
  return {
    oaRef: match[1].trim(),
    cleanText: text.replace(OA_REF_PATTERN, '').trim(),
  };
}

export function attachOaRef(text: string, oaRef: string): string {
  const { cleanText } = parseOaRef(text);
  const normalized = oaRef.trim().replace(/[\[\]]/g, '');
  if (!normalized) return cleanText;
  return cleanText
    ? `[原办公审批单号: ${normalized}] ${cleanText}`
    : `[原办公审批单号: ${normalized}]`;
}

export interface ProgressStep {
  label: string;
  status: 'done' | 'current' | 'returned' | 'pending';
}

/** 面向子公司普通员工的「快递物流式 4 节点进度条」 */
export function employeeProgressSteps(state: string): ProgressStep[] {
  const returned = state === 'returned' || state === 'rejected' || state === 'awaiting_info';
  if (returned) {
    return [
      { label: '已提交', status: 'done' },
      { label: '退回待补件', status: 'returned' },
      { label: '共享中心入账', status: 'pending' },
      { label: '出纳打款', status: 'pending' },
    ];
  }
  if (state === 'paid' || state === 'settled' || state === 'exported' || state === 'completed') {
    return [
      { label: '已提交', status: 'done' },
      { label: '原审批核验', status: 'done' },
      { label: '共享中心入账', status: 'done' },
      { label: '出纳已打款', status: 'done' },
    ];
  }
  if (state === 'handed_off' || state === 'finance_approved' || state === 'approved') {
    return [
      { label: '已提交', status: 'done' },
      { label: '原审批核验', status: 'done' },
      { label: '共享中心审核中', status: 'current' },
      { label: '出纳待打款', status: 'pending' },
    ];
  }
  if (state === 'department_approved') {
    return [
      { label: '已提交', status: 'done' },
      { label: '子公司已放行', status: 'current' },
      { label: '共享中心入账', status: 'pending' },
      { label: '出纳打款', status: 'pending' },
    ];
  }
  return [
    { label: '已提交', status: 'done' },
    { label: '待核对原审批', status: 'current' },
    { label: '共享中心入账', status: 'pending' },
    { label: '出纳打款', status: 'pending' },
  ];
}
