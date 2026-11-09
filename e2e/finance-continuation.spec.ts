import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { checked, formalFixture, reviewedManual } from './formal-books-fixtures';

for (const width of [1365, 390]) {
  test(`往来办理 ${width} 真实部分核销撤销重办返回保留筛选`, async ({ page, request }) => {
    test.setTimeout(120000);
    await page.setViewportSize({ width, height: 844 });
    const f = await formalFixture(request, false, '往来办理');
    await checked(
      await request.post('/api/subledger/control-accounts', {
        headers: f.headers,
        data: {
          book_id: f.book.id,
          kind: 'payable',
          control_account_id: f.accounts[1].id,
          control_account_version: 1,
          effective_from: '2026-01',
          reason: '本地虚构应付配置',
        },
      }),
    );
    const party = await checked(
      await request.post(`/api/books/${f.book.id}/ledger/entities`, {
        headers: f.headers,
        data: { kind: 'counterparty', code: 'SETTLEMENT', name: '虚构核销供方' },
      }),
    );
    const item = await checked(
      await request.post('/api/subledger/items', {
        headers: f.headers,
        data: {
          book_id: f.book.id,
          kind: 'payable',
          counterparty_id: party.id,
          control_account_id: f.accounts[1].id,
          control_account_version: 1,
          original_date: '2026-10-01',
          amount: '10000.00',
          reason: '本地往来事项',
        },
      }),
    );
    const payment = await reviewedManual(request, f.headers, f.reviewer, {
      book_id: f.book.id,
      command_key: crypto.randomUUID(),
      voucher_date: '2026-10-20',
      description: '已复核付款3000',
      reason: '虚构核销付款',
      lines: [
        { account_id: f.accounts[1].id, account_version: 1, debit: '3000.00' },
        { account_id: f.accounts[0].id, account_version: 1, credit: '3000.00' },
      ],
    });
    await page.goto(
      `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`,
    );
    await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
    const panel = page.getByRole('region', { name: '往来与期末办理', exact: true });
    await panel.getByLabel('往来类型').selectOption('payable');
    await panel.getByLabel('账龄截止日期').fill('2026-12-31');
    await panel.getByRole('button', { name: '查看并办理核销' }).click();
    const detail = page.getByRole('region', { name: '往来事项办理', exact: true });
    await expect(detail).toContainText('当前未结 10000.00 元');
    await detail.getByLabel('收付款凭证', { exact: true }).selectOption(payment.voucher.id);
    await detail.getByRole('button', { name: '查看所选正式凭证' }).click();
    const voucherDialog = page.getByRole('region', { name: '正式凭证详情', exact: true });
    await expect(voucherDialog).toContainText('已复核付款3000');
    await voucherDialog.getByRole('button', { name: '关闭凭证详情' }).click();
    await expect(detail.getByLabel('收付款凭证', { exact: true })).toHaveValue(payment.voucher.id);
    await detail.getByLabel('本次核销金额（元）').fill('3000.00');
    await detail.getByLabel('核销或撤销理由').fill('核对该笔正式付款');
    await detail.getByRole('button', { name: '核对后登记核销' }).click();
    await detail.getByRole('button', { name: '确认办理', exact: true }).click();
    await expect(detail).toContainText('当前未结 7000.00 元');
    await detail.getByLabel('核销或撤销理由').fill('人工发现需撤回核对');
    await detail.getByRole('button', { name: '撤销这笔核销' }).click();
    await detail.getByRole('button', { name: '确认办理', exact: true }).click();
    await expect(detail).toContainText('当前未结 10000.00 元');
    await expect(detail).toContainText('撤销理由：人工发现需撤回核对');
    await detail.getByLabel('收付款凭证', { exact: true }).selectOption(payment.voucher.id);
    await detail.getByLabel('本次核销金额（元）').fill('2000.00');
    await detail.getByLabel('核销或撤销理由').fill('重新核对后部分核销');
    await detail.getByRole('button', { name: '核对后登记核销' }).click();
    await detail.getByRole('button', { name: '确认办理', exact: true }).click();
    await expect(detail).toContainText('当前未结 8000.00 元');
    const final = await checked(
      await request.get(`/api/subledger/items/${item.id}/workspace?book_id=${f.book.id}`, {
        headers: f.headers,
      }),
      200,
    );
    expect(final.allocations).toHaveLength(2);
    expect(
      final.allocations.filter((row: { reversal_id: string | null }) => row.reversal_id),
    ).toHaveLength(1);
    expect(final.item.remaining_cents).toBe(800000);
    expect(final.vouchers[0].available_cents).toBe(100000);
    const bounds = await detail.boundingBox();
    for (const control of await detail.locator('input,select,fieldset').all()) {
      const box = await control.boundingBox();
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
    }
    mkdirSync('tmp_test/finance-continuation/screenshots', { recursive: true });
    await page.screenshot({
      path: `tmp_test/finance-continuation/screenshots/settlement-${width}.png`,
      fullPage: true,
    });
    await detail.getByRole('button', { name: '返回账龄（保留筛选）' }).click();
    await expect(panel.getByLabel('往来类型')).toHaveValue('payable');
    await expect(panel.getByLabel('账龄截止日期')).toHaveValue('2026-12-31');
    await expect(panel).toContainText('未结金额 8000.00 元');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });

  test(`月度服务单 ${width} 直达往来资产并刷新返回原范围`, async ({ page, request }) => {
    const f = await formalFixture(request, false, '月度办理');
    await page.setViewportSize({ width, height: 844 });
    await page.goto(
      `/workspace/service?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`,
    );
    for (const [label, section, tab] of [
      ['办理往来核销', 'aging', '往来账龄'],
      ['办理资产计提', 'periodic', '资产与期末草稿'],
    ]) {
      await expect(
        page.getByRole('heading', { name: `${f.company.name} · 月度服务单`, exact: true }),
      ).toBeVisible();
      await page.getByText('往来核销与资产计提', { exact: true }).click();
      await page.getByRole('button', { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`finance=${section}`));
      await page.reload();
      await expect(
        page
          .getByRole('navigation', { name: '往来与期末办理类型' })
          .getByRole('button', { name: tab, exact: true }),
      ).toHaveAttribute('aria-pressed', 'true');
      await page.getByRole('button', { name: '返回本月服务单', exact: true }).click();
      await expect(page).toHaveURL(/\/workspace\/service/);
      expect(new URL(page.url()).searchParams.get('book')).toBe(f.book.id);
    }
  });
}
