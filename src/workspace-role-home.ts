import type { WorkspaceProfile } from './workspace-types';
import { presentationRole, roleHome } from './workspace-presentation';

export type RoleHomeGuide = {
  label: string;
  focus: string;
  actions: string[];
  boundary: string;
  firstStep: string;
  handoff: string;
  example: string;
};

function firstUse(
  profile: WorkspaceProfile,
): Pick<RoleHomeGuide, 'firstStep' | 'handoff' | 'example'> {
  const role = presentationRole(profile);
  const required = {
    employee: profile.can_submit,
    approver: profile.can_approve,
    finance: profile.can_finance,
    accountant: profile.can_accounting,
    reviewer: profile.can_review,
    firm_owner: profile.can_firm_operations || profile.can_manage,
    admin: profile.can_manage || profile.can_firm_operations,
    manager: true,
    viewer: true,
  };
  if (!required[role])
    return {
      firstStep: '当前岗位尚未获得对应操作权限，请联系集团共享财务中心管理员确认分工。',
      handoff: '把单据名称与需要协助的步骤告知负责人，请勿借用他人账号代操作。',
      example: '例如：说明“我需要核验本公司单据，但当前尚未分配核验入口”。',
    };
  switch (role) {
    case 'employee':
      return {
        firstStep: '选择报销、采购或日常收支，填写原办公审批单号并上传发票，核对金额与用途即可提交。',
        handoff: '提交后自动推送子公司负责人核对放行并转共享中心；被退回时在原单直接补交资料。',
        example: '例如：车队垫付维保费，上传发票并填写原办公审批单号；断网离线也可手工填表提交。',
      };
    case 'approver':
      return {
        firstStep: '在待放行清单中对照原办公审批单号与票据原件，确认无误后一键放行。',
        handoff: '发现票据缺失或金额不符可一键退回补件；放行后自动流转给财务核验与共享中心。',
        example: '例如：确认原办公系统已审批通过，核对用途与部门归属无误后点“一键放行”。',
      };
    case 'finance':
      return {
        firstStep: '打开待核验单据，核对原办公审批截图、原始发票、金额及收款账号信息。',
        handoff: '核验无误后一键移交集团共享财务中心做账；高风险单据仍须复核主管把关。',
        example: '例如：员工报销已在原办公系统审结，核验发票真伪后移交共享中心生成凭证。',
      };
    case 'accountant':
      return {
        firstStep: '在顶栏选定所分管的子公司与账期，优先处理待办收件箱中的退回、补件与分录草稿。',
        handoff: '资料不全先退回补件；草稿配平后提交总账复核主管审核，断网时可直接纯手工填制凭证。',
        example: '例如：发票识别金额与原审批单不一致时，保留红线提示并退回核实，不强行凑平。',
      };
    case 'reviewer':
      return {
        firstStep: '优先核验绿通合规包批量放行，再逐笔严审高风险红线拦截单与原始票据。',
        handoff: '发现缺原审批单、首次收款账号存疑或跨期票直接退回经办人；复核通过后方可入账打款。',
        example: '例如：借贷平衡但缺少原办公审批依据的单据，须补齐凭证附件后再放行。',
      };
    case 'firm_owner':
      return {
        firstStep: '打开集团经营驾驶舱与集团总览，查看各子公司盈亏进度、结账卡点与人员负荷。',
        handoff: '发现某子公司临近月末仍有资料缺口或人员积压时，及时协调分工与催办补件。',
        example: '例如：某子公司月末仍缺银行对账单，先安排催办责任人，未结账公司诚实标“核算中”。',
      };
    case 'admin':
      return {
        firstStep: '先核对集团旗下各子公司的业态档案与共享中心人员分管矩阵，再检查智能规则配置。',
        handoff: '将具体账务判断交给核算与复核会计，将子公司开通与权限调整集中在配置台维护。',
        example: '例如：为新入职会计勾选分管哪两家子公司，系统即刻生效严格的数据隔离边界。',
      };
    default:
      return {
        firstStep: '查看本公司本月经营收支简报、待确认放行事项与周期资料提交进度。',
        handoff: '对某笔开支或月报有疑问时，可直接在右侧智能财税副驾提问或联系分管会计。',
        example: '例如：随时询问本月本公司前五大开支构成，或查看哪些单据仍在审核中。',
      };
  }
}

/**
 * Presentation-only summary. It never grants access: every action is still
 * re-authorized by the API and the current scope key.
 */
export function roleHomeGuide(profile: WorkspaceProfile): RoleHomeGuide {
  const home = roleHome(presentationRole(profile));
  const actions: string[] = [];
  if (profile.can_submit) actions.push('提交本人报销与补件');
  if (profile.can_approve) actions.push('核对原办公审批一键放行');
  if (profile.can_finance) actions.push('财务核验并移交共享中心');
  if (profile.can_review) actions.push('凭证复核与绿通放行');
  if (profile.can_accounting) actions.push('办理所分管子公司账套');
  if (profile.can_manage) actions.push('维护子公司与岗位授权');
  if (profile.can_firm_operations) actions.push('查看集团共享中心总览');
  if (!actions.length) actions.push('只读查阅当前授权范围');
  return {
    ...firstUse(profile),
    label: home.name,
    focus: home.focus,
    actions,
    boundary: '此处仅展示当前岗位授权能力；切换子公司、直达链接与批量操作仍由服务端逐次校验。',
  };
}
