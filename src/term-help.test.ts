import { createElement, Fragment } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { IntakeWizard } from './IntakeWizard';
import type { BusinessContext } from './business-types';
import type { Session } from './types';

const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构所',
  user_name: '虚构员工',
  firm_id: 'firm-a',
  user_id: 'employee-a',
  role: 'client_member',
  scope_key: 'offline-a',
};
const context: BusinessContext = {
  companies: [
    {
      id: 'company-a',
      name: '虚构企业 A',
      role: 'employee',
      department_id: 'department-a',
      departments: [{ id: 'department-a', name: '行政部' }],
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

describe('V07 术语在原操作附近解释，不形成新业务入口', () => {
  it('同一术语多实例默认折叠，解释区域与 summary 具有不同且可关联的 ID', async () => {
    const { TermHelp } = await import('./TermHelp');
    const html = renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        createElement(TermHelp, { term: 'counterparty' }),
        createElement(TermHelp, { term: 'counterparty' }),
      ),
    );
    expect(html.match(/<details/g)).toHaveLength(2);
    expect(html).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(html).toContain('交易对方是什么？');
    const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
    expect(new Set(ids).size).toBe(ids.length);
    for (const match of html.matchAll(/aria-controls="([^"]+)"/g)) expect(ids).toContain(match[1]);
    for (const match of html.matchAll(/aria-labelledby="([^"]+)"/g))
      expect(ids).toContain(match[1]);
  });

  it('使用固定虚构例子，借贷不解释为收支，导出不等于外部软件接收', async () => {
    const { TermHelp } = await import('./TermHelp');
    const fetch = vi.spyOn(globalThis, 'fetch');
    try {
      const render = (term: 'payment_status' | 'debit_credit' | 'export_receipt' | 'draft') =>
        renderToStaticMarkup(createElement(TermHelp, { term }));
      expect(render('payment_status')).toContain('员工垫付');
      expect(render('payment_status')).toContain('企业是否已付款');
      expect(render('debit_credit')).toContain('不能直接当成收入和支出');
      expect(render('debit_credit')).toContain('虚构示例');
      expect(render('export_receipt')).toContain('不代表外部财务软件已接收');
      expect(render('export_receipt')).toContain('结果未知');
      expect(render('draft')).toContain('不代表已记账');
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });

  it('员工字段解释与控件保持关联，但 details 不嵌入 label 也不改变提交状态', () => {
    const html = renderToStaticMarkup(
      createElement(IntakeWizard, {
        session,
        context,
        onClose: () => {},
        onCompleted: () => {},
        onBusyChange: () => {},
      }),
    );
    expect(html).toContain('发生日期是什么？');
    expect(html).toContain('交易对方是什么？');
    expect(html).toContain('企业付款状态是什么？');
    for (const match of html.matchAll(/<label\b[^>]*>([\s\S]*?)<\/label>/g))
      expect(match[1]).not.toContain('<details');
    for (const name of ['发生日期', '交易对方', '企业付款状态']) {
      const field = html.match(new RegExp(`<(?:input|select)[^>]*aria-label="${name}"[^>]*>`))?.[0];
      const reference = field?.match(/aria-describedby="([^"]+)"/)?.[1];
      expect(reference).toBeTruthy();
      expect(html).toContain(`id="${reference}"`);
    }
    expect(html).toContain('尚未提交');
    expect(html).toContain('提交审批');
    expect(html).not.toContain('借方和贷方是什么？');
    expect(html).not.toContain('导出和回执有什么区别？');
  });

  it('日常收支模板没有企业付款字段，也不展示报销付款解释', () => {
    const html = renderToStaticMarkup(
      createElement(IntakeWizard, {
        session,
        context,
        initialTemplate: 'daily_cashflow',
        onClose: () => {},
        onCompleted: () => {},
        onBusyChange: () => {},
      }),
    );
    expect(html).toContain('收支方向');
    expect(html).not.toContain('企业付款状态是什么？');
    expect(html).not.toContain('aria-label="企业付款状态"');
  });

  it('说明无需悬停才能使用，并具有共享触控尺寸与键盘焦点样式', () => {
    const css = readFileSync(new URL('./term-help.css', import.meta.url), 'utf-8');
    expect(css).toContain('min-height: var(--control-height-touch)');
    expect(css).toContain('.term-help > summary:focus-visible');
    expect(css).toContain('font-size: var(--font-size-sm)');
    expect(css).toContain('overflow-wrap: anywhere');
    expect(css).not.toMatch(/#[\da-f]{3,8}\b/i);
  });

  it('会计释义只放在读取到的同屏凭证核对区，不代替现有复核引导', () => {
    const source = readFileSync(new URL('./InlineReviewDrawer.tsx', import.meta.url), 'utf-8');
    expect(source).toContain('<TermHelp term="draft"');
    expect(source).toContain('<TermHelp term="debit_credit"');
    expect(source).toContain('<TermHelp term="export_receipt"');
    expect(source.indexOf('<TermHelp term="draft"')).toBeGreaterThan(
      source.indexOf('<WorkflowGuide'),
    );
    expect(source).toContain('inline-review-totals');
    expect(source.indexOf('<TermHelp term="draft"')).toBeGreaterThan(
      source.indexOf('inline-review-totals'),
    );
    expect(source.indexOf('<TermHelp term="export_receipt"')).toBeLessThan(
      source.indexOf('aria-label="我已核对同屏原件与分录"'),
    );
    expect(source).toContain("closest('.accounting-context-assistant, .term-help')");
    expect(source).toContain('a[href], summary, iframe');
  });
});
