import { expect, test, type Page } from '@playwright/test';
import type { WorkspaceProfile, WorkspaceTask } from '../src/workspace-types';

test.setTimeout(60_000);
const output = `tmp_test/v08/${process.env.V08_SCREENSHOT_PHASE ?? 'green'}`;
type Persona = 'employee' | 'finance' | 'accountant';

function task(id: string, patch: Partial<WorkspaceTask>): WorkspaceTask {
  return {
    key: `business:${id}`,
    kind: 'business',
    ref: id,
    company_id: 'v08-display-company',
    company_name: 'V08虚构资料协作企业',
    book_id: null,
    event_id: null,
    draft_id: null,
    batch_id: null,
    title: `V08虚构事项 ${id}`,
    state: 'returned',
    priority: 1,
    due_on: null,
    updated_at: '2026-10-24T00:00:00Z',
    bucket: 'action',
    action_label: '修改并重提',
    responsible: '本企业资料提交人',
    overdue: false,
    amount: '128.50',
    ...patch,
  };
}

/**
 * Actual local identities with explicit read-only task display projections.
 * Capability withdrawal below tests UI fail-closed behavior, not backend RLS.
 * V06 and existing end-to-end business tests retain real authorization coverage.
 */
async function project(page: Page, persona: Persona) {
  let companyId = '';
  let withdrawn = false;
  const writes: string[] = [];
  const detailReads: string[] = [];
  const definitions =
    persona === 'employee'
      ? [
          task('own-returned', { title: '报销被退回，请本人补充' }),
          task('material-returned', {
            key: 'material:material-returned',
            kind: 'material',
            title: '银行资料被退回，请本人补充',
          }),
          task('other-returned', {
            bucket: 'waiting',
            title: '同事的事项仍待同事修改',
            responsible: '同事资料提交人',
          }),
          task('material-submitted', {
            key: 'material:material-submitted',
            kind: 'material',
            state: 'submitted',
            bucket: 'waiting',
            title: '资料已交给会计核对',
            action_label: '查看进度',
            responsible: '本企业会计',
          }),
        ]
      : persona === 'finance'
        ? [
            task('finance-check', {
              key: 'finance:finance-check',
              kind: 'finance',
              state: 'department_approved',
              title: '企业财务核对原件与业务事实',
              action_label: '核验资料',
              responsible: '本企业财务核验人',
            }),
          ]
        : [
            task('review-draft', {
              key: 'review:review-draft',
              kind: 'review',
              state: 'draft',
              title: '凭证仍待会计复核',
              action_label: '打开复核',
            }),
            task('approved-export', {
              key: 'export:approved-export',
              kind: 'export',
              state: 'approved',
              title: '凭证已审核但尚未导出',
              action_label: '导出已审核凭证',
            }),
            task('unknown-receipt', {
              key: 'delivery:unknown-receipt',
              kind: 'delivery',
              state: 'unknown',
              title: '外部导入结果未知',
              action_label: '重新发送',
            }),
          ];

  function rows(bucket: string) {
    const selected = definitions
      .filter((row) => row.bucket === bucket)
      .map((row) => ({ ...row, company_id: companyId || row.company_id }));
    return {
      tasks: selected,
      counts: {
        action: definitions.filter((row) => row.bucket === 'action').length,
        waiting: definitions.filter((row) => row.bucket === 'waiting').length,
      },
      pagination: { total: selected.length, limit: 20, offset: 0, has_more: false },
    };
  }
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) writes.push(path);
    if (/^\/api\/workspace\/tasks\/[^/]+\/[^/]+$/.test(path)) detailReads.push(path);
  });
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
  await page.route(/\/api\/workspace\/(bootstrap|profile)(\?|$)/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const body = await response.json();
    const profile: WorkspaceProfile = body.profile ?? body;
    companyId = profile.companies[0]?.id ?? '';
    if (persona === 'employee') {
      // Additional capabilities must never change the primary employee vocabulary.
      profile.can_accounting = !withdrawn;
      profile.can_review = !withdrawn;
      if (withdrawn) {
        profile.can_submit = false;
        profile.can_approve = false;
        profile.can_finance = false;
        profile.can_manage = false;
      }
    }
    await route.fulfill({
      response,
      json: body.profile ? { ...body, profile, tasks: rows('action') } : profile,
    });
  });
  await page.route(/\/api\/workspace\/tasks\?/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    await route.fulfill({
      response,
      json: rows(new URL(route.request().url()).searchParams.get('bucket') ?? 'action'),
    });
  });
  return { writes, detailReads, withdraw: () => (withdrawn = true) };
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${device} V08 员工本人退回事项和周期资料显示待我处理，兼任不改变用语`, async ({ page }) => {
    const audit = await project(page, 'employee');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await expect(page.locator('.client-portal')).toBeVisible();
    await expect(page.locator('.agent-sidebar')).toHaveCount(0);
    for (const key of ['business:own-returned', 'material:material-returned']) {
      const row = page.locator(`[data-task-key="${key}"]`);
      await expect(row).toHaveAttribute('data-summary', 'mine');
      await expect(row.locator('.workspace-state-pill')).toHaveText('待我处理');
      await expect(row.locator('.workspace-task-detail')).toHaveText('需要修改后重交');
      await expect(row.locator('.workspace-task-guidance')).toContainText('退回原因');
      await expect(row.getByRole('button', { name: '修改并重提', exact: true })).toBeVisible();
      await expect(row).not.toContainText('独立验收');
    }
    await page.screenshot({ path: `${output}/returned-own-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });

  test(`${device} V08 员工等待他人修改和核对资料，不冒充本人待办或完成`, async ({ page }) => {
    const audit = await project(page, 'employee');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await page
      .getByRole('region', { name: '我的工作概览', exact: true })
      .getByRole('button', { name: /我的办理进度/ })
      .click();
    const submitted = page.locator('[data-task-key="material:material-submitted"]');
    await expect(submitted).toHaveAttribute('data-summary', 'waiting');
    await expect(submitted.locator('.workspace-task-detail')).toHaveText(
      '资料已提交，等待核对',
    );
    await expect(submitted).not.toContainText('独立验收');
    await expect(submitted).not.toContainText('已完成');
    await expect(submitted.getByRole('button', { name: '查看进度', exact: true })).toBeVisible();
    const other = page.locator('[data-task-key="business:other-returned"]');
    await expect(other).not.toHaveAttribute('data-summary', 'mine');
    await expect(other).toContainText('同事资料提交人');
    await expect(other.getByRole('button', { name: '修改并重提', exact: true })).toHaveCount(0);
    await expect(other.locator('.workspace-task-guidance')).not.toContainText('修改同一事项');
    await page.screenshot({ path: `${output}/employee-waiting-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });

  test(`${device} V08 退回事项在能力撤回后不继续显示本人修改提示`, async ({ page }) => {
    const audit = await project(page, 'employee');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    const own = page.locator('[data-task-key="business:own-returned"]');
    await expect(own.getByRole('button', { name: '修改并重提', exact: true })).toBeVisible();
    audit.withdraw();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.client-portal')).toBeVisible();
    for (const key of ['business:own-returned', 'material:material-returned']) {
      const row = page.locator(`[data-task-key="${key}"]`);
      await expect(row).not.toHaveAttribute('data-summary', 'mine');
      await expect(row.getByRole('button', { name: '修改并重提', exact: true })).toHaveCount(0);
      await expect(row.getByRole('button', { name: '查看详情', exact: true })).toBeVisible();
      await expect(row.locator('.workspace-task-guidance')).toContainText(/权限|授权/);
    }
    await page.screenshot({ path: `${output}/returned-withdrawn-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });

  test(`${device} V08 企业财务保留核验责任和准确阶段`, async ({ page }) => {
    const audit = await project(page, 'finance');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    const row = page.locator('[data-task-key="finance:finance-check"]');
    await expect(row).toHaveAttribute('data-summary', 'mine');
    await expect(row.locator('.workspace-task-detail')).toHaveText('待财务核验');
    await expect(row.locator('.workspace-task-kind')).toHaveText('财务核验');
    await expect(row.locator('.workspace-task-guidance')).toContainText(/原件/);
    await expect(row.getByRole('button', { name: '核验资料', exact: true })).toBeVisible();
    await expect(row).not.toContainText('已完成');
    await page.screenshot({ path: `${output}/finance-precise-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });

  test(`${device} V08 会计视角保留复核导出阶段，未知回执不建议重发`, async ({ page }) => {
    const audit = await project(page, 'accountant');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    const review = page.locator('[data-task-key="review:review-draft"]');
    await expect(review.locator('.workspace-task-detail')).toHaveText('待会计复核');
    await expect(review.locator('.workspace-task-kind')).toHaveText('凭证复核');
    await expect(review.locator('.workspace-task-guidance')).toContainText('原件');
    const approved = page.locator('[data-task-key="export:approved-export"]');
    await expect(approved).not.toHaveAttribute('data-summary', 'done');
    await expect(approved.locator('.workspace-task-detail')).toHaveText('已审核待导出');
    const unknown = page.locator('[data-task-key="delivery:unknown-receipt"]');
    await expect(unknown).toHaveAttribute('data-summary', 'blocked');
    await expect(unknown.locator('.workspace-task-guidance')).toContainText('不要重复发送');
    await expect(unknown.getByRole('button', { name: /重新发送|重试/ })).toHaveCount(0);
    await unknown.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/accounting-precise-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });
}
