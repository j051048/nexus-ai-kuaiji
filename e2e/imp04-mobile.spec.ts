import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);

test('IMP04 mobile intake shows amount and date in the first viewport after opening', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  await page.getByRole('button', { name: /提交报销/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: /选择附件/ })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '提交审批' })).toBeDisabled();
  await page.screenshot({ path: 'tmp_test/imp/imp-20261106-a/imp04-intake-after.png', fullPage: false });
  const amount = await dialog.getByLabel('事项金额').boundingBox();
  const date = await dialog.getByRole('textbox', { name: '发生日期', exact: true }).boundingBox();
  const footer = await dialog.locator('.intake-footer').boundingBox();
  const metrics = await page.evaluate(() => {
    const field = document.querySelector('.intake-fields') as HTMLElement | null;
    const step = document.querySelector('.intake-current-step') as HTMLElement | null;
    const upload = document.querySelector('.intake-upload') as HTMLElement | null;
    return {
      fieldsTop: field?.getBoundingClientRect().top ?? null,
      stepHeight: step?.getBoundingClientRect().height ?? null,
      uploadHeight: upload?.getBoundingClientRect().height ?? null,
      overflow: document.documentElement.scrollWidth - innerWidth,
    };
  });
  expect(amount?.y ?? 9999).toBeLessThan(760);
  expect((amount?.y ?? 9999) + (amount?.height ?? 0)).toBeLessThan(footer?.y ?? 0);
  expect(date?.y ?? 9999).toBeLessThan(844);
  expect(metrics.fieldsTop ?? 9999).toBeLessThan(700);
  expect(metrics.overflow).toBeLessThanOrEqual(0);
});

for (const width of [375, 1366]) {
  test(`IMP04 ${width}px keeps amount/date and attachment action usable`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1366 ? 900 : 812 });
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await choosePersona(page, 'employee');
    await page.getByRole('button', { name: /提交报销/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('事项金额')).toBeVisible();
    await expect(dialog.getByRole('textbox', { name: '发生日期', exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: /选择附件/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
