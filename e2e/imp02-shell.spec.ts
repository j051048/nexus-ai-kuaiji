import { expect, test, type Page } from '@playwright/test';

const BASE = process.env.IMP02_BASE_URL || 'http://127.0.0.1:8082';

async function switchRole(page: Page, role: string) {
  await page.locator('select[aria-label="切换验证身份"]').evaluate((el, value) => {
    const select = el as HTMLSelectElement;
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }, role);
  await page.waitForTimeout(1600);
}

for (const viewport of [
  { name: 'desktop-1366', width: 1366, height: 768 },
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'mobile-390', width: 390, height: 844 },
  { name: 'mobile-375', width: 375, height: 812 },
]) {
  test.describe(`IMP02 shell ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('roles keep unit and identity visible without decorative duplicate headings', async ({
      page,
    }) => {
      await page.goto(BASE, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2200);
      for (const role of ['employee', 'department', 'finance', 'accountant', 'reviewer']) {
        await switchRole(page, role);
        const identity = page.locator('.account-identity');
        await expect(identity).toBeVisible();
        await expect(identity).toContainText(/所属事务所|所属企业/);
        await expect(identity).toContainText(/权限与范围/);
        const h1 = page.locator('h1').first();
        await expect(h1).toBeVisible();
        await expect(page.locator('body')).not.toContainText('HENGZHANG · ACCOUNTING AGENT');
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        expect(overflow).toBeLessThanOrEqual(0);
        const actionable = page.locator('main button:not([disabled]), main a[href]').first();
        await expect(actionable).toBeAttached();
      }
    });

    test('expanding identity is keyboard native and never writes', async ({ page }) => {
      await page.goto(BASE, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2200);
      let writes = 0;
      page.on('request', (request) => {
        if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) writes += 1;
      });
      const summary = page.locator('.account-identity summary').first();
      await summary.focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('.account-identity details')).toHaveAttribute('open', '');
      await expect(page.locator('.account-identity-details')).toContainText('授权范围');
      expect(writes).toBe(0);
    });
  });
}
