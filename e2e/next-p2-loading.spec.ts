import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { openAccounting, returnWorkspaceHome } from './ui-navigation';

test.setTimeout(90_000);

for (const width of [1280, 390]) {
  test(`P2 首页按需加载且账套深链接可用 ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    const requests: string[] = [];
    page.on('request', (request) => requests.push(new URL(request.url()).pathname));
    const started = Date.now();
    await page.goto('/workspace/home');
    await expect(page.getByLabel('我的工作概览')).toBeVisible();
    await expect(page.getByRole('button', { name: '刷新工作台', exact: true })).toBeEnabled();
    expect(requests.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
    expect(requests).not.toContain('/api/workbench');
    const bookAssets = () => requests.filter((path) => /\/BookWorkspaceShell(?:[.-])/.test(path));
    expect(bookAssets()).toHaveLength(0);
    expect(
      requests.some((path) => /\/(AdminWorkspace|LegacyMigrationWorkspace)(?:[.-])/.test(path)),
    ).toBe(false);
    const metrics = await page.evaluate(() => ({
      paint: performance
        .getEntriesByType('paint')
        .map((entry) => ({ name: entry.name, ms: entry.startTime })),
      resources: performance.getEntriesByType('resource').map((entry) => {
        const resource = entry as PerformanceResourceTiming;
        return {
          path: new URL(resource.name).pathname,
          bytes: resource.decodedBodySize,
          durationMs: resource.duration,
        };
      }),
    }));
    const metricsPath = testInfo.outputPath('home-metrics.json');
    writeFileSync(
      metricsPath,
      JSON.stringify({ width, readyMs: Date.now() - started, requests, ...metrics }, null, 2),
    );
    await testInfo.attach('home-metrics', { path: metricsPath, contentType: 'application/json' });
    await page.screenshot({ path: testInfo.outputPath(`home-${width}.png`), fullPage: true });
    await openAccounting(page);
    await expect(page.getByRole('list', { name: '客户企业', exact: true })).toBeVisible();
    expect(bookAssets().filter((path) => /\.(js|tsx)$/.test(path))).toHaveLength(1);
    await expect
      .poll(() => requests.filter((path) => /^\/api\/books\/[^/]+$/.test(path)).length)
      .toBeGreaterThan(0);
    await expect(page.getByText('正在核对当前凭证…', { exact: true })).toHaveCount(0);
    await expect(page.getByText('正在读取分录任务…', { exact: true })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`accounting-${width}.png`), fullPage: true });
    const deepLink = page.url();
    await returnWorkspaceHome(page);
    await expect(page.getByLabel('我的工作概览')).toBeVisible();
    await page.goto(deepLink);
    await expect(page.getByRole('list', { name: '客户企业', exact: true })).toBeVisible();
  });
}
