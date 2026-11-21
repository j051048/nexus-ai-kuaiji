import { describe, expect, it } from 'vitest';
import { parseTaxFLink, taxFLinkUrl } from './taxf-link';

describe('taxf deep link', () => {
  it('parses a whitelisted line and offset', () => {
    expect(parseTaxFLink('?taxf_line=6001&taxf_offset=40')).toEqual({
      line: '6001',
      offset: 40,
    });
  });

  it('drops anything that is not a plain line code', () => {
    expect(parseTaxFLink('?taxf_line=%3Cscript%3E')).toBeNull();
    expect(parseTaxFLink('?taxf_line=')).toBeNull();
    expect(parseTaxFLink('')).toBeNull();
  });

  it('falls back to the first page when the offset is unusable', () => {
    expect(parseTaxFLink('?taxf_line=6001&taxf_offset=-5')?.offset).toBe(0);
    expect(parseTaxFLink('?taxf_line=6001&taxf_offset=abc')?.offset).toBe(0);
  });

  it('adds the selection to an existing query and keeps the fragment', () => {
    expect(taxFLinkUrl('/workspace/tax-f?period=2026-10#profit', '6001', 20)).toBe(
      '/workspace/tax-f?period=2026-10&taxf_line=6001&taxf_offset=20#profit',
    );
  });

  it('refuses to build a link for an invalid line', () => {
    expect(taxFLinkUrl('/workspace/tax-f', 'a b')).toBe('/workspace/tax-f');
  });
});
