import { readFileSync } from 'node:fs';
import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

test.setTimeout(90_000);
const output = 'tmp_test/review-focus-20261105';
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));
async function data(response: APIResponse, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return response.json();
}
async function identity(request: APIRequestContext, persona: string) {
  const value = await data(await request.get(`/api/session?persona=${persona}`));
  return { Authorization: `Bearer ${value.token}` };
}
async function bindPersona(page: Page, persona: string) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}
// Same real local business APIs and synthetic original as the existing V03 path.
async function fixture(request: APIRequestContext, handoff = false) {
  const employee = await identity(request, 'employee');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const owner = await identity(request, 'accountant');
  const context: BusinessContext = await data(
    await request.get('/api/business/context', { headers: employee }),
  );
  const company = context.companies.find((c) => c.can_submit && c.department_id)!;
  expect(company).toBeTruthy();
  const tag = crypto.randomUUID().slice(0, 8),
    party = `虚构核验供应商-${tag}`;
  const book = await data(
    await request.post('/api/books', {
      headers: owner,
      data: {
        company_id: company.id,
        name: `核验办理-${tag}`,
        period: '2026-10',
      },
    }),
    201,
  );
  await data(
    await request.put(`/api/books/${book.id}/members/local-business-finance`, {
      headers: owner,
      data: { role: 'editor' },
    }),
  );
  await data(
    await request.post(`/api/books/${book.id}/ledger/entities`, {
      headers: owner,
      data: { kind: 'counterparty', code: `FOCUS-${tag}`, name: party },
    }),
    201,
  );
  let event: BusinessEvent = await data(
    await request.post('/api/business/events', {
      headers: employee,
      data: {
        company_id: company.id,
        department_id: company.department_id,
        template_id: 'reimbursement',
        payload: {
          occurred_on: '2026-10-12',
          description: `虚构办公用品核验-${tag}`,
          counterparty: party,
          amount: '128.50',
          direction: 'expense',
          payment_status: 'unpaid',
          category: 'office',
          purpose: '本地界面验收',
        },
      },
    }),
    201,
  );
  event = await data(
    await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers: employee,
        multipart: { file: { name: `合成票据-${tag}.png`, mimeType: 'image/png', buffer: image } },
      },
    ),
  );
  for (const [command, headers] of [
    ['submit', employee],
    ['department-approve', department],
  ] as const) {
    event = await data(
      await request.post(`/api/business/events/${event.id}/${command}`, {
        headers,
        data: { expected_version: event.version },
      }),
    );
  }
  if (handoff) {
    event = await data(
      await request.post(`/api/business/events/${event.id}/finance-approve`, {
        headers: finance,
        data: { expected_version: event.version },
      }),
    );
    const result = await data(
      await request.post(`/api/business/events/${event.id}/handoff`, {
        headers: finance,
        data: {
          expected_version: event.version,
          book_id: book.id,
          idempotency_key: `focus-${tag}`,
        },
      }),
    );
    expect(result.draft_id).toBeTruthy();
    event = await data(await request.get(`/api/business/events/${event.id}`, { headers: owner }));
    const check = await data(
      await request.post(`/api/business/events/${event.id}/reconcile`, { headers: owner }),
    );
    for (const finding of check.findings.filter((f: { code: string }) =>
      f.code.startsWith('ocr_missing_'),
    )) {
      await data(
        await request.post(`/api/intelligence/findings/${finding.id}/resolve`, {
          headers: owner,
          data: { reason: '仅合成资料：人工核对日期、金额和附件，不代表真实票据验真。' },
        }),
      );
    }
  }
  return { event, book, owner, employee, finance };
}
async function openFinance(page: Page, event: BusinessEvent) {
  await bindPersona(page, 'finance');
  await page.goto(`/workspace/business?event=${event.id}`);
  const detail = page.getByLabel('业务事项详情', { exact: true });
  await expect(
    detail.getByRole('heading', { name: event.payload.description, exact: true }),
  ).toBeVisible();
  return detail;
}
async function openReview(page: Page, bookId: string) {
  await bindPersona(page, 'accountant');
  await page.goto(`/workspace/review?book=${bookId}`);
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '查看证据', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toBeVisible();
  return drawer;
}
for (const width of [1440, 390]) {
  test(`财务先核对原件再办理，历史与辅助说明不挡住操作 ${width}`, async ({ page, request }) => {
    const f = await fixture(request);
    await page.setViewportSize({ width, height: 900 });
    const detail = await openFinance(page, f.event);
    await expect(
      page.getByRole('tab', { name: '企业财务', exact: true, includeHidden: true }),
    ).toHaveAttribute('aria-selected', 'true');
    const guide = detail.getByRole('region', { name: '事项办理引导', exact: true });
    await expect(guide.locator('details').first()).not.toHaveAttribute('open');
    await expect(guide.getByText('查看核验步骤与说明', { exact: true })).toBeVisible();
    await expect(detail.locator('.business-history')).not.toHaveAttribute('open');
    await page.screenshot({ path: `${output}/finance-entry-${width}.png`, fullPage: false });
    const writes: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST') writes.push(new URL(r.url()).pathname);
    });
    await detail.getByRole('button', { name: '核对原件', exact: true }).click();
    await detail.getByRole('button', { name: '预览原件', exact: true }).click();
    await expect(detail.getByRole('img', { name: /^原始票据 / })).toBeVisible();
    expect(writes).toEqual([]);
    await page.screenshot({ path: `${output}/finance-original-${width}.png`, fullPage: false });
    await detail.getByRole('button', { name: '办理本事项', exact: true }).click();
    await expect(
      detail.getByRole('button', { name: '财务核验通过', exact: true }),
    ).toBeInViewport();
    expect(
      await detail.evaluate((node) => {
        const actions = node.querySelector('.business-actions')!;
        return (
          !!(
            node.querySelector('.intelligence-panel')!.compareDocumentPosition(actions) &
            Node.DOCUMENT_POSITION_FOLLOWING
          ) &&
          !!(
            actions.compareDocumentPosition(node.querySelector('.business-history')!) &
            Node.DOCUMENT_POSITION_FOLLOWING
          )
        );
      }),
    ).toBe(true);
    await expect(detail.locator('details.intelligence-attachment-list')).not.toHaveAttribute(
      'open',
    );
    await expect(detail.locator('.business-return textarea')).not.toBeVisible();
    await page.screenshot({ path: `${output}/finance-action-${width}.png`, fullPage: false });
    await detail.getByRole('button', { name: '财务核验通过', exact: true }).click();
    await expect(detail.getByLabel('交接到账套', { exact: true })).toBeVisible();
    await detail.getByLabel('交接到账套', { exact: true }).selectOption(f.book.id);
    await detail.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
    await expect(guide).toContainText('业务已交接会计');
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    await expect(
      detail.getByRole('region', { name: '票据识别与交叉核对', exact: true }),
    ).toHaveCount(1);
    expect(writes.filter((path) => path.endsWith('/finance-approve'))).toHaveLength(1);
    expect(writes.filter((path) => path.endsWith('/handoff'))).toHaveLength(1);
    expect(writes.some((path) => /\/approve$|\/exports$/.test(path))).toBe(false);
    const current = await data(await request.get(`/api/books/${f.book.id}`, { headers: f.owner }));
    expect(current.drafts.every((d: { status: string }) => d.status === 'draft')).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
  test(`会计只显示一组状态，原件与分录可直接定位且确认后独立审核 ${width}`, async ({
    page,
    request,
  }) => {
    const f = await fixture(request, true);
    await page.setViewportSize({ width, height: 900 });
    const drawer = await openReview(page, f.book.id);
    await expect(drawer.locator('.accounting-context-assistant')).toHaveCount(0);
    await expect(drawer.locator('.inline-review-state')).toHaveCount(0);
    await expect(drawer.getByText('待会计审核', { exact: true })).toHaveCount(1);
    await expect(drawer.getByRole('button', { name: '保存草稿', exact: true })).toHaveCount(0);
    const writes: string[] = [];
    page.on('request', (r) => {
      if (r.method() !== 'GET') writes.push(new URL(r.url()).pathname);
    });
    await drawer.getByRole('button', { name: '查看原件', exact: true }).click();
    await expect(drawer.getByRole('img', { name: /^原始票据 / })).toBeInViewport();
    await page.screenshot({ path: `${output}/accounting-original-${width}.png`, fullPage: false });
    await drawer.getByRole('button', { name: '核对分录', exact: true }).click();
    await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toBeInViewport();
    await page.screenshot({ path: `${output}/accounting-facts-${width}.png`, fullPage: false });
    await drawer.getByRole('button', { name: '前往审核确认', exact: true }).click();
    await expect(drawer.getByLabel('我已核对同屏原件与分录', { exact: true })).not.toBeChecked();
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    expect(writes).toEqual([]);
    await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
    await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
    await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
    expect(writes.filter((path) => path.endsWith('/approve'))).toHaveLength(1);
    expect(
      (await data(await request.get(`/api/books/${f.book.id}/exports`, { headers: f.owner })))
        .exports,
    ).toEqual([]);
    expect([403, 404]).toContain(
      (await request.get(`/api/books/${f.book.id}`, { headers: f.employee })).status(),
    );
    expect(await drawer.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  });
}
test('退回入口按需展开，原因必填，保留原事项并交回补充', async ({ page, request }) => {
  const f = await fixture(request);
  const detail = await openFinance(page, f.event);
  await detail.getByRole('button', { name: '办理本事项', exact: true }).click();
  await detail.locator('.business-return > summary').click();
  await expect(detail.getByRole('button', { name: '退回修改', exact: true })).toBeDisabled();
  await detail.getByLabel('退回原因', { exact: true }).fill('请补充采购用途与原始清单');
  await detail.getByRole('button', { name: '退回修改', exact: true }).click();
  await expect(detail.getByLabel('本次退回与补充说明')).toContainText('请补充采购用途与原始清单');
  const current = await data(
    await request.get(`/api/business/events/${f.event.id}`, { headers: f.employee }),
  );
  expect(current.status).toBe('returned');
  expect(current.mapping).toBeNull();
});

test('折叠识别明细不隐藏核对阻断，核验冲突只读取最新事项不自动重发', async ({ page, request }) => {
  const f = await fixture(request);
  const check = await data(
    await request.post(`/api/business/events/${f.event.id}/reconcile`, { headers: f.finance }),
  );
  const blocking = check.findings.find(
    (finding: { severity: string }) => finding.severity === 'blocking',
  );
  expect(blocking).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 900 });
  const detail = await openFinance(page, f.event);
  await expect(detail.locator('details.intelligence-attachment-list')).not.toHaveAttribute('open');
  await expect(detail.getByText(blocking.payload.message, { exact: true })).toBeVisible();
  let attempts = 0;
  await page.route(`**/api/business/events/${f.event.id}/finance-approve`, async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '事项版本已更新，请重新核对' }),
    });
  });
  await detail.getByRole('button', { name: '办理本事项', exact: true }).click();
  await detail.getByRole('button', { name: '财务核验通过', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '事项版本已更新' })).toContainText(
    '已重新读取最新事项',
  );
  await expect(detail.getByRole('alert')).toBeInViewport();
  await page.screenshot({ path: `${output}/finance-conflict-390.png`, fullPage: false });
  await expect(detail.getByRole('button', { name: '财务核验通过', exact: true })).toBeEnabled();
  await expect(detail.getByLabel('交接到账套', { exact: true })).toHaveCount(0);
  expect(attempts).toBe(1);
  const current = await data(
    await request.get(`/api/business/events/${f.event.id}`, { headers: f.employee }),
  );
  expect(current.status).toBe('department_approved');
  expect(current.mapping).toBeNull();
});
