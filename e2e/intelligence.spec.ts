import { choosePersona, openBusiness, openManagement } from './ui-navigation';
import { expect, test, type Page } from '@playwright/test';

const receipt = {
  name: '第四阶段虚构票据.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};
const companyName = '青屿设计（虚构示例）';

async function persona(page: Page, id: 'employee' | 'finance' | 'accountant') {
  await choosePersona(page, id);
  await openBusiness(page);
}

async function createWithOriginal(page: Page, description: string) {
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.getByRole('button', { name: /^员工报销/ }).click();
  await page.getByLabel('事项客户企业').selectOption({ label: companyName });
  await page.getByLabel('发生日期', { exact: true }).fill('2026-10-10');
  await page.getByLabel('事项金额', { exact: true }).fill('268.10');
  await page.getByLabel('事项摘要', { exact: true }).fill(description);
  await page.getByLabel('交易对方', { exact: true }).fill('第四阶段虚构供应商');
  await page.getByLabel('事项分类', { exact: true }).selectOption('office');
  await page.getByLabel('业务用途', { exact: true }).fill('第四阶段隔离浏览器验证');
  if (
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .count()
  )
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
  await page.getByRole('button', { name: '暂存草稿', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  const uploaded = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && response.url().includes('/attachments?'),
  );
  await page.getByLabel('上传业务事项附件').setInputFiles(receipt);
  const response = await uploaded;
  expect(response.status()).toBe(200);
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
  return response.json() as Promise<{
    id: string;
    company_id: string;
    version: number;
    attachments: { id: string }[];
  }>;
}

async function openEvent(page: Page, description: string, scope?: '企业财务') {
  if (scope) await page.getByRole('tab', { name: scope, exact: true }).click();
  // Finance now starts with pending verification; this test deliberately
  // inspects an employee's draft without changing its approval state.
  if (scope)
    if ((await page.getByLabel('筛选业务事项状态', { exact: true }).inputValue()) !== '')
      await page.getByLabel('筛选业务事项状态', { exact: true }).selectOption('');
  await page
    .getByRole('button')
    .filter({ has: page.getByText(description, { exact: true }) })
    .click();
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
}

test('真实本地 UI：企业启用、未配置服务禁识别、员工登记核对异常及财务独立处理', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  await openBusiness(page);
  await page.getByRole('button', { name: '部门与企业设置', exact: true }).click();
  const settings = page.getByRole('dialog');
  await settings.getByLabel('客户企业', { exact: true }).selectOption({ label: companyName });
  await expect(
    settings.getByText('当前服务：等待管理员配置。所有结果均需人工核对，会计审核后方可导出。', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(settings.getByText('该企业未开启', { exact: true })).toBeVisible();
  await settings.getByRole('button', { name: '开启票据与模型处理', exact: true }).click();
  await expect(settings.getByText('该企业已开启', { exact: true })).toBeVisible();
  await settings.getByRole('button', { name: '完成', exact: true }).click();
  await expect(settings).toHaveCount(0);

  await persona(page, 'employee');
  const description = '第四阶段MVP真实本地核对流程';
  const event = await createWithOriginal(page, description);
  try {
    const panel = page.getByRole('region', { name: '票据识别与交叉核对' });
    await expect(panel.getByText('等待管理员配置模型服务', { exact: false })).toBeVisible();
    await expect(panel.getByRole('button', { name: '识别票据', exact: true })).toBeDisabled();
    await expect(panel.getByRole('button', { name: '重新核对', exact: true })).toBeEnabled();
    await expect(
      panel
        .getByText('这是最新资料的即时核对发现，请先“重新核对”登记后再记录人工处理。', {
          exact: true,
        })
        .first(),
    ).toBeVisible();
    await expect(panel.getByRole('button', { name: '记录人工处理', exact: true })).toHaveCount(0);
    await panel.getByRole('button', { name: '重新核对', exact: true }).click();
    await expect(
      panel.getByText('这是最新资料的即时核对发现，请先“重新核对”登记后再记录人工处理。', {
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      panel.getByText('附件尚未成功识别，需人工核对或重试', { exact: true }),
    ).toBeVisible();
    await expect(panel.getByRole('button', { name: '记录人工处理', exact: true })).toHaveCount(0);
    await openManagement(page);
    await page.getByRole('button', { name: '票据核对异常', exact: true }).click();
    await expect(page.getByText(description, { exact: true }).first()).toBeVisible();

    await persona(page, 'finance');
    await openEvent(page, description, '企业财务');
    await expect(panel.getByRole('button', { name: '识别票据', exact: true })).toBeDisabled();
    const reason = '仅使用虚构测试原件，由独立企业财务核对金额，真实票据仍须会计复核。';
    await panel
      .getByRole('textbox', { name: /^异常处理依据 / })
      .first()
      .fill(reason);
    await panel.getByRole('button', { name: '记录人工处理', exact: true }).first().click();
    await expect(panel.getByText('处理依据：' + reason, { exact: true })).toBeVisible();
    await expect(panel.getByText('已人工处理', { exact: true }).first()).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'artifacts/intelligence-workbench.png', fullPage: true });

    await persona(page, 'employee');
    await openEvent(page, description);
    await expect(panel.getByText('处理依据：' + reason, { exact: true })).toBeVisible();
    await expect(panel.getByRole('textbox', { name: /^异常处理依据 / })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: '记录人工处理', exact: true })).toHaveCount(0);
  } finally {
    // Clean only the explicitly fictional company policy in this run's isolated database.
    const session = await request.get('/api/session?persona=accountant', { timeout: 5000 });
    const owner = await session.json();
    const reset = await request.put(`/api/companies/${event.company_id}/intelligence/settings`, {
      timeout: 5000,
      headers: { Authorization: `Bearer ${owner.token}` },
      data: { enabled: false },
    });
    expect(reset.status()).toBe(200);
  }
});

test('显式合成 OCR 响应仅验证展示：字段、未知税额、原文与页码', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  await persona(page, 'employee');
  const description = '第四阶段合成响应展示验证（非真实识别）';
  const event = await createWithOriginal(page, description);
  const rawText = '合成 UI 测试数据，非真实供应商识别。\n第四阶段虚构供应商\n合计 268.10 CNY';
  await page.route(`**/api/business/events/${event.id}/intelligence`, async (route) => {
    const real = await route.fetch();
    expect(real.status()).toBe(200);
    const detail = await real.json();
    await route.fulfill({
      response: real,
      json: {
        ...detail,
        runs: [
          {
            id: 'synthetic-ui-rendering-only',
            kind: 'ocr',
            status: 'succeeded',
            attachment_id: event.attachments[0].id,
            input_version: event.version,
            provider: '合成响应：仅展示测试',
            model: 'fixture-vision-no-supplier',
            prompt_version: 'ui-fixture-only',
            created_at: '2026-10-11T12:00:00Z',
            result: {
              documents: [
                {
                  document_type: 'receipt',
                  invoice_number: 'UI-FIXTURE-001',
                  issued_on: '2026-10-10',
                  seller_name: '第四阶段虚构供应商',
                  seller_tax_id: null,
                  buyer_name: null,
                  buyer_tax_id: null,
                  gross_amount: '268.10',
                  net_amount: null,
                  tax_amount: null,
                  currency: 'CNY',
                  pages: [{ page: 1, raw_text: rawText }],
                  evidence: [{ field: 'gross_amount', page: 1, quote: '合计 268.10 CNY' }],
                },
              ],
            },
          },
        ],
      },
    });
  });
  const panel = page.getByRole('region', { name: '票据识别与交叉核对' });
  await panel.getByRole('button', { name: '刷新票据核对结果', exact: true }).click();
  await expect(
    panel.getByText('合成响应：仅展示测试 · fixture-vision-no-supplier', { exact: true }),
  ).toBeVisible();
  await expect(panel.getByText('UI-FIXTURE-001', { exact: true })).toBeVisible();
  await expect(panel.getByText('¥ 268.10', { exact: true })).toBeVisible();
  const tax = panel.getByText('税额', { exact: true }).locator('..');
  await expect(tax.getByText('待核对', { exact: true })).toBeVisible();
  await expect(tax.getByText(/0\.00/)).toHaveCount(0);
  await panel.getByText('识别依据与页码', { exact: true }).click();
  await expect(panel.getByText('价税合计 · 第 1 页', { exact: true })).toBeVisible();
  await expect(panel.getByText('合计 268.10 CNY', { exact: true })).toBeVisible();
  await panel.getByText('第 1 页转录文本', { exact: true }).click();
  await expect(panel.locator('pre')).toHaveText(rawText);
  await panel.screenshot({ path: 'artifacts/intelligence-synthetic-ocr-ui.png' });
});
