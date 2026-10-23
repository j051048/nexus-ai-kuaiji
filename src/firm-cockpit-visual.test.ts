import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FirmCockpitContent } from './FirmCockpit';
import type { FirmClient, FirmSnapshot } from './firm-cockpit-focus';

const client: FirmClient = {
  company_id: 'company-a',
  company_name: '虚构首尔华东商业贸易与信息技术服务有限责任公司',
  book_id: 'book-a',
  period: '2026-10',
  period_status: 'open',
  stage: 'review',
  tasks: { intake: 0, review: 3, exception: 0, delivery: 0, overdue: 0 },
  lead_user_id: 'accountant-a',
  lead_name: '林会计',
  reviewer_name: '王复核',
  due_on: '2026-10-28',
  risk: 99,
};

function snapshot(companies: FirmClient[]): FirmSnapshot {
  return {
    period: '2026-10',
    summary: {
      clients: companies.length,
      opened: companies.length,
      closed: 0,
      overdue: 0,
      exceptions: 0,
      risk_clients: 0,
      projection_stale: 0,
    },
    stages: {},
    companies,
    workload: [],
    risks: [],
  };
}

const content = (companies: FirmClient[]) =>
  renderToStaticMarkup(
    createElement(FirmCockpitContent, {
      data: snapshot(companies),
      onOpenBook: () => undefined,
      onOpenInbox: () => undefined,
      onMonthly: () => undefined,
      onOnboarding: () => undefined,
    }),
  );

describe('V02 事务所工作台的视觉层级', () => {
  it('空态只突出新增客户，已有客户才以客户进度为主操作', () => {
    const empty = content([]);
    expect([...empty.matchAll(/class="button primary"/g)]).toHaveLength(1);
    expect(empty).toMatch(/class="button primary"[^>]*>新增客户/);
    expect(content([client])).toMatch(/class="button primary"[^>]*>客户进度/);
  });

  it('仅真实需协调原因获得强调，待复核阶段或风险分不冒充异常', () => {
    const normal = content([client]);
    expect(normal).not.toContain('cockpit-client needs-coordination');
    expect(normal).toContain(client.company_name);
    expect(normal).toContain('待会计复核');
    const overdue = content([{ ...client, tasks: { ...client.tasks, overdue: 1 } }]);
    expect(overdue).toContain('cockpit-client needs-coordination');
    expect(overdue).toContain('需协调：');
    expect(overdue).toContain('1 项待办已逾期');
  });

  it('卡片和阶段只使用全局语义 token，阶段色不解释为风险', () => {
    const css = readFileSync(new URL('./cockpit.css', import.meta.url), 'utf8');
    expect(css).not.toMatch(/#[\da-f]{3,8}\b/i);
    expect(css.match(/\.cockpit-summary\s*\{([^}]+)\}/)?.[1]).toContain(
      'background: var(--surface)',
    );
    expect(css).not.toMatch(/\.cockpit-stage\.(?:review|delivery)[^{]*\{[^}]*--status-warning/);
  });

  it('手机概览改为完整指标行，长客户名与阶段保留独立布局', () => {
    const css = readFileSync(new URL('./cockpit.css', import.meta.url), 'utf8');
    const mobile = css.slice(css.indexOf('@media (max-width: 520px)'));
    expect(mobile.match(/\.cockpit-summary-values\s*\{([^}]+)\}/)?.[1]).toContain(
      'grid-template-columns: minmax(0, 1fr)',
    );
    expect(css.match(/\.cockpit-client-main\s*\{([^}]+)\}/)?.[1]).toContain('display: grid');
    expect(css).toContain('min-height: var(--control-height-touch)');
    expect(css).not.toMatch(/text-overflow:\s*ellipsis|line-clamp/);
  });
});
