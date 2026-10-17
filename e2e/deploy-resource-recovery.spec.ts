import { expect, test } from '@playwright/test';

test('部署更换静态资源后自动无缓存重载一次', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#root')).toBeVisible();
  const recoveryRequest = page.waitForRequest(
    (request) => request.isNavigationRequest() && request.url().includes('_deploy_reload='),
    { timeout: 15_000 },
  );
  await page.evaluate(() => {
    window.dispatchEvent(new Event('vite:preloadError'));
  });
  await recoveryRequest;
  await expect.poll(() => page.url()).not.toContain('_deploy_reload=');
  await expect(page.locator('#root')).toBeVisible();
});
