import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { choosePersona, openNavigation } from './ui-navigation';

for (const persona of ['employee', 'finance', 'accountant']) {
  test(`QA 岗位首屏与键盘可达性 ${persona}`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('/workspace/home');
    await choosePersona(page, persona);
    await expect(
      page.getByRole('region', { name: '当前职责与授权范围', exact: true }),
    ).toBeVisible();
    const scan = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
      .analyze();
    await info.attach('axe-results', {
      body: JSON.stringify(scan.violations, null, 2),
      contentType: 'application/json',
    });
    expect(scan.violations).toEqual([]);
    if (persona === 'accountant' && (page.viewportSize()?.width ?? 1280) <= 740) {
      await openNavigation(page);
      await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toBeVisible();
      const navigationScan = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
        .analyze();
      expect(navigationScan.violations).toEqual([]);
      await page.getByRole('button', { name: '关闭导航', exact: true }).click();
      await expect(
        page.getByRole('button', { name: '打开工作空间导航', exact: true }),
      ).toBeFocused();
    } else {
      await expect(page.getByRole('navigation').first()).toBeVisible();
    }
    // Verify actual keyboard operation instead of merely counting tabindexes.
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => document.activeElement !== document.body)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath(`${persona}.png`), fullPage: true });
  });
}
