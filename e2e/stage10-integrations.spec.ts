import { independentReviewer } from './independent-review';
import { openAccounting, openBookTools } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('财务软件文件底座真实 UI：配置、人工确认参考、冻结交付与权限隔离', async ({
  page,
  request,
}) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` },
    marker = crypto.randomUUID().slice(0, 8);
  const created = await request.post('/api/books', {
    headers,
    data: { name: `第十阶段 T+ 接入合成验证 ${marker}`, period: '2026-11' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const imported = await request.post(`/api/books/${book.id}/imports`, {
    headers,
    data: {
      source_name: '明确合成的银行资料',
      rows: [
        {
          external_id: `bridge:${marker}`,
          date: '2026-11-03',
          description: `办公用品合成交易 ${marker}`,
          counterparty: '合成供应商',
          amount: '80.00',
          direction: 'expense',
          evidence_ref: `合成票据 ${marker}`,
        },
      ],
    },
  });
  expect(imported.status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const draft = (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts[0];
  const reviewer = await independentReviewer(request, headers, book.id);
  expect(
    (
      await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
        headers: reviewer,
        data: { expected_version: draft.version },
      })
    ).status(),
  ).toBe(200);
  const exported = await request.post(`/api/books/${book.id}/exports`, {
    headers,
    data: { draft_ids: [draft.id], idempotency_key: crypto.randomUUID() },
  });
  expect(exported.status()).toBe(200);
  const exportValue = await exported.json();
  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(`第十阶段 T+ 接入合成验证 ${marker}`);
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  await openBookTools(page);
  await page.getByRole('button', { name: '财务软件接入', exact: true }).click();
  await expect(page.getByRole('heading', { name: '财务软件接入', exact: true })).toBeVisible();
  await expect(page.locator('.integration-offline')).toHaveText('文件模式');
  await page.getByLabel('客户 T+ 账套标识', { exact: true }).fill(`synthetic-customer-${marker}`);
  await page
    .getByLabel('科目映射 · 每行“本地科目,外部科目”', { exact: true })
    .fill('1002,100201\n6602.02,660202');
  await page
    .getByLabel('本次操作依据', { exact: true })
    .fill('合成客户的科目及导出文件格式用于界面验证');
  const configResponse = page.waitForResponse(
    (r) => r.url().endsWith('/integrations/tplus/configuration') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '保存新映射版本', exact: true }).click();
  const configured = await configResponse;
  expect(configured.status()).toBe(200);
  expect(typeof configured.request().postDataJSON()).toBe('object');
  const base = `/api/books/${book.id}/integrations/tplus`;
  const template = await (await request.get(base + '/template', { headers })).json();
  template.records[0].external_id = `customer-export-account:${marker}`;
  template.records[0].name = '合成客户银行存款科目';
  await page.locator('.integration-file input[type=file]').setInputFiles({
    name: '合成T+参考.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(template), 'utf8'),
  });
  await page.getByRole('button', { name: '预览导入', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '共 1 条 · 重复 0 · 冲突 0', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '确认保留参考记录', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认保留参考记录', exact: true }).click();
  await expect(page.getByRole('button', { name: '已确认保留', exact: true })).toBeDisabled();
  const after = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(after.sources).toHaveLength(1);
  expect(after.drafts).toHaveLength(1);
  await page.getByLabel('选择已审核导出', { exact: true }).selectOption(exportValue.id);
  const download = page.waitForEvent('download'),
    preparation = page.waitForResponse(
      (r) => r.url().endsWith('/integrations/tplus/outbox') && r.request().method() === 'POST',
    );
  await page.getByRole('button', { name: '冻结并下载交付包', exact: true }).click();
  const intentResponse = await preparation;
  expect(intentResponse.status()).toBe(201);
  const intent = await intentResponse.json();
  expect((await download).suggestedFilename()).toContain('tplus-package-');
  expect(intent.package.live_enabled).toBe(false);
  expect(intent.automatic_resend).toBe(false);
  expect(
    intent.package.vouchers[0].lines
      .map((line: { account_code: string }) => line.account_code)
      .sort(),
  ).toEqual(['100201', '660202']);
  expect((await request.post(base + `/outbox/${intent.id}/dispatch`, { headers })).status()).toBe(
    409,
  );
  const artifactResponse = page.waitForResponse(
    (r) => r.url().endsWith(`/outbox/${intent.id}/artifacts`) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '生成 54 列', exact: true }).click();
  const artifact = await (await artifactResponse).json();
  expect(artifact.profile).toBe('hengzhang.tplus.synthetic54.v1');
  expect(artifact.row_count).toBe(1);
  await expect(page.getByText(/54 列合成适配文件已冻结/)).toBeVisible();
  const csvDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 CSV', exact: true }).click();
  expect((await csvDownload).suggestedFilename()).toContain('tplus54-synthetic-');
  const csvResponse = await request.get(base + `/artifacts/${artifact.id}/download`, { headers });
  expect(csvResponse.status()).toBe(200);
  const csvText = await csvResponse.text();
  const header = csvText
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)[0]
    .split(',');
  const codeIndex = header.indexOf('外部凭证编号');
  const codes = Array.from(
    new Set(
      csvText
        .replace(/^\uFEFF/, '')
        .split(/\r?\n/)
        .slice(1)
        .filter(Boolean)
        .map((line) => line.split(',')[codeIndex]),
    ),
  );
  expect(codes.length).toBe(1);
  const receipt = [
    '行号,外部凭证编号,结果,原因',
    ...codes.map((code, index) => `${index + 1},${code},accepted,`),
  ].join('\n');
  await page.getByLabel('客户回执编号', { exact: true }).fill(`R-UI-${marker}`);
  await page.getByLabel('逐行回执 CSV').setInputFiles({
    name: '客户逐行回执.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(receipt, 'utf8'),
  });
  await page.getByRole('button', { name: '登记回执', exact: true }).click();
  await expect(page.getByText(/接受 1、拒绝 0、重复 0、未知 0/)).toBeVisible();
  await expect(page.getByText(/外部过账未确认/)).toBeVisible();
  expect(artifact.native_tplus_format).toBe(false);
  await expect(page.getByText('对方已接收', { exact: false })).toBeVisible();
  await expect(page.getByText('对方已过账', { exact: false })).toHaveCount(0);
  await expect(page.getByText('未过账', { exact: true })).toBeVisible();
  const voucher = intent.package.vouchers[0] as {
    external_code: string;
    lines: { account_code: string; debit: string }[];
  };
  const debitCents = voucher.lines.reduce(
    (sum, line) => sum + Math.round(Number(line.debit) * 100),
    0,
  );
  const accounts = Array.from(new Set(voucher.lines.map((line) => line.account_code))).join('|');
  const returnCsv = [
    '批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目',
    `${intent.external_code},T54-${voucher.external_code},T+-R-${marker},accepted,,2026-10-31,${(debitCents / 100).toFixed(2)},${accounts}`,
  ].join('\n');
  await page.locator('.integration-returns input[type=file]').setInputFiles({
    name: '客户返回文件.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(returnCsv, 'utf8'),
  });
  await page.getByRole('button', { name: '核对返回文件', exact: true }).click();
  await expect(page.getByText(/返回文件已核对/)).toBeVisible();
  await expect(page.getByText(/已接收 · 已过账/)).toBeVisible();
  await page.getByText(/逐笔明细/).click();
  await expect(page.getByText(`T54-${voucher.external_code}`)).toBeVisible();
  await page.screenshot({ path: 'artifacts/stage10-financial-bridge.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.integration-artifact').scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: '下载 CSV', exact: true })).toBeVisible();
  await expect(page.getByText(/外部过账未确认/)).toBeVisible();
  await page.screenshot({ path: 'artifacts/stage10-financial-bridge-390.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  const employee = await (await request.get('/api/session?persona=employee')).json();
  expect(
    (
      await request.get(base + `/outbox/${intent.id}`, {
        headers: { Authorization: `Bearer ${employee.token}` },
      })
    ).status(),
  ).toBe(404);
});
