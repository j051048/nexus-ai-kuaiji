import { describe, expect, it } from 'vitest';
import { isCompositionKey } from './keyboard';

describe('isCompositionKey', () => {
  it('detects native IME composition', () => {
    expect(isCompositionKey({ nativeEvent: { isComposing: true }, keyCode: 13 })).toBe(true);
  });

  it('detects the legacy IME keyCode and keeps plain Enter usable', () => {
    expect(isCompositionKey({ nativeEvent: { isComposing: false }, keyCode: 229 })).toBe(true);
    expect(isCompositionKey({ nativeEvent: { isComposing: false }, keyCode: 13 })).toBe(false);
  });
});
