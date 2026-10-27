import { chromium, type FullConfig } from '@playwright/test';

/**
 * Dev-mode only: pay Vite's cold transform cost once, before the frozen cases.
 *
 * The development server compiles modules on demand, so the first navigation
 * of a run can take far longer than the 30s a case is allowed to spend on it.
 * That cost is build infrastructure, not business behaviour: the preview run
 * (prebuilt assets) exercises the same cases without it. Loading the entry
 * bundle once here keeps every case's own 30s budget for product behaviour,
 * and the timeout below is not a case threshold.
 */
export default async function warmUpDevServer(config: FullConfig) {
  const baseURL = config.projects[0]?.use?.baseURL;
  if (!baseURL) throw new Error('warm-up needs use.baseURL');
  const started = Date.now();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(baseURL, { waitUntil: 'domcontentloaded', timeout: 180_000 });
    await page.waitForFunction(
      () => {
        const root = document.getElementById('root');
        return !!root && root.children.length > 0;
      },
      undefined,
      { timeout: 180_000 },
    );
    console.log(`[e2e-warmup] dev entry bundle ready in ${Date.now() - started}ms`);
  } finally {
    await browser.close();
  }
}
