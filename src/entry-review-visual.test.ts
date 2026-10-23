/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import postcss from 'postcss';
import { describe, expect, it, vi } from 'vitest';
import { EvidenceViewer, type ReviewAttachment } from './EvidenceViewer';
import { JournalEntryGrid } from './JournalEntryGrid';
import { InlineReviewDrawer } from './InlineReviewDrawer';
import type { Session } from './types';

const css = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8');
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构所',
  user_name: '虚构会计',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'reviewer',
  scope_key: 'offline-a',
};
const attachment: ReviewAttachment = {
  id: 'attachment-a',
  filename: '虚构企业采购发票与补充业务证明-原件.png',
  media_type: 'image/png',
  sha256: 'a'.repeat(64),
  size_bytes: 12345,
  contentPath: '/business/attachments/attachment-a/content',
};

describe('V03 同屏复核与分录展示', () => {
  it('offers one primary approval action without removing queue navigation or explicit approval', () => {
    vi.stubGlobal('window', { matchMedia: () => ({ matches: false }) });
    try {
      const html = renderToStaticMarkup(
        createElement(InlineReviewDrawer, {
          session,
          bookId: 'book-a',
          draftId: 'draft-a',
          onClose: () => {},
          queue: [
            { book_id: 'book-a', draft_id: 'draft-a' },
            { book_id: 'book-a', draft_id: 'draft-b' },
          ],
        }),
      );
      expect(html.match(/class="button primary"/g)).toHaveLength(1);
      expect(html).toContain('确认审核');
      expect(html).toContain('审核并下一笔');
      expect(html).not.toContain('导出凭证');
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it.each(['inline-review.css', 'evidence-viewer.css'])(
    '%s uses readable root tokens and no tiny or local palette',
    (name) => {
      const text = css(name);
      expect(
        [...text.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)]
          .map((match) => Number(match[1]))
          .filter((size) => size < 12),
      ).toEqual([]);
      expect(text).not.toMatch(/#[\da-f]{3,8}\b/i);
      expect(text).not.toMatch(/--(?:surface|line|text-muted|brand):/);
    },
  );

  it('keeps mobile fact inputs and navigation readable and touchable', () => {
    const root = postcss.parse(css('inline-review.css'));
    const mobile = (selector: string, property: string) => {
      const values: string[] = [];
      root.walkRules((rule) => {
        if (
          rule.selectors.includes(selector) &&
          rule.parent?.type === 'atrule' &&
          /max-width:\s*700px/.test(rule.parent.params)
        )
          rule.walkDecls(property, (decl) => {
            values.push(decl.value);
          });
      });
      return values;
    };
    expect(mobile('.inline-review-label textarea', 'font-size')).toContain(
      'var(--font-size-input-mobile)',
    );
    expect(mobile('.inline-review-pagination .button', 'min-height')).toContain(
      'var(--control-height-touch)',
    );
    expect(mobile('.inline-review-header-actions .icon-button', 'min-width')).toContain(
      'var(--control-height-touch)',
    );
  });

  it('shows the complete authorized attachment name and keeps text references distinct from previewed originals', () => {
    const withFile = renderToStaticMarkup(
      createElement(EvidenceViewer, {
        session,
        attachments: [attachment],
        reference: '归档编号 A',
      }),
    );
    expect(withFile).toMatch(
      /class="inline-file-name"[^>]*>虚构企业采购发票与补充业务证明-原件.png/,
    );
    expect(withFile).toContain('选择复核原件');
    const textOnly = renderToStaticMarkup(
      createElement(EvidenceViewer, {
        session,
        attachments: [],
        reference: '<script>虚构资料引用</script>',
      }),
    );
    expect(textOnly).toContain('当前来源只有文字引用');
    expect(textOnly).toContain('&lt;script&gt;');
    expect(textOnly).not.toContain('<img');
    expect(textOnly).not.toContain('已核验');
  });

  it('labels journal currency without rewriting decimals, auxiliary values or frozen controls', () => {
    const html = renderToStaticMarkup(
      createElement(JournalEntryGrid, {
        lines: [
          {
            account_code: '6602',
            debit: '123456789.01',
            credit: '0.00',
            auxiliary_values: { counterparty: '虚构供应商 A' },
          },
        ],
        accounts: [{ code: '6602', name: '管理费用' }],
        readonly: true,
        busy: false,
        mode: 'inline',
        onChange: () => {},
      }),
    );
    expect(html).toContain('journal-grid-readonly');
    expect(html.match(/（元）/g)).toHaveLength(2);
    expect(html).toContain('value="123456789.01"');
    expect(html).toContain('value="0.00"');
    expect(html).toContain('value="虚构供应商 A"');
    expect(html).toContain('aria-label="同屏第 1 行借方"');
    expect(html.match(/disabled=""/g)).toHaveLength(5);
    expect(html).not.toContain('确认审核');
  });
});
