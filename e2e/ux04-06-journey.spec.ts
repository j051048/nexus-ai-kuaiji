import { readFileSync } from 'node:fs';
import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test';
import { choosePersona, openBusiness } from './ui-navigation';
import { independentReviewer } from './independent-review';

test.setTimeout(90_000);
async function body(response: APIResponse, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return response.json();
}
async function auth(request: APIRequestContext, persona = 'accountant') {
  const value = await body(await request.get(`/api/session?persona=${persona}`));
  return { Authorization: `Bearer ${value.token}` };
}
for (const width of [1280, 390]) {
  test(`退回消息直达真实原事项且不代替已读或提交 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const employee = await auth(request, 'employee'),
      approver = await auth(request, 'department');
    const context = await body(await request.get('/api/business/context', { headers: employee }));
    const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
    let event = await body(
      await request.post('/api/business/events', {
        headers: employee,
        data: {
          company_id: company.id,
          template_id: 'reimbursement',
          payload: {
            occurred_on: '2026-10-03',
            description: `补件原事项-${width}`,
            counterparty: '耗材商店',
            amount: '12.50',
            direction: 'expense',
            payment_status: 'unpaid',
            category: 'office',
          },
        },
      }),
      201,
    );
    event = await body(
      await request.post(
        `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
        {
          headers: employee,
          multipart: {
            file: {
              name: '合成原件.pdf',
              mimeType: 'application/pdf',
              buffer: Buffer.from('%PDF-1.4\n% Synthetic evidence only\n%%EOF\n'),
            },
          },
        },
      ),
    );
    event = await body(
      await request.post(`/api/business/events/${event.id}/submit`, {
        headers: employee,
        data: { expected_version: event.version },
      }),
    );
    await body(
      await request.post(`/api/business/events/${event.id}/return`, {
        headers: approver,
        data: { expected_version: event.version, reason: '请补充本次差旅用途' },
      }),
    );
    const messages = await body(await request.get('/api/client/messages', { headers: employee }));
    const message = messages.messages.find(
      (m: { related_ref: string }) => m.related_ref === event.id,
    );
    expect(message.read_at).toBeNull();
    await page.goto('/');
    await choosePersona(page, 'employee');
    await page
      .getByRole('navigation', { name: '客户服务', exact: true })
      .getByRole('button', { name: '消息', exact: true })
      .click();
    // Read and source actions stay separate: opening never submits or signs.
    const targetResponse = page.waitForResponse(
      (response) =>
        decodeURIComponent(new URL(response.url()).pathname) ===
        `/api/client/messages/${message.id}/target`,
    );
    await page
      .locator('.client-messages article')
      .filter({ hasText: '事项已退回，请补充后重新提交' })
      .first()
      .getByRole('button', { name: '打开原事项', exact: true })
      .click();
    const target = await (await targetResponse).json();
    expect(target.event_id).toBe(event.id);
    await expect(page).toHaveURL(new RegExp(`event=${event.id}`));
    await expect(page.getByText('请补充本次差旅用途', { exact: true }).first()).toBeVisible();
    const current = await body(
      await request.get(`/api/business/events/${event.id}`, { headers: employee }),
    );
    expect(current.status).toBe('returned');
    const after = await body(await request.get('/api/client/messages', { headers: employee }));
    expect(after.messages.find((m: { id: string }) => m.id === message.id).read_at).toBeNull();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({ path: `tmp_test/ux04-06-20261104/message-${width}.png` });
  });
  test(`真实 XML 预填且迟到结果保留人工金额 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await auth(request),
      employee = await auth(request, 'employee');
    const context = await body(await request.get('/api/business/context', { headers: employee }));
    const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
    await body(
      await request.put(`/api/companies/${company.id}/intelligence/settings`, {
        headers,
        data: { enabled: true },
      }),
    );
    await page.goto('/');
    await choosePersona(page, 'employee');
    await openBusiness(page);
    await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('事项客户企业')).toHaveValue(company.id);
    const xml = readFileSync(
      'nexus_backend/tests/fixtures/stage16_SYNTHETIC_einvoice_0_3.xml',
      'utf8',
    ).replace('第一客户企业', company.name);
    await dialog.getByLabel('一体化录入上传附件').setInputFiles({
      name: '离线合成票.xml',
      mimeType: 'application/xml',
      buffer: Buffer.from(xml),
    });
    await expect(dialog.getByText('离线合成票.xml', { exact: true })).toBeVisible();
    let release!: () => void;
    const barrier = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/recognize', async (route) => {
      const response = await route.fetch();
      await barrier;
      await route.fulfill({ response });
    });
    await dialog.getByRole('button', { name: '解析 XML 并预填', exact: true }).click();
    await dialog.getByLabel('事项金额', { exact: true }).fill('128.50');
    release();
    await expect(dialog.getByText('识别建议已就绪', { exact: true })).toBeVisible();
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
    await expect(dialog.getByLabel('发生日期', { exact: true })).toHaveValue('2026-10-03');
    await expect(dialog.getByLabel('交易对方', { exact: true })).toHaveValue('耗材商店');
    await expect(dialog.getByLabel('事项分类', { exact: true })).not.toBeVisible();
    await expect(
      dialog.getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true }),
    ).toBeVisible();
    expect(await dialog.evaluate((node) => node.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: `tmp_test/ux04-06-20261104/intake-${width}.png` });
    await page.unrouteAll({ behavior: 'wait' });
  });

  test(`开账步骤实际保存并返回重查 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await auth(request);
    const company = await body(
      await request.post('/api/companies', {
        headers,
        data: { name: `开账验收-${crypto.randomUUID()}`, tax_id: '' },
      }),
      201,
    );
    const book = await body(
      await request.post('/api/books', {
        headers,
        data: { company_id: company.id, name: '开账验收', period: '2026-10' },
      }),
      201,
    );
    await page.goto(`/workspace/formal-books?book=${book.id}&company=${company.id}&period=2026-10`);
    await page
      .getByText('开账准备（核对模板、科目、子账、银行、期初与岗位分工）', { exact: true })
      .click();
    const wizard = page.locator('.setup-wizard');
    await wizard.locator('#setup-template').getByRole('button').click();
    await page.getByLabel('本次办理依据').fill('本地虚构企业开户核对');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({ path: `tmp_test/ux04-06-20261104/setup-form-${width}.png` });
    await page.getByRole('button', { name: '确认从本月生效', exact: true }).click();
    await expect(page.getByText('已保存，请核对记录后继续下一步。', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '返回开账清单并重查', exact: true }).click();
    await expect(wizard.locator('#setup-template')).toHaveAttribute('data-state', 'done');
    await wizard.locator('#setup-accounts').getByRole('button').click();
    await page.getByLabel('本次办理依据').fill('本地虚构银行科目');
    await page.getByLabel('科目编码', { exact: true }).fill('1002');
    await page.getByLabel('科目名称', { exact: true }).fill('银行存款');
    await page.getByRole('button', { name: '登记科目', exact: true }).click();
    await expect(
      page.getByRole('listitem').filter({ hasText: /^1002 银行存款 · V1$/ }),
    ).toBeVisible();
    await page.getByRole('button', { name: '返回开账清单并重查', exact: true }).click();
    await expect(wizard.locator('#setup-accounts')).toHaveAttribute('data-state', 'done');
    await wizard.locator('#setup-opening').getByRole('button').click();
    await page.getByLabel('本次办理依据').fill('虚构新设企业零余额期初');
    await page.getByRole('combobox', { name: '采用政策', exact: true }).selectOption({ index: 1 });
    await page.getByRole('button', { name: '建立本月期初包', exact: true }).click();
    await expect(page.getByRole('combobox', { name: '采用政策', exact: true })).toHaveCount(0);
    await page
      .getByRole('combobox', { name: '本月期初包', exact: true })
      .selectOption({ index: 1 });
    await page
      .getByLabel('期初余额（每行：科目编码,年初余额,本年借方,本年贷方,期初余额）')
      .fill('1002,0,0,0,0');
    const imported = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        /\/opening\/packages\/[^/]+\/versions$/.test(new URL(response.url()).pathname),
    );
    await page.getByRole('button', { name: '保存期初草稿', exact: true }).click();
    const version = (await (await imported).json()).version;
    await page.getByRole('button', { name: '校验', exact: true }).click();
    await expect(page.getByText('V1 · 已校验', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '提交复核', exact: true }).click();
    await expect(page.getByText('V1 · 待独立核准', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '独立核准', exact: true })).toBeDisabled();
    const reviewer = await independentReviewer(request, headers, book.id);
    await body(
      await request.post(`/api/books/${book.id}/ledger/managers`, {
        headers,
        data: { user_id: 'local-reviewer', reason: '虚构期初包独立复核' },
      }),
      201,
    );
    await body(
      await request.post(`/api/opening/versions/${version.id}/approve`, {
        headers: reviewer,
        data: { expected_version: version.version, reason: '第二身份核准虚构零余额' },
      }),
    );
    await page.getByRole('button', { name: '重新读取记录', exact: true }).click();
    await expect(page.getByText('V1 · 已核准', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '冻结', exact: true }).click();
    await expect(page.getByText('V1 · 已冻结', { exact: true })).toBeVisible();
    const bound = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/posting/opening-bindings',
    );
    await page.getByRole('button', { name: '绑定本月账套', exact: true }).click();
    expect((await (await bound).json()).binding.book_id).toBe(book.id);
    await page.getByRole('button', { name: '返回开账清单并重查', exact: true }).click();
    await expect(wizard.locator('#setup-opening')).toHaveAttribute('data-state', 'done');
    await expect(page).toHaveURL(new RegExp(`book=${book.id}`));
    await page.screenshot({ path: `tmp_test/ux04-06-20261104/setup-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() =>
      [...document.querySelectorAll('body *')]
        .filter((e) => {
          const r = e.getBoundingClientRect();
          return r.width > 0 && r.right > innerWidth + 1;
        })
        .slice(0, 20)
        .map((e) => ({ tag: e.tagName, css: e.className, width: e.getBoundingClientRect().width })),
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      JSON.stringify(overflow),
    ).toBe(true);
  });

  test(`批量入职只预检当前企业且未配置身份服务不发邀请 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await auth(request);
    const company = await body(
      await request.post('/api/companies', {
        headers,
        data: { name: `名单验收-${crypto.randomUUID()}`, tax_id: '' },
      }),
      201,
    );
    const policy = await body(
      await request.get(`/api/companies/${company.id}/submission-policy`, { headers }),
    );
    await body(
      await request.put(`/api/companies/${company.id}/submission-policy`, {
        headers,
        data: { mode: 'direct', expected_version: policy.version, reason: '虚构企业集中提交' },
      }),
    );
    await page.goto(`/admin/organizations/${company.id}?tab=members`);
    await page.getByText('批量员工入职（先检查名单）', { exact: true }).click();
    await page
      .getByLabel('入职名单')
      .fill('虚构员工,synthetic@example.test\n重复员工,synthetic@example.test');
    await page.getByRole('button', { name: '检查名单', exact: true }).click();
    await expect(page.getByText(/第 2 人：名单中邮箱重复/)).toBeVisible();
    await page.getByLabel('入职名单').fill('虚构员工,synthetic@example.test');
    await page.getByRole('button', { name: '检查名单', exact: true }).click();
    await expect(
      page.getByRole('button', { name: '确认生成 1 人的邀请', exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByText('当前未启用账号开通服务，名单已检查，尚未生成邀请。'),
    ).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: `tmp_test/ux04-06-20261104/invites-${width}.png`,
      fullPage: true,
    });
  });
}

