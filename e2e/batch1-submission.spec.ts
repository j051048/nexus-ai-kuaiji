import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { independentReviewer, pinReviewer } from './independent-review';
import { choosePersona, openBusiness } from './ui-navigation';

test('无部门企业管理员上传 XML 核对直接提交，由授权会计接收、独立复核后导出', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` },
    adminHeaders = { Authorization: `Bearer ${finance.token}` };
  const marker = crypto.randomUUID().slice(0, 8),
    name = `无部门直提企业 ${marker}`;
  const created = await request.post('/api/companies', {
    headers,
    data: { name, tax_id: '913101TESTBUYER' },
  });
  expect(created.status()).toBe(201);
  const company = await created.json();
  let grantedBook: string | undefined;
  try {
    expect(
      (
        await request.put(`/api/companies/${company.id}/members`, {
          headers,
          data: { email: 'finance@demo.example.invalid', role: 'admin', department_id: null },
        })
      ).status(),
    ).toBe(200);
    const bookResponse = await request.post('/api/books', {
      headers,
      data: { name: `直提试点账套 ${marker}`, company_id: company.id, period: '2026-10' },
    });
    expect(bookResponse.status()).toBe(201);
    const book = await bookResponse.json();
    grantedBook = book.id;
    expect(
      (
        await request.put(`/api/books/${book.id}/members/local-business-finance`, {
          headers,
          data: { role: 'editor' },
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await request.post(`/api/books/${book.id}/ledger/entities`, {
          headers,
          data: { kind: 'counterparty', code: marker, name: '耗材商店' },
        })
      ).status(),
    ).toBe(201);
    expect(
      (
        await request.put(`/api/companies/${company.id}/intelligence/settings`, {
          headers,
          data: { enabled: true },
        })
      ).status(),
    ).toBe(200);
    await page.goto('/');
    await openBusiness(page);
    await page.getByRole('button', { name: '部门与企业设置', exact: true }).click();
    const settings = page.getByRole('dialog', { name: '部门与企业设置' });
    await settings.locator('#organization-company').selectOption(company.id);
    await settings.getByLabel('企业资料提交路径').selectOption('direct');
    await settings
      .getByLabel('提交方式变更依据')
      .fill('合成无部门企业由管理员集中供票、授权会计接收');
    await settings.getByRole('button', { name: '保存提交方式' }).click();
    await expect(settings.getByRole('status')).toContainText('提交方式已保存');
    await settings.getByRole('button', { name: '完成', exact: true }).click();
    expect(
      (await (await request.get(`/api/companies/${company.id}/departments`, { headers })).json())
        .departments,
    ).toEqual([]);
    await choosePersona(page, 'finance');
    await openBusiness(page);
    await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
    const intake = page.getByRole('dialog');
    await intake.getByLabel('事项客户企业').selectOption(company.id);
    await intake.getByRole('button', { name: /^采购 \/ 费用/ }).click();
    const xml = readFileSync(
      'nexus_backend/tests/fixtures/stage16_SYNTHETIC_einvoice_0_3.xml',
      'utf8',
    ).replace('第一客户企业', name);
    await intake.getByLabel('一体化录入上传附件').setInputFiles({
      name: `合成数电票-${marker}.xml`,
      mimeType: 'application/xml',
      buffer: Buffer.from(xml),
    });
    await expect(intake.getByText(`合成数电票-${marker}.xml`, { exact: true })).toBeVisible();
    await intake.getByRole('button', { name: '解析 XML 并预填', exact: true }).click();
    await expect(intake.getByText('识别建议已就绪', { exact: true })).toBeVisible();
    const candidates = intake.locator('.intake-suggested-field');
    for (const label of ['票面总金额', '票据日期（请核对发生日）', '交易对方']) {
      await candidates
        .filter({ hasText: label })
        .getByRole('button', { name: '采用此字段', exact: true })
        .click();
    }
    await intake.getByLabel('事项摘要', { exact: true }).fill(`XML 无部门直提办公用品 ${marker}`);
    await intake.getByLabel('事项分类', { exact: true }).selectOption('office');
    await expect(intake.getByLabel('事项金额', { exact: true })).toHaveValue('0.30');
    await expect(intake.getByLabel('交易对方', { exact: true })).toHaveValue('耗材商店');
    await expect(intake.getByLabel('发生日期', { exact: true })).toHaveValue('2026-10-03');
    await expect(intake.getByLabel('事项所属部门')).toHaveValue('');
    await expect(intake.getByRole('button', { name: '提交财务处理', exact: true })).toBeDisabled();
    await intake
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
    await page.setViewportSize({ width: 390, height: 844 });
    await intake.getByLabel('事项金额', { exact: true }).scrollIntoViewIfNeeded();
    expect(await intake.evaluate((node) => node.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: 'artifacts/batch1-xml-submission-mobile.png', fullPage: false });
    await page.setViewportSize({ width: 1440, height: 960 });
    await intake.getByRole('button', { name: '提交财务处理', exact: true }).click();
    await expect(intake).toHaveCount(0);
    await expect(page.getByText('待会计接收', { exact: true }).first()).toBeVisible();
    const own = await (
      await request.get(`/api/business/events?scope=own&company_id=${company.id}`, {
        headers: adminHeaders,
      })
    ).json();
    expect(own.events ?? own.items).toHaveLength(1);
    const event = (own.events ?? own.items)[0];
    expect(event.department_id).toBeNull();
    expect(event.submission_mode).toBe('direct');
    expect(event.status).toBe('submitted');
    expect(
      (
        await request.post(`/api/business/events/${event.id}/handoff`, {
          headers: adminHeaders,
          data: {
            book_id: book.id,
            expected_version: event.version,
            idempotency_key: crypto.randomUUID(),
          },
        })
      ).status(),
    ).toBe(403);
    await choosePersona(page, 'accountant');
    await openBusiness(page);
    await page.getByRole('tab', { name: '会计接收', exact: true }).click();
    await page.getByLabel('筛选业务事项企业').selectOption(company.id);
    await page
      .getByRole('button')
      .filter({ has: page.getByText(`XML 无部门直提办公用品 ${marker}`, { exact: true }) })
      .click();
    await expect(page.getByRole('button', { name: '部门审批通过' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '财务核验通过' })).toHaveCount(0);
    await page.locator('#handoff-book').selectOption(book.id);
    await page.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/business/events/${event.id}`, { headers })).json())
            .status,
      )
      .toBe('handed_off');
    const handed = await (
      await request.get(`/api/business/events/${event.id}`, { headers })
    ).json();
    expect(handed.status).toBe('handed_off');
    expect(handed.mapping).toBeTruthy();
    const draftId = handed.mapping.draft_id;
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/books/${book.id}/drafts/${handed.mapping.draft_id}`, {
                headers,
              })
            ).json()
          ).issues,
        { timeout: 15000 },
      )
      .toEqual([]);
    const draft = await (
      await request.get(`/api/books/${book.id}/drafts/${draftId}`, { headers })
    ).json();
    expect(draft.status).toBe('draft');
    expect(
      (
        await request.post(`/api/books/${book.id}/exports`, {
          headers,
          data: { draft_ids: [draftId], idempotency_key: crypto.randomUUID() },
        })
      ).status(),
    ).toBe(409);
    expect(
      (
        await request.put(`/api/companies/${company.id}/members`, {
          headers,
          data: { email: 'finance@demo.example.invalid', role: 'finance', department_id: null },
        })
      ).status(),
    ).toBe(200);
    const applicantBook = await request.get(`/api/books/${book.id}`, { headers: adminHeaders });
    expect(applicantBook.status()).toBe(200);
    expect((await applicantBook.json()).book.permissions.review).toBe(false);
    expect(
      (
        await request.post(`/api/books/${book.id}/drafts/${draftId}/approve`, {
          headers: adminHeaders,
          data: { expected_version: draft.version },
        })
      ).status(),
    ).toBe(403);
    await independentReviewer(request, headers, book.id);
    await pinReviewer(page);
    await page.goto(`/workspace/review?review_book=${book.id}&review_draft=${draftId}`);
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toHaveValue(
      `XML 无部门直提办公用品 ${marker}`,
    );
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
    await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
    await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
    await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
    await page.goto(`/workspace/accounting?book=${book.id}`);
    await page.getByLabel(`选择凭证 XML 无部门直提办公用品 ${marker}`, { exact: true }).check();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出凭证 CSV', exact: true }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(
      (await (await request.get(`/api/books/${book.id}/drafts/${draftId}`, { headers })).json())
        .status,
    ).toBe('exported');
  } finally {
    // Preserve the fictional invoice, task, review and export evidence while
    // removing this test's temporary grants from the shared demo identity.
    if (grantedBook)
      expect(
        (
          await request.delete(`/api/books/${grantedBook}/members/local-business-finance`, {
            headers,
          })
        ).status(),
      ).toBe(200);
    expect(
      (
        await request.delete(`/api/companies/${company.id}/members/local-business-finance`, {
          headers,
        })
      ).status(),
    ).toBe(200);
  }
});
