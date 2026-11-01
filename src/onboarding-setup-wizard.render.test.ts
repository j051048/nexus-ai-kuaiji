import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SetupStepList, type SetupBody } from './OnboardingSetupWizard';

const step = (overrides: Partial<SetupBody['steps'][number]>) => ({
  id: 'template',
  title: '核算模板与适用范围',
  complete: false,
  required: true,
  detail: '尚未启用覆盖本月的会计政策。',
  owner_role: '账套会计',
  next_action: '在期初与政策中确认适用准则、纳税人身份与生效期间',
  action_target: { kind: 'opening', book_id: 'book-a', period: '2026-10' },
  ...overrides,
});

const body = (steps: SetupBody['steps']): SetupBody => ({
  company_id: 'company-a',
  book_id: 'book-a',
  period: '2026-10',
  ready: steps.every((item) => item.complete),
  steps,
  updated_at: '2026-10-31T00:00:00+08:00',
  note: '按当前有效账号、权限与已登记记录计算；开通向导不能证明本月资料齐全。',
});

describe('opening setup wizard list', () => {
  it('names the responsible role and never implies the wizard performs the step', () => {
    const html = renderToStaticMarkup(createElement(SetupStepList, { body: body([step({})]) }));
    expect(html).toContain('开账准备 0/1 项已完成');
    expect(html).toContain('待办理');
    expect(html).toContain('负责岗位：账套会计');
    expect(html).toContain('本向导只读取已登记记录，不会代替确认');
    expect(html).not.toContain('href="#formal-close"');
  });

  it('links only in-page kinds to the month-close panel', () => {
    const html = renderToStaticMarkup(
      createElement(SetupStepList, {
        body: body([step({ id: 'bank', title: '银行账户与覆盖证据', action_target: { kind: 'bank', book_id: 'book-a', period: '2026-10' } })]),
      }),
    );
    expect(html).toContain('href="#formal-close"');
    expect(html).toContain('去月结面板办理');
  });

  it('reports readiness without hiding the note', () => {
    const html = renderToStaticMarkup(
      createElement(SetupStepList, { body: body([step({ complete: true })]) }),
    );
    expect(html).toContain('开账准备 1/1 项已完成，可以开始本月记账。');
    expect(html).toContain('已完成');
    expect(html).not.toContain('待办理');
  });
});
