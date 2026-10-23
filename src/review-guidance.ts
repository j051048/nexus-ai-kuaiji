import type { WorkflowGuidance, WorkflowGuideStep } from './WorkflowGuide';
import type { Draft } from './types';

export interface ReviewGuidanceInput {
  status: Draft['status'];
  sourceInactive: boolean;
  sourceAvailable: boolean;
  hasEvidenceReference: boolean;
  editable: boolean;
  canReview: boolean;
  canOpenCase?: boolean;
  canExport: boolean;
  periodClosed: boolean;
  dirty: boolean;
  balanced: boolean;
  issueCount: number;
  busy: boolean;
  error: string;
  evidenceError: string;
  requiresAcknowledgement: boolean;
  acknowledged: boolean;
}

/** Presentation only: this explains existing controls and never authorizes an action. */
export function reviewGuidance(input: ReviewGuidanceInput): WorkflowGuidance {
  const steps: WorkflowGuideStep[] = [
    { id: 'originals', label: '核对原件与事实', state: 'upcoming' },
    { id: 'journal', label: '检查分录与差异', state: 'upcoming' },
    { id: 'review', label: '保存并确认审核', state: 'upcoming' },
    { id: 'export', label: '审核后导出', state: 'upcoming' },
  ];
  const result = (
    status: string,
    next: string,
    current: number,
    tone: WorkflowGuidance['tone'] = 'neutral',
    checks: readonly string[] = [],
  ): WorkflowGuidance => {
    steps[current].state = tone === 'warning' ? 'blocked' : 'current';
    return { title: '四步完成会计复核', steps, status, next, tone, checks };
  };
  if (input.sourceInactive)
    return result(
      '此来源已替代或隔离，仅保留历史记录',
      '查看来源历史，返回列表找到当前有效的事项或凭证；不要继续审核或导出此副本。',
      2,
      'warning',
    );
  if (input.status === 'exported') {
    steps.forEach((step) => {
      step.state = 'done';
    });
    const failed = !!input.error || !!input.evidenceError;
    return {
      title: '四步完成会计复核',
      steps,
      tone: failed ? 'warning' : 'success',
      status: `已导出，历史凭证不可直接修改${failed ? '；当前读取或操作异常' : ''}`,
      next: failed
        ? '请先查看错误并重新读取，再返回交付流程核验已有导出和回执；如需更正，请使用后续调整流程。'
        : '返回交付流程核验导入回执；如需更正，请使用后续调整流程。',
      checks: ['已导出不代表外部财务软件已经接收，仍须核验回执和差异。'],
    };
  }
  const approved = input.status === 'approved';
  const knownStatus = approved ? '已审核，尚未导出；' : '';
  if (approved)
    steps.slice(0, 3).forEach((step) => {
      step.state = 'done';
    });
  if (input.busy)
    return result(
      `${knownStatus}当前操作正在处理`,
      '请等待当前操作完成，不要重复保存或审核；完成后核对返回状态。',
      approved ? 3 : 2,
    );
  if (input.error)
    return result(
      `${knownStatus}本次操作未确认完成`,
      '请先查看错误并重新读取当前版本与授权，再核对后提交。',
      approved ? 3 : 2,
      'warning',
    );
  if (input.evidenceError)
    return result(
      `${knownStatus}原件暂不可读取，尚不能完成本次原件核对`,
      '请先排查原件读取失败并重新读取；识别文字或凭据引用不能代替原件核对。',
      0,
      'warning',
    );
  if (!input.sourceAvailable)
    return result(
      `${knownStatus}来源记录暂不可用`,
      '返回列表重新读取当前账套，确认来源与凭证对应后再核对。',
      0,
      'warning',
    );
  if (approved) {
    if (input.periodClosed)
      return result(
        '已审核，期间已关闭',
        '请联系负责会计核对已有导出记录或后续调整方式；当前关闭期间不能新导出。',
        3,
        'warning',
        ['已审核事实保留；关闭期间不能修改、重新审核或新导出凭证。'],
      );
    return result(
      '已审核，尚未导出',
      input.canExport
        ? '返回列表，勾选已审核凭证，再使用现有导出流程；导出后仍须核验外部回执。'
        : '请交由有导出权限的会计，在列表勾选已审核凭证并完成导出；你可继续查看此凭证。',
      3,
      'neutral',
      ['已审核凭证已冻结；更正须走调整流程，不能在此覆盖历史。'],
    );
  }
  if (input.periodClosed)
    return result(
      '期间已关闭，本页只查看',
      '请联系负责会计核对后续调整方式，不能直接修改或审核已关闭期间的草稿。',
      2,
      'warning',
    );
  if (!input.editable && !input.canReview)
    return result(
      '当前凭证为只读权限',
      '可查看原件与分录；需要准备、修正或审核时，请交由授权会计处理。',
      2,
      'warning',
    );
  if (!input.hasEvidenceReference)
    return result(
      '缺少凭据引用',
      `${
        input.editable
          ? '先补充凭据引用并核对对应原件；'
          : '请交由有编辑权限的会计补充凭据引用，再核对对应原件；'
      }${
        input.canOpenCase
          ? '缺资料时使用“指派异常 / 请求补件”。'
          : '资料不足时，请联系负责会计协调补充资料。'
      }`,
      0,
      'warning',
    );
  if (!input.balanced)
    return result(
      '借贷金额无效或不平衡',
      input.editable
        ? '检查分录金额与借贷差额，修正后保存草稿，再重新核对。'
        : '检查分录金额与借贷差额，请交由有编辑权限的会计修正并保存后，再重新核对。',
      1,
      'warning',
    );
  if (input.dirty)
    return result(
      '有尚未保存的修改',
      input.editable
        ? '先点击“保存草稿”，等待服务端按最新版本重新校验，再核对返回的问题，由获授权复核员审核。'
        : '当前没有编辑权限，不能保存本次修改；请保留修正依据，联系有编辑权限的会计处理后重新读取。',
      2,
      input.editable ? 'neutral' : 'warning',
    );
  if (input.issueCount > 0)
    return result(
      `仍有 ${input.issueCount} 项待处理问题`,
      input.canOpenCase
        ? '先按待处理问题核对资料和分录；资料不足时使用“指派异常 / 请求补件”，处理后再复核。'
        : '先按待处理问题核对资料和分录；资料不足时请联系负责会计协调补充资料，处理后再复核。',
      1,
      'warning',
    );
  if (!input.canReview)
    return result(
      '你可以准备草稿，审核由复核员完成',
      '核对原件和分录，需修正时保存草稿；资料完整后请交由获授权复核员确认审核。',
      2,
    );
  const checks = [
    '凭据引用不等于原件已经核验，请核对金额、日期、交易对方及附件。',
    '借贷平衡不代表会计处理正确，还须核对科目和业务依据；AI 仅提供建议。',
  ];
  if (input.requiresAcknowledgement) {
    if (!input.acknowledged)
      return result(
        '待你核对同屏原件与分录',
        '核对无误后，勾选现有“我已核对原件、金额和会计处理”，再点击“确认审核”。',
        0,
        'neutral',
        checks,
      );
    steps[0].state = 'done';
    steps[1].state = 'done';
    return result(
      '已勾选核对，尚未审核',
      '确认本次原件、金额和科目均无误后，点击“确认审核”。',
      2,
      'neutral',
      checks,
    );
  }
  return result(
    '待会计核对，尚未审核',
    '先点击“同屏查看原件与凭证”核对原件，再检查分录；确认无误后点击“确认审核”。',
    0,
    'neutral',
    checks,
  );
}
