import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { choosePersona, selectIntakeCompany } from './ui-navigation';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

test.setTimeout(90_000);
const output = `tmp_test/v03/${process.env.V03_SCREENSHOT_PHASE ?? 'current'}`;
// Deterministic synthetic document, visibly marked as non-production test data.
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));
const longName = '虚构华东先进制造与综合技术服务有限公司第一分公司';

test('740px 窄屏同屏复核仍可读完整科目，不被固定金额列挤成空栏', async ({ page, request }) => {
  const fixture = await reviewFixture(request);
  await page.setViewportSize({ width: 740, height: 900 });
  const drawer = await openReview(page, fixture.book.id);
  const account = drawer.getByLabel('同屏第 1 行科目', { exact: true });
  await account.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${output}/review-tablet.png`, fullPage: false });
  expect((await account.boundingBox())!.width).toBeGreaterThanOrEqual(140);
  await noOverflow(page, drawer);
});

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}
async function fontAtLeast(locator: Locator, size: number) {
  await expect(locator).toBeVisible();
  expect
    .soft(await locator.evaluate((node) => parseFloat(getComputedStyle(node).fontSize)))
    .toBeGreaterThanOrEqual(size);
}
async function noOverflow(page: Page, dialog: Locator) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
}
async function openReview(page: Page, bookId: string) {
  await page.goto(`/workspace/review?book=${bookId}`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '查看证据', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toBeVisible();
  return drawer;
}
async function reviewFixture(request: APIRequestContext) {
  const employee = await identity(request, 'employee');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const owner = await identity(request, 'accountant');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find((row) => row.can_submit && row.department_id)!;
  expect(company).toBeTruthy();
  const tag = crypto.randomUUID().slice(0, 8);
  const party = `虚构原件供应商-${tag}`;
  const createdBook = await request.post('/api/books', {
    headers: owner,
    data: { company_id: company.id, name: `V03 虚构核对-${tag}`, period: '2026-10' },
  });
  expect(createdBook.status()).toBe(201);
  const book = await createdBook.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: owner,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/ledger/entities`, {
        headers: owner,
        data: { kind: 'counterparty', code: `V03-${tag}`, name: party },
      })
    ).status(),
  ).toBe(201);
  const createdEvent = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: `V03 虚构办公采购-${tag}`,
        counterparty: party,
        amount: '123456789.01',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '只在隔离数据库验证表单和复核展示',
      },
    },
  });
  expect(createdEvent.status()).toBe(201);
  let event: BusinessEvent = await createdEvent.json();
  for (const filename of [`V03 虚构原件-${tag}.png`]) {
    const upload = await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers: employee,
        multipart: { file: { name: filename, mimeType: 'image/png', buffer: image } },
      },
    );
    expect(upload.status()).toBe(200);
    event = await upload.json();
  }
  for (const [command, headers] of [
    ['submit', employee],
    ['department-approve', department],
    ['finance-approve', finance],
  ] as const) {
    const response = await request.post(`/api/business/events/${event.id}/${command}`, {
      headers,
      data: { expected_version: event.version },
    });
    expect(response.status()).toBe(200);
    event = await response.json();
  }
  const handed = await request.post(`/api/business/events/${event.id}/handoff`, {
    headers: finance,
    data: {
      expected_version: event.version,
      book_id: book.id,
      idempotency_key: `v03-handoff-${tag}`,
    },
  });
  expect(handed.status()).toBe(200);
  const mapping = await handed.json();
  expect(mapping.draft_id).toBeTruthy();
  event = await (await request.get(`/api/business/events/${event.id}`, { headers: owner })).json();
  return { employee, owner, book, event, draftId: mapping.draft_id as string };
}

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 375, height: 812 },
]) {
  const device = viewport.width < 700 ? 'mobile' : 'desktop';
  test(`${device} 员工录入：真实附件与草稿状态可读，尚未确认不提交`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'employee');
    await page
      .getByRole('region', { name: '提交与进度' })
      .getByRole('button', { name: /提交报销/ })
      .click();
    const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
    await selectIntakeCompany(dialog);
    await expect(dialog.locator('.intake-template-description').first()).not.toBeVisible();
    await dialog.locator('.intake-template-picker > summary').click();
    await expect(dialog.locator('.intake-template-description').first()).toBeVisible();
    await dialog.locator('.intake-template-picker > summary').click();
    await dialog.getByLabel('事项摘要', { exact: true }).fill('V03 虚构报销草稿，不提交');
    await dialog.getByLabel('事项金额', { exact: true }).fill('123456789.01');
    await dialog.getByLabel('交易对方', { exact: true }).fill(longName);
    await dialog.getByLabel('发生日期', { exact: true }).fill('2026-10-12');
    const fileName = `V03 虚构长文件名-采购票据与补充业务说明-${crypto.randomUUID().slice(0, 8)}.png`;
    await dialog
      .getByLabel('一体化录入上传附件', { exact: true })
      .setInputFiles({ name: fileName, mimeType: 'image/png', buffer: image });
    await expect(dialog.locator('.intake-file')).toContainText(fileName);
    await expect(dialog.locator('.intake-file')).toContainText('原件已保存');
    await expect(dialog.getByRole('status', { name: '草稿保存状态', exact: true })).toContainText(
      '草稿已保存',
    );
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    await dialog.locator('.intake-scroll').evaluate((node) => {
      node.scrollTop = 0;
    });
    await page.screenshot({ path: `${output}/employee-entry-${device}.png`, fullPage: false });
    await dialog.locator('.intake-file').scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/employee-attachment-${device}.png`, fullPage: false });
    await fontAtLeast(dialog.locator('.intake-file-header span'), 12);
    await fontAtLeast(
      dialog.getByLabel('事项金额', { exact: true }),
      device === 'mobile' ? 16 : 14,
    );
    await noOverflow(page, dialog);
    await dialog
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
    await expect(dialog.getByRole('region', { name: '员工录入引导' })).toContainText('尚未提交');
    await dialog.getByRole('button', { name: '关闭业务事项表单', exact: true }).click();
    await expect(dialog).toHaveCount(0);
  });

  test(`${device} 会计同屏：真实原件、长金额与未审状态，返回保留选择`, async ({
    page,
    request,
  }) => {
    const fixture = await reviewFixture(request);
    await page.setViewportSize(viewport);
    const drawer = await openReview(page, fixture.book.id);
    expect(
      await drawer
        .locator('.inline-review-source')
        .evaluate(
          (node) =>
            !!(
              node.compareDocumentPosition(document.querySelector('.review-term-help')!) &
              Node.DOCUMENT_POSITION_FOLLOWING
            ),
        ),
    ).toBe(true);
    await expect(drawer.locator('details.inline-review-rationale')).toHaveCount(1);
    await expect(drawer.locator('details.inline-review-rationale')).not.toHaveAttribute('open');
    const original = drawer.getByRole('img', { name: /原始票据 V03 虚构原件/ });
    await expect(original).toBeVisible();
    await expect
      .poll(() => original.evaluate((node) => (node as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0);
    await expect(drawer.getByText('待会计审核', { exact: true })).toBeVisible();
    expect(
      await drawer
        .locator('.inline-review-source > div:first-child dd')
        .evaluate(
          (node) => node.scrollHeight <= parseFloat(getComputedStyle(node).lineHeight) * 1.1,
        ),
    ).toBe(true);
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await page.screenshot({ path: `${output}/review-evidence-${device}.png`, fullPage: false });
    await fontAtLeast(drawer.locator('.inline-file-meta'), 12);
    await fontAtLeast(
      drawer.getByLabel('同屏凭证摘要', { exact: true }),
      device === 'mobile' ? 16 : 14,
    );
    const table = drawer.getByRole('table', { name: '同屏凭证分录表', exact: true });
    await table.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/review-lines-${device}.png`, fullPage: false });
    expect(
      (await table.getByLabel('同屏第 1 行科目', { exact: true }).boundingBox())!.width,
    ).toBeGreaterThanOrEqual(180);
    for (const label of ['同屏第 1 行借方', '同屏第 2 行贷方']) {
      const amount = table.getByLabel(label, { exact: true });
      await expect(amount).toHaveValue('123456789.01');
      await amount.scrollIntoViewIfNeeded();
      const fit = await amount.evaluate((node) => {
        const input = node as HTMLInputElement,
          style = getComputedStyle(input);
        const context = document.createElement('canvas').getContext('2d')!;
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        return (
          context.measureText(input.value).width <=
          input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) + 1
        );
      });
      expect(fit).toBe(true);
    }
    if (device === 'mobile') {
      for (const name of ['上一笔', '下一笔']) {
        const bounds = await drawer.getByRole('button', { name, exact: true }).boundingBox();
        expect.soft(bounds?.height).toBeGreaterThanOrEqual(44);
      }
    }
    await noOverflow(page, drawer);
    await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
    await expect(drawer).toHaveCount(0);
    await expect(page.getByLabel('选择本页凭证', { exact: true })).toBeChecked();
    expect(
      (
        await (
          await request.get(`/api/books/${fixture.book.id}/exports`, { headers: fixture.owner })
        ).json()
      ).exports,
    ).toEqual([]);
  });
}

