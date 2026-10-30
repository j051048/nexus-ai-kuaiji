import { test, expect, type Browser, type Page, type Locator } from '@playwright/test';
import { readFileSync } from 'node:fs';

type Account = { email: string; password: string };
type Scenario = {
  book_id: string;
  company_id: string;
  period: string;
  report_id: string;
  coverage_id: string;
  accounts: { maker: Account; reviewer: Account; client: Account; foreign: Account };
};
type Fixture = {
  auth_origin: string;
  auth_local_origin: string;
  cases: { desktop: Scenario; mobile: Scenario };
};
const fixture: Fixture = JSON.parse(
  readFileSync(
    process.env.P02_BROWSER_FIXTURE || '.codex/p02-r1-browser/fixture.json',
    'utf8',
  ).replace(/^\uFEFF/, ''),
);
async function signIn(page: Page, account: Account) {
  await page.getByLabel('工作邮箱', { exact: true }).fill(account.email);
  await page.getByLabel('密码', { exact: true }).fill(account.password);
  await page.getByRole('button', { name: '登录工作空间', exact: true }).click();
  await expect(page.getByLabel('工作邮箱', { exact: true })).toHaveCount(0);
}
if (
  !/^https:\/\/c01-real-auth-2551[12]\.example\.test$/.test(fixture.auth_origin) ||
  !/^http:\/\/127\.0\.0\.1:2551[12]$/.test(fixture.auth_local_origin)
)
  throw new Error('P02 requires the isolated local identity fixture');

