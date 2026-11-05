import { expect, test, type Locator, type Page } from '@playwright/test';
import { choosePersona, openNavigation } from './ui-navigation';

test.setTimeout(60_000);

const output = `tmp_test/v01/${process.env.V01_SCREENSHOT_PHASE ?? 'current'}`;
const longName = '虚构华东先进制造与综合技术服务有限公司第一分公司';

async function fontAtLeast(locator: Locator, pixels: number) {
  await expect(locator).toBeVisible();
  expect
    .soft(await locator.evaluate((node) => Number.parseFloat(getComputedStyle(node).fontSize)))
    .toBeGreaterThanOrEqual(pixels);
}

async function noPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

async function globalTokens(page: Page) {
  const result = await page.evaluate(() => {
    const host = document.createElement('section');
    // Intentionally outside review-workbench/queue: these public tokens may not
    // depend on an unrelated component having rendered or imported its CSS.
    host.setAttribute('data-v01-probe', '');
    host.style.cssText =
      'position:fixed;left:-1000px;top:0;background:var(--surface-soft);color:var(--text-muted);border:1px solid var(--border)';
    document.body.append(host);
    const root = getComputedStyle(document.documentElement);
    const sample = getComputedStyle(host);
    const names = [
      '--font-sans',
      '--font-numeric',
      '--font-size-xs',
      '--font-size-sm',
      '--font-size-base',
      '--font-size-input',
      '--font-size-input-mobile',
      '--font-size-title',
      '--font-size-section',
      '--text-primary',
      '--text-secondary',
      '--text-muted',
      '--surface',
      '--surface-soft',
      '--border',
    ];
    const values = Object.fromEntries(
      names.map((name) => [name, root.getPropertyValue(name).trim()]),
    );
    const output = {
      values,
      background: sample.backgroundColor,
      borderStyle: sample.borderTopStyle,
      borderColor: sample.borderTopColor,
    };
    host.remove();
    return output;
  });
  for (const [name, value] of Object.entries(result.values)) expect.soft(value, name).not.toBe('');
  expect.soft(result.background).not.toBe('rgba(0, 0, 0, 0)');
  expect.soft(result.borderStyle).toBe('solid');
  expect.soft(result.borderColor).not.toBe('rgba(0, 0, 0, 0)');
  return result.values;
}

async function titleUsesSans(page: Page) {
  const title = page.getByRole('heading', { level: 1 }).first();
  await expect(title).toBeVisible();
  const family = await title.evaluate((node) => getComputedStyle(node).fontFamily);
  expect.soft(family).toContain('sans-serif');
  expect.soft(family).not.toMatch(/Songti|SimSun|Noto Serif|Georgia|Times New Roman/i);
}

