import { choosePersona, openBusiness, openManagement } from './ui-navigation';
import { expect, test, type Page } from '@playwright/test';

const receipt = { name: '第四阶段虚构票据.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') };
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
  if (await page.getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true }).count()) await page.getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true }).click();
  await page.getByRole('button', { name: '保存事项', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  const uploaded = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/attachments?'));
  await page.getByLabel('上传业务事项附件').setInputFiles(receipt);
  const response = await uploaded;
  expect(response.status()).toBe(200);
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
  return response.json() as Promise<{ id: string; company_id: string; version: number; attachments: { id: string }[] }>;
}

async function openEvent(page: Page, description: string, scope?: '企业财务') {
  if (scope) await page.getByRole('tab', { name: scope, exact: true }).click();
  // Finance now starts with pending verification; this test deliberately
  // inspects an employee's draft without changing its approval state.
  if (scope) if (await page.getByLabel('筛选业务事项状态', { exact: true }).inputValue() !== '') await page.getByLabel('筛选业务事项状态', { exact: true }).selectOption('');
  await page.getByRole('button').filter({ has: page.getByText(description, { exact: true }) }).click();
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
}

test('真实本地 UI：企业启用、未配置服务禁识别、员工登记核对异常及财务独立处理', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  await openBusiness(page);
  await page.getByRole('button', { name: '部门与企业设置', exact: true }).click();
  const settings = page.getByRole('dialog');
  await settings.getByLabel('客户企业', { exact: true }).selectOption({ label: companyName });
  await expect(settings.getByText('当前服务：等待管理员配置。所有结果均需人工核对，会计审核后方可导出。', { exact: true })).toBeVisible();
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
    await expect(panel.getByText('这是最新资料的即时核对发现，请先“重新核对”登记后再记录人工处理。', { exact: true }).first()).toBeVisible();
    await expect(panel.getByRole('button', { name: '记录人工处理', exact: true })).toHaveCount(0);
    await panel.getByRole('button', { name: '重新核对', exact: true }).click();
    await expect(panel.getByText('这是最新资料的即时核对发现，请先“重新核对”登记后再记录人工处理。', { exact: true })).toHaveCount(0);
    await expect(panel.getByText('附件尚未成功识别，需人工核对或重试', { exact: true })).toBeVisible();
    await expect(panel.getByRole('button', { name: '记录人工处理', exact: true })).toHaveCount(0);
    await openManagement(page); await page.getByRole('button', { name: '票据核对异常', exact: true }).click();
    await expect(page.getByText(description, { exact: true }).first()).toBeVisible();

    await persona(page, 'finance');
    await openEvent(page, description, '企业财务');
    await expect(panel.getByRole('button', { name: '识别票据', exact: true })).toBeDisabled();
    const reason = '仅使用虚构测试原件，由独立企业财务核对金额，真实票据仍须会计复核。';
    await panel.getByRole('textbox', { name: /^异常处理依据 / }).first().fill(reason);
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
    const reset = await request.put(`/api/companies/${event.company_id}/intelligence/settings`, { timeout: 5000, headers: { Authorization: `Bearer ${owner.token}` }, data: { enabled: false } });
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
  await page.route(`**/api/business/events/${event.id}/intelligence`, async route => {
    const real = await route.fetch();
    expect(real.status()).toBe(200);
    const detail = await real.json();
    await route.fulfill({ response: real, json: { ...detail, runs: [{
      id: 'synthetic-ui-rendering-only', kind: 'ocr', status: 'succeeded', attachment_id: event.attachments[0].id,
      input_version: event.version, provider: '合成响应：仅展示测试', model: 'fixture-vision-no-supplier',
      prompt_version: 'ui-fixture-only', created_at: '2026-10-11T12:00:00Z', result: { documents: [{
        document_type: 'receipt', invoice_number: 'UI-FIXTURE-001', issued_on: '2026-10-10', seller_name: '第四阶段虚构供应商',
        seller_tax_id: null, buyer_name: null, buyer_tax_id: null, gross_amount: '268.10', net_amount: null, tax_amount: null, currency: 'CNY',
        pages: [{ page: 1, raw_text: rawText }], evidence: [{ field: 'gross_amount', page: 1, quote: '合计 268.10 CNY' }],
      }] },
    }] } });
  });
  const panel = page.getByRole('region', { name: '票据识别与交叉核对' });
  await panel.getByRole('button', { name: '刷新票据核对结果', exact: true }).click();
  await expect(panel.getByText('合成响应：仅展示测试 · fixture-vision-no-supplier', { exact: true })).toBeVisible();
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

test('独立 OCR 合成证据：字段定位、低分与缺证、手机预览及切换身份清理', async ({ page }) => {
  await page.addInitScript(() => {
    const state = { created: [] as string[], revoked: [] as string[] };
    Object.assign(window, { syntheticOcrUrls: state });
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob) => { const url = create(blob); state.created.push(url); return url; };
    URL.revokeObjectURL = (url) => { state.revoked.push(url); revoke(url); };
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  await persona(page, 'employee');
  const event = await createWithOriginal(page, '独立OCR展示验证（全部为合成票据）');
  const image = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 1500; canvas.height = 900;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#fffdf5'; ctx.fillRect(0, 0, 1500, 900);
    ctx.fillStyle = '#40523b'; ctx.font = '42px sans-serif';
    ctx.fillText('合成测试收据 · 非真实业务', 120, 130);
    ctx.font = '30px sans-serif';
    ctx.fillText('第四阶段虚构供应商', 120, 300);
    ctx.fillText('价税合计 268.10 CNY', 120, 550);
    ctx.fillStyle = '#aaaaaa'; ctx.fillText('仅验证文字框定位与权限，不代表识别准确率。', 120, 750);
    return canvas.toDataURL('image/png').split(',')[1];
  });
  let previewReads = 0;
  let forbidden = false;
  await page.route(`**/api/business/events/${event.id}/intelligence/runs/synthetic-paddle-ui/pages/1`, async route => {
    previewReads += 1;
    expect(route.request().headers().authorization).toMatch(/^Bearer /);
    await route.fulfill(forbidden
      ? { status: 403, json: { detail: '测试撤销的附件权限' } }
      : { status: 200, contentType: 'image/png', headers: { 'Cache-Control': 'no-store' }, body: Buffer.from(image, 'base64') });
  });
  await page.route(`**/api/business/events/${event.id}/intelligence`, async route => {
    const real = await route.fetch();
    const detail = await real.json();
    await route.fulfill({ response: real, json: { ...detail, runs: [{
      id: 'synthetic-paddle-ui', kind: 'ocr', status: 'succeeded', attachment_id: event.attachments[0].id,
      input_version: event.version, provider: '合成独立OCR展示测试', model: 'fixture-no-real-recognition',
      prompt_version: 'ui-fixture-only', created_at: '2026-10-14T12:00:00Z', result: {
        documents: [{ document_type: 'receipt', invoice_number: 'SYNTHETIC-PADDLE-001', issued_on: '2026-10-10', seller_name: '第四阶段虚构供应商', seller_tax_id: null, buyer_name: null, buyer_tax_id: null, gross_amount: '268.10', net_amount: null, tax_amount: null, currency: 'CNY', pages: [{ page: 1, raw_text: '模型生成的转录（与独立文字来源分开）' }], evidence: [{ field: 'gross_amount', page: 1, quote: '价税合计 268.10 CNY' }] }],
        ocr_evidence: {
          schema_version: 'independent-ocr-v1', engine: 'paddleocr', engine_version: 'fixture', model: 'PP-OCR UI fixture', preprocessing_version: 'paddle-input-v1', elapsed_ms: 450, recognized_pages: 1, native_pages: 0,
          pages: [{ page: 1, width: 1500, height: 900, source: 'paddleocr', blocks: [
            { id: 'p1-seller', text: '第四阶段虚构供应商', score: 0.51, polygon: [[110, 260], [700, 260], [700, 310], [110, 310]] },
            { id: 'p1-amount', text: '价税合计 268.10 CNY', score: 0.991, polygon: [[110, 510], [750, 510], [750, 565], [110, 565]] },
          ] }],
          bindings: [
            { document_index: 0, field: 'gross_amount', page: 1, block_ids: ['p1-amount'], score: 0.991, status: 'matched' },
            { document_index: 0, field: 'seller_name', page: 1, block_ids: ['p1-seller'], score: 0.51, status: 'low_confidence' },
            { document_index: 0, field: 'invoice_number', page: 1, block_ids: [], score: null, status: 'unmatched' },
          ],
        },
      },
    }] } });
  });
  const panel = page.getByRole('region', { name: '票据识别与交叉核对' });
  await panel.getByRole('button', { name: '刷新票据核对结果', exact: true }).click();
  const independent = panel.getByRole('region', { name: '独立文字识别证据', exact: true });
  await expect(independent).toBeVisible();
  expect(previewReads).toBe(0);
  await panel.getByRole('button', { name: '核对价税合计的文字证据', exact: true }).click();
  await expect(independent.getByRole('img', { name: '独立 OCR 证据第 1 页', exact: true })).toBeVisible();
  await expect(independent.locator('polygon')).toHaveAttribute('points', '110,510 750,510 750,565 110,565');
  await expect(independent.getByText('字段文字识别分数：99.1%', { exact: true })).toBeVisible();
  await expect(independent.getByText(/不代表会计处理正确率/)).toBeVisible();
  await independent.getByText('第 1 页独立识别原文与分数', { exact: true }).click();
  await expect(independent.getByText('文字分数 51.0%', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: '核对销售方的文字证据', exact: true }).click();
  await expect(independent.locator('polygon')).toHaveClass('needs-review');
  await expect(independent.getByText(/请人工核对原件；如金额/)).toBeVisible();
  await panel.getByRole('button', { name: '核对发票号码的文字证据', exact: true }).click();
  await expect(independent.locator('polygon')).toHaveCount(0);
  await expect(independent.getByText('此字段没有可定位的文字框，请人工核对原件。', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: '核对价税合计的文字证据', exact: true }).click();
  expect(previewReads).toBe(1);
  await independent.screenshot({ path: 'artifacts/stage19-independent-ocr-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(independent.locator('polygon')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await independent.screenshot({ path: 'artifacts/stage19-independent-ocr-mobile.png' });
  await independent.getByRole('button', { name: '放大票据文字', exact: true }).click();
  await expect(independent.getByRole('button', { name: '适应页面宽度', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const previewUrl = await independent.getByRole('img', { name: '独立 OCR 证据第 1 页', exact: true }).getAttribute('src');
  await choosePersona(page, 'accountant');
  await expect(page.getByRole('img', { name: '独立 OCR 证据第 1 页', exact: true })).toHaveCount(0);
  expect(await page.evaluate((url) => (window as unknown as { syntheticOcrUrls: { revoked: string[] } }).syntheticOcrUrls.revoked.includes(url!), previewUrl)).toBe(true);
  await persona(page, 'employee');
  await openEvent(page, '独立OCR展示验证（全部为合成票据）');
  forbidden = true;
  const rejectedPreview = page.waitForResponse(response => response.url().includes('/runs/synthetic-paddle-ui/pages/1') && response.status() === 403);
  await panel.getByRole('button', { name: '核对价税合计的文字证据', exact: true }).click();
  await rejectedPreview;
  await expect(page.getByRole('img', { name: '独立 OCR 证据第 1 页', exact: true })).toHaveCount(0);
});
