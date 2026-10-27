import type { BusinessEvent, BusinessStatus } from './business-types';
import type { WorkflowGuidance } from './WorkflowGuide';

const labels: Record<BusinessStatus, string> = {
  draft: '草稿',
  submitted: '待部门审批',
  department_approved: '待财务核验',
  finance_approved: '待交接会计',
  handed_off: '已交接会计',
  returned: '已退回',
};

export function businessStatusLabel(event: Pick<BusinessEvent, 'status' | 'submission_mode'>) {
  return event.status === 'submitted' && event.submission_mode === 'direct'
    ? '待会计接收'
    : labels[event.status];
}

function handoffMapping(event: BusinessEvent) {
  if (event.mapping_visibility === 'restricted') return 'restricted';
  if (event.mapping?.draft_id) return 'visible';
  if (event.mapping_visibility === 'missing' || event.mapping_visibility === 'visible')
    return 'missing';
  return 'unknown';
}

export function businessHandoffNotice(event: BusinessEvent | void) {
  if (event?.status !== 'handed_off')
    return '交接结果暂未确认，请重新读取事项并核对交接记录；不要重复交接。';
  switch (handoffMapping(event)) {
    case 'visible':
      return '已交接并关联凭证草稿，仍需会计复核后导出。';
    case 'restricted':
      return '业务已交接会计；当前账号不显示会计关联，等待会计处理，无需重复提交。';
    case 'missing':
      return '业务已交接，草稿关联暂未找到。请联系授权会计核对交接记录，不要重复创建事项。';
    default:
      return '业务已交接会计；具体处理进度请向授权会计确认，无需重复提交。';
  }
}

interface BusinessGuidanceState {
  busy?: boolean;
  error?: string;
  booksLoading?: boolean;
  booksError?: string;
  booksReady?: boolean;
  hasEligibleBook?: boolean;
  selectedBookEligible?: boolean;
}

