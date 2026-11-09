/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { IntakeWizard } from './IntakeWizard';
import type { BusinessContext } from './business-types';
import type { Session } from './types';

const session: Session = {
  token: 'offline-visual',
  mode: 'local',
  firm_name: '虚构事务所',
  user_name: '虚构员工',
  firm_id: 'firm-a',
  user_id: 'employee-a',
  role: 'client_member',
  scope_key: 'v03-visual-a',
};
const context: BusinessContext = {
  companies: [
    {
      id: 'company-a',
      name: '虚构华东先进制造与综合技术服务有限公司第一分公司',
      role: 'employee',
      department_id: 'department-a',
      departments: [{ id: 'department-a', name: '综合行政与客户支持部' }],
      can_submit: true,
      can_finance: false,
      can_manage_approvers: false,
      approver_department_ids: [],
      submission_policy: { mode: 'department', version: 1, reason: '', updated_at: null },
    },
  ],
  templates: [],
  scopes: { own: true, department: false, company: false, accounting: false },
};
const render = () =>
  renderToStaticMarkup(
    createElement(IntakeWizard, {
      session,
      context,
      onClose: () => {},
      onCompleted: () => {},
      onBusyChange: () => {},
    }),
  );
const css = () => readFileSync(new URL('intake.css', import.meta.url), 'utf8');

describe('V03 employee intake presentation', () => {
  it('shows the selected template in text while keeping only the existing three choices', () => {
    const templates = render()
      .split('aria-label="选择业务模板"')[1]
      .split('intake-company-grid')[0];
    expect(templates.match(/<button/g)).toHaveLength(3);
    expect(templates.match(/aria-pressed="true"/g)).toHaveLength(1);
    expect(templates).toContain('当前模板');
    expect(templates).toContain('class="intake-template-selected" aria-hidden="true"');
  });

  it('names the existing attachment and verification sections without adding another step flow', () => {
    const html = render();
    expect(html).toContain('aria-labelledby="intake-originals-title"');
    expect(html).toContain('id="intake-originals-title"');
    expect(html).toContain('aria-labelledby="intake-fields-title"');
    expect(html).toContain('id="intake-fields-title"');
    expect(html.match(/aria-label="员工录入引导"/g)).toHaveLength(1);
    expect(html.match(/class="intake-step-number" aria-hidden="true"/g)).toHaveLength(2);
  });

  it('announces the existing save state atomically and links confirmation to its submission path', () => {
    const html = render();
    expect(html).toContain(
      'class="intake-save-state" role="status" aria-label="草稿保存状态" aria-live="polite" aria-atomic="true"',
    );
    expect(html).toMatch(/type="checkbox"[^>]*aria-describedby="intake-submit-help"/);
    expect(html).toContain('id="intake-submit-help"');
    expect(html).toContain('内容与原件仅保存在当前账号获授权的企业');
    expect(html).not.toContain('草稿已保存');
  });

  it('uses shared semantic surfaces and keyboard focus for attachment, AI and error states', () => {
    const source = css();
    expect(source).toMatch(/\.intake-upload\s*\{[^}]*background:\s*var\(--surface-subtle\)/);
    expect(source).toMatch(/\.intake-ai-help\s*\{[^}]*background:\s*var\(--status-info-soft\)/);
    expect(source).toMatch(
      /\.intake-modal \.form-error\s*\{[^}]*background:\s*var\(--status-danger-soft\)/,
    );
    expect(source).toContain('.intake-modal :is(button, summary):focus-visible');
    expect(source).toContain('outline: 2px solid var(--focus-ring)');
  });

  it('preserves readable mobile fields and reserves touch space for all attachment actions', () => {
    const actionSizes: string[] = [];
    postcss.parse(css()).walkRules((rule) => {
      if (!rule.selectors.includes('.intake-modal .text-button')) return;
      rule.walkDecls('min-height', (decl) => {
        actionSizes.push(decl.value);
      });
    });
    expect(actionSizes).toContain('var(--control-height-touch)');
    expect(css()).toContain('font-size: var(--font-size-input-mobile)');
    expect(render()).toMatch(/class="button primary"[^>]*disabled=""/);
    expect(render()).toContain('选择附件');
  });
});
