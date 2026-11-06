import type { WorkflowGuidance } from './WorkflowGuide';
import type { IntakeRun } from './intake-types';

export interface IntakeGuideInput {
  companyReady: boolean;
  companiesAvailable?: boolean;
  departmentReady: boolean;
  templateLabel: string;
  fieldsValidation: string;
  attachmentCount: number;
  confirmed: boolean;
  busy: string;
  error: string;
  saved: boolean;
  direct: boolean;
  runs: readonly Pick<IntakeRun, 'status'>[];
}

/** Presentation only: validation, attachments and request status remain owned by
 * the existing form. OCR and a saved draft never constitute a submission. */
export function intakeGuidance(input: IntakeGuideInput): WorkflowGuidance {
  const scopeReady = input.companyReady && (input.direct || input.departmentReady);
  const contentReady = scopeReady && !input.fieldsValidation && input.attachmentCount > 0;
  const checks = [
    '识别不是必填步骤；可手工填写。识别建议仍须逐项核对，不能代替本人确认。',
    input.direct
      ? '提交后由授权会计接收资料；会计复核后才能导出。'
      : '提交后：部门审批 → 企业财务核验 → 会计复核；会计复核后才能导出。',
  ];
  const guide: WorkflowGuidance = {
    title: `完成这三步，提交${input.templateLabel}`,
    steps: [
      { id: 'scope', label: '确认企业与模板', state: scopeReady ? 'done' : 'current' },
      {
        id: 'content',
        label: '附票并填写',
        state: contentReady ? 'done' : scopeReady ? 'current' : 'upcoming',
      },
      { id: 'confirm', label: '核对后提交', state: contentReady ? 'current' : 'upcoming' },
    ],
    status: input.saved ? '草稿已保存，尚未提交' : '填写中，尚未提交',
    next: '',
    checks,
  };

  if (!input.companyReady) {
    if (input.companiesAvailable !== false) {
      guide.steps[0].state = 'current';
      guide.status = '请先选择客户企业，尚未提交';
      guide.next = '选择本次业务所属企业；仅显示当前账号可提交的范围。';
      return guide;
    }
    guide.steps[0].state = 'blocked';
    guide.status = '当前没有可提交的企业';
    guide.next = '联系企业管理员确认企业和提交权限，再重新打开录入。';
    guide.tone = 'warning';
    return guide;
  }
  if (!scopeReady) {
    guide.status = '请先确认所属部门，尚未提交';
    guide.next = '选择本次业务所属部门；没有可选部门时联系企业管理员。';
    return guide;
  }
  if (input.busy === 'submit') {
    guide.status = '正在提交，等待服务端确认';
    guide.next = '请等待提交结果，不要重复点击；成功后会打开事项处理进度。';
    return guide;
  }
  if (input.busy === 'saving' || input.busy === 'complete') {
    guide.status = '正在保存草稿，尚未提交';
    guide.next = '请等待保存结果；暂存后仍需核对并提交，才会进入后续处理。';
    return guide;
  }
  if (input.busy) {
    guide.status = '正在处理当前操作，尚未提交';
    guide.next = '等待当前操作结束，再核对附件和填写内容。';
    return guide;
  }
  if (input.error) {
    guide.status = '需要处理提示，尚未确认提交成功';
    guide.next = '先查看下方错误说明；结果不确定时读取最新草稿核对，不要重复提交。';
    guide.tone = 'warning';
    return guide;
  }
  if (input.runs.some((run) => run.status === 'stale')) {
    checks.unshift('识别已中断且未自动重发；请手工核对原件，或联系管理员核定后恢复识别。');
    guide.tone = 'warning';
  } else if (input.runs.some((run) => run.status === 'failed')) {
    checks.unshift('部分票据识别失败；可以对照原件手工核对填写，不会自动补造字段。');
    guide.tone = 'warning';
  } else if (input.runs.some((run) => run.status === 'running')) {
    checks.unshift('票据识别处理中；仍可对照原件手工核对，识别完成不代表已经提交。');
  } else if (input.runs.some((run) => run.status === 'succeeded')) {
    checks.unshift('识别建议已返回；采用字段后仍需本人核对日期、金额和交易对方。');
  }

  if (!input.attachmentCount) {
    guide.next = '提交前上传至少一份业务凭据，再填写发生日期、金额、摘要和交易对方。';
  } else if (input.fieldsValidation) {
    guide.next = `继续核对业务信息：${input.fieldsValidation}`;
  } else if (!input.confirmed) {
    guide.status = '待核对确认，尚未提交';
    guide.next = '对照原件核对日期、金额和交易对方，勾选“我已核对原件”后再提交。';
  } else {
    guide.status = '可以提交，尚未提交';
    guide.next = `点击“${input.direct ? '提交财务处理' : '提交审批'}”；若还要补充资料，可先暂存草稿。`;
  }
  return guide;
}
