import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { RecognizedDocument } from './IntelligencePanel';
import { canApplyProposal, canResolveFinding, canStartIntelligenceAction, journalResult, latestRuns, ocrResult, processingUnavailable, recognizedMoney, RUN_WAIT_TIMEOUT_MS, runPending, runStatusLabel, runWaitingTimedOut, shouldReadAfterFailure } from './intelligence-helpers';
import type { IntelligenceConfig, IntelligenceDetail, IntelligenceRun, OcrDocument } from './intelligence-types';

const document: OcrDocument = {
  document_type: 'invoice', invoice_number: '12345678', issued_on: '2026-10-11',
  seller_name: '某办公用品公司', seller_tax_id: null, buyer_name: '客户企业', buyer_tax_id: null,
  gross_amount: '0.30', net_amount: null, tax_amount: null, currency: 'CNY',
  pages: [{ page: 2, raw_text: '发票价税合计 0.30 元' }], evidence: [{ field: 'gross_amount', page: 2, quote: '价税合计：0.30 元' }],
};
const config: IntelligenceConfig = { configured: true, vision_ready: true, journal_ready: true, provider_label: '试点服务', enabled_company_ids: ['company-a'] };
const detail: IntelligenceDetail = { runs: [], findings: [], bank_candidates: [], bank_links: [], permissions: { process: true, resolve: true, link_bank: true, apply_proposal: true } };
function run(overrides: Partial<IntelligenceRun> = {}): IntelligenceRun {
  return { id: 'run-1', kind: 'proposal', status: 'succeeded', draft_id: 'draft-1', input_version: 4, result: { description: '办公支出', lines: [{ account_code: '6602', debit: '0.30', credit: '0.00' }, { account_code: '1002', debit: '0.00', credit: '0.30' }], reasoning: '依据已核对办公用品发票', evidence_refs: [{ attachment_id: 'attachment-a', extraction_id: 'ocr-a', field: 'gross_amount', page: 2 }], warnings: [] }, provider: '试点服务', model: 'model-v1', prompt_version: 'journal-v1', created_at: '2026-10-11T12:00:00Z', ...overrides };
}

