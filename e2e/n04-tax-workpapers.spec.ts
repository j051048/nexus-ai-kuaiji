import { expect, test, type Locator } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { checked } from './formal-fixtures';
import {
  changeFormalSource,
  deliveryUrl,
  openTax,
  pinPersona,
  reviewTaxPaper,
  taxFixture,
  taxFullFixture,
  taxPaper,
} from './n04-tax-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 15_000 });

async function expectAmount(panel: Locator, label: string, amount: string) {
  await expect(
    panel.locator('.tax-totals > div').filter({ hasText: label }).locator('strong'),
  ).toHaveText(amount);
}

for (const viewport of [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`N04 ${viewport.width} 档案到独立复核导出使用真实身份和发票`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await taxFullFixture(request);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize(viewport);
    let panel = await openTax(page, f);
    await expect(panel).toContainText('尚未申报');
    await expect(panel.getByRole('combobox', { name: '纳税人类型', exact: true })).toBeVisible();
    await expect(panel.getByLabel('增值税率（%）', { exact: true })).toHaveValue('');
    await expect(panel.getByRole('button', { name: '生成税务底稿', exact: true })).toBeDisabled();
    await panel.getByLabel('增值税率（%）', { exact: true }).fill('3');
    await panel.getByLabel('附加税率（%）', { exact: true }).fill('12');
    await panel
      .getByLabel('适用依据', { exact: true })
      .fill('本地虚构场景人工核定口径，不作为真实企业申报依据');
    const saved = page.waitForResponse(
      (r) => r.url().endsWith('/api/tax/profile') && r.request().method() === 'PUT',
    );
    await panel.getByRole('button', { name: '保存税务档案', exact: true }).click();
    const savedResponse = await saved;
    expect(savedResponse.status()).toBe(200);
    expect(savedResponse.request().postDataJSON()).toMatchObject({
      book_id: f.book.id,
      vat_rate_bp: 300,
      surtax_rate_bp: 1200,
    });
    await expect(panel.getByRole('region', { name: '税务档案', exact: true })).toContainText(
      '增值税率 3%',
    );
    const mapping = panel.getByRole('region', { name: '来源与科目用途', exact: true });
    await expect(mapping.getByRole('combobox')).toHaveCount(4);
    for (const account of f.accounts) {
      const field = mapping.getByLabel(`${account.code} ${account.name} 版本 1 用途`, {
        exact: true,
      });
      await expect(field).toHaveValue('');
      await field.selectOption(account.id === f.vat.id ? 'vat_payable' : 'excluded');
    }
    await panel
      .getByLabel('生成说明', { exact: true })
      .fill('明确确认四个科目版本，销项票税额30、正式账税额25由独立会计解释');
    const generated = page.waitForResponse(
      (r) => r.url().endsWith('/api/tax/workpapers') && r.request().method() === 'POST',
    );
    await panel.getByRole('button', { name: '生成税务底稿', exact: true }).click();
    const generateResponse = await generated;
    expect(generateResponse.status(), await generateResponse.text()).toBe(201);
    const paper = await generateResponse.json();
    expect(generateResponse.request().postDataJSON()).toMatchObject({
      book_id: f.book.id,
      period: '2026-10',
      vat_accounts: expect.arrayContaining([
        { account_id: f.vat.id, account_version: 1, role: 'vat_payable' },
      ]),
    });
    expect(generateResponse.request().postDataJSON().vat_accounts).toHaveLength(4);
    expect(paper).toMatchObject({
      tax: '30.00',
      ledger: '25.00',
      variance: '5.00',
      status: 'draft',
      filing: 'not_filed',
    });
    await expectAmount(panel, '测算增值税', '30.00');
    await expectAmount(panel, '账表差异', '5.00');
    const frozen = panel.getByRole('region', { name: '本版冻结科目用途', exact: true });
    await expect(frozen.getByRole('listitem')).toHaveCount(4);
    await expect(frozen.getByRole('listitem').filter({ hasText: f.vat.name })).toContainText(
      '计入增值税账面对照金额',
    );
    await panel.getByText('查看本版来源证据（1 行发票）', { exact: true }).click();
    const invoice = panel.locator('.tax-evidence dl');
    await expect(invoice).toHaveCount(1);
    for (const [label, value] of [
      ['发票号码', f.invoiceNumber],
      ['不含税金额（元）', '1,000.00'],
      ['票面税额（元）', '30.00'],
      ['价税合计（元）', '1,030.00'],
    ]) {
      await expect(
        invoice
          .locator('div')
          .filter({ has: page.getByText(label, { exact: true }) })
          .locator('dd'),
      ).toHaveText(value);
    }
    expect(f.invoiceNumber).toMatch(/^0000\d+$/);
    await expect(panel).toContainText('你是本版制单人');
    await expect(panel.getByRole('button', { name: '独立复核底稿', exact: true })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: '导出已复核 CSV', exact: true })).toBeDisabled();
    await page.screenshot({
      path: testInfo.outputPath(`n04-tax-${viewport.width}-draft.png`),
      fullPage: true,
    });

    // Switch to a real, separately authorized local reviewer. No privilege claims are forged.
    await pinPersona(page, 'reviewer');
    panel = await openTax(page, f);
    await expect(panel.getByRole('button', { name: '保存税务档案', exact: true })).toHaveCount(0);
    await expect(
      panel.getByRole('region', { name: '本版冻结科目用途', exact: true }).getByRole('listitem'),
    ).toHaveCount(4);
    const review = panel.getByRole('button', { name: '独立复核底稿', exact: true });
    await expect(review).toBeDisabled();
    await panel
      .getByLabel('独立复核说明', { exact: true })
      .fill('独立核对原始销项票、正式账科目和5.00差异；底稿仅供核对，后续账务调整由人工另行办理');
    await panel
      .getByRole('checkbox', { name: '我已核对来源、科目用途及差异，确认独立复核本版底稿' })
      .check();
    const reviewed = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/tax/workpapers/${paper.id}/review`) &&
        r.request().method() === 'POST',
    );
    await review.click();
    const reviewResponse = await reviewed;
    expect(reviewResponse.status(), await reviewResponse.text()).toBe(200);
    expect(reviewResponse.request().postDataJSON()).toMatchObject({
      book_id: f.book.id,
      expected_version: paper.version,
    });
    const approved = await reviewResponse.json();
    expect(approved.reviewed_by).toBe('local-reviewer');
    expect(approved.created_by).not.toBe(approved.reviewed_by);
    await expect(panel.getByRole('combobox', { name: '底稿版本', exact: true })).toContainText(
      '已复核',
    );
    const csv = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' && r.url().includes(`/api/tax/workpapers/${paper.id}/csv?`),
    );
    const downloaded = page.waitForEvent('download');
    await panel.getByRole('button', { name: '导出已复核 CSV', exact: true }).click();
    const [csvResponse, download] = await Promise.all([csv, downloaded]);
    expect(csvResponse.status()).toBe(200);
    expect(new URL(csvResponse.url()).searchParams.get('source_digest')).toBe(paper.source_digest);
    expect(new URL(csvResponse.url()).searchParams.get('expected_version')).toBe(
      String(paper.version),
    );
    const filename = testInfo.outputPath(`n04-tax-${viewport.width}-reviewed.csv`);
    await download.saveAs(filename);
    const bytes = readFileSync(filename);
    expect(bytes.length).toBeGreaterThan(100);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(
      (await csvResponse.allHeaders())['x-content-sha256'],
    );
    expect(bytes.toString('utf8')).toContain('申报状态,not_filed');
    expect(bytes.toString('utf8')).toContain('独立复核人,"local-reviewer"');
    expect(download.suggestedFilename()).toBe(`税务底稿-2026-10-v${paper.version}.csv`);
    await expect(panel).toContainText('系统未提交申报');
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      )
      .toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`n04-tax-${viewport.width}-reviewed.png`),
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}

test('N04 税务按钮复用既有工作台尺寸和字号', async ({ page, request }, testInfo) => {
  const f = await taxFixture(request);
  await page.setViewportSize({ width: 1280, height: 900 });
  const panel = await openTax(page, f);
  const buttons = await panel
    .getByRole('button', { name: '重新读取税务数据', exact: true })
    .evaluate((node) => ({
      height: node.getBoundingClientRect().height,
      fontSize: Number.parseFloat(getComputedStyle(node).fontSize),
    }));
  await testInfo.attach('tax-button-metrics', {
    body: Buffer.from(JSON.stringify(buttons)),
    contentType: 'application/json',
  });
  expect(buttons.height).toBeGreaterThanOrEqual(36);
  expect(buttons.fontSize).toBeGreaterThanOrEqual(14);
});

test('N04 来源变化后导出409不自动重试或复核', async ({ page, request }) => {
  const f = await taxFullFixture(request),
    paper = await taxPaper(request, f);
  await reviewTaxPaper(request, f, paper);
  const panel = await openTax(page, f);
  const exportButton = panel.getByRole('button', { name: '导出已复核 CSV', exact: true });
  await expect(exportButton).toBeEnabled();
  await changeFormalSource(request, f);
  const calls: string[] = [],
    downloads: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/api/tax/') && (r.method() !== 'GET' || r.url().includes('/csv?')))
      calls.push(r.url());
  });
  page.on('download', (d) => downloads.push(d.suggestedFilename()));
  const failed = page.waitForResponse((r) =>
    r.url().includes(`/api/tax/workpapers/${paper.id}/csv?`),
  );
  await exportButton.click();
  expect((await failed).status()).toBe(409);
  await expect(panel.getByRole('alert')).toContainText('来源');
  await expect(exportButton).toBeDisabled();
  await panel.getByRole('button', { name: '重新读取税务数据', exact: true }).click();
  await expect(panel.getByRole('combobox', { name: '底稿版本', exact: true })).toBeVisible();
  await expect(exportButton).toBeDisabled();
  expect(calls).toHaveLength(1);
  expect(downloads).toEqual([]);
  const next = await taxPaper(request, f, true);
  await reviewTaxPaper(request, f, next);
  await panel.getByRole('button', { name: '重新读取税务数据', exact: true }).click();
  await expect(panel.getByRole('combobox', { name: '底稿版本', exact: true })).toHaveValue(next.id);
  await expect(exportButton).toBeEnabled();
  // A rejected old snapshot does not poison a separately regenerated, reviewed version.
  expect(calls).toHaveLength(1);
});

test('N04 导出503不自动重发重新读取后才可手工重试', async ({ page, request }) => {
  const f = await taxFullFixture(request),
    paper = await taxPaper(request, f);
  await reviewTaxPaper(request, f, paper);
  let calls = 0;
  await page.route(`**/api/tax/workpapers/${paper.id}/csv?**`, async (route) => {
    calls++;
    if (calls === 1)
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'local fault injection',
          request_id: 'n04-tax-offline-503',
        }),
      });
    else await route.continue();
  });
  const panel = await openTax(page, f);
  const button = panel.getByRole('button', { name: '导出已复核 CSV', exact: true });
  await button.click();
  await expect(panel.getByRole('alert')).toContainText('重新读取');
  await expect(button).toBeDisabled();
  expect(calls).toBe(1);
  await panel.getByRole('button', { name: '重新读取税务数据', exact: true }).click();
  await expect(button).toBeEnabled();
  expect(calls).toBe(1);
  const downloaded = page.waitForEvent('download');
  await button.click();
  await downloaded;
  expect(calls).toBe(2);
});

test('N04 不支持的一般纳税人季度口径明确转人工', async ({ page, request }) => {
  const f = await taxFullFixture(request);
  await checked(
    await request.put('/api/tax/profile', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        taxpayer_type: 'general',
        filing_cycle: 'quarterly',
        vat_rate_bp: 1300,
        surtax_rate_bp: 1200,
        effective_from: '2026-10',
        reason: '虚构不支持口径不得按小规模简算',
      },
    }),
    200,
  );
  const calls: string[] = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/api/tax/workpapers') && r.method() === 'POST') calls.push(r.url());
  });
  const panel = await openTax(page, f);
  await expect(panel).toContainText('一般纳税人或季度场景请交人工处理');
  await expect(panel.getByRole('button', { name: '生成税务底稿', exact: true })).toBeDisabled();
  expect(calls).toEqual([]);
});

test('N04 任一读取范围错误不局部展示另一响应的底稿金额', async ({ page, request }) => {
  const f = await taxFullFixture(request),
    paper = await taxPaper(request, f);
  await page.route('**/api/tax/context?**', async (route) => {
    const response = await route.fetch(),
      body = await response.json();
    body.scope.company_id = 'another-company';
    await route.fulfill({ response, json: body });
  });
  const panel = await openTax(page, f);
  await expect(panel.getByRole('alert')).toContainText('校验未通过');
  await expect(panel.getByRole('combobox', { name: '底稿版本', exact: true })).toHaveCount(0);
  await expect(panel.locator('.tax-totals')).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '导出已复核 CSV', exact: true })).toHaveCount(0);
  await expect(panel).not.toContainText(paper.source_digest);
});

test('N04 撤销真实账套授权后重新读取清除金额和下载操作', async ({ page, request }) => {
  const f = await taxFullFixture(request),
    paper = await taxPaper(request, f);
  await reviewTaxPaper(request, f, paper);
  await pinPersona(page, 'reviewer');
  const panel = await openTax(page, f);
  await expectAmount(panel, '测算增值税', '30.00');
  await checked(
    await request.delete(`/api/books/${f.book.id}/members/local-reviewer`, {
      headers: f.headers,
    }),
    200,
  );
  const denied = page.waitForResponse((r) => r.url().includes('/api/tax/context?'));
  await panel.getByRole('button', { name: '重新读取税务数据', exact: true }).click();
  expect((await denied).status()).toBe(404);
  await expect(panel.getByRole('alert')).toContainText('授权已变化');
  await expect(panel.locator('.tax-totals')).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '导出已复核 CSV', exact: true })).toHaveCount(0);
  await expect(page).not.toHaveURL(/\/login(?:\?|$)/);
});

test('N04 慢A响应不覆盖切换后B企业范围', async ({ page, request }) => {
  const a = await taxFullFixture(request),
    b = await taxFixture(request);
  const paper = await taxPaper(request, a);
  let release!: () => void, observed!: () => void, finished!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    observed = resolve;
  });
  const delivered = new Promise<void>((resolve) => {
    finished = resolve;
  });
  await page.route('**/api/tax/context?**', async (route) => {
    const response = await route.fetch();
    const isA = new URL(route.request().url()).searchParams.get('book_id') === a.book.id;
    if (isA) {
      observed();
      await held;
    }
    await route.fulfill({ response }).catch(() => {
      /* Aborted A is the expected scope transition. */
    });
    if (isA) finished();
  });
  await page.goto(
    deliveryUrl(a).replace(encodeURIComponent(a.tag), encodeURIComponent('N04税务虚构企业')),
  );
  await page.getByRole('button', { name: '税务底稿', exact: true }).click();
  try {
    await started;
    await page.getByText('切换账套', { exact: true }).click();
    await page.getByRole('listitem').filter({ hasText: b.tag }).click();
    await expect(page).toHaveURL(new RegExp(`book=${b.book.id}`));
    const panel = page.getByRole('region', { name: '税务底稿', exact: true });
    // Changing books may deliberately return to the primary delivery tab.
    if (!(await panel.isVisible()))
      await page.getByRole('button', { name: '税务底稿', exact: true }).click();
    await expect(panel).toContainText(b.book.name);
    release();
    await delivered;
    await expect(panel).toContainText('本月还没有底稿');
    await expect(panel).not.toContainText(a.book.name);
    await expect(panel).not.toContainText(paper.source_digest);
    await expect(panel.locator('.tax-totals')).toHaveCount(0);
  } finally {
    release();
  }
});

test('N04 可读不等于可复核且他企底稿不会读取', async ({ page, request }) => {
  const f = await taxFullFixture(request),
    foreign = await taxFixture(request),
    paper = await taxPaper(request, f);
  await checked(
    await request.put(`/api/books/${f.book.id}/members/local-business-finance`, {
      headers: f.headers,
      data: { role: 'viewer' },
    }),
    200,
  );
  await checked(
    await request.delete(`/api/books/${foreign.book.id}/members/local-business-finance`, {
      headers: foreign.headers,
    }),
    200,
  );
  await pinPersona(page, 'finance');
  const panel = await openTax(page, f);
  await expectAmount(panel, '账表差异', '5.00');
  await expect(panel.getByRole('button', { name: '独立复核底稿', exact: true })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '生成税务底稿', exact: true })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '导出已复核 CSV', exact: true })).toBeDisabled();
  expect(
    (
      await request.get(`/api/tax/workpapers/${paper.id}?book_id=${foreign.book.id}`, {
        headers: f.finance,
      })
    ).status(),
  ).toBe(404);
  expect(
    (
      await request.get(`/api/tax/context?book_id=${foreign.book.id}&period=2026-10`, {
        headers: f.finance,
      })
    ).status(),
  ).toBe(404);
});