test('原件 503 明确提示失败，不自动重发审核或导出，也不闪回登录', async ({ page, request }) => {
  const fixture = await reviewFixture(request);
  let reads = 0;
  const criticalWrites: string[] = [];
  await page.route('**/api/business/attachments/*/content', async (route) => {
    reads++;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      headers: { 'x-request-id': 'qa-original-unavailable' },
      json: { detail: 'V03 本地原件服务暂不可用' },
    });
  });
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/(approve|exports)$/.test(new URL(req.url()).pathname))
      criticalWrites.push(req.url());
  });
  const drawer = await openReview(page, fixture.book.id);
  await expect(
    drawer.getByRole('alert').filter({ hasText: '服务暂时不可用，请稍后重新读取' }),
  ).toBeVisible();
  await expect(drawer.getByRole('alert')).toContainText('qa-original-unavailable');
  await expect(drawer).not.toContainText('V03 本地原件服务暂不可用');
  await expect(drawer.getByRole('img', { name: /^原始票据 / })).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await expect(page).toHaveURL(/\/workspace\/review/);
  await page.screenshot({ path: `${output}/review-original-error-desktop.png`, fullPage: false });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: `${output}/review-original-error-mobile.png`, fullPage: false });
  await noOverflow(page, drawer);
  expect(reads).toBe(1);
  expect(criticalWrites).toEqual([]);
});

