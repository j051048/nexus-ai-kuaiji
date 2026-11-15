import { test, expect } from '@playwright/test';

test('L05 reduced motion suppresses global animation and transition duration', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const durations = await page.evaluate(() => {
    const node = document.createElement('div');
    node.style.animation = 'spin 1s linear infinite';
    node.style.transition = 'opacity 1s ease';
    document.body.append(node);
    const style = getComputedStyle(node);
    const result = {
      animation: style.animationDuration,
      transition: style.transitionDuration,
      scroll: getComputedStyle(document.documentElement).scrollBehavior,
    };
    node.remove();
    return result;
  });
  expect(['0.01ms', '1e-05s']).toContain(durations.animation);
  expect(['0.01ms', '1e-05s']).toContain(durations.transition);
  expect(durations.scroll).toBe('auto');
});
