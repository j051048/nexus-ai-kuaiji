import { expect, test, type Locator, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
const phase = process.env.V02_SCREENSHOT_PHASE ?? 'current';
const output = `tmp_test/v02/${phase}`;
const companyName = '虚构华东先进制造与综合技术服务有限公司第一分公司';

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

async function keyboardFocus(page: Page, locator: Locator) {
  await locator.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  expect(
    await locator.evaluate((node) => {
      const style = getComputedStyle(node);
      return (
        node.matches(':focus-visible') &&
        ((style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2) ||
          style.boxShadow !== 'none')
      );
    }),
  ).toBe(true);
}

function watchReads(page: Page) {
  const paths: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && request.method() === 'GET') paths.push(path);
  });
  return paths;
}

async function recordHome(page: Page, reads: string[]) {
  await test.info().attach('home-layout-and-reads', {
    contentType: 'application/json',
    body: JSON.stringify(
      {
        reads,
        sections: await page.locator('main section').count(),
        headings: await page.locator('main h2').allTextContents(),
        cardHeight: await page
          .locator('.workspace-primary-intake')
          .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height)),
      },
      null,
      2,
    ),
  });
}

async function installFinancePresentation(page: Page) {
  let companyId = '';
  function tasks(bucket = 'action') {
    const all = [
      {
        key: 'finance:v02-only-read',
        kind: 'finance',
        ref: 'v02-only-read',
        company_id: companyId,
        company_name: companyName,
        book_id: null,
        event_id: null,
        draft_id: null,
        batch_id: null,
        title: '虚构采购事项：附件齐全，等待财务核验',
        state: 'department_approved',
        priority: 80,
        due_on: '2026-10-01',
        updated_at: '2026-10-01T00:00:00Z',
        bucket: 'action',
        action_label: '核验资料',
        responsible: '企业财务',
        overdue: true,
        amount: '123456789.01',
      },
      {
        key: 'case:v02-unknown',
        kind: 'case',
        ref: 'v02-unknown',
        company_id: companyId,
        company_name: companyName,
        book_id: null,
        event_id: null,
        draft_id: null,
        batch_id: null,
        title: '虚构回执状态待核实，不能视为已完成',
        state: 'unknown',
        priority: 70,
        due_on: null,
        updated_at: '2026-10-01T00:00:00Z',
        bucket: 'waiting',
        action_label: '查看进度',
        responsible: '会计',
        overdue: false,
      },
    ];
    const rows = all.filter((row) => row.bucket === bucket);
    return {
      tasks: rows,
      counts: { waiting: 1 },
      pagination: { limit: 20, offset: 0, total: rows.length, has_more: false },
    };
  }
  await page.route(/\/api\/workspace\/(bootstrap|profile)(\?|$)/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const profile = data.profile ?? data;
    if (profile.role !== 'finance') {
      await route.fulfill({ response, json: data });
      return;
    }
    companyId = profile.companies[0]?.id ?? '';
    // Preserve real identities and all capabilities; only read-only display data changes.
    if (profile.companies[0]) profile.companies[0].name = companyName;
    await route.fulfill({
      response,
      json: data.profile ? { ...data, profile, tasks: tasks() } : profile,
    });
  });
  await page.route(/\/api\/workspace\/tasks\?/, async (route) => {
    const response = await route.fetch();
    if (!companyId || !response.ok()) {
      await route.fulfill({ response });
      return;
    }
    await route.fulfill({
      response,
      json: tasks(new URL(route.request().url()).searchParams.get('bucket') ?? 'action'),
    });
  });
}

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 375, height: 812 },
]) {
  const device = viewport.width < 640 ? 'mobile' : 'desktop';

  test(`${device} 员工工作台：三模板紧凑可读，进度更早触达且复用录入`, async ({ page }) => {
    const reads = watchReads(page);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'employee');
    const primary = page.getByRole('region', { name: '提交与进度', exact: true });
    await expect(primary).toBeVisible();
    await expect(page.locator('.workspace-inbox .pagination-controls')).toBeVisible();
    await page.screenshot({ path: `${output}/employee-home-${device}.png`, fullPage: true });
    await recordHome(page, reads);
    await expect(primary.getByRole('button')).toHaveCount(3);
    for (const label of [/提交报销/, /采购 \/ 费用/, /日常收支/]) {
      const entry = primary.getByRole('button', { name: label });
      const bounds = await entry.boundingBox();
      expect(bounds?.height).toBeGreaterThanOrEqual(device === 'mobile' ? 44 : 36);
      expect(await entry.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    }
    if (device === 'mobile') expect((await primary.boundingBox())?.height).toBeLessThanOrEqual(330);
    expect(reads.filter((path) => path === '/api/agent-workspace/summary')).toEqual([]);
    await noOverflow(page);
    const submit = primary.getByRole('button', { name: /提交报销/ });
    await keyboardFocus(page, submit);
    await submit.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('事项金额', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
  });

  test(`${device} 企业财务：下一项优先，未知数量与等待状态不伪装完成`, async ({ page }) => {
    const reads = watchReads(page);
    await installFinancePresentation(page);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'finance');
    const primary = page.getByRole('region', { name: '资料核验与移交', exact: true });
    await expect(primary.getByRole('button', { name: '核验下一项', exact: true })).toBeVisible();
    await expect(page.locator('.workspace-inbox .pagination-controls')).toBeVisible();
    await page.screenshot({ path: `${output}/finance-home-${device}.png`, fullPage: true });
    await recordHome(page, reads);
    await expect(
      primary.getByText('虚构采购事项：附件齐全，等待财务核验', { exact: true }),
    ).toBeVisible();
    const summary = page.getByRole('region', { name: '我的工作概览', exact: true });
    const needs = summary.getByRole('button', { name: /需要我处理/ });
    await expect(needs.locator('strong')).toHaveText('—');
    await expect(needs).toContainText('数量暂未汇总');
    const next = primary.getByRole('button', { name: '核验下一项', exact: true });
    const other = primary.getByRole('button', { name: '查看待核验事项', exact: true });
    expect(await next.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(
      await other.evaluate((node) => getComputedStyle(node).backgroundColor),
    );
    await keyboardFocus(page, next);
    await noOverflow(page);
    await summary.getByRole('button', { name: /等待/ }).click();
    await expect(
      page.getByText('虚构回执状态待核实，不能视为已完成', { exact: true }),
    ).toBeVisible();
    await expect(primary.getByRole('button', { name: '核验下一项', exact: true })).toHaveCount(0);
    await expect(page.locator('.workspace-task-state')).toContainText('状态待核实');
    expect(reads.filter((path) => path === '/api/agent-workspace/summary')).toEqual([]);
    await other.click();
    await expect(page.getByRole('tab', { name: '企业财务', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test(`${device} 事务所：客户进度突出，完整风险和长客户名可读`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const reads = watchReads(page);
    await page.route(/\/api\/firm\/cockpit(\?|$)/, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      if (data.companies?.length) {
        data.companies[0] = {
          ...data.companies[0],
          company_name: companyName,
          stage: 'review',
          tasks: { ...data.companies[0].tasks, overdue: 2, exception: 1 },
        };
      }
      await route.fulfill({ response, json: data });
    });
    await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
    const summary = page.getByRole('region', { name: '全所运营概览', exact: true });
    await expect(summary).toBeVisible();
    await expect(page.getByText(companyName, { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: `${output}/firm-home-${device}.png`, fullPage: true });
    await recordHome(page, reads);
    await expect(page.getByRole('heading', { name: '需要你协调', exact: true })).toBeVisible();
    await expect(
      page.getByText('2 项待办已逾期；1 项异常待处理', { exact: false }).first(),
    ).toBeVisible();
    await expect(page.getByText('待会计复核', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('heading', { name: '人员安排', exact: true })).toBeVisible();
    if (device === 'mobile') {
      const metrics = summary.locator('.cockpit-summary-values article');
      const first = await metrics.nth(0).boundingBox();
      const second = await metrics.nth(1).boundingBox();
      expect(second!.y).toBeGreaterThan(first!.y + first!.height - 1);
      expect(
        (await summary.getByRole('button', { name: '客户进度', exact: true }).boundingBox())!
          .height,
      ).toBeGreaterThanOrEqual(44);
    }
    await noOverflow(page);
    expect(reads.filter((path) => path === '/api/firm/cockpit')).toHaveLength(1);
    const progress = summary.getByRole('button', { name: '客户进度', exact: true });
    await keyboardFocus(page, progress);
    await progress.click();
    await expect(page).toHaveURL(/\/workspace\/monthly/);
  });
}

test('事务所空态：保持原有开通路径，不凭空出现客户完成状态', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route(/\/api\/firm\/cockpit(\?|$)/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      response,
      json: {
        ...data,
        summary: { ...data.summary, clients: 0, opened: 0, closed: 0 },
        companies: [],
        workload: [],
        risks: [],
      },
    });
  });
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '从第一家客户开始', exact: true })).toBeVisible();
  await page.screenshot({ path: `${output}/firm-empty-mobile.png`, fullPage: true });
  const action = page
    .locator('.cockpit-first-client')
    .getByRole('button', { name: '新增客户', exact: true });
  await action.click();
  await expect(page.getByRole('heading', { name: '客户标准化开通', exact: true })).toBeVisible();
});