test('真实原件人工确认后才能审核，审核不自动导出，员工无会计账套读取权', async ({
  page,
  request,
}) => {
  const fixture = await reviewFixture(request);
  const reconciliation = await request.post(`/api/business/events/${fixture.event.id}/reconcile`, {
    headers: fixture.owner,
  });
  expect(reconciliation.ok()).toBe(true);
  const assessment = await reconciliation.json();
  const unrecognized = assessment.findings.filter((finding: { code: string }) =>
    finding.code.startsWith('ocr_missing_'),
  );
  expect(unrecognized).toHaveLength(1);
  for (const finding of unrecognized) {
    expect(
      (
        await request.post(`/api/intelligence/findings/${finding.id}/resolve`, {
          headers: fixture.owner,
          data: {
            reason: '本地合成资料验收：会计人工核对日期金额用途及附件；不代表真实票据验真。',
          },
        })
      ).ok(),
    ).toBe(true);
  }
  const drawer = await openReview(page, fixture.book.id);
  await expect(drawer.getByRole('img', { name: /^原始票据 / })).toBeVisible();
  const approve = drawer.getByRole('button', { name: '确认审核', exact: true });
  await expect(approve).toBeDisabled();
  await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
  const current = await (
    await request.get(`/api/books/${fixture.book.id}/drafts/${fixture.draftId}`, {
      headers: fixture.owner,
    })
  ).json();
  expect(current.status).toBe('approved');
  expect(
    (
      await (
        await request.get(`/api/books/${fixture.book.id}/exports`, { headers: fixture.owner })
      ).json()
    ).exports,
  ).toEqual([]);
  expect([403, 404]).toContain(
    (await request.get(`/api/books/${fixture.book.id}`, { headers: fixture.employee })).status(),
  );
});
