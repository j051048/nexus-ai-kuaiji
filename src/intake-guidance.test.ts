import { describe, expect, it } from 'vitest';
import { intakeGuidance, type IntakeGuideInput } from './intake-guidance';

const ready: IntakeGuideInput = {
  companyReady: true,
  departmentReady: true,
  templateLabel: '员工报销',
  fieldsValidation: '',
  attachmentCount: 1,
  confirmed: true,
  busy: '',
  error: '',
  saved: true,
  direct: false,
  runs: [],
};

describe('录入引导只解释真实表单状态', () => {
  it('无企业提交权限时先处理授权，不从旧草稿推荐上传或提交', () => {
    const guide = intakeGuidance({ ...ready, companyReady: false });
    expect(guide.status).toBe('当前没有可提交的企业');
    expect(guide.next).toContain('联系企业管理员');
    expect(guide.steps[0].state).toBe('blocked');
    expect(guide.steps[2].state).toBe('upcoming');
  });
  it('部门审批路径缺少部门先确认范围', () => {
    const guide = intakeGuidance({ ...ready, departmentReady: false });
    expect(guide.status).toContain('所属部门');
    expect(guide.steps[0].state).toBe('current');
  });
  it('直提路径不强制部门，也不凭空增加审批节点', () => {
    const guide = intakeGuidance({ ...ready, direct: true, departmentReady: false });
    expect(guide.steps[0].state).toBe('done');
    expect(guide.next).toContain('提交财务处理');
    expect(guide.checks?.join(' ')).toContain('由授权会计接收资料');
    expect(guide.checks?.join(' ')).not.toContain('部门审批');
  });
  it('已有附件仍使用现有字段验证错误，不把识别成功当字段有效', () => {
    const guide = intakeGuidance({
      ...ready,
      fieldsValidation: '请填写交易对方（不超过 200 个字符）。',
      runs: [{ status: 'succeeded' }],
    });
    expect(guide.steps[1].state).toBe('current');
    expect(guide.next).toContain('请填写交易对方');
    expect(guide.status).toContain('尚未提交');
  });
  it('有效字段没有附件时说明提交要求，暂存不算提交', () => {
    const guide = intakeGuidance({ ...ready, attachmentCount: 0 });
    expect(guide.next).toContain('至少一份业务凭据');
    expect(guide.status).toBe('草稿已保存，尚未提交');
    expect(guide.steps[1].state).toBe('current');
  });
  it('字段与附件齐备仍须本人核对确认', () => {
    const guide = intakeGuidance({ ...ready, confirmed: false });
    expect(guide.steps.map((step) => step.state)).toEqual(['done', 'done', 'current']);
    expect(guide.next).toContain('勾选');
    expect(guide.next).toContain('日期、金额和交易对方');
  });
  it('部门路径准备完成只说可提交，下一环节仍需财务和会计', () => {
    const guide = intakeGuidance(ready);
    expect(guide.status).toBe('可以提交，尚未提交');
    expect(guide.next).toContain('提交审批');
    expect(guide.steps[2].state).toBe('current');
    expect(guide.checks?.join(' ')).toContain('部门审批 → 企业财务核验 → 会计复核');
    expect(guide.checks?.join(' ')).toContain('会计复核后才能导出');
  });
  it.each(['saving', 'complete'])('保存%s不是提交成功，不提前完成最后一步', (busy) => {
    const guide = intakeGuidance({ ...ready, busy });
    expect(guide.status).toBe('正在保存草稿，尚未提交');
    expect(guide.steps[2].state).toBe('current');
    expect(guide.next).toContain('等待保存结果');
  });
  it('提交请求在途不宣告提交成功', () => {
    const guide = intakeGuidance({ ...ready, busy: 'submit' });
    expect(guide.status).toBe('正在提交，等待服务端确认');
    expect(guide.next).toContain('不要重复点击');
    expect(guide.steps[2].state).toBe('current');
  });
  it('提交错误优先提示核对最新状态，不盲目重试', () => {
    const guide = intakeGuidance({ ...ready, error: '提交超时，请读取最新草稿' });
    expect(guide.status).toBe('需要处理提示，尚未确认提交成功');
    expect(guide.next).toContain('读取最新草稿');
    expect(guide.next).toContain('不要重复提交');
    expect(guide.tone).toBe('warning');
  });
  it.each(['running', 'succeeded', 'failed', 'stale'] as const)(
    '识别%s不成为强制流程，不等于已经提交',
    (status) => {
      const guide = intakeGuidance({ ...ready, runs: [{ status }] });
      expect(guide.status).toBe('可以提交，尚未提交');
      expect(guide.checks?.join(' ')).toContain('识别不是必填步骤');
      expect(guide.steps[2].state).toBe('current');
      expect(guide.next).toContain('提交审批');
    },
  );
  it('识别中断不建议重复发起费用未知的调用', () => {
    const guide = intakeGuidance({ ...ready, runs: [{ status: 'stale' }] });
    expect(guide.checks?.join(' ')).toContain('未自动重发');
    expect(guide.checks?.join(' ')).toContain('手工核对');
    expect(guide.tone).toBe('warning');
  });
  it('未知草稿没有虚构保存成功，也没有强制先识别', () => {
    const guide = intakeGuidance({ ...ready, saved: false, confirmed: false });
    expect(guide.status).toBe('待核对确认，尚未提交');
    expect(guide.checks?.join(' ')).toContain('识别不是必填步骤');
  });
});
