import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { choosePersona } from './ui-navigation';

const personas = ['accountant', 'employee', 'department', 'finance', 'reviewer'] as const;
const widths = [1366, 1920, 390, 375, 683] as const;

for (const persona of personas) {
  test(`I 壳层 ${persona} 首屏、身份与窄屏`, async ({ page }) => {
    test.setTimeout(120_000);
    mkdirSync('tmp_test/tax-i', { recursive: true });
    for (const width of widths) {
      await page.setViewportSize({ width, height: width <= 390 ? 844 : 768 });
      await page.goto('/workspace/home');
      await choosePersona(page, persona);
      const heading = page.locator('h1:visible').first();
      await expect(heading).toBeVisible();
      await expect(page.locator('h1:visible')).toHaveCount(1);
      await expect(page.getByRole('region', { name: /当前职责与授权范围|当前身份/ }).first()).toBeVisible();
      const identity = page.getByText(/当前身份|所属企业|所属事务所/).first();
      await expect(identity).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        `${persona} ${width}px horizontal overflow`,
      ).toBe(true);
      if (width === 1366 || width === 390 || width === 683) {
        await page.screenshot({
          path: `tmp_test/tax-i/${persona}-${width}.png`,
          fullPage: true,
        });
        const action = page
          .locator(
            persona === 'employee'
              ? '.workspace-primary-intake'
              : persona === 'finance'
                ? '.workspace-primary-finance'
                : '.workspace-inbox, .workspace-summary',
          )
          .first();
        const box = await action.boundingBox();
        expect(box?.y ?? 9999, `${persona} ${width}px primary action above fold`).toBeLessThan(
          width === 1366 ? 768 : 844,
        );
      }
    }
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/workspace/home');
    await choosePersona(page, persona);
    const inboxBox = await page.locator('.workspace-inbox').boundingBox();
    const guideBox = await page.locator('.workspace-role-guide').boundingBox();
    expect(guideBox?.y ?? 0).toBeGreaterThan(inboxBox?.y ?? 0);
    const mainAction = page
      .locator(
        persona === 'employee'
          ? '.workspace-primary-intake'
          : persona === 'finance'
            ? '.workspace-primary-finance'
            : '.workspace-inbox, .workspace-summary',
      )
      .first();
    await expect(mainAction).toBeVisible();
    const box = await mainAction.boundingBox();
    expect(box?.y ?? 9999).toBeLessThan(768);
  });
}
