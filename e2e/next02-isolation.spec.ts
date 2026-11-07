import { test, expect, type Browser, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { validateIdentityOrigin } from '../scripts/p02_identity_origin';

/**
 * NEXT02 isolation evidence on the same real PG/GoTrue fixture as P02.
 *
 * These are deliberately negative browser flows: a signed reviewer must not
 * borrow another company's book, and the client identity must never gain the
 * firm-only review, close or batch-export capabilities. No business response is
 * fabricated with route.fulfill; only the synthetic auth hostname is remapped
 * to the isolated local GoTrue process.
 */

type Account = { email: string; password: string };
type Scenario = {
  book_id: string;
  company_id: string;
  period: string;
  report_id: string;
  report_version: number;
  initial_close_id: string;
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

validateIdentityOrigin(fixture, process.env.P02_EXPECTED_AUTH_LOCAL_ORIGIN);

async function signIn(page: Page, account: Account) {
  await page.getByLabel('工作邮箱', { exact: true }).fill(account.email);
  await page.getByLabel('密码', { exact: true }).fill(account.password);
  await page.getByRole('button', { name: '登录工作空间', exact: true }).click();
  await expect(page.getByLabel('工作邮箱', { exact: true })).toHaveCount(0);
}

async function login(
  browser: Browser,
  account: Account,
  viewport: { width: number; height: number },
  path: string,
) {
  const context = await browser.newContext({ viewport, baseURL: 'http://127.0.0.1:18180' });
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
  await page.goto(path);
  await signIn(page, account);
  return { context, page, headers: () => authenticatedHeaders };
}

test('P02 foreign company book and reports stay invisible in the browser', async ({
  browser,
}) => {
  const own = fixture.cases.desktop;
  const foreign = fixture.cases.mobile;
  const query = new URLSearchParams({
    book: foreign.book_id,
    company: foreign.company_id,
    period: foreign.period,
  });
  const session = await login(
    browser,
    own.accounts.reviewer,
    { width: 1440, height: 1000 },
    `/workspace/formal-books?${query}`,
  );
  await expect.poll(() => Boolean(session.headers().Authorization)).toBe(true);

  const trial = await session.page.request.get(
    `/api/books/${foreign.book_id}/trial-balance`,
    { headers: session.headers() },
  );
  expect(trial.status(), 'foreign trial balance must be non-disclosing').toBe(404);

  const reports = await session.page.request.get(`/api/reports?book_id=${foreign.book_id}`, {
    headers: session.headers(),
  });
  expect(reports.status(), 'foreign report list must be non-disclosing').toBe(404);

  const pack = await session.page.request.get(
    `/api/books/${foreign.book_id}/delivery/monthly-package`,
    { headers: session.headers() },
  );
  expect(pack.status(), 'foreign monthly package must be non-disclosing').toBe(404);

  const alert = session.page.getByRole('alert').filter({ hasText: /无权|不存在|失败|无法/ });
  await expect(alert.first()).toBeVisible();
  await expect(
    session.page.getByRole('table', { name: '本期正式科目余额 · 单位：人民币元', exact: true }),
  ).toHaveCount(0);
  await expect(session.page.getByText('9,731.90', { exact: true })).toHaveCount(0);
  await session.page.screenshot({
    path: test.info().outputPath('next02-foreign-book-denied.png'),
    fullPage: true,
  });
  await session.context.close();
});

test('P02 client identity cannot review, lock or batch export', async ({ browser }) => {
  const s = fixture.cases.desktop;
  const query = new URLSearchParams({ book: s.book_id, company: s.company_id, period: s.period });
  const session = await login(
    browser,
    s.accounts.client,
    { width: 1440, height: 1000 },
    `/workspace/formal-books?${query}`,
  );
  await expect.poll(() => Boolean(session.headers().Authorization)).toBe(true);

  const review = await session.page.request.post(`/api/reports/${s.report_id}/review`, {
    headers: session.headers(),
    data: { expected_version: s.report_version, reason: '客户身份不得执行独立复核' },
  });
  expect([403, 404], `client report review returned ${review.status()}`).toContain(
    review.status(),
  );

  const lock = await session.page.request.post(`/api/b04-close/${s.initial_close_id}/lock`, {
    headers: session.headers(),
    data: { expected_version: 1, reason: '客户身份不得锁定会计期间' },
  });
  expect([403, 404], `client close lock returned ${lock.status()}`).toContain(lock.status());

  const batch = await session.page.request.post(
    '/api/firm/batch-operations/review-export/preview',
    {
      headers: session.headers(),
      data: { company_ids: [s.company_id], period: s.period },
    },
  );
  expect([403, 404], `client batch export returned ${batch.status()}`).toContain(batch.status());

  await expect(
    session.page.getByRole('button', { name: '独立复核报表', exact: true }),
  ).toHaveCount(0);
  await expect(session.page.getByText('客户已签收', { exact: true })).toHaveCount(0);
  await session.page.screenshot({
    path: test.info().outputPath('next02-client-readonly.png'),
    fullPage: true,
  });
  await session.context.close();
});
