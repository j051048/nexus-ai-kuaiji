import { expect, test } from '@playwright/test';
import { openNavigation } from './ui-navigation';

test.setTimeout(90_000);

test('批量开期为既有客户创建当月账套、逐户报告并保持幂等', async ({ page, request }) => {
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` };
  const tag = crypto.randomUUID().slice(0, 8);
  for (const suffix of ['甲', '乙']) {
    const company = await (
      await request.post('/api/companies', {
        headers,
        data: { name: `批量开期${suffix}${tag}`, tax_id: '' },
      })
    ).json();
    const book = await request.post('/api/books', {
      headers,
      data: { name: `批量开期${suffix}${tag} 2026-10`, company_id: company.id, period: '2026-10' },
    });
    expect(book.status()).toBe(201);
  }
  await page.goto('/');
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('button', { name: '月度交付', exact: true })
    .click();
  await page.getByLabel('客户进度月份').fill('2026-11');
  await page.getByLabel('搜索月度客户').fill(`批量开期`);
  await page.getByRole('button', { name: '查找', exact: true }).click();
  await expect(page.getByText(`批量开期甲${tag}`)).toBeVisible();

  await page.getByRole('button', { name: '批量开期', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '月初批量开期', exact: true });
  await dialog.getByLabel('批量开期搜索范围').fill(`批量开期`);
  await dialog.getByLabel('批量开期操作依据').fill('合成验证：月初批量开期两家客户');
  // Execution stays disabled until the read-only precheck has been seen.
  const runButton = dialog.getByRole('button', { name: '开始批量开期', exact: true });
  await expect(runButton).toBeDisabled();
  await dialog.getByRole('button', { name: '预检范围', exact: true }).click();
  await expect(
    dialog.getByText('将开期 2 家；跳过 0 家、阻塞 0 家', { exact: false }),
  ).toBeVisible();
  await expect(runButton).toBeEnabled();
  await runButton.click();
  await expect(dialog.getByText('已开期 2 家')).toBeVisible({ timeout: 30_000 });
  await expect(dialog.getByText(`批量开期甲${tag}`)).toBeVisible();
  await expect(
    dialog.getByText('长期账套尚未建立资料模板', { exact: false }).first(),
  ).toBeVisible();
  await dialog.screenshot({ path: 'artifacts/monthly-batch-dialog.png' });
  await dialog.getByRole('button', { name: '完成批量开期', exact: true }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByLabel('搜索月度客户').fill(`批量开期甲${tag}`);
  await page.getByRole('button', { name: '查找', exact: true }).click();
  await expect(page.getByText(`批量开期甲${tag} 2026-11`)).toBeVisible();

  const listed = await (
    await request.get(
      `/api/monthly/workbench?period=2026-11&search=${encodeURIComponent('批量开期')}&limit=20&offset=0`,
      { headers },
    )
  ).json();
  const ours = listed.rows.filter((row: { company_name: string }) =>
    row.company_name.startsWith('批量开期'),
  );
  expect(ours).toHaveLength(2);
  for (const row of ours) {
    expect(row.status).toBe('open');
    expect(row.book_name).toContain('2026-11');
  }
  await page.screenshot({ path: 'artifacts/monthly-batch-opened.png', fullPage: true });
});
