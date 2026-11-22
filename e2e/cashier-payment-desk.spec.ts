import { expect, test } from '@playwright/test';
import { checked, formalFixture } from './formal-books-fixtures';
import { pinPersona } from './n04-tax-fixtures';

test.setTimeout(150_000);
test.use({ actionTimeout: 15_000 });

const url = (f: Awaited<ReturnType<typeof formalFixture>>) =>
  `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`;

async function openDesk(page: import('@playwright/test').Page) {
  await page.getByText('出纳付款审批（制单 / 资金审批 / 银行回执）', { exact: true }).click();
  const desk = page.getByRole('region', { name: '出纳付款审批台', exact: true });
  await expect(desk).toBeVisible();
  return desk;
}

test('STEP2 出纳制单与资金审批人放款由两个不同身份完成', async ({ page, request }) => {
  const f = await formalFixture(request);
  // 岗位只能由事务所管理者授予；这里用建账身份（管理者）把出纳岗位给独立复核身份。
  const granted = await checked(
    await request.put('/api/treasury/payment-roles/local-reviewer', {
      headers: f.headers,
      params: { book_id: f.book.id },
      data: { role: 'cashier', reason: '本地虚构场景授权出纳制单' },
    }),
    200,
  );
  expect(granted.role).toBe('cashier');

  // 出纳身份：能制单、能提交，但不能给自己放款。
  await pinPersona(page, 'reviewer');
  await page.goto(url(f));
  let desk = await openDesk(page);
  await expect(desk).toContainText('我的岗位：cashier');
  await expect(desk).toContainText('制单可用');
  await desk.getByLabel('收款人', { exact: true }).fill('虚构供应商甲');
  await desk.getByLabel('收款账号', { exact: true }).fill('6222 0000 0000 0009');
  await desk.getByLabel('金额（元）', { exact: true }).fill('1200');
  await desk.getByRole('button', { name: '创建付款指令', exact: true }).click();
  await expect(desk).toContainText('虚构供应商甲');
  await expect(desk).toContainText('草稿');
  await desk.getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(desk).toContainText('待审批');
  await expect(desk).toContainText('本人制单，须由他人审批');
  await expect(desk.getByRole('button', { name: '批准放款', exact: true })).toHaveCount(0);

  // 资金审批身份：另一名有复核权的管理者放款，制单人与审批人因此必然不同。
  await pinPersona(page, 'accountant');
  await page.goto(url(f));
  desk = await openDesk(page);
  await expect(desk).toContainText('放款审批可用');
  await desk.getByRole('button', { name: '批准放款', exact: true }).click();
  await expect(desk).toContainText('已批准');
  await expect(desk).toContainText('制单人与审批人不是同一人');

  const orders = await checked(
    await request.get('/api/treasury/payment-orders', {
      headers: f.headers,
      params: { book_id: f.book.id },
    }),
    200,
  );
  expect(orders).toHaveLength(1);
  expect(orders[0].status).toBe('approved');
  expect(orders[0].maker_user_id).not.toBe(orders[0].approver_user_id);
  expect(orders[0].approver_user_id).toBe('local-accountant');
});
