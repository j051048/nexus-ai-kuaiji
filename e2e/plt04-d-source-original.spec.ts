import { expect, test } from '@playwright/test';
import { checked, reviewedManual } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
]) {
  test(`PLT04-D attaches and downloads a non-import source original ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    const imported = await checked(
      await request.post(`/api/books/${f.book.id}/imports`, {
        headers: f.headers,
        data: {
          source_name: '虚构平台结算单',
          rows: [
            {
              external_id: `plt04-original-${viewport.width}`,
              date: '2026-10-16',
              description: '平台结算单待补原件',
              counterparty: '虚构平台',
              amount: '100.00',
              direction: 'income',
              evidence_ref: '原有文字引用，无原件对象',
            },
          ],
        },
      }),
      200,
    );
    expect(imported.imported).toBe(1);
    const book = await (
      await request.get(`/api/books/${f.book.id}`, { headers: f.headers })
    ).json();
    const source = book.sources.find(
      (row: { description: string }) => row.description === '平台结算单待补原件',
    );
    expect(source?.id).toBeTruthy();
    await reviewedManual(request, f.headers, f.reviewer, {
      book_id: f.book.id,
      command_key: `plt04-source-original-${viewport.width}`,
      voucher_date: '2026-10-16',
      description: '来源原件补传验收',
      reason: '独立复核后记录一笔带来源引用的正式凭证',
      lines: [
        {
          account_id: f.accounts[0].id,
          account_version: 1,
          debit: '100.00',
          credit: '0.00',
          source_ref: source.id,
        },
        {
          account_id: f.revenue.id,
          account_version: 1,
          debit: '0.00',
          credit: '100.00',
          source_ref: source.id,
        },
      ],
    });

    await page.setViewportSize(viewport);
    await openTax(page, f);
    const panel = page.getByRole('region', { name: '税务底稿', exact: true });
    await panel
      .getByRole('row')
      .filter({ hasText: '营业收入' })
      .getByRole('button', { name: '查看依据' })
      .click();
    await expect(panel.getByText(/下钻：/)).toBeVisible();
    const upload = panel.locator('.tax-original-upload').first();
    await expect(upload).toBeVisible();
    await upload.locator('input[type="file"]').setInputFiles({
      name: '平台结算单.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4\nplt04 source original\n%%EOF'),
    });
    await expect(panel.getByText(/来源原件已绑定/)).toBeVisible({ timeout: 20000 });
    const open = panel.getByRole('button', { name: '打开原件' }).first();
    await expect(open).toBeVisible();
    const downloadPromise = page.waitForEvent('download');
    await open.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^来源原件-.*\.pdf$/);
    await page.screenshot({
      path: `tmp_test/plt04-source-original-${viewport.width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}
