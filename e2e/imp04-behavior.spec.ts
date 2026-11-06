import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);

async function openIntake(page: import('@playwright/test').Page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  await page.getByRole('button', { name: /提交报销/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  return dialog;
}

test('IMP04 changing a field invalidates the prior confirmation', async ({ page }) => {
  const dialog = await openIntake(page);
  await dialog.getByLabel('事项金额').fill('128.50');
  const confirm = dialog.getByRole('checkbox');
  await confirm.check();
  await expect(confirm).toBeChecked();
  await dialog.getByLabel('事项金额').fill('129.00');
  await expect(confirm).not.toBeChecked();
});

test('IMP04 focused amount remains above the sticky footer in a short viewport', async ({ page }) => {
  const dialog = await openIntake(page);
  await dialog.getByLabel('事项金额').fill('128.50');
  await page.setViewportSize({ width: 390, height: 560 });
  await dialog.getByLabel('事项金额').scrollIntoViewIfNeeded();
  await dialog.getByLabel('事项金额').click();
  await page.waitForTimeout(250);
  const amount = await dialog.getByLabel('事项金额').boundingBox();
  const footer = await dialog.locator('.intake-footer').boundingBox();
  expect(amount).not.toBeNull();
  expect(footer).not.toBeNull();
  expect((amount?.y ?? 9999) + (amount?.height ?? 0)).toBeLessThan(footer?.y ?? 0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('IMP04 employee with several companies must choose one explicitly', async ({ page }) => {
  const dialog = await openIntake(page);
  const company = dialog.getByRole('combobox', { name: '事项客户企业' });
  await expect(company).toHaveValue('');
  await expect(company.getByRole('option', { name: '请选择客户企业' })).toHaveCount(1);
  await expect(dialog.getByRole('button', { name: '提交审批' })).toBeDisabled();
});
