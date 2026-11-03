import { chromium, type FullConfig } from '@playwright/test';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Dev-mode only: pay Vite's cold transform cost once, before the frozen cases.
 *
 * The development server compiles modules on demand, so the first navigation
 * of a run can take far longer than the 30s a case is allowed to spend on it.
 * That cost is build infrastructure, not business behaviour: the preview run
 * (prebuilt assets) exercises the same cases without it. Loading the entry
 * and existing lazy component modules keeps each case's budget for behaviour,
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
    // Import actual lazy modules without mounting them or invoking their APIs.
    // Merely warming the entry leaves first-use route transforms in the 5s UI
    // expectation window (notably the large legacy-migration/review modules).
    const src = resolve('src');
    const modules = new Set<string>();
    for (const name of readdirSync(src).filter(
      (name) => /\.tsx?$/.test(name) && !name.includes('.test.'),
    )) {
      for (const match of readFileSync(resolve(src, name), 'utf8').matchAll(
        /\bimport\(['"]\.\/([^'"]+)['"]\)/g,
      )) {
        if (existsSync(resolve(src, `${match[1]}.tsx`))) modules.add(`/src/${match[1]}.tsx`);
      }
    }
    await page.evaluate(
      async (paths) => {
        let timeout: ReturnType<typeof setTimeout> | undefined;
        try {
          await Promise.race([
            (async () => {
              for (let start = 0; start < paths.length; start += 4) {
                await Promise.all(paths.slice(start, start + 4).map((path) => import(path)));
              }
            })(),
            new Promise((_, reject) => {
              timeout = setTimeout(
                () => reject(new Error('Dev module warm-up exceeded 180s')),
                180_000,
              );
            }),
          ]);
        } finally {
          clearTimeout(timeout);
        }
      },
      [...modules].sort(),
    );
    console.log(
      `[e2e-warmup] entry and ${modules.size} lazy modules ready in ${Date.now() - started}ms`,
    );
  } finally {
    await browser.close();
  }
}