async function login(
  browser: Browser,
  account: Account,
  viewport: { width: number; height: number },
  path: string,
) {
  const context = await browser.newContext({ viewport, baseURL: 'http://127.0.0.1:18180' });
  // Only remap the synthetic auth hostname to the real local GoTrue process.
  // No fabricated JWT, fixture API response, business route interception or CDN.
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === fixture.auth_origin) {
      url.protocol = 'http:';
      url.hostname = '127.0.0.1';
      url.port = new URL(fixture.auth_local_origin).port;
      url.pathname = url.pathname.replace(/^\/auth\/v1/, '') || '/';
      const response = await route.fetch({ url: url.toString() });
      await route.fulfill({ response });
      return;
    }
    if (
      url.origin === 'http://127.0.0.1:18180' ||
      url.protocol === 'blob:' ||
      url.protocol === 'data:'
    )
      return route.continue();
    throw new Error(`Unexpected nonlocal browser request to ${url.origin}`);
  });
  const page = await context.newPage();
  let authenticatedHeaders: Record<string, string> = {};
  page.on('request', (request) => {
    const url = new URL(request.url());
    const headers = request.headers();
    if (
      url.origin === 'http://127.0.0.1:18180' &&
      url.pathname.startsWith('/api/') &&
      headers.authorization
    )
      authenticatedHeaders = {
        Authorization: headers.authorization,
        ...(headers['x-firm-id'] ? { 'X-Firm-Id': headers['x-firm-id'] } : {}),
      };
  });
  page.setDefaultTimeout(20_000);
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/') || url.origin === fixture.auth_origin)
      console.info(`P02 ${url.pathname} ${response.status()}`);
  });
  await page.goto(path);
  await signIn(page, account);
  return { context, page, headers: () => authenticatedHeaders };
}
async function panelFor(page: Page) {
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const panel = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await expect(panel.getByText('正在读取月结证据…', { exact: true })).toHaveCount(0);
  return panel;
}
async function post(page: Page, button: Locator, endpoint: string) {
  const response = page.waitForResponse(
    (result) => result.url().endsWith(endpoint) && result.request().method() === 'POST',
  );
  await button.click();
  const result = await response;
  expect(result.ok(), `POST ${endpoint} status=${result.status()}`).toBe(true);
  return result.json();
}
for (const name of ['desktop', 'mobile'] as const) {
  test(`P02 real PG signed identity month-close ${name}`, async ({ browser }, testInfo) => {
    const s = fixture.cases[name];
    const viewport =
      name === 'desktop' ? { width: 1440, height: 1000 } : { width: 390, height: 844 };
    const query = new URLSearchParams({ book: s.book_id, company: s.company_id, period: s.period });
    const path = `/workspace/formal-books?${query}`;
    const reviewer = await login(browser, s.accounts.reviewer, viewport, path);
    await expect.poll(() => Boolean(reviewer.headers().Authorization)).toBe(true);
    const balanceResponse = await reviewer.page.request.get(
      `/api/books/${s.book_id}/trial-balance`,
      { headers: reviewer.headers() },
    );
    expect(balanceResponse.status()).toBe(200);
    const balance = (await balanceResponse.json()) as {
      scope: { book_id: string; period: string };
      rows: { code: string; closing_cents: number }[];
    };
    expect(balance.scope.book_id).toBe(s.book_id);
    expect(balance.scope.period).toBe(s.period);
    expect(balance.rows.find((row) => row.code === '1002')?.closing_cents).toBe(973190);
    expect(balance.rows.find((row) => row.code === '6602.02')?.closing_cents).toBe(26810);
    const balances = reviewer.page.getByRole('table', {
      name: '本期正式科目余额 · 单位：人民币元',
      exact: true,
    });
    await expect(
      balances
        .getByRole('row', { name: /1002 · 银行存款/ })
        .getByRole('cell')
        .nth(3),
    ).toHaveText('9,731.90');
    await expect(
      balances
        .getByRole('row', { name: /6602.02 · 管理费用/ })
        .getByRole('cell')
        .nth(3),
    ).toHaveText('268.10');
    const panel = await panelFor(reviewer.page);
    await expect(panel.getByRole('heading', { name: /银行对账 · 需处理/ })).toBeVisible();
    await expect(panel.getByRole('heading', { name: /子账控制 · 已对平/ })).toBeVisible();
    await panel
      .getByRole('combobox', { name: '本次报表版本', exact: true })
      .selectOption(s.report_id);
    await expect(panel.getByRole('region', { name: '资产负债表', exact: true })).toContainText(
      '9,731.90',
    );
    await expect(panel.getByRole('region', { name: '资产负债表', exact: true })).toContainText(
      '10,000.00',
    );
    await expect(panel.getByRole('region', { name: '利润表', exact: true })).toContainText(
      '268.10',
    );
    await panel
      .getByLabel('办理说明（至少 3 字）')
      .fill('虚构银行原件、往来余额与正式账簿逐项核对');
    await post(
      reviewer.page,
      panel.getByRole('button', { name: '独立复核报表', exact: true }),
      `/api/reports/${s.report_id}/review`,
    );
    const blocked = await post(
      reviewer.page,
      panel.getByRole('button', { name: '生成月结预检', exact: true }),
      '/api/b04-close/preflight',
    );
    expect(blocked.close.status).toBe('preflight_failed');
    await expect(panel.getByText(/预检已保存，但仍有阻断/)).toBeVisible();
    await expect(panel.getByText(/处理岗位：/).first()).toBeVisible();
    await expect(panel.getByRole('button', { name: '确认正式锁期', exact: true })).toHaveCount(0);
    await reviewer.page.screenshot({
      path: testInfo.outputPath(`p02-blocked-${name}.png`),
      fullPage: true,
    });
    await panel.getByText('原件证据定位', { exact: true }).click();
    const downloading = reviewer.page.waitForEvent('download');
    await panel.getByRole('button', { name: '下载银行原件核对', exact: true }).click();
    const original = await downloading;
    await original.saveAs(testInfo.outputPath(`bank-original-${name}.csv`));
    await expect(
      panel.getByText('已下载并校验银行原件，请与覆盖金额核对。', { exact: true }),
    ).toBeVisible();
    await post(
      reviewer.page,
      panel.getByRole('button', { name: '核验银行覆盖', exact: true }),
      `/api/subledger/bank-coverages/${s.coverage_id}/review`,
    );
    await expect(panel.getByRole('heading', { name: /银行对账 · 已对平/ })).toBeVisible();
    const ready = await post(
      reviewer.page,
      panel.getByRole('button', { name: '生成月结预检', exact: true }),
      '/api/b04-close/preflight',
    );
    expect(ready.close.status).toBe('preflight_passed');
    await expect(
      panel.getByRole('button', { name: '独立复核月结预检', exact: true }),
    ).toBeDisabled();
    const maker = await login(browser, s.accounts.maker, viewport, path);
    const makerPanel = await panelFor(maker.page);
    await makerPanel
      .getByRole('combobox', { name: '本次报表版本', exact: true })
      .selectOption(s.report_id);
    await makerPanel
      .getByLabel('办理说明（至少 3 字）')
      .fill('独立核对预检版本、银行原件与全部月结检查');
    await post(
      maker.page,
      makerPanel.getByRole('button', { name: '独立复核月结预检', exact: true }),
      `/api/b04-close/${ready.close.id}/review`,
    );
    maker.page.once('dialog', (dialog) => dialog.accept());
    const locked = await post(
      maker.page,
      makerPanel.getByRole('button', { name: '确认正式锁期', exact: true }),
      `/api/b04-close/${ready.close.id}/lock`,
    );
    expect(locked.close.status).toBe('locked');
    const published = await post(
      maker.page,
      makerPanel.getByRole('button', { name: '发布正式报表', exact: true }),
      `/api/b04-close/${ready.close.id}/publish`,
    );
    expect(published.close.status).toBe('published');
    await expect(makerPanel.getByText(/正式报表已发布；请在月度服务单/)).toBeVisible();
    await expect(
      makerPanel.getByText(`月结 V${published.close.version} · 已发布`, { exact: true }),
    ).toBeVisible();
    await expect(makerPanel.getByText('正在读取月结证据…', { exact: true })).toHaveCount(0);
    expect(
      await maker.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    ).toBe(true);
    await maker.page.screenshot({
      path: testInfo.outputPath(`p02-published-${name}.png`),
      fullPage: true,
    });
    await reviewer.context.close();
    await maker.context.close();
  });

  test(`P02 real PG signed identity delivery and acknowledgement ${name}`, async ({
    browser,
  }, testInfo) => {
    const s = fixture.cases[name];
    const viewport =
      name === 'desktop' ? { width: 1440, height: 1000 } : { width: 390, height: 844 };
    const query = new URLSearchParams({ book: s.book_id, company: s.company_id, period: s.period });
    const maker = await login(
      browser,
      s.accounts.maker,
      viewport,
      `/workspace/formal-books?${query}`,
    );

    await maker.page
      .getByRole('navigation', { name: '做账五步流水线', exact: true })
      .getByRole('button', { name: '⑤ 交付与导出', exact: true })
      .click();
    await expect(maker.page).toHaveURL(/\/workspace\/delivery\?/);
    const delivery = maker.page.getByRole('region', { name: '月度交付包', exact: true });
    await delivery
      .getByRole('combobox', { name: '交付报表版本', exact: true })
      .selectOption(s.report_id);
    await delivery
      .getByLabel('交付或签收说明', { exact: true })
      .fill('虚构非零银行与往来月结包，已复核并正式发布');
    await delivery.getByRole('checkbox', { name: '我已核对本企业、本月份及本次版本' }).check();
    const response = await post(
      maker.page,
      delivery.getByRole('button', { name: '生成本月交付包', exact: true }),
      `/api/books/${s.book_id}/delivery/monthly-package`,
    );
    const pack = response.package ?? response;
    expect(pack.book_id).toBe(s.book_id);
    expect(pack.company_id).toBe(s.company_id);
    expect(pack.manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(
      (
        await maker.page.request.post(
          `/api/books/${s.book_id}/delivery/monthly-package/${pack.id}/acknowledge`,
          {
            headers: maker.headers(),
            data: { manifest_sha256: pack.manifest_sha256, reason: '验证会计不能替客户签收' },
          },
        )
      ).status(),
    ).toBe(403);
    await maker.page.screenshot({
      path: testInfo.outputPath(`p02-package-${name}.png`),
      fullPage: true,
    });

    const clientPath = `/workspace/briefing?${query}&package=${pack.id}`;
    const client = await login(browser, s.accounts.client, viewport, clientPath);
    const signing = client.page.getByRole('region', { name: '月度交付包', exact: true });
    await expect(
      signing.getByText(`本月资料包（第 ${pack.version} 版）`, { exact: false }),
    ).toBeVisible();
    await expect(signing.getByRole('button', { name: '生成本月交付包', exact: true })).toHaveCount(
      0,
    );
    const packageDownload = client.page.waitForEvent('download');
    await signing
      .getByRole('button', { name: `下载第 ${pack.version} 版资料包`, exact: true })
      .click();
    const archive = await packageDownload;
    const archivePath = testInfo.outputPath(`monthly-package-${name}.zip`);
    await archive.saveAs(archivePath);
    const archiveBytes = readFileSync(archivePath);
    expect(archiveBytes.length).toBeGreaterThan(100);
    expect(archiveBytes.subarray(0, 2).toString('ascii')).toBe('PK');
    await signing
      .getByLabel('签收说明', { exact: true })
      .fill('已收到本月份本版本全部资料，签收不表示税务已申报');
    await signing
      .getByRole('checkbox', { name: '我已核对月份与版本，并确认收到本版全部文件' })
      .check();
    const accepted = await post(
      client.page,
      signing.getByRole('button', { name: `确认收到第 ${pack.version} 版`, exact: true }),
      `/api/books/${s.book_id}/delivery/monthly-package/${pack.id}/acknowledge`,
    );
    expect(accepted.manifest_sha256 ?? accepted.package?.manifest_sha256).toBe(
      pack.manifest_sha256,
    );
    await expect(signing.getByText('客户已签收', { exact: true })).toBeVisible();
    await client.page.reload();
    await signIn(client.page, s.accounts.client);
    await expect(client.page).toHaveURL(new RegExp(`package=${pack.id}`));
    await expect(signing.getByText('客户已签收', { exact: true })).toBeVisible();
    expect(
      await client.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    ).toBe(true);
    await client.page.screenshot({
      path: testInfo.outputPath(`p02-client-${name}.png`),
      fullPage: true,
    });
    const foreign = await login(browser, s.accounts.foreign, viewport, clientPath);
    await expect.poll(() => Boolean(foreign.headers().Authorization)).toBe(true);
    const denied = await foreign.page.request.get(
      `/api/books/${s.book_id}/delivery/monthly-package/${pack.id}`,
      { headers: foreign.headers() },
    );
    expect(denied.status()).toBe(404);
    const wrongManifest = await client.page.request.post(
      `/api/books/${s.book_id}/delivery/monthly-package/${pack.id}/acknowledge`,
      {
        headers: client.headers(),
        data: { manifest_sha256: 'f'.repeat(64), reason: '错误版本不得改变已有签收' },
      },
    );
    expect(wrongManifest.status()).toBe(409);
    await expect(foreign.page.getByText(pack.manifest_sha256, { exact: true })).toHaveCount(0);
    await expect(
      foreign.page.getByRole('button', { name: `确认收到第 ${pack.version} 版`, exact: true }),
    ).toHaveCount(0);
    await expect(
      foreign.page.getByRole('button', { name: `下载第 ${pack.version} 版资料包`, exact: true }),
    ).toHaveCount(0);
    for (const instance of [maker, client, foreign]) await instance.context.close();
  });
}
