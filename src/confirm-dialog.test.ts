import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { canConfirm, ConfirmDialog, requiredPhrase, type ConfirmRequest } from './confirm-dialog';

const lock: ConfirmRequest = {
  title: '确认正式锁期',
  description: '锁期后本月正式账务冻结。',
  subject: '2026-10 月结',
  company: '虚构示例事务所',
  period: '2026-10',
  consequence: '锁期后不能直接更改正式账务，本次不支持重开。',
  irreversible: true,
  confirmLabel: '确认正式锁期',
};

const discard: ConfirmRequest = {
  title: '放弃未保存的说明？',
  description: '返回待办会放弃尚未保存的说明。',
};

const render = (request: ConfirmRequest, typed = '') =>
  renderToStaticMarkup(
    createElement(ConfirmDialog, {
      request,
      typed,
      onTyped: () => {},
      onConfirm: () => {},
      onCancel: () => {},
    }),
  );

describe('confirmation gate', () => {
  it('defaults the typed phrase to period, then object', () => {
    expect(requiredPhrase(lock)).toBe('2026-10');
    expect(requiredPhrase({ ...lock, period: undefined })).toBe('2026-10 月结');
  });

  it('lets reversible actions pass without typing', () => {
    expect(canConfirm(discard, '')).toBe(true);
  });

  it('requires the exact phrase for irreversible actions', () => {
    expect(canConfirm(lock, '')).toBe(false);
    expect(canConfirm(lock, '2026-1')).toBe(false);
    expect(canConfirm(lock, ' 2026-10 ')).toBe(true);
  });

  it('refuses an irreversible action with nothing checkable to type', () => {
    const vague = { ...lock, period: undefined, subject: undefined, phrase: undefined };
    expect(canConfirm(vague, '')).toBe(false);
    expect(canConfirm(vague, 'anything')).toBe(false);
  });

  it('renders the facts, the consequence and a disabled confirm until typed', () => {
    const html = render(lock);
    expect(html).toContain('对象');
    expect(html).toContain('2026-10 月结');
    expect(html).toContain('虚构示例事务所');
    expect(html).toContain('不支持重开');
    expect(html).toContain('请输入「2026-10」以确认');
    expect(html).toContain('disabled');
  });

  it('enables confirm once the phrase matches', () => {
    const html = render(lock, '2026-10');
    expect(html).not.toContain('disabled');
  });

  it('keeps reversible dialogs free of the typed-phrase input', () => {
    const html = render(discard);
    expect(html).not.toContain('请输入');
    expect(html).not.toContain('disabled');
  });
});