test('入职模拟回执错企业时停止后续人员，不展示错绑激活链接', async ({ page, request }) => {
  const headers = await auth(request);
  const company = await body(
    await request.post('/api/companies', {
      headers,
      data: { name: `离线开户回执-${crypto.randomUUID()}`, tax_id: '' },
    }),
    201,
  );
  const policy = await body(
    await request.get(`/api/companies/${company.id}/submission-policy`, { headers }),
  );
  await body(
    await request.put(`/api/companies/${company.id}/submission-policy`, {
      headers,
      data: { mode: 'direct', expected_version: policy.version, reason: '离线身份回执边界验证' },
    }),
  );
  await page.route('**/api/admin/account-requests/preview', async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), provisioning_enabled: true },
    });
  });
  const commands: Record<string, string>[] = [];
  await page.route('**/api/admin/account-requests', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const command = route.request().postDataJSON();
    commands.push(command);
    await route.fulfill({
      json: {
        request: {
          ...command,
          id: `synthetic-${commands.length}`,
          status: 'awaiting_activation',
          company_id: commands.length === 1 ? company.id : 'wrong-company',
        },
        activation_url: `${new URL(page.url()).origin}/synthetic-activation/${commands.length}`,
      },
    });
  });
  await page.goto(`/admin/organizations/${company.id}?tab=members`);
  await page.getByText('批量员工入职（先检查名单）', { exact: true }).click();
  await page
    .getByLabel('入职名单')
    .fill('虚构甲,a@example.test\n虚构乙,b@example.test\n虚构丙,c@example.test');
  await page.getByRole('button', { name: '检查名单', exact: true }).click();
  await page.getByRole('button', { name: '确认生成 3 人的邀请', exact: true }).click();
  await expect(page.getByLabel('a@example.test 激活链接')).toHaveValue(/synthetic-activation\/1$/);
  await expect(page.getByText('b@example.test：未确认结果，后续人员尚未办理')).toBeVisible();
  await expect(page.getByLabel('b@example.test 激活链接')).toHaveCount(0);
  expect(commands).toHaveLength(2);
  expect(commands[0].idempotency_key).not.toBe(commands[1].idempotency_key);
  await page.unrouteAll({ behavior: 'wait' });
});
