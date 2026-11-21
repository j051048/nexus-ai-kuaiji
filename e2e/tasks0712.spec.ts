import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import { choosePersona } from './ui-navigation';

async function fixture(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const company = await (
    await request.post('/api/companies', {
      headers,
      data: { name: '0712虚构企业' + crypto.randomUUID(), tax_id: '' },
    })
  ).json();
  const book = await (
    await request.post('/api/books', {
      headers,
      data: { company_id: company.id, name: '0712试点账套', period: '2026-10' },
    })
  ).json();
  expect(book.id).toBeTruthy();
  return { session, headers, company, book };
}
async function shot(page: Page, name: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  mkdirSync('tmp_test/tasks0712-20261107/screenshots', { recursive: true });
  await page.screenshot({
    path: `tmp_test/tasks0712-20261107/screenshots/${name}.png`,
    fullPage: true,
  });
}
for (const width of [1365, 390]) {
  test(`0712 ${width} 试点样本和产品边界可核对`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { headers, book, company } = await fixture(request);
    const query = `book=${book.id}&company=${company.id}&period=${book.period}`;
    await page.goto(`/workspace/efficiency?${query}`);
    const pilot = page.getByRole('region', { name: '试点样本口径' });
    await expect(pilot).toContainText('当前已复核对象 0 个');
    await expect(page.getByText('暂无计时样本', { exact: true })).toBeVisible();
    await shot(page, `pilot-${width}`);
    const started = await request.post('/api/efficiency/sessions', {
      headers,
      data: { book_id: book.id, activity: 'review', idempotency_key: crypto.randomUUID() },
    });
    expect(started.status(), await started.text()).toBe(200);
    await page.reload();
    await expect(pilot).toContainText('单人办理不计作独立复核');
    await expect(page.locator('.efficiency-summary')).toContainText('0秒');
    const timer = await started.json();
    expect(
      (
        await request.post(`/api/efficiency/sessions/${timer.id}/commands`, {
          headers,
          data: {
            action: 'complete',
            expected_version: timer.version,
            idempotency_key: crypto.randomUUID(),
          },
        })
      ).ok(),
    ).toBe(true);
    await page.route(`**/api/books/${book.id}/efficiency`, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ json: { ...(await response.json()), book_id: 'foreign' } });
    });
    await page.reload();
    await expect(
      page.getByText('统计范围或样本不一致，请重新读取；不会推算缺失数据。'),
    ).toBeVisible();
    await expect(pilot).toHaveCount(0);
    await page.goto(`/workspace/formal-books?${query}`);
    await expect(page.getByText(/不代表外部财务软件已过账/)).toBeVisible();
    await page.goto(`/workspace/delivery?${query}`);
    await page.getByRole('button', { name: '税务底稿', exact: true }).click();
    await expect(page.getByRole('region', { name: '税务底稿', exact: true })).toContainText(
      '以人工登记及回执为准',
    );
    await shot(page, `boundary-${width}`);
  });
  test(`0712 ${width} 运行记录按范围展示且人工打开处理`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { headers } = await fixture(request);
    await page.goto('/workspace/observability');
    await expect(page.getByRole('heading', { name: '运行可观测性' })).toBeVisible();
    await expect(page.getByText('AI 已知费用小计', { exact: true })).toBeVisible();
    // The real API is used first; then inject one malformed tenant response.
    await expect(page.locator('.observability-cards:not(.observability-operations):not(.observability-value):not(.observability-radar-counts)')).toBeVisible();
    await expect(page.locator('section[aria-label="办理积压与队列年龄"]')).toContainText('待复核凭证');
    await expect(page.locator('section[aria-label="FDE 价值基线"]')).toContainText('价值基线');
    await expect(page.locator('section[aria-label="持续结账雷达"]')).toContainText('持续结账雷达');
    await expect(page.locator('section[aria-label="持续结账雷达"]')).toContainText('仍需人工确认');
    await expect(page.locator('section[aria-label="FDE 价值基线"]')).toContainText('系统耗时不是人工工时');
    const fsc = page.locator('section[aria-label="五行业档案矩阵"]');
    await expect(fsc).toContainText('已确认');
    await expect(fsc).toContainText('未知不等于不适用');
    const organization = page.locator('section[aria-label="组织与分工投影"]');
    await expect(organization).toContainText('组织与分工');
    await expect(organization).toContainText('只读投影');
    await expect(organization).toContainText('指派不会授予权限');
    await expect(organization).toContainText('家企业');
    const contracts = page.locator('section[aria-label="FDE 适配器契约"]');
    await expect(contracts).toContainText('本地合成契约');
    await expect(contracts).toContainText('tax_xlsx');
    await expect(contracts).toContainText('未接真实外部');
    await expect(contracts).toContainText('非原生格式');
    await expect(contracts).toContainText('不授予写入或过账权限');
    await expect(page.getByRole('link', { name: '打开待复核' })).toHaveAttribute(
      'href',
      '/workspace/monthly?focus=review',
    );
    await expect(page.getByRole('link', { name: '打开税工作台' })).toHaveAttribute(
      'href',
      '/workspace/accounting',
    );
    await page.route('**/api/firm/observability?*', async (route) => {
      const data = await (await route.fetch()).json();
      await route.fulfill({
        json: {
          ...data,
          sla: {
            thresholds: {
              queue_age_seconds: 900,
              source_rejections: 0,
              notification_unknown: 0,
            },
            breaches: [{ code: 'queue_age', count: 1200 }],
            automatic_action: false,
          },
        },
      });
    });
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('共享中心 SLA 超限');
    await shot(page, `monitor-sla-${width}`);
    await page.unroute('**/api/firm/observability?*');
    await page.reload();
    await expect(page.getByRole('heading', { name: '运行可观测性' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'FDE 适配器契约' })).toBeVisible();
    await expect(page.locator('section[aria-label="五行业档案矩阵"]')).toContainText('已确认');
    await expect(page.locator('section[aria-label="组织与分工投影"]')).toContainText('只读投影');
    await expect(page.getByRole('region', { name: '导入信封' })).toContainText('尚无导入信封');
    await expect(page.getByRole('region', { name: '证据政策' })).toContainText('尚无生效证据政策');
    await expect(page.getByLabel('五行业能力矩阵')).toContainText('未启用');
    await expect(page.locator('section[aria-label="行业契约"]')).toContainText('份生效契约');
    await expect(page.locator('section[aria-label="行业契约"]')).toContainText('契约是配置证据');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await shot(page, `monitor-${width}`);
    const month = await page.getByLabel('运行记录月份').inputValue();
    const value = await (
      await request.get(`/api/firm/observability?period=${month}`, { headers })
    ).json();
    let wrongFirm = false;
    await page.route('**/api/firm/observability?*', async (route) => {
      await route.fulfill({
        json: {
          ...value,
          firm_id: wrongFirm ? 'foreign' : value.firm_id,
          cost_summary: {
            sample_count: 1,
            known_micro: null,
            unknown_count: 1,
            estimated_count: 0,
          },
          signals: [
            {
              code: 'cost_unknown',
              owner_role: 'admin',
              next_action: '请核查未知费用，不自动重发。',
              target: 'operations',
              automatic_action: false,
            },
          ],
        },
      });
    });
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(page.getByRole('region', { name: '运行异常下一步' })).toContainText('不自动重发');
    await page.getByRole('link', { name: '打开费用与运行记录' }).click();
    await expect(page).toHaveURL(/\/workspace\/operations/);
    await page.goto('/workspace/observability');
    wrongFirm = true;
    await expect(page.getByRole('button', { name: '刷新', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('运行记录范围不一致');
    await expect(page.locator('.observability-cards')).toHaveCount(0);
  });
  test(`0712 ${width} 原件解析后仍由员工确认且保留人工金额`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { headers } = await fixture(request);
    const employee = await (await request.get('/api/session?persona=employee')).json();
    const context = await (
      await request.get('/api/business/context', {
        headers: { Authorization: `Bearer ${employee.token}` },
      })
    ).json();
    const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
    expect(
      (
        await request.put(`/api/companies/${company.id}/intelligence/settings`, {
          headers,
          data: { enabled: true },
        })
      ).ok(),
    ).toBe(true);
    await page.goto('/workspace/home');
    await choosePersona(page, 'employee');
    await page.getByRole('button', { name: /^提交报销/ }).click();
    const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
    await dialog.getByRole('combobox', { name: '事项客户企业' }).selectOption(company.id);
    await expect(dialog.getByLabel('票据办理下一步')).toContainText('先拍照');
    const xml = readFileSync(
      'nexus_backend/tests/fixtures/stage16_SYNTHETIC_einvoice_0_3.xml',
      'utf8',
    ).replace('第一客户企业', company.name);
    await dialog.getByLabel('一体化录入上传附件').setInputFiles({
      name: '0712虚构票.xml',
      mimeType: 'application/xml',
      buffer: Buffer.from(xml),
    });
    await expect(dialog.getByLabel('票据办理下一步')).toContainText('原件已保存');
    await dialog.getByLabel('事项金额', { exact: true }).fill('128.50');
    await dialog.getByRole('button', { name: '解析 XML 并预填', exact: true }).click();
    await expect(dialog.getByText('识别建议已就绪', { exact: true })).toBeVisible();
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
    await expect(dialog.getByLabel('票据办理下一步')).toContainText('核对');
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    expect(await dialog.getByRole('checkbox').filter({ visible: true }).count()).toBeGreaterThan(0);
    await shot(page, `intake-${width}`);
  });
}
