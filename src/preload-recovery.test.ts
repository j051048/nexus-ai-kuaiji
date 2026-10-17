import { describe, expect, it } from 'vitest';
import { isChunkLoadError } from './preload-recovery';

describe('deployment resource recovery', () => {
  it.each([
    'Unable to preload CSS for /assets/monthly-old.css',
    'Failed to fetch dynamically imported module: /assets/Monthly-old.js',
    'Importing a module script failed.',
    'Loading chunk 42 failed.',
  ])('recognizes stale deployment errors: %s', (message) => {
    expect(isChunkLoadError(new Error(message))).toBe(true);
  });

  it('does not reload for ordinary business or validation errors', () => {
    expect(isChunkLoadError(new Error('凭证已经变化，请刷新后重试'))).toBe(false);
    expect(isChunkLoadError('network timeout')).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });
});
