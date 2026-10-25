import { expect, test, type Page } from '@playwright/test';
import type { WorkspaceTask } from '../src/workspace-types';
import { choosePersona, openNavigation } from './ui-navigation';

test.setTimeout(60_000);
const phase = process.env.V04_V05_SCREENSHOT_PHASE ?? 'current';
const output = `tmp_test/v04-v05/${phase}`;
const companyName = '虚构华东先进制造与综合技术服务有限公司第一分公司';

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

function task(id: string, patch: Partial<WorkspaceTask>): WorkspaceTask {
  return {
    key: `case:${id}`,
    kind: 'case',
    ref: id,
    company_id: 'v05-synthetic-company',
    company_name: companyName,
    book_id: null,
    event_id: null,
    draft_id: null,
    batch_id: null,
    title: `虚构事项 ${id}`,
    state: 'open',
    priority: 60,
    due_on: null,
    updated_at: '2026-10-24T00:00:00Z',
    bucket: 'action',
    action_label: '查看事项',
    responsible: '虚构负责人（企业财务与会计协同处理）',
    overdue: false,
    amount: '123456789.01',
    ...patch,
  };
}

/** Read-only display projections on real local identities, not authorization evidence. */
async function projectedTasks(page: Page, mode: 'client' | 'accounting') {
  let role = '';
  let companyId = '';
  const writes: string[] = [];
  const detailReads: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) writes.push(path);
    if (/^\/api\/workspace\/tasks\/[^/]+\/[^/]+$/.test(path)) detailReads.push(path);
  });
  const rows = (bucket: string) => {
    const all =
      mode === 'client'
        ? [
            task('shared-finance', {
              key: 'finance:v05-shared',
              kind: 'finance',
              title: '虚构采购资料：同一状态随岗位显示下一步',
              state: 'department_approved',
              bucket: role === 'finance' ? 'action' : 'waiting',
              action_label: '核验资料',
              responsible: '本企业财务核验人',
            }),
          ]
        : [
            task('unknown-receipt', {
              key: 'delivery:v05-unknown-receipt',
              kind: 'delivery',
              title: '虚构外部回执结果未知',
              state: 'unknown',
              action_label: '查看结果',
              responsible: '虚构交付负责人',
            }),
            task('approved', {
              key: 'export:v05-approved',
              kind: 'export',
              state: 'approved',
              title: '虚构已审核凭证，尚未导出',
              action_label: '导出已审核凭证',
            }),
            task('queued', {
              key: 'ocr:v05-queued',
              kind: 'ocr',
              state: 'queued',
              bucket: 'waiting',
              title: '虚构票据等待识别',
              action_label: '查看进度',
            }),
          ];
    const selected = all
      .filter((row) => row.bucket === bucket)
      .map((row) => ({
        ...row,
        company_id: companyId || row.company_id,
      }));
    return {
      tasks: selected,
      counts: {
        action: all.filter((row) => row.bucket === 'action').length,
        waiting: all.filter((row) => row.bucket === 'waiting').length,
      },
      pagination: { total: selected.length, limit: 20, offset: 0, has_more: false },
    };
  };
  await page.route(/\/api\/workspace\/(bootstrap|profile)(\?|$)/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const body = await response.json();
    const profile = body.profile ?? body;
    role = profile.display_role ?? profile.role;
    companyId = profile.companies[0]?.id ?? '';
    if (mode === 'client' && ['employee', 'finance'].includes(role)) {
      // Additional display capabilities must not change the primary client shell.
      profile.can_accounting = true;
      profile.can_review = true;
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
  return { writes, detailReads };
}

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 375, height: 812 },
]) {
  const device = viewport.width < 640 ? 'mobile' : 'desktop';

  test(`${device} V04 事实修订深链接只高亮当前页，保留原授权入口`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/revision', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '业务事实纠错', exact: true })).toBeVisible();
    await openNavigation(page);
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(nav.getByRole('button', { name: '事实修订与调整', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await page.screenshot({ path: `${output}/revision-navigation-${device}.png`, fullPage: true });
    await noOverflow(page);
  });

  test(`${device} V04 所长导航：四项主入口，设置区分人员、费用和技术诊断`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
    await openNavigation(page);
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    const primary = nav.locator(':scope > button.agent-primary-item');
    await expect(primary).toHaveCount(4);
    for (const name of ['全所概览', '客户企业', '月度交付', '异常与补件']) {
      await expect(primary.filter({ hasText: name })).toHaveCount(1);
      await expect(nav.getByRole('button', { name, exact: true })).toHaveClass(
        /agent-primary-item/,
      );
    }
    const settings = nav.locator('details[aria-label="设置"]');
    await expect(settings).not.toHaveAttribute('open', '');
    await page.screenshot({ path: `${output}/firm-primary-${device}.png`, fullPage: false });
    await settings.locator(':scope > summary').click();
    for (const name of ['人员与授权', '费用与人工投入', '技术诊断']) {
      await expect(settings.getByText(name, { exact: true })).toBeVisible();
    }
    await expect(
      settings.getByRole('button', { name: '企业与人员管理', exact: true }),
    ).toBeVisible();
    await expect(
      settings.getByRole('button', { name: '费用与运行监控', exact: true }),
    ).toBeVisible();
    await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
    await page.screenshot({ path: `${output}/firm-navigation-${device}.png`, fullPage: true });
    const diagnostics = settings.getByText('技术诊断', { exact: true });
    await diagnostics.scrollIntoViewIfNeeded();
    const diagnosticBounds = await diagnostics.boundingBox();
    expect(diagnosticBounds!.y).toBeGreaterThanOrEqual(0);
    expect(diagnosticBounds!.y + diagnosticBounds!.height).toBeLessThanOrEqual(viewport.height);
    await page.screenshot({ path: `${output}/firm-settings-${device}.png`, fullPage: false });
    if (device === 'mobile') {
      await page.getByRole('button', { name: '关闭导航', exact: true }).click();
      await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: '打开工作空间导航', exact: true }),
      ).toBeFocused();
    }
    await noOverflow(page);
  });

  test(`${device} V05 员工与财务：同一状态按职责提示，兼任权限不切换门户`, async ({ page }) => {
    const audit = await projectedTasks(page, 'client');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'employee');
    const nav = page.getByRole('navigation', { name: '客户服务', exact: true });
    expect(await nav.getByRole('button').count()).toBeGreaterThanOrEqual(3);
    expect(await nav.getByRole('button').count()).toBeLessThanOrEqual(5);
    await expect(page.locator('.agent-sidebar')).toHaveCount(0);
    const overview = page.getByRole('region', { name: '我的工作概览', exact: true });
    await overview.getByRole('button', { name: /我的办理进度/ }).click();
    const row = page.locator('[data-task-key="finance:v05-shared"]');
    await expect(row).toHaveAttribute('data-summary', 'waiting');
    await expect(row).toContainText('等待处理');
    // V08 uses plain copy for the employee; the same stage remains professional for finance below.
    await expect(row).toContainText('等待财务核对资料');
    await expect(row.locator('.workspace-task-guidance')).toContainText('下一步');
    await expect(row).toContainText('本企业财务核验人');
    await expect(row.getByRole('button', { name: '核验资料', exact: true })).toHaveCount(0);
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/employee-task-${device}.png`, fullPage: true });
    await noOverflow(page);
    await choosePersona(page, 'finance');
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    await expect(page.locator('.agent-sidebar')).toHaveCount(0);
    await expect(row).toHaveAttribute('data-summary', 'mine');
    await expect(row).toContainText('待我处理');
    await expect(row).toContainText('待财务核验');
    await expect(row.locator('.workspace-task-guidance')).toContainText('下一步');
    await expect(row.getByRole('button', { name: '核验资料', exact: true })).toBeVisible();
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/finance-task-${device}.png`, fullPage: true });
    await noOverflow(page);
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });

  test(`${device} V05 会计任务：未知回执不重发，已审核不冒充完成，后台等待可解释`, async ({
    page,
  }) => {
    const audit = await projectedTasks(page, 'accounting');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '事务所管理', exact: true })).toBeVisible();
    const unknown = page.locator('[data-task-key="delivery:v05-unknown-receipt"]');
    await expect(unknown).toHaveAttribute('data-summary', 'blocked');
    await expect(unknown).toContainText('需要关注');
    await expect(unknown).toContainText(/待核实|待确认/);
    await expect(unknown.locator('.workspace-task-guidance')).toContainText(/结果|回执/);
    await expect(unknown.getByRole('button', { name: /重试|重发/ })).toHaveCount(0);
    const approved = page.locator('[data-task-key="export:v05-approved"]');
    await expect(approved).not.toHaveAttribute('data-summary', 'done');
    await expect(approved).toContainText('已审核待导出');
    await expect(approved.locator('.workspace-task-guidance')).toContainText(/导出/);
    await unknown.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/accounting-tasks-${device}.png`, fullPage: true });
    await noOverflow(page);
    await page
      .getByRole('region', { name: '我的工作概览', exact: true })
      .getByRole('button', { name: /等待/ })
      .click();
    const queued = page.locator('[data-task-key="ocr:v05-queued"]');
    await expect(queued).toHaveAttribute('data-summary', 'running');
    await expect(queued).toContainText('排队');
    await expect(queued.locator('.workspace-task-guidance')).toContainText('下一步');
    await queued.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/queued-task-${device}.png`, fullPage: true });
    expect(audit.writes).toEqual([]);
    expect(audit.detailReads).toEqual([]);
  });
}
