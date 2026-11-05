import { test, expect } from '@playwright/test';
import { checked, formalFixture, identity } from './formal-books-fixtures';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);
for (const width of [1280, 390]) {
  test(`跨客户筛选与服务单返回保留月份和筛选 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await identity(request, 'accountant');
    const tag = `连续办理-${crypto.randomUUID().slice(0, 8)}`;
    const books = [];
    for (const n of [1, 2]) {
      const company = await checked(
        await request.post('/api/companies', {
          headers,
          data: { name: `${tag}-${n}`, tax_id: '' },
        }),
      );
      books.push(
        await checked(
          await request.post('/api/books', {
            headers,
            data: { company_id: company.id, name: `${tag}-${n}`, period: '2026-10' },
          }),
        ),
      );
    }
    await checked(
      await request.put(`/api/books/${books[0].id}/service-plan`, {
        headers,
        data: {
          expected_version: 0,
          lead_user_id: 'local-accountant',
          due_on: '2026-11-08',
          reason: '虚构连续办理指派',
        },
      }),
      200,
    );
    await page.goto('/workspace/monthly?period=2026-10');
    await page.getByLabel('搜索月度客户').fill(tag);
    await page.getByRole('button', { name: '查找', exact: true }).click();
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(2);
    await page.getByRole('button', { name: '我负责', exact: true }).click();
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
    await expect(page.locator('.monthly-work-next')).toContainText('2026-11-08');
    await page.getByRole('button', { name: '继续办理', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`book=${books[0].id}`));
    await page.getByRole('button', { name: '返回客户月度进度', exact: true }).click();
    await expect(page.getByRole('button', { name: '我负责', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByLabel('搜索月度客户')).toHaveValue(tag);
    await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-10');
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
    if (width < 640) {
      const heading = await page
        .getByRole('heading', { name: '客户月度工作', exact: true })
        .boundingBox();
      expect(heading!.width).toBeGreaterThan(width * 0.7);
    }
    await page.screenshot({
      path: `tmp_test/ux07-09-20261104/monthly-${width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
  for (const kind of ['reversal', 'adjustment'] as const) {
    test(`原凭证${kind}先预览独立复核再记账 ${width}`, async ({ page, request }) => {
      await page.setViewportSize({ width, height: 900 });
      const f = await formalFixture(request);
      const before = await checked(
        await request.get(`/api/posting/vouchers/${f.voucher.voucher.id}`, { headers: f.headers }),
        200,
      );
      await page.goto(
        `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`,
      );
      await page.getByRole('button', { name: '现金与银行日记账', exact: true }).click();
      await page.getByRole('button', { name: '查看凭证 记-1', exact: true }).first().click();
      const detail = page.getByRole('region', { name: '正式凭证详情', exact: true });
      await detail
        .getByRole('button', {
          name: kind === 'reversal' ? '整笔记错：准备冲销' : '金额或科目差错：准备调整',
          exact: true,
        })
        .click();
      const panel = detail.getByRole('region', { name: '手工凭证办理', exact: true });
      await expect(panel.getByRole('region', { name: '更正影响预览' })).toBeVisible();
      await panel.getByLabel('制单说明', { exact: true }).fill('原凭证整笔录入重复，核对后冲销');
      if (kind === 'adjustment') {
        await expect(panel.getByLabel('第 1 行借方金额', { exact: true })).toHaveValue('0.00');
        await panel.getByLabel('第 1 行借方金额', { exact: true }).fill('5.00');
        await panel.getByLabel('第 2 行贷方金额', { exact: true }).fill('5.00');
      }
      const submit = panel.getByRole('button', { name: '提交待复核', exact: true });
      await expect(submit).toBeDisabled();
      await panel.getByRole('checkbox', { name: '已核对日期、金额、科目和以上更正影响' }).check();
      await expect(submit).toBeEnabled();
      await panel.getByRole('button', { name: '添加分录', exact: true }).click();
      await panel.getByRole('button', { name: '删除本行', exact: true }).last().click();
      await expect(submit).toBeDisabled();
      await panel.getByRole('checkbox', { name: '已核对日期、金额、科目和以上更正影响' }).check();
      await submit.click();
      await expect(
        panel.getByText('已提交待复核，尚未正式记账。请按当前政策安排会计审核。'),
      ).toBeVisible();
      const list = await checked(
        await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.headers }),
        200,
      );
      const current = list.requests.find(
        (r: { content: { relation_kind?: string } }) => r.content.relation_kind === kind,
      );
      expect(current.content.original_voucher_id).toBe(f.voucher.voucher.id);
      expect(current.posted_voucher_id).toBeFalsy();
      expect(
        (
          await request.post(`/api/posting/manual-requests/${current.id}/approve`, {
            headers: f.headers,
            data: {
              expected_version: current.version,
              expected_policy_version: current.policy_version,
              reason: '不允许自审',
            },
          })
        ).status(),
      ).toBe(403);
      const approved = await checked(
        await request.post(`/api/posting/manual-requests/${current.id}/approve`, {
          headers: f.reviewer,
          data: {
            expected_version: current.version,
            expected_policy_version: current.policy_version,
            reason: '独立核对原件与冲销金额',
          },
        }),
        200,
      );
      await panel.getByRole('button', { name: '重新读取手工凭证', exact: true }).click();
      await panel.getByRole('button', { name: '确认正式记账', exact: true }).click();
      await expect(
        panel.getByText('正式记账完成，请重新读取账簿核对；此操作不代替会计导出。'),
      ).toBeVisible();
      expect(approved.request.independent).toBe(true);
      const after = await checked(
        await request.get(`/api/posting/vouchers/${f.voucher.voucher.id}`, { headers: f.headers }),
        200,
      );
      expect(after.voucher).toEqual(before.voucher);
      expect(after.lines).toEqual(before.lines);
      expect(after.relations).toHaveLength(1);
      for (const cell of await panel
        .locator('tbody td:nth-child(2), tbody td:nth-child(3)')
        .all()) {
        await expect(cell).toHaveCSS('white-space', 'nowrap');
      }
      await page.screenshot({
        path: `tmp_test/ux07-09-20261104/correction-${kind}-${width}.png`,
        fullPage: true,
      });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
    });
  }
  test(`员工待办优先且新建与键盘操作可用 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await choosePersona(page, 'employee');
    const inbox = page.locator('.workspace-inbox'),
      create = page.getByRole('region', { name: '提交与进度' });
    await expect(inbox).toBeVisible();
    await expect(create).toBeVisible();
    expect((await inbox.boundingBox())!.y).toBeLessThan((await create.boundingBox())!.y);
    const button = create.getByRole('button', { name: /提交报销/ });
    await button.focus();
    await expect(button).toBeFocused();
    await page.screenshot({
      path: `tmp_test/ux07-09-20261104/employee-${width}.png`,
      fullPage: true,
    });
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('发生日期', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}

for (const width of [1280, 390]) {
  test(`只读身份查看更正入口时给出可执行说明 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const f = await formalFixture(request);
    await checked(
      await request.put(`/api/books/${f.book.id}/members/local-business-finance`, {
        headers: f.headers,
        data: { role: 'viewer' },
      }),
      200,
    );
    await page.goto('/');
    await choosePersona(page, 'finance');
    // Local demo identity is memory-only; a full reload deliberately defaults
    // to the demo owner. Navigate inside the app and assert the actual identity.
    await page.evaluate((url) => {
      history.pushState({}, '', url);
      dispatchEvent(new PopStateEvent('popstate'));
    }, `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`);
    await expect(page.getByLabel('切换验证身份')).toHaveValue('finance');
    await page.getByRole('button', { name: '现金与银行日记账', exact: true }).click();
    await page.getByRole('button', { name: '查看凭证 记-1', exact: true }).first().click();
    const detail = page.getByRole('region', { name: '正式凭证详情', exact: true });
    await detail.getByRole('button', { name: '金额或科目差错：准备调整', exact: true }).click();
    const panel = detail.getByRole('region', { name: '手工凭证办理', exact: true });
    await expect(
      panel.getByText(
        '当前授权或期间状态不允许制单。请联系本账套主办会计确认可办理期间；原凭证继续保留。',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(panel.getByRole('button', { name: '提交待复核', exact: true })).toHaveCount(0);
  });
  test(`管理者先协调企业财务先核验 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/workspace/firm?period=2026-10');
    await expect(page.locator('.cockpit-coordination')).toBeVisible();
    expect((await page.locator('.cockpit-coordination').boundingBox())!.y).toBeLessThan(
      (await page.locator('.cockpit-summary').boundingBox())!.y,
    );
    await page.screenshot({
      path: `tmp_test/ux07-09-20261104/manager-${width}.png`,
      fullPage: true,
    });
    await choosePersona(page, 'finance');
    await expect(page.getByRole('region', { name: '资料核验与移交', exact: true })).toBeVisible();
    await page.screenshot({
      path: `tmp_test/ux07-09-20261104/finance-${width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}
