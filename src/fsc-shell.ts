import type { PresentationRole } from './workspace-types';

export interface FscRoleProfile {
  /** 集团共享财务中心 4+2 岗位对外中文称谓 */
  title: string;
  /** 所属端侧分类：共享中心专业中台 或 子公司极简门户 */
  groupLabel: '共享财务中心' | '子公司门户';
  /** 首屏一句大白话岗位职责与优先事项指引 */
  dutySummary: string;
  /** 聊天副驾顶部「账号级别数据查询权限边界徽章」 */
  scopeBadge: string;
  /** 权限边界补充说明（防越权透明提示） */
  scopeDetail: string;
  /** 贴合本岗位日常习惯的 4 个高频大白话一键提问气泡 */
  quickPrompts: string[];
}

const MANUAL_MODE_KEY = 'hengzhang:ui:manual-mode:v1';
const ASSISTANT_DOCK_KEY = 'hengzhang:ui:assistant-docked:v1';

const ROLE_PROFILES: Record<PresentationRole, FscRoleProfile> = {
  firm_owner: {
    title: '共享中心负责人 / 集团领导',
    groupLabel: '共享财务中心',
    dutySummary: '总览集团各子公司收支盈亏、结账锁期进度、内部集采抵销与共享中心人员负荷。',
    scopeBadge: '全集团汇总数据范围',
    scopeDetail: '可查询全集团已授权子公司的经营收支、结账进度与合并抵销数据。',
    quickPrompts: [
      '本月各家子公司谁盈利最多、哪家有亏损风险？',
      '还有哪几家子公司本月尚未完成报税和月末结账？',
      '后勤集采和内部医院、物业之间的内部往来核对清楚了吗？',
      '目前共享中心财务人员各自的待办工作负荷怎么样？',
    ],
  },
  admin: {
    title: '共享中心管理员',
    groupLabel: '共享财务中心',
    dutySummary: '管理旗下子公司档案与业态、配置共享中心分管权限矩阵及智能审核规则。',
    scopeBadge: '全集团管理与配置范围',
    scopeDetail: '可查询全集团已授权子公司的运营进度、权限分工与系统配置状态。',
    quickPrompts: [
      '目前哪些子公司还没完成初始化建档或人员分管授权？',
      '本月全集团报税与月末结账整体进度卡在哪个环节？',
      '有哪些高频退回异常可以通过调整共享规则提前拦截？',
      '帮我检查当前已授权子公司的账套启用与运行状态。',
    ],
  },
  reviewer: {
    title: '总账复核主管',
    groupLabel: '共享财务中心',
    dutySummary: '优先放行绿通合规包，逐笔严审高风险红线拦截单，把关正式凭证与月结锁期。',
    scopeBadge: '获授权复核子公司范围',
    scopeDetail: '仅查询您获授权复核的子公司凭证、原件与异常单据，未授权公司自动拦截。',
    quickPrompts: [
      '今天有哪些合规单据和待复核凭证可以优先批量核对？',
      '当前有哪些高风险或存在金额差异的异常单据需要重点复核？',
      '本月还有哪些凭证缺少原始票据或原办公审批依据？',
      '帮我核对当前子公司的借贷试算与科目挂账是否存在异常。',
    ],
  },
  accountant: {
    title: '分管核算会计',
    groupLabel: '共享财务中心',
    dutySummary: '包干处理所分管子公司的票据核对、分录草稿、银企对账及月度报税底稿。',
    scopeBadge: '仅限所分管子公司账套',
    scopeDetail: '严格隔离您分管范围外的子公司数据，仅读取您有权办理的账套与业务单据。',
    quickPrompts: [
      '我分管的子公司今天还有几笔退回或待补件的异常单？',
      '帮我核对当前子公司尚未入账的发票与银行流水差异。',
      '本月税费计提和月末结账前，这家子公司还缺哪些资料？',
      '遇到跨期发票或缺少专票的情况，按规范应该怎么处理？',
    ],
  },
  finance: {
    title: '子公司财务 / 资金核验',
    groupLabel: '子公司门户',
    dutySummary: '核对原办公审批单与原始票据完整性，一键移交集团共享财务中心做账与打款。',
    scopeBadge: '仅限本子公司财务数据',
    scopeDetail: '仅可查询您所属子公司的报销单据、核验进度与财务简报，无法跨公司查看。',
    quickPrompts: [
      '今天有哪些单据已过原办公审批，等我核验移交共享中心？',
      '本公司有哪些单据因为票据或抬头问题被共享中心退回了？',
      '本月本公司还有哪些周期资料尚未提交齐全？',
      '帮我梳理一下本公司近期大额开支和待核销款项。',
    ],
  },
  manager: {
    title: '子公司总经理 / 负责人',
    groupLabel: '子公司门户',
    dutySummary: '核对原办公审批结果一键放行，随时掌握本公司本月经营收支简报与资料进度。',
    scopeBadge: '仅限本子公司经营数据',
    scopeDetail: '仅可查询您负责的子公司经营简报、审批单据与财务进度，严格隔离其他子公司。',
    quickPrompts: [
      '本公司本月总收入、总支出和净结余大概是多少？',
      '有哪些已过原办公审批的单据正等我确认放行？',
      '本月本公司开支最高的前几项费用花在了哪里？',
      '集团共享财务中心退回了我们公司哪些待补件单据？',
    ],
  },
  approver: {
    title: '子公司部门负责人',
    groupLabel: '子公司门户',
    dutySummary: '核对本部门业务用途与原办公审批结果，一键放行或说明原因退回补件。',
    scopeBadge: '仅限本部门审批范围',
    scopeDetail: '仅可查询您分管部门的待审批事项与关联资料，无法越权查看其他部门或公司账套。',
    quickPrompts: [
      '本部门今天还有几笔单据正等我确认放行？',
      '有没有超过预期还没处理完的本部门报销或费用单？',
      '近期本部门有哪些单据被财务退回要求补充资料？',
      '核对原办公审批结果后，在这里放行需要注意什么？',
    ],
  },
  employee: {
    title: '子公司员工 / 报销人',
    groupLabel: '子公司门户',
    dutySummary: '拍照传票、附上原办公审批单号即可提交报销，像查快递一样随时跟踪打款进度。',
    scopeBadge: '仅限本人提交的单据',
    scopeDetail: '严格保护隐私，您只能查询自己提交的报销与收支单据，看不到他人和公司账簿。',
    quickPrompts: [
      '我最近提交的报销单现在审核到哪一步了？',
      '被退回的那笔单据具体缺什么资料，怎么补交？',
      '提交报销或日常垫付时，必须填写原办公审批单号吗？',
      '开具电子发票时，公司抬头和税号应该注意什么？',
    ],
  },
  viewer: {
    title: '只读查阅成员',
    groupLabel: '子公司门户',
    dutySummary: '查看当前授权范围内的单据进度、周期资料清单与已发布的月度经营简报。',
    scopeBadge: '仅限当前只读授权范围',
    scopeDetail: '仅支持只读查阅当前授权数据，不可发起审批、改账或导出动作。',
    quickPrompts: [
      '当前授权范围内有哪些已发布的月度经营简报？',
      '本月还有哪些周期资料清单尚未上传齐备？',
      '帮我汇总当前可查看范围内的待处理事项状态。',
      '如何区分草稿数据和已经正式月结发布的数据？',
    ],
  },
};

