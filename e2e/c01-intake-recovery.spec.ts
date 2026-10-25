import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { choosePersona, openBusiness } from './ui-navigation';

// These are UI recovery contracts against the real isolated SQLite API. Only
// the persisted in-flight state is synthetic; no supplier or worker restart is
// represented by route mocks. AK05 PG process recovery has separate evidence.
test.setTimeout(90_000);

async function fixture(request: APIRequestContext, kind: 'stale' | 'running') {
  const session = await (await request.get('/api/session?persona=employee')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const context = await (await request.get('/api/business/context', { headers })).json();
  const company = context.companies.find(
    (row: { name: string }) => row.name === '青屿设计（虚构示例）',
  );
  expect(company?.department_id).toBeTruthy();
  const nonce = crypto.randomUUID();
  const description = `C01浏览器恢复-${kind}-${nonce}`;
  const created = await request.post('/api/intake/drafts', {
    headers,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      idempotency_key: nonce,
      template_id: 'reimbursement',
      fields: {
        occurred_on: '2026-10-13',
        description,
        amount: '18.60',
        counterparty: '虚构人工核对供方',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '本地故障验收，非真实发票',
      },
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  let draft = await created.json();
  const xml = readFileSync(
    'nexus_backend/tests/fixtures/stage16_SYNTHETIC_einvoice_0_3.xml',
    'utf8',
  ).replace('第一客户企业', company.name);
  const upload = await request.post(
    `/api/intake/drafts/${draft.id}/attachments?expected_version=${draft.version}`,
    {
      headers,
      multipart: {
        file: {
          name: `C01虚构恢复-${nonce}.xml`,
          mimeType: 'application/xml',
          buffer: Buffer.from(xml),
        },
      },
    },
  );
  expect(upload.ok(), await upload.text()).toBe(true);
  draft = await upload.json();
  const attachment = draft.attachments[0];
  const recognized = await request.post(
    `/api/intake/drafts/${draft.id}/attachments/${attachment.id}/recognize`,
    {
      headers,
      data: { expected_version: draft.version, idempotency_key: crypto.randomUUID() },
    },
  );
  expect(recognized.ok(), await recognized.text()).toBe(true);
  const old = await recognized.json();
  expect(old.status).toBe('succeeded');
  expect(old.result.documents[0].gross_amount).toBe('0.30');
  // This helper cannot touch PG, arbitrary SQLite paths or another test's row.
  const prepared = JSON.parse(
    execFileSync(
      process.env.C01_TEST_PYTHON || 'python',
      ['nexus_backend/tests/c01_prepare_intake_browser.py'],
      {
        input: JSON.stringify({
          draft_id: draft.id,
          attachment_id: attachment.id,
          nonce,
          kind,
          mode: process.env.E2E_FRONTEND_MODE || 'dev',
        }),
        env: { ...process.env, C01_BROWSER_INTAKE_FIXTURE: '1' },
        encoding: 'utf8',
        timeout: 20_000,
      },
    ),
  );
  expect(prepared.synthetic_state).toBe(true);
  return { id: draft.id as string, description, headers, runId: prepared.run_id as string };
}

async function reopen(page: Page, description: string, reload = false) {
  if (reload) await page.reload();
  else await page.goto('/');
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
  await dialog.getByText(/继续未完成的录入/).click();
  await dialog.getByRole('button', { name: new RegExp(description) }).click();
  await expect(dialog.getByRole('status')).toContainText('已恢复服务端草稿');
  return dialog;
}

for (const width of [1440, 375]) {
  test(`F12 ${width} 过期识别恢复保留原件且不采用旧建议，可人工确认后提交`, async ({
    page,
    request,
  }, testInfo) => {
    const value = await fixture(request, 'stale');
    const writes: string[] = [];
    page.on('request', (row) => {
      if (row.method() === 'POST') writes.push(new URL(row.url()).pathname);
    });
    await page.setViewportSize({ width, height: 900 });
    const dialog = await reopen(page, value.description);
    await expect(
      dialog.getByRole('alert').filter({ hasText: '识别已中断，未自动重发' }),
    ).toBeVisible();
    await expect(
      dialog.getByRole('button', { name: '解析 XML 并预填', exact: true }),
    ).toBeDisabled();
    await expect(dialog.getByRole('button', { name: '采用此字段', exact: true })).toHaveCount(0);
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('18.60');
    expect(writes.filter((path) => /\/(recognize|complete)$/.test(path))).toEqual([]);
    const persisted = await (
      await request.get(`/api/intake/drafts/${value.id}`, { headers: value.headers })
    ).json();
    expect(persisted.runs.find((run: { id: string }) => run.id === value.runId).status).toBe(
      'stale',
    );
    expect(persisted.runs[0].id).toBe(value.runId);
    expect(persisted.runs[0].result).toBeNull();
    expect(persisted.runs).toHaveLength(2);
    expect(persisted.event_id).toBeNull();
    await dialog.getByLabel('事项金额', { exact: true }).fill('64.20');
    await dialog.getByLabel('事项金额', { exact: true }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`f12-${width}-manual-recovery.png`),
      fullPage: false,
    });
    await dialog
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
    const submitted = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().endsWith(`/api/intake/drafts/${value.id}/complete`),
    );
    await dialog.getByRole('button', { name: '提交审批', exact: true }).click();
    const completed = await submitted;
    expect(completed.ok(), await completed.text()).toBe(true);
    const result = await completed.json();
    expect(result.event.payload.amount).toBe('64.20');
    expect(result.event.status).toBe('submitted');
    expect(writes.filter((path) => path.endsWith('/recognize'))).toEqual([]);
    expect(writes.filter((path) => path.endsWith('/complete'))).toHaveLength(1);
    await expect(dialog).toHaveCount(0);
  });

  test(`F13 ${width} 识别运行态保存后刷新重开不重复识别、不自动提交`, async ({
    page,
    request,
  }, testInfo) => {
    const value = await fixture(request, 'running');
    const writes: string[] = [];
    page.on('request', (row) => {
      if (row.method() === 'POST') writes.push(new URL(row.url()).pathname);
    });
    await page.setViewportSize({ width, height: 900 });
    let dialog = await reopen(page, value.description);
    await expect(
      dialog.getByRole('button', { name: '识别处理中，读取最新草稿查看', exact: true }),
    ).toBeDisabled();
    await expect(dialog.getByRole('button', { name: '采用此字段', exact: true })).toHaveCount(0);
    await dialog.getByLabel('事项金额', { exact: true }).fill('22.20');
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/intake/drafts/${value.id}`, { headers: value.headers })
            ).json()
          ).fields.amount,
      )
      .toBe('22.20');
    await dialog.getByRole('button', { name: '关闭业务事项表单', exact: true }).click();
    dialog = await reopen(page, value.description, true);
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('22.20');
    await expect(
      dialog.getByRole('button', { name: '识别处理中，读取最新草稿查看', exact: true }),
    ).toBeDisabled();
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    const persisted = await (
      await request.get(`/api/intake/drafts/${value.id}`, { headers: value.headers })
    ).json();
    expect(persisted.runs).toHaveLength(2);
    expect(persisted.runs.find((run: { id: string }) => run.id === value.runId).status).toBe(
      'running',
    );
    expect(persisted.runs[0].id).toBe(value.runId);
    expect(persisted.runs[0].result).toBeNull();
    expect(persisted.event_id).toBeNull();
    expect(writes.filter((path) => /\/(recognize|complete)$/.test(path))).toEqual([]);
    await dialog
      .getByRole('button', { name: '识别处理中，读取最新草稿查看', exact: true })
      .scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`f13-${width}-running-restored.png`),
      fullPage: false,
    });
  });
}
