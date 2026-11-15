import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

type FormalFixture = Awaited<ReturnType<typeof taxFullFixture>>;

const file = readFileSync('e2e/fixtures/tax06-fee.xlsx').toString('base64');

async function prepare(request: Parameters<typeof taxFullFixture>[0], f: FormalFixture) {
  await checked(
    await request.post('/api/opening/accounts', {
      headers: f.headers,
      data: {
        ledger_id: f.ledger.id,
        code: '1122',
        name: '虚构驾驶员应收',
        category: 'asset',
        normal_balance: 'debit',
        effective_from: '2026-01',
        effective_to: '2026-12',
        reason: '规费批次专用应收科目',
      },
    }),
  );
  const subjects: Record<string, { id: string }> = {};
  for (const [kind, external, name] of [
    ['driver', 'D1', '张师傅'],
    ['driver', 'D2', '李师傅'],
    ['vehicle', 'V1', '京A1'],
    ['vehicle', 'V2', '京A2'],
  ]) {
    const created = await checked(
      await request.post(`/api/books/${f.book.id}/ledger/transport/subjects`, {
        headers: f.headers,
        data: { kind, external_id: external, display_name: name },
      }),
    );
    subjects[external] = created;
  }
  for (const [driver, vehicle] of [
    ['D1', 'V1'],
    ['D2', 'V2'],
  ]) {
    await checked(
      await request.post(`/api/books/${f.book.id}/ledger/transport/assignments`, {
        headers: f.headers,
        data: {
          vehicle_id: subjects[vehicle].id,
          driver_id: subjects[driver].id,
          shift: 'full',
          contract_external_id: 'C1',
          valid_from: '2026-10-01',
          valid_to: '2026-10-03',
        },
      }),
    );
  }
  await checked(
    await request.post(`/api/tax/books/${f.book.id}/sources`, {
      headers: f.headers,
      data: {
        filename: '规费.xlsx',
        content_base64: file,
        source_system: '客户运输台账',
        external_id: 'S-FEE-UI',
        version: 1,
        sheet_name: '运输明细',
        rule_version: 'tax-source-v1',
        column_map: {
          external_id: '行ID',
          date: '日期',
          description: '摘要',
          amount: '金额',
          quantity: '数量',
          target_id: '目标',
          driver_external_id: '驾驶员编号',
          vehicle_external_id: '车辆编号',
          discount: '优惠减免',
          adjustment: '调整',
          adjustment_reason: '调整原因',
        },
      },
    }),
  );
}

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`TAX06 manager generates balanced fee batch and independent reviewer freezes it ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    await prepare(request, f);
    await page.setViewportSize(viewport);
    await openTax(page, f);
    const panel = page.locator('section[aria-labelledby="tax-fee-title"]');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByLabel(/规费\.xlsx/).check();
    await panel.getByRole('button', { name: '生成规费批次草稿' }).click();
    await expect(
      panel.getByText('规费批次草稿已生成；须由另一名有复核权的会计独立复核。'),
    ).toBeVisible();
    await expect(panel.getByText('¥175.00', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('2 / 0', { exact: true })).toBeVisible();
    await expect(panel.getByText('按驾驶员')).toBeVisible();
    await expect(panel.getByText('张师傅 · 1 行 · ¥90.00')).toBeVisible();
    await expect(panel.getByText('按车辆')).toBeVisible();
    await expect(panel.locator('tbody tr').filter({ hasText: 'L1' })).toBeVisible();
    await expect(panel.locator('tbody tr').filter({ hasText: 'L2' })).toBeVisible();
    await panel.getByRole('button', { name: '独立复核并冻结' }).click();
    await expect(panel.getByRole('alert')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );

    const batches = await checked(
      await request.get(`/api/tax/books/${f.book.id}/fee-batches`, { headers: f.headers }),
      200,
    );
    const draft = batches.items[0];
    await checked(
      await request.post(`/api/tax/books/${f.book.id}/fee-batches/${draft.id}/review`, {
        headers: f.reviewer,
        data: { expected_version: draft.version, reason: '浏览器验收独立复核' },
      }),
      200,
    );
    await panel.getByRole('button', { name: '刷新' }).click();
    await panel.getByText(/历史批次/).click();
    await panel.getByRole('button', { name: /2026-10 · v2 · 已复核/ }).click();
    await expect(panel.getByText('已复核', { exact: true })).toBeVisible();
    await page.screenshot({ path: `tmp_test/tax06-fees-${viewport.width}.png`, fullPage: true });
  });
}