export function fscRoleProfile(
  role: PresentationRole,
  options?: { companyName?: string; companyCount?: number },
): FscRoleProfile {
  const base = ROLE_PROFILES[role] ?? ROLE_PROFILES.viewer;
  const companyName = options?.companyName?.trim();
  const companyCount = options?.companyCount ?? 0;

  let scopeBadge = base.scopeBadge;
  if (companyName) {
    if (role === 'firm_owner' || role === 'admin') {
      scopeBadge = `当前聚焦：${companyName}（全集团权限）`;
    } else if (role === 'reviewer' || role === 'accountant') {
      scopeBadge = `当前子公司：${companyName}`;
    } else if (role !== 'employee') {
      scopeBadge = `仅限：${companyName}`;
    }
  } else if (companyCount > 1 && (role === 'firm_owner' || role === 'admin')) {
    scopeBadge = `全集团 ${companyCount} 家子公司汇总范围`;
  } else if (companyCount > 1 && (role === 'accountant' || role === 'reviewer')) {
    scopeBadge = `所分管 ${companyCount} 家子公司范围`;
  }

  return {
    ...base,
    scopeBadge,
  };
}

export function readManualMode(): boolean {
  try {
    return window.sessionStorage.getItem(MANUAL_MODE_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeManualMode(enabled: boolean): void {
  try {
    if (enabled) window.sessionStorage.setItem(MANUAL_MODE_KEY, '1');
    else window.sessionStorage.removeItem(MANUAL_MODE_KEY);
  } catch {
    // 隐私模式或无存储环境静默跳过，仅保持内存态
  }
}

export function readAssistantDocked(): boolean {
  try {
    return window.sessionStorage.getItem(ASSISTANT_DOCK_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeAssistantDocked(docked: boolean): void {
  try {
    if (docked) window.sessionStorage.setItem(ASSISTANT_DOCK_KEY, '1');
    else window.sessionStorage.removeItem(ASSISTANT_DOCK_KEY);
  } catch {
    // 隐私模式静默跳过
  }
}