async function keyboardFocus(page: Page, target: Locator) {
  // Keyboard focus is observable through focus-visible, not only element.focus().
  await target.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  expect(
    await target.evaluate((node) => {
      const style = getComputedStyle(node);
      return (
        node.matches(':focus-visible') &&
        ((style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2) ||
          style.boxShadow !== 'none')
      );
    }),
  ).toBe(true);
}

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 375, height: 812 },
]) {
  const device = viewport.width < 640 ? 'mobile' : 'desktop';

  test(`${device} 员工录入：字体与控件可读，长金额不溢出，禁用边界保持`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'employee');
    const primary = page.getByRole('region', { name: '提交与进度', exact: true });
    await expect(primary).toBeVisible();
    // Counts can intentionally be unknown. A rendered page footer confirms
    // the authorized task page loaded without inventing a zero count.
    await expect(page.locator('.workspace-inbox .pagination-controls')).toBeVisible();
    await page.screenshot({ path: `${output}/employee-home-${device}.png`, fullPage: true });
    await primary.getByRole('button', { name: /提交报销/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('事项金额', { exact: true })).toBeVisible();
    await dialog.getByLabel('事项金额', { exact: true }).fill('123456789.01');
    await dialog.getByLabel('交易对方', { exact: true }).fill(longName);
    await dialog
      .getByLabel('事项摘要', { exact: true })
      .fill('虚构长名称和金额可读性验证，尚未提交');
    const submit = dialog.getByRole('button', { name: '提交审批', exact: true });
    await expect(submit).toBeDisabled();
    await dialog.locator('.intake-scroll').evaluate((node) => {
      node.scrollTop = 0;
    });
    await page.screenshot({ path: `${output}/employee-intake-${device}.png`, fullPage: false });
    await noPageOverflow(page);
    expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await fontAtLeast(
      dialog.getByLabel('事项金额', { exact: true }),
      device === 'mobile' ? 16 : 14,
    );
    await fontAtLeast(
      dialog.getByLabel('交易对方', { exact: true }),
      device === 'mobile' ? 16 : 14,
    );
    await fontAtLeast(submit, 13);
    await fontAtLeast(dialog.locator('.workflow-guide-message p').first(), 12);
    await keyboardFocus(page, dialog.getByLabel('事项金额', { exact: true }));
    await dialog.getByLabel('事项金额', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/employee-fields-${device}.png`, fullPage: false });
    await globalTokens(page);
    await expect(submit).toBeDisabled();
    // This is a presentation-only fixture: no OCR, submission or approval call.
  });

  test(`${device} 企业财务及事务所：导航、标题和全局变量不依赖懒加载顺序`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'finance');
    const primary = page.getByRole('region', { name: '资料核验与移交', exact: true });
    await expect(primary).toBeVisible();
    await expect(page.locator('.workspace-inbox .pagination-controls')).toBeVisible();
    await page.screenshot({ path: `${output}/finance-home-${device}.png`, fullPage: true });
    await noPageOverflow(page);
    await fontAtLeast(primary.getByRole('button', { name: '查看待核验事项', exact: true }), 13);
    await fontAtLeast(page.locator('.workspace-task-search input'), device === 'mobile' ? 16 : 14);
    await fontAtLeast(
      page
        .getByRole('navigation', { name: '客户服务' })
        .getByRole('button', { name: '待核验', exact: true }),
      13,
    );
    await titleUsesSans(page);
    const tokensBeforeLazyPage = await globalTokens(page);

    await choosePersona(page, 'accountant');
    await page.route(/\/api\/firm\/cockpit(\?|$)/, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      // Change only presentation text; keep every server identity and permission.
      for (const rows of [data.companies, data.coordination]) {
        if (rows?.length) rows[0].company_name = longName;
      }
      await route.fulfill({ response, json: data });
    });
    await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: '全所运营概览', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(longName, { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: `${output}/firm-home-${device}.png`, fullPage: true });
    await titleUsesSans(page);
    await fontAtLeast(page.getByRole('button', { name: '客户进度', exact: true }), 13);
    await noPageOverflow(page);
    if (device === 'mobile') {
      const breadcrumbLines = await page.locator('.breadcrumbs').evaluate((node) => {
        const text = [...node.childNodes].find(
          (child) => child.nodeType === Node.TEXT_NODE && child.textContent?.trim(),
        );
        if (!text) throw new Error('Missing workspace breadcrumb');
        const range = document.createRange();
        range.selectNodeContents(text);
        return new Set([...range.getClientRects()].map((rect) => rect.top)).size;
      });
      expect(breadcrumbLines).toBe(1);
    }
    await openNavigation(page);
    await fontAtLeast(
      page
        .getByRole('navigation', { name: '主导航' })
        .getByRole('button', { name: '全所概览', exact: true }),
      13,
    );
    const work = page
      .getByRole('navigation', { name: '主导航' })
      .locator('details')
      .filter({ has: page.locator('summary').filter({ hasText: /^会计工作区$/ }) });
    if ((await work.getAttribute('open')) === null) await work.locator('summary').click();
    // Use real SPA navigation: a document reload would discard the preceding
    // stylesheet order and could not demonstrate lazy CSS coexistence.
    await work.getByRole('button', { name: '跨企业批量复核', exact: true }).click();
    await expect(page.locator('.review-workbench')).toBeVisible();
    expect(await globalTokens(page)).toEqual(tokensBeforeLazyPage);
    await page.getByRole('link', { name: '衡账，返回工作台', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
    expect(await globalTokens(page)).toEqual(tokensBeforeLazyPage);
    await noPageOverflow(page);
  });
}

test('会计同屏复核：桌面与手机完整展示长金额，原件未核验仍不可审核或导出', async ({
  page,
  request,
}) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const tag = crypto.randomUUID().slice(0, 8);
  const party = `虚构长金额供应商-${tag}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name: `虚构长金额展示企业-${tag}`, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  expect(
    (
      await request.post(`/api/books/${book.id}/ledger/entities`, {
        headers,
        data: { kind: 'counterparty', code: `V01-${tag}`, name: party },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '本地虚构长金额视觉验证',
          rows: [
            {
              external_id: `v01-${tag}`,
              date: '2026-10-12',
              description: `虚构大额办公采购-${tag}`,
              counterparty: party,
              amount: '123456789.01',
              direction: 'expense',
              evidence_ref: '合成文字引用，仅验证布局，不代表已核验原件',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(detail.drafts).toHaveLength(1);

  const criticalWrites: string[] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/(approve|exports)$/.test(new URL(req.url()).pathname))
      criticalWrites.push(req.url());
  });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(`/workspace/review?book=${book.id}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '查看证据', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText('当前来源只有文字引用', { exact: true })).toBeVisible();
  const table = drawer.getByRole('table', { name: '同屏凭证分录表', exact: true });
  await expect(table).toBeVisible();

  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 375, height: 812 },
  ]) {
    await page.setViewportSize(viewport);
    const device = viewport.width < 640 ? 'mobile' : 'desktop';
    await table.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/review-amounts-${device}.png`, fullPage: false });
    for (const name of ['同屏第 1 行借方', '同屏第 2 行贷方']) {
      const amount = table.getByLabel(name, { exact: true });
      await expect(amount).toHaveValue('123456789.01');
      await amount.scrollIntoViewIfNeeded();
      await fontAtLeast(amount, device === 'mobile' ? 16 : 14);
      const fit = await amount.evaluate((node) => {
        const input = node as HTMLInputElement;
        const style = getComputedStyle(input);
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d')!;
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const width = context.measureText(input.value).width;
        const available =
          input.clientWidth -
          Number.parseFloat(style.paddingLeft) -
          Number.parseFloat(style.paddingRight);
        const bounds = input.getBoundingClientRect();
        return {
          textFits: width <= available + 1,
          withinViewport: bounds.left >= -1 && bounds.right <= innerWidth + 1,
        };
      });
      expect(fit).toEqual({ textFits: true, withinViewport: true });
    }
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    if (device === 'mobile') {
      const remove = table.getByRole('button', { name: '同屏删除第 1 行', exact: true });
      const box = await remove.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(44);
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    }
    await noPageOverflow(page);
  }
  expect(criticalWrites).toEqual([]);
  const current = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(current.drafts[0].status).toBe('draft');
  expect(
    (await (await request.get(`/api/books/${book.id}/exports`, { headers })).json()).exports,
  ).toEqual([]);
});