/** Presentation only: permissions and facts come from the authorized event response. */
export function businessGuidance(
  event: BusinessEvent,
  state: BusinessGuidanceState = {},
): WorkflowGuidance {
  const direct = event.submission_mode === 'direct';
  const stages = direct
    ? [
        ['prepare', '核对资料'],
        ['handoff', '授权会计接收'],
        ['review', '会计复核与导出'],
      ]
    : [
        ['prepare', '填写资料'],
        ['department', '部门审批'],
        ['finance', '财务核验'],
        ['handoff', '交接会计'],
        ['review', '会计复核与导出'],
      ];
  const current =
    event.status === 'draft' || event.status === 'returned'
      ? 0
      : direct
        ? event.status === 'handed_off'
          ? 2
          : 1
        : { submitted: 1, department_approved: 2, finance_approved: 3, handed_off: 4 }[
            event.status
          ];
  const steps: WorkflowGuidance['steps'] = stages.map(([id, label], index) => ({
    id,
    label,
    state:
      index < current
        ? 'done'
        : index > current
          ? 'upcoming'
          : event.status === 'returned' ||
              (event.status === 'handed_off' && handoffMapping(event) === 'missing')
            ? 'blocked'
            : 'current',
  }));
  const guide: WorkflowGuidance = {
    title: event.permissions.finance_approve
      ? '核验这件业务，再交给会计'
      : '这件事项办到哪一步了？',
    steps,
    status: '',
    next: '',
    tone: 'neutral',
  };
  const facts = '对照原件核对金额、交易对方、发生日期和收付状态；识别结果只作参考。';
  const canPrepare = event.permissions.edit || event.permissions.submit;
  if (event.status === 'draft' || event.status === 'returned') {
    if (event.status === 'returned') {
      const reason = [...event.actions]
        .reverse()
        .find((action) => action.decision === 'return' || action.stage === 'return')
        ?.reason?.trim();
      guide.status = reason
        ? `事项已退回：${reason}`
        : '事项已退回，未显示退回原因。请查看审批记录或联系处理人员。';
      guide.tone = 'warning';
    } else guide.status = '资料已保存为草稿，尚未提交。';
    guide.next = !canPrepare
      ? '等待申请人核对资料并提交；当前账号只能查看这件事项。'
      : !event.attachments.length
        ? event.permissions.edit
          ? '先上传原始附件，再核对事项内容。至少有 1 个真实附件才能提交。'
          : '当前缺少原始附件，但此账号不能上传；请联系申请人补充后再处理。'
        : event.permissions.edit && event.status === 'returned'
          ? '按退回原因点击“修改事项”，补齐内容或附件；' +
            (event.permissions.submit
              ? '核对后重新提交同一事项。'
              : '当前账号暂无提交权限，请联系企业管理员核对授权。')
          : event.permissions.submit
            ? direct
              ? '核对资料后点击“提交审批”，交由授权会计接收。'
              : '核对资料后点击“提交审批”，等待部门审批。'
            : '可点击“修改事项”补齐资料；当前账号暂无提交权限，请联系企业管理员核对授权。';
    guide.checks = [facts, '原始附件已上传不代表票据真实性或业务用途已获核验。'];
  } else if (event.status === 'submitted' && !direct) {
    guide.status = '资料已提交，等待部门审批；尚未进入财务核验。';
    guide.next = event.permissions.department_approve
      ? '核对业务用途和原件；无误后点击“部门审批通过”。' +
        (event.permissions.return
          ? '有问题填写原因后退回修改。'
          : '如需补充资料，请联系申请人或有退回权限的处理人员，不要先批准。')
      : '等待获授权的部门审批人处理。你可在审批记录查看进度，无需重复提交。';
    guide.checks = ['核对业务是否真实发生、用途是否清楚；部门审批通过后才进入财务核验。'];
  } else if (event.status === 'department_approved') {
    guide.status = '部门审批已通过，等待财务核验；尚未生成正式凭证。';
    guide.next = event.permissions.finance_approve
      ? '先核对原件与业务事实；无误后点击“财务核验通过”，再选择账套交给会计。'
      : '等待本企业获授权财务核验资料。当前账号没有这一步的核验权限。';
    guide.checks = [
      facts,
      event.permissions.return
        ? '金额不符、附件缺失或用途不清时，填写具体退回原因后点击“退回修改”；不要先批准。'
        : '发现资料问题时联系获授权财务或申请人补充，当前账号不能代为批准。',
      '财务核验是业务资料确认，不等于会计审核、正式记账或报税。',
    ];
  } else if (event.status === 'finance_approved' || (event.status === 'submitted' && direct)) {
    guide.status = direct
      ? '资料已直接提交，等待授权会计接收；尚未记账。'
      : '财务核验已通过，等待交接会计；尚未记账。';
    guide.checks = [
      facts,
      '仅能选择本企业、事项发生月份的开放且可编辑账套。生成草稿后仍须会计复核。',
    ];
    const month = event.payload.occurred_on.slice(0, 7);
    if (!event.permissions.handoff) {
      guide.next = direct
        ? '等待授权会计接收；可查看审批记录，无需重复提交。'
        : '等待本企业有交接权限的财务选择账套。当前账号没有交接权限。';
    } else if (state.booksError) {
      guide.next = `账套未读取成功：${state.booksError}。请点击“重新读取事项账套”，确认授权后再交接。`;
      guide.tone = 'warning';
    } else if (state.booksLoading || !state.booksReady) {
      guide.next = `正在读取本企业 ${month} 的可交接账套，请等待结果；不要重复交接。`;
    } else if (!state.hasEligibleBook) {
      guide.next = `尚未找到本企业 ${month} 的可交接账套；如有更多账套先继续读取，否则请联系管理员核对月份和授权。`;
      guide.tone = 'warning';
    } else {
      guide.next = state.selectedBookEligible
        ? '确认原件与所选账套无误后，点击“生成凭证草稿”。这一步不会完成会计审核或导出。'
        : `核对原件后，在下方选择本企业 ${month} 的可交接账套，再生成凭证草稿。`;
    }
  } else {
    const mapping = handoffMapping(event);
    if (mapping === 'visible') {
      guide.status = '业务已交接会计；这不代表凭证已审核或已导出。';
      guide.next =
        '由有账套权限的授权会计打开会计复核，核对分录、金额和原件。审核通过后才能导出；具体进度以会计工作台为准。';
    } else if (mapping === 'restricted') {
      guide.status = '业务已交接会计；当前账号不显示会计关联，这不表示处理异常。';
      guide.next =
        '等待会计处理；可查看本事项的审批记录。如需了解会计处理进度，请联系本企业财务，无需重复提交。';
    } else if (mapping === 'missing') {
      guide.status = '业务状态为已交接，但草稿关联暂未找到；不能据此确认凭证已生成。';
      guide.next = '请联系授权会计核对交接记录，不要重复创建事项或交接。';
      guide.tone = 'warning';
    } else {
      guide.status = '业务已交接会计；当前页面未提供会计处理进度。';
      guide.next = '具体处理进度请向授权会计确认；可查看本事项的审批记录，无需重复提交。';
    }
    guide.checks = ['事项交接、会计审核和导出是不同阶段；业务状态不会替代凭证状态。'];
  }
  if (state.error) {
    guide.status += ` 当前操作提示：${state.error}`;
    guide.next = '请先重新读取事项，核对当前状态与附件，再决定是否继续；结果未确认前不要重复提交。';
    guide.tone = 'warning';
  }
  if (state.busy) guide.next = '正在处理当前操作，请等待结果；不要重复提交或切换事项。';
  return guide;
}