describe('票据金额与原文呈现', () => {
  it.each([null, undefined, ''])('将缺失值 %s 显示为待核对，不伪造零税额', value => expect(recognizedMoney(value)).toBe('待核对'));
  it('区分零税额与缺失税额，并精确保留大额的分', () => {
    expect(recognizedMoney('0.00')).toBe('¥ 0.00');
    expect(recognizedMoney('999999999999.99')).toBe('¥ 999,999,999,999.99');
    expect(recognizedMoney('0.30', 'USD')).toBe('USD 0.30');
  });
  it('展示页码与字段引文，转录内容作为文本进行 HTML 转义', () => {
    const html = renderToStaticMarkup(createElement(RecognizedDocument, { document: { ...document, seller_name: '<script>alert(1)</script>', pages: [{ page: 2, raw_text: '<img src=x onerror=alert(1)>' }] } }));
    expect(html).toContain('第 2 页'); expect(html).toContain('价税合计：0.30 元');
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img'); expect(html).not.toContain('<img src=x');
    expect(html).toContain('待核对'); expect(html).not.toContain('100%');
  });
});
describe('供应商配置与企业处理开关', () => {
  it('加载中、未配置、企业关闭分别保留真实不可用状态', () => {
    expect(processingUnavailable(null, 'company-a', 'ocr')).toContain('读取');
    expect(processingUnavailable({ ...config, configured: false }, 'company-a', 'ocr')).toContain('等待管理员配置');
    expect(processingUnavailable(config, 'company-b', 'ocr')).toContain('尚未开启');
    expect(processingUnavailable(config, 'company-a', 'ocr')).toBe('');
  });
  it('将视觉与分录模型的配置分别检查', () => {
    const partial = { ...config, journal_ready: false };
    expect(processingUnavailable(partial, 'company-a', 'ocr')).toBe('');
    expect(processingUnavailable(partial, 'company-a', 'proposal')).toContain('等待管理员配置');
  });
});
describe('只采用当前版本且人工处理了阻断异常的建议', () => {
  it('草稿版本改变后拒绝旧建议', () => {
    expect(canApplyProposal(detail, run(), 4)).toBe(true);
    expect(canApplyProposal(detail, run(), 5)).toBe(false);
  });
  it.each(['running', 'failed', 'stale'] as const)('拒绝 %s 任务结果', status => expect(canApplyProposal(detail, run({ status }), 4)).toBe(false));
  it('没有编辑权限不能采用，也不能绕过未处理阻断异常', () => {
    expect(canApplyProposal({ ...detail, permissions: { ...detail.permissions, apply_proposal: false } }, run(), 4)).toBe(false);
    const finding = { id: 'finding-1', code: 'amount_mismatch', severity: 'blocking' as const, payload: { message: '金额不符' }, resolved: false };
    expect(canApplyProposal({ ...detail, findings: [finding] }, run(), 4)).toBe(false);
    expect(canApplyProposal({ ...detail, findings: [{ ...finding, resolved: true, resolution_reason: '已补件并核对原件' }] }, run(), 4)).toBe(true);
  });
  it('即时异常未登记 id 时不能请求人工处理，已登记异常仍检查处理权限', () => {
    const virtual = { id: null, code: 'amount_mismatch', severity: 'blocking' as const, payload: { message: '金额不符' }, resolved: false };
    expect(canResolveFinding(detail, virtual)).toBe(false);
    expect(canResolveFinding(detail, { ...virtual, id: 'finding-1' })).toBe(true);
    expect(canResolveFinding({ ...detail, permissions: { ...detail.permissions, resolve: false } }, { ...virtual, id: 'finding-1' })).toBe(false);
    expect(canApplyProposal({ ...detail, findings: [virtual] }, run(), 4)).toBe(false);
  });
  it('持久任务未结束的阻断不能通过人工确认绕过', () => {
    const pending = { id: 'queue-finding', code: 'queue_pending_attachment-a', severity: 'blocking' as const, payload: { message: '后台任务尚未完成', non_overridable: true }, resolved: false };
    expect(canResolveFinding(detail, pending)).toBe(false);
    expect(canApplyProposal({ ...detail, findings: [pending] }, run(), 4)).toBe(false);
  });
  it('失败的最新OCR不会退回展示较早成功结果为当前识别', () => {
    const old = run({ id: 'old', kind: 'ocr', attachment_id: 'attachment-a', result: { documents: [document] } });
    const failed = run({ id: 'latest', kind: 'ocr', attachment_id: 'attachment-a', result: null, status: 'failed', created_at: '2026-10-11T12:01:00Z' });
    expect(latestRuns([old, failed], 'ocr').map(item => item.id)).toEqual(['latest']);
    expect(ocrResult(failed)).toBe(null); expect(journalResult(old)).toBe(null);
  });
});
describe('人工草稿与处理恢复', () => {
  it.each(['ocr:attachment-a', 'proposal', 'apply', 'reconcile', 'resolve:finding-a', 'link', 'unlink'])('草稿有未保存修改时阻止 %s，保留人工输入', action => {
    expect(canStartIntelligenceAction(action, true, false, false)).toBe(false);
    expect(canStartIntelligenceAction(action, false, false, false)).toBe(true);
  });
  it('未保存草稿仍可只读刷新，但不会绕过并发操作锁', () => {
    expect(canStartIntelligenceAction('refresh', true, false, false)).toBe(true);
    expect(canStartIntelligenceAction('refresh', true, true, false)).toBe(false);
    expect(canStartIntelligenceAction('ocr:a', false, false, true)).toBe(false);
  });
  it('版本冲突或响应不确定时重新读取版本，身份或业务校验失败不触发范围恢复', () => {
    expect(shouldReadAfterFailure(new ApiError('版本已改变', 409))).toBe(true);
    expect(shouldReadAfterFailure(new ApiError('响应未确认', 503))).toBe(true);
    expect(shouldReadAfterFailure(new TypeError('connection closed'))).toBe(true);
    expect(shouldReadAfterFailure(new ApiError('无权访问', 403))).toBe(false);
    expect(shouldReadAfterFailure(new ApiError('输入不正确', 422))).toBe(false);
  });
  it('3 分钟等待边界允许手工重新发起，保留服务器 running 状态而不伪称失败', () => {
    const running = run({ status: 'running' });
    const start = Date.parse(running.created_at);
    expect(runPending(running, start + RUN_WAIT_TIMEOUT_MS - 1)).toBe(true);
    expect(runWaitingTimedOut(running, start + RUN_WAIT_TIMEOUT_MS)).toBe(true);
    expect(runPending(running, start + RUN_WAIT_TIMEOUT_MS)).toBe(false);
    expect(runStatusLabel(running, start + RUN_WAIT_TIMEOUT_MS)).toContain('等待超时');
    expect(runStatusLabel(running, start + RUN_WAIT_TIMEOUT_MS)).not.toContain('处理失败');
    expect(running.status).toBe('running');
  });
  it('成功或失败记录不被误判为等待超时，未知或未来开始时间保持待处理', () => {
    const longAfter = Date.parse('2026-10-12T12:00:00Z');
    expect(runWaitingTimedOut(run(), longAfter)).toBe(false);
    expect(runWaitingTimedOut(run({ status: 'failed' }), longAfter)).toBe(false);
    expect(runPending(run({ status: 'running', created_at: 'invalid' }), longAfter)).toBe(true);
    expect(runPending(run({ status: 'running', created_at: '2026-10-13T12:00:00Z' }), longAfter)).toBe(true);
  });
});
