import { describe, expect, it } from 'vitest';
import { reviewGuidance, type ReviewGuidanceInput } from './review-guidance';

const input: ReviewGuidanceInput = {
  status: 'draft',
  sourceInactive: false,
  sourceAvailable: true,
  hasEvidenceReference: true,
  editable: true,
  canReview: true,
  canOpenCase: true,
  canExport: true,
  periodClosed: false,
  dirty: false,
  balanced: true,
  issueCount: 0,
  busy: false,
  error: '',
  evidenceError: '',
  requiresAcknowledgement: false,
  acknowledged: false,
};
const guide = (changes: Partial<ReviewGuidanceInput> = {}) =>
  reviewGuidance({ ...input, ...changes });
const text = (changes: Partial<ReviewGuidanceInput>) => {
  const value = guide(changes);
  return `${value.status} ${value.next} ${(value.checks ?? []).join(' ')}`;
};

describe('会计复核仅呈现真实状态与可用下一步', () => {
  it('普通复核须先核对原件；引用存在不表示核对已完成', () => {
    const value = guide();
    expect(value.title).toBe('四步完成会计复核');
    expect(value.steps.map((step) => step.label)).toEqual([
      '核对原件与事实',
      '检查分录与差异',
      '保存并确认审核',
      '审核后导出',
    ]);
    expect(value.steps[0].state).not.toBe('done');
    expect(value.next).toContain('同屏查看原件与凭证');
    expect(value.next).toContain('确认审核');
    expect(value.checks?.join(' ')).toContain('凭据引用不等于原件已经核验');
  });
  it('同屏未勾选核对时说明既有确认项，不额外增加门槛', () => {
    expect(text({ requiresAcknowledgement: true })).toContain('我已核对原件、金额和会计处理');
    expect(guide({ requiresAcknowledgement: true }).steps[0].state).not.toBe('done');
  });
  it('同屏已核对且干净平衡时指向现有确认审核', () => {
    const value = guide({ requiresAcknowledgement: true, acknowledged: true });
    expect(value.steps[2].state).toBe('current');
    expect(value.next).toContain('确认审核');
    expect(value.status).not.toContain('已审核');
  });
  it('编辑后优先提示保存，不能直接审核', () => {
    const value = guide({ dirty: true });
    expect(value.status).toContain('未保存');
    expect(value.next).toContain('保存草稿');
    expect(value.next).not.toContain('直接审核');
    expect(value.steps[2].state).toBe('current');
  });
  it('金额不平衡优先核对分录，即使还有未保存修改', () => {
    expect(text({ balanced: false, dirty: true })).toContain('借贷');
    expect(guide({ balanced: false }).steps[1].state).toBe('blocked');
    expect(guide({ balanced: false }).tone).toBe('warning');
  });
  it('待处理问题须先解决或指派补件', () => {
    expect(text({ issueCount: 2 })).toContain('2 项待处理问题');
    expect(text({ issueCount: 2 })).toContain('指派异常 / 请求补件');
  });
  it.each([
    { canOpenCase: false, canReview: true },
    { canOpenCase: false, canReview: false },
    { canOpenCase: undefined, canReview: true },
  ])('没有异常指派入口时不编造本页动作：%j', (capability) => {
    for (const changes of [{ issueCount: 2 }, { hasEvidenceReference: false }]) {
      const value = text({ ...capability, ...changes });
      expect(value).not.toContain('指派异常 / 请求补件');
      expect(value).toContain('负责会计');
    }
  });
  it.each([true, false])('改动后先保存，由服务端重新校验旧问题：有审核权=%s', (canReview) => {
    const value = guide({ dirty: true, issueCount: 2, canReview });
    expect(value.status).toContain('未保存');
    expect(value.next).toContain('保存草稿');
    expect(value.next).toContain('重新校验');
    expect(value.next).not.toContain('指派异常 / 请求补件');
    expect(value.status).not.toContain('问题已解决');
    expect(value.steps[2].state).toBe('current');
  });
  it('改动、旧问题同时存在时保留金额不平衡的优先级', () => {
    const value = guide({ dirty: true, issueCount: 2, balanced: false });
    expect(value.status).toContain('借贷');
    expect(value.steps[1].state).toBe('blocked');
  });
  it('缺来源和缺引用分别解释，不能宣称已核对', () => {
    expect(text({ sourceAvailable: false })).toContain('来源记录');
    expect(text({ hasEvidenceReference: false })).toContain('凭据引用');
    expect(guide({ hasEvidenceReference: false }).steps[0].state).toBe('blocked');
  });
  it('原件失败时先修复读取，不建议强行审核', () => {
    const value = guide({ evidenceError: '原件服务暂不可用' });
    expect(value.status).toContain('原件');
    expect(value.next).toContain('重新读取');
    expect(value.next).toContain('不能代替原件核对');
    expect(value.steps[0].state).toBe('blocked');
  });
  it('写入冲突或授权更新后需要重新读取，不把错误当成保存成功', () => {
    expect(text({ error: '版本冲突' })).toContain('重新读取');
    expect(guide({ error: '授权更新' }).tone).toBe('warning');
  });
  it('进行中不鼓励重复提交', () => {
    expect(text({ busy: true })).toContain('不要重复');
    expect(text({ busy: true })).toContain('等待');
  });
  it('只读不能引导保存或本人审核', () => {
    expect(text({ editable: false, canReview: false })).toContain('只读');
    expect(text({ editable: false, canReview: false })).toContain('授权会计');
    expect(guide({ editable: false, canReview: false }).steps[2].state).toBe('blocked');
  });
  it('只能制单时说明由复核员审核，仍保留准备草稿路径', () => {
    const value = guide({ canReview: false });
    expect(value.next).toContain('复核员');
    expect(value.status).toContain('准备草稿');
    expect(text({ canReview: false, dirty: true })).toContain('保存草稿');
  });
  it('仅有复核权限不等于只能查看，但无编辑权限时不能引导本人修正或补引用', () => {
    expect(text({ editable: false, canReview: true })).toContain('确认审核');
    expect(text({ editable: false, canReview: true, hasEvidenceReference: false })).toContain(
      '有编辑权限的会计',
    );
    expect(text({ editable: false, canReview: true, balanced: false })).toContain(
      '有编辑权限的会计',
    );
    expect(text({ editable: false, canReview: true, dirty: true })).not.toContain('点击“保存草稿”');
  });
  it('关期与权限失效分别解释，不能建议直接修改', () => {
    expect(text({ periodClosed: true })).toContain('已关闭');
    expect(text({ periodClosed: true })).toContain('调整');
    expect(guide({ periodClosed: true }).steps[2].state).toBe('blocked');
  });
  it('已替代来源保留历史，不提示继续审核或导出', () => {
    expect(text({ sourceInactive: true, status: 'approved' })).toContain('历史');
    expect(text({ sourceInactive: true, status: 'approved' })).toContain('当前有效');
    expect(guide({ sourceInactive: true }).steps[2].state).toBe('blocked');
  });
  it('已审核不等于已导出；两入口均返回现有列表勾选导出', () => {
    const value = guide({ status: 'approved', editable: false });
    expect(value.status).toBe('已审核，尚未导出');
    expect(value.steps[2].state).toBe('done');
    expect(value.steps[3].state).toBe('current');
    expect(value.next).toContain('返回列表');
    expect(value.next).toContain('勾选');
    expect(value.next).not.toContain('在此导出');
  });
  it('无导出权限时交给获授权会计，不暗示权限升级', () => {
    expect(text({ status: 'approved', canExport: false })).toContain('有导出权限的会计');
    expect(text({ status: 'approved', canExport: false })).not.toContain('点击导出');
  });
  it('已审核但期间已关闭时保留审核事实，不再指向新导出', () => {
    const value = guide({ status: 'approved', periodClosed: true });
    expect(value.status).toContain('已审核');
    expect(value.status).toContain('已关闭');
    expect(value.next).not.toContain('勾选');
    expect(value.next).not.toContain('再使用现有导出流程');
    expect(value.next).toContain('负责会计');
    expect(value.next).toContain('已有导出');
    expect(value.next).toContain('调整');
    expect(value.steps[2].state).toBe('done');
    expect(value.steps[3].state).toBe('blocked');
  });
  it.each([{ error: '版本冲突' }, { evidenceError: '原件服务失败' }])(
    '已审核凭证读取或操作异常时保留审核状态：%j',
    (failure) => {
      const value = guide({ status: 'approved', ...failure });
      expect(value.status).toContain('已审核');
      expect(value.status).not.toContain('尚未审核');
      expect(value.next).toContain('重新读取');
      expect(value.steps[2].state).toBe('done');
      expect(value.tone).toBe('warning');
    },
  );
  it('已导出后读取或操作异常仍显示历史状态及错误，不掩盖失败', () => {
    const value = guide({ status: 'exported', error: '原表下载失败' });
    expect(value.status).toContain('已导出');
    expect(value.status).toContain('异常');
    expect(value.next).toContain('错误');
    expect(value.next).toContain('重新读取');
    expect(value.next).toContain('调整');
    expect(value.tone).toBe('warning');
    expect(value.steps[3].state).toBe('done');
  });
  it('已导出历史不可改，不能表示外部系统已入账', () => {
    const value = guide({ status: 'exported' });
    expect(value.status).toContain('历史');
    expect(value.next).toContain('回执');
    expect(value.next).toContain('调整');
    expect(value.next).not.toContain('已入账');
    expect(value.steps.every((step) => step.state === 'done')).toBe(true);
  });
  it('无副作用，不修改输入', () => {
    const frozen = Object.freeze({ ...input });
    expect(() => reviewGuidance(frozen)).not.toThrow();
    expect(frozen).toEqual(input);
  });
});
