import { beforeEach, describe, expect, it } from 'vitest';
import {
  confirmLeaveIfDirty,
  hasUnsavedEdits,
  registerUnsavedGuard,
  resetUnsavedGuards,
  unsavedLabels,
} from './unsaved-guard';
import {
  beginScope,
  currentEpoch,
  isCurrentScope,
  resetScope,
  scopedController,
} from './request-scope';

beforeEach(() => {
  resetUnsavedGuards();
  resetScope();
});

describe('unsaved guard registry', () => {
  it('starts clean and reports registered editors', () => {
    expect(hasUnsavedEdits()).toBe(false);
    const off = registerUnsavedGuard('review', '凭证复核编辑');
    registerUnsavedGuard('monthly', '月度办理说明');
    expect(hasUnsavedEdits()).toBe(true);
    expect(unsavedLabels()).toEqual(['凭证复核编辑', '月度办理说明']);
    off();
    expect(unsavedLabels()).toEqual(['月度办理说明']);
    expect(hasUnsavedEdits()).toBe(true);
  });

  it('refuses an empty identifier so a typo cannot silently disable the guard', () => {
    expect(() => registerUnsavedGuard('  ', 'x')).toThrow();
  });

  it('lets the caller leave without asking when nothing is dirty', async () => {
    await expect(
      confirmLeaveIfDirty({ title: '切换期间', description: '切换期间会重新加载账套数据' }),
    ).resolves.toBe(true);
  });

  it('refuses to leave when an editor is dirty and no confirmation host is mounted', async () => {
    // 没有挂载 ConfirmHost 时必须拒绝离开（宁可不切，也不能静默丢修改）。
    registerUnsavedGuard('review', '凭证复核编辑');
    await expect(
      confirmLeaveIfDirty({ title: '切换期间', description: '切换期间会重新加载账套数据' }),
    ).resolves.toBe(false);
  });

  it('reset clears every editor (used on logout and on a forced scope reset)', () => {
    registerUnsavedGuard('a', 'A');
    registerUnsavedGuard('b', 'B');
    resetUnsavedGuards();
    expect(hasUnsavedEdits()).toBe(false);
  });
});

describe('context-switch epoch', () => {
  it('bumps on every switch and aborts controllers from the previous epoch', () => {
    const first = scopedController();
    expect(isCurrentScope(first.epoch)).toBe(true);
    expect(first.signal.aborted).toBe(false);

    const next = beginScope();
    expect(next).toBe(first.epoch + 1);
    expect(currentEpoch()).toBe(next);
    expect(first.signal.aborted).toBe(true);
    expect(isCurrentScope(first.epoch)).toBe(false);
    expect(isCurrentScope(next)).toBe(true);
  });

  it('keeps a controller registered after the switch from being aborted by it', () => {
    const current = scopedController();
    beginScope();
    const fresh = scopedController();
    expect(fresh.signal.aborted).toBe(false);
    expect(isCurrentScope(fresh.epoch)).toBe(true);
    // 旧 epoch 的信号仍然保持 aborted，不会因为新注册而被“复活”。
    expect(current.signal.aborted).toBe(true);
  });

  it('a late response carrying an old epoch is recognisable and must be dropped', () => {
    const captured = currentEpoch();
    beginScope();
    beginScope();
    expect(isCurrentScope(captured)).toBe(false);
  });

  it('release detaches a settled controller so completed requests do not accumulate', () => {
    const settled = scopedController();
    settled.release();
    beginScope();
    expect(settled.signal.aborted).toBe(false);
  });
});
