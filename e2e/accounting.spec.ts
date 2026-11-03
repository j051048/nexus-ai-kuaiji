import { independentReviewer, pinReviewer } from './independent-review';
import { confirmStatement, openAccounting, previewPastedStatement } from './ui-navigation';
import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const csv =
  'external_id,date,description,counterparty,amount,direction,evidence_ref\nqa-bank:202610:001,2026-10-10,办公用品采购,端到端虚构商店,268.10,expense,\n';

test('导入去重、补证、修改复核、冻结和真实 CSV 下载', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await openAccounting(page);
  await expect(page.getByText('本地验证环境', { exact: false })).toBeVisible();
  await expect(page.getByRole('listitem').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/accounting-workbench.png', fullPage: true });
  await page.getByRole('button', { name: '新建账套', exact: true }).click();
  await page.getByLabel('企业名称', { exact: true }).fill('端到端验证企业');
  await page.getByLabel('会计期间').fill('2026-10');
  await page.getByRole('button', { name: '创建账套', exact: true }).click();
  await expect(page.getByRole('heading', { name: '端到端验证企业', exact: true })).toBeVisible();

  for (const expected of ['成功导入 1 条流水', '成功导入 0 条流水，跳过 1 条重复记录']) {
    await previewPastedStatement(page, csv);
    await confirmStatement(page);
    await expect(page.getByRole('status')).toContainText(expected);
  }
  await previewPastedStatement(page, csv.replace('268.10', '268.11'));
  await expect(page.getByRole('dialog').locator('.import-wizard-row.invalid')).toContainText(
    '同一交易号对应不同内容，整批禁止确认',
  );
  await expect(
    page.getByRole('dialog').getByRole('button', { name: '确认导入', exact: true }),
  ).toBeDisabled();
  await page.getByRole('button', { name: '关闭导入向导', exact: true }).click();
  await page.getByRole('button', { name: '生成记账建议', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已生成 1 张凭证草稿');
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await page.getByLabel('补充凭据引用').fill('QA-虚构发票-001');
  await page.getByRole('button', { name: '补充', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('凭据引用已补充');

  await page.getByLabel('凭证摘要', { exact: true }).fill('办公用品采购（已核对）');
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('草稿已保存');
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const current = new URL(page.url());
  await independentReviewer(
    request,
    { Authorization: `Bearer ${owner.token}` },
    current.searchParams.get('book')!,
  );
  await pinReviewer(page);
  await page.reload();
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('凭证已审核');
  await expect(page.getByLabel('凭证摘要', { exact: true })).toBeDisabled();

  await page.getByLabel('选择全部可导出凭证').check();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出凭证 CSV', exact: true }).click();
  const download = await downloadPromise;
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  const content = await readFile(downloadedPath!, 'utf8');
  expect(content).toContain('办公用品采购（已核对）');
  expect(content).toContain('268.10');
  expect(content).toContain('QA-虚构发票-001');
  expect(content.trim().split('\r\n')).toHaveLength(3);
  await expect(page.getByRole('status')).toContainText('已导出 1 张凭证');
  await expect(page.getByRole('button', { name: '导出凭证 CSV', exact: true })).toBeDisabled();
  await page.reload();
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill('端到端验证企业');
  await page.getByRole('listitem').filter({ hasText: '端到端验证企业' }).click();
  await expect(page.getByText('此凭证已导出，修改入口已锁定。', { exact: false })).toBeVisible();
  await page.getByRole('tab', { name: /导出记录/ }).click();
  const historyDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: /下载/ }).last().click();
  const restored = await historyDownload;
  expect(await readFile((await restored.path())!, 'utf8')).toBe(content);
});

test('统一待办直接打开异常凭证，回到账套清空搜索后可以恢复', async ({ page }) => {
  await page.goto('/workspace/home');
  await page.getByLabel('筛选待办类型').selectOption('review');
  await page.getByRole('button', { name: '筛选待办企业', exact: true }).click();
  const companyPicker = page.getByRole('region', { name: '企业快搜', exact: true });
  await companyPicker.getByLabel('搜索企业名称', { exact: true }).fill('禾田商贸');
  await companyPicker.getByRole('button').filter({ hasText: '禾田商贸（虚构示例）' }).click();
  await page
    .locator('.workspace-task')
    .filter({ hasText: '禾田商贸' })
    .first()
    .getByRole('button', { name: '同屏复核', exact: true })
    .click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByLabel('同屏待处理问题')).toBeVisible();
  await drawer.getByRole('button', { name: '关闭同屏复核', exact: true }).click();
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill('不存在的企业 QA');
  await expect(page.getByText('没有符合条件的企业')).toBeVisible();
  await page.getByRole('button', { name: '清空搜索', exact: true }).click();
  await expect(page.getByRole('listitem').filter({ hasText: '禾田商贸' })).toBeVisible();
});

test('窄屏工作台可读且页面没有横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await openAccounting(page);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('listitem').first()).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBe(false);
});
