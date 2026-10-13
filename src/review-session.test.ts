import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearReviewSession,
  previewMediaType,
  readReviewPackage,
  readReviewWorkspace,
  saveReviewPackage,
  saveReviewWorkspace,
} from './review-session';

const state = {
  company: 'c1',
  period: '2026-10',
  template: 'all',
  risk: 'all',
  offset: 20,
  selected: ['d1'],
  previewId: 'p1',
  commandKey: 'key1',
  packageItems: null,
};
describe('复核会话只在当前授权内存范围保留', () => {
  beforeEach(clearReviewSession);
  it('离开页面后保留筛选和选择，读写返回独立副本', () => {
    saveReviewWorkspace('scope-a', state);
    state.selected.push('d2');
    const restored = readReviewWorkspace('scope-a')!;
    expect(restored.selected).toEqual(['d1']);
    restored.selected.push('d3');
    expect(readReviewWorkspace('scope-a')?.selected).toEqual(['d1']);
    state.selected.pop();
  });
  it('身份或企业授权范围切换立即清空，切回也不能恢复旧范围', () => {
    saveReviewWorkspace('scope-a', state);
    expect(readReviewWorkspace('scope-b')).toBeNull();
    expect(readReviewWorkspace('scope-a')).toBeNull();
  });
  it('显式撤权清空工作区与复核包，包选择签名不串用', () => {
    saveReviewWorkspace('scope-a', state);
    saveReviewPackage('scope-a', {
      signature: 'sig1',
      packageId: 'pkg1',
      selected: ['d1'],
      prepareKey: 'pk',
      confirmationKey: 'ck',
      selectionKey: { signature: '', key: '' },
      changed: false,
    });
    expect(readReviewPackage('scope-a', 'sig2')).toBeNull();
    expect(readReviewPackage('scope-a', 'sig1')?.packageId).toBe('pkg1');
    clearReviewSession();
    expect(readReviewWorkspace('scope-a')).toBeNull();
    expect(readReviewPackage('scope-a', 'sig1')).toBeNull();
  });
  it('只允许受支持的图片与 PDF 预览，不执行 HTML、SVG 或链接原文', () => {
    expect(previewMediaType('image/png')).toBe('image');
    expect(previewMediaType('application/pdf')).toBe('pdf');
    expect(previewMediaType('text/html')).toBeNull();
    expect(previewMediaType('image/svg+xml')).toBeNull();
    expect(previewMediaType('https://example.com/invoice')).toBeNull();
  });
});
