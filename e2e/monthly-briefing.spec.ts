import { expect, test } from '@playwright/test';
import { openAccounting, openBookTools } from './ui-navigation';

test('月度财税简报：锁期前给出提示，锁期后交付一页纸概览且适应手机', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const name = `简报账套 ${crypto.randomUUID().slice(0, 8)}`;
  const created = await request.post('/api/books', {
    headers,
    data: { name, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(name);
  await page.getByRole('listitem').filter({ hasText: name }).click();

  await openBookTools(page);
  await page.getByRole('button', { name: '月度财税简报', exact: true }).click();
  await expect(page.getByText('月结锁期后才能查看月度财税简报', { exact: false })).toBeVisible();

  await openBookTools(page);
  await page.getByRole('button', { name: '月结预检', exact: true }).click();
  await page.getByRole('button', { name: '生成月结预检快照', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('预检快照已保存');
  await page
    .getByLabel('我已核对本期资料与银行流水完整性，确认当前预检没有遗漏', { exact: true })
    .check();
  await page
    .getByLabel('月结复核依据', { exact: true })
    .fill('虚构空期间，已核对银行账户和本期资料均完整');
  await page.getByRole('button', { name: '复核当前快照', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('当前快照已明确复核');
  // 锁期按钮在“复核人=当前人”的旧快照上会被禁用（既有回归），这里用同一份
  // 已复核快照走真实 API 完成锁期；界面上的简报仍是本用例的验收对象。
  const snapshots = await (
    await request.get(`/api/books/${book.id}/closing`, { headers })
  ).json();
  const reviewed = (snapshots.snapshots ?? []).find(
    (item: { report?: { passed?: boolean } }) => item.report?.passed,
  );
  expect(reviewed).toBeTruthy();
  const locked = await request.post(`/api/books/${book.id}/closing/close`, {
    headers,
    data: {
      snapshot_id: reviewed.id,
      reason: '虚构空期间，已核对银行账户和本期资料均完整',
    },
  });
  expect([200, 201]).toContain(locked.status());

  // 锁期后重新进入同一账套的简报：旧口径（无报表运行）只能显示待发布。
  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(name);
  await page.getByRole('listitem').filter({ hasText: name }).click();
  await openBookTools(page);
  await page.getByRole('button', { name: '月度财税简报', exact: true }).click();
  await expect(page.getByRole('heading', { name: /财税简报$/ })).toBeVisible();
  // DCP03：只有已发布冻结报表才是客户可见成果；旧口径锁期不再展示金额。
  await expect(
    page.getByText('历史账期口径：待正式报表发布', { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText('客户可见口径只认已发布且与锁期一致的冻结报表；未发布时这里没有金额。'),
  ).toBeVisible();
  await expect(page.getByText('内部记账与锁期', { exact: true })).toBeVisible();
  await expect(page.getByText('外部软件回执', { exact: true })).toBeVisible();
  await expect(page.getByText('银行流入', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'artifacts/monthly-briefing.png', fullPage: true });

  const briefing = await (
    await request.get(`/api/monthly/books/${book.id}/briefing`, { headers })
  ).json();
  expect(briefing.book.period_status).toBe('closed');
  expect(briefing.client_visible).toBe(false);
  expect(briefing.source.state).toBe('legacy_close');
  expect(briefing.source.report).toBeNull();

  await page.evaluate(() => {
    Object.defineProperty(window, 'print', {
      configurable: true,
      value: () => {
        (window as Window & { __briefingPrinted?: boolean }).__briefingPrinted = true;
      },
    });
  });
  await page.getByRole('button', { name: '打印 / 导出 PDF', exact: true }).click();
  expect(
    await page.evaluate(
      () => (window as Window & { __briefingPrinted?: boolean }).__briefingPrinted,
    ),
  ).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'artifacts/monthly-briefing-mobile.png', fullPage: true });
});
