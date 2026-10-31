import { describe, expect, it } from 'vitest';
import { assistantDeliveryTarget } from './assistant-delivery-target';

const target = {
  schema: 'assistant-month-close-v1',
  company_id: 'a',
  book_id: 'b',
  period: '2026-10',
  inputs_fingerprint: 'a'.repeat(64),
};
const evidence = {
  id: 'book:b',
  kind: 'book',
  label: '本月核对',
  detail: '',
  book_id: 'b',
  delivery_target: target,
};
describe('交付诊断只导航到授权账套当前月结，不执行业务动作', () => {
  it('接受服务器冻结的账套、企业、月份与指纹', () => {
    expect(assistantDeliveryTarget(evidence)).toEqual(target);
  });
  it.each([
    { book_id: 'other' },
    { period: '2026-13' },
    { company_id: '' },
    { inputs_fingerprint: 'unknown' },
    { schema: 'other' },
    { href: 'https://evil.test' },
  ])('拒绝伪造/畸形目标 %o', (change) => {
    expect(
      assistantDeliveryTarget({ ...evidence, delivery_target: { ...target, ...change } }),
    ).toBeNull();
  });
  it('普通原件引用不推导月结入口', () => {
    expect(assistantDeliveryTarget({ ...evidence, kind: 'attachment' })).toBeNull();
    expect(assistantDeliveryTarget({ ...evidence, delivery_target: undefined })).toBeNull();
  });
});
