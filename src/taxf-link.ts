const LINE = /^[A-Za-z0-9_.-]{1,32}$/;

export type TaxFLink = { line: string; offset: number };

/**
 * Read the drilldown selection out of a query string.
 *
 * Only a whitelisted line code and a small offset are accepted: the value is
 * echoed back to the API, so anything else is dropped rather than forwarded.
 */
export function parseTaxFLink(search: string): TaxFLink | null {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const line = (params.get('taxf_line') ?? '').trim();
  if (!LINE.test(line)) return null;
  const raw = (params.get('taxf_offset') ?? '0').trim();
  const offset = /^\d{1,6}$/.test(raw) ? Number(raw) : 0;
  return { line, offset: offset >= 0 ? offset : 0 };
}

/** Build a shareable URL for the current drilldown without touching history. */
export function taxFLinkUrl(href: string, line: string, offset = 0): string {
  if (!LINE.test(line)) return href;
  const [base, hash = ''] = href.split('#');
  const [path, query = ''] = base.split('?');
  const params = new URLSearchParams(query);
  params.set('taxf_line', line);
  params.set('taxf_offset', String(Math.max(0, Math.trunc(offset))));
  const next = `${path}?${params.toString()}`;
  return hash ? `${next}#${hash}` : next;
}
