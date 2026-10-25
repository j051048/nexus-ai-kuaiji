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
      firstStep: '当前岗位尚未获得对应操作权限，请联系本企业或事务所管理员确认分工。',
      handoff: '把事项名称与需要协助的步骤交给负责人，不要借用他人账号。',
      example: '例如：说明“我需要核验本企业资料，但当前没有核验入口”。',
    };
  switch (role) {
    case 'employee':
      return {
        firstStep: '选择报销、采购费用或日常收支，上传原件，再核对金额和用途。',
        handoff: '提交后按本企业流程等待部门审批或企业财务处理；退回时从原事项补充。',
        example: '例如：垫付文具费，上传票据，说明购买用途；提交不等于已经记账。',
      };
    case 'approver':
      return {
        firstStep: '从本部门待审批事项打开原资料，核对业务用途和申请人。',
        handoff: '资料有误先退回说明；审批通过后交给企业财务核验。',
        example: '例如：核对一次差旅的出差用途与所属部门，不替申请人补造事实。',
      };
    case 'finance':
      return {
        firstStep: '打开待核验事项，核对原件、金额、日期及是否已经付款。',
        handoff: '核验通过后移交对应账套的会计；会计仍须独立复核后才能导出。',
        example: '例如：员工报销已获部门审批，先核对资料，再移交会计，不等于确认入账。',
      };
    case 'accountant':
      return {
        firstStep: '选择获授权客户和月份，对照原件处理异常与分录草稿。',
        handoff: '资料不清先补件；草稿交给获授权复核人，复核通过后再明确导出。',
        example: '例如：识别金额与业务金额不同，保留差异并联系资料负责人，不补造分录凑平。',
      };
    case 'reviewer':
      return {
        firstStep: '从待复核清单打开原件与凭证，逐项核对金额、科目和处理依据。',
        handoff: '有差异退回经办人；会计复核通过后才可导出，外部系统还要核对回执。',
        example: '例如：凭证借贷平衡但缺原件，仍应补齐资料，不能只看平衡就审核。',
      };
    case 'firm_owner':
      return {
        firstStep: '从客户月度进度查看资料缺口、交付期限和未分派工作。',
        handoff: '为客户安排主办、复核及资料负责人，明确期限，再跟踪异常是否解决。',
        example: '例如：某客户月末仍缺银行资料，先分派补件负责人，不把空清单当作已完成。',
      };
    case 'admin':
      return {
        firstStep: '先确认获授权企业、员工岗位和账号权限，再检查开通情况。',
        handoff: '将账务判断交给会计，将缺权限或账号问题交给对应管理员。',
        example: '例如：为企业员工分配本人提交权限；开通账号不意味着可以复核所有企业。',
      };
    default:
      return {
        firstStep: '查看当前获授权企业的服务进度、待确认问题与已提供的报告。',
        handoff: '有疑问时提供事项名称或报告月份，请客户资料负责人或会计核对。',
        example: '例如：询问本月尚缺哪些资料；没有报告不代表本月没有业务。',
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
  if (profile.can_submit) actions.push('提交本人事项与补件');
  if (profile.can_approve) actions.push('处理部门审批');
  if (profile.can_finance) actions.push('财务核验与移交');
  if (profile.can_review) actions.push('凭证复核与审核后导出');
  if (profile.can_accounting) actions.push('进入当前授权账套');
  if (profile.can_manage) actions.push('成员与服务单协作');
  if (profile.can_firm_operations) actions.push('事务所运营视图');
  if (!actions.length) actions.push('读取当前授权范围');
  return {
    ...firstUse(profile),
    label: home.name,
    focus: home.focus,
    actions,
    boundary: '这里只显示当前授权能力；切换企业、直接打开链接和批量操作仍由服务端逐次校验。',
  };
}
