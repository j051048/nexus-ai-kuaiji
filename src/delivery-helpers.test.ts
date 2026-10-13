import { describe, expect, it } from 'vitest';
import {
  decodeReceiptBytes,
  deliveryIssueLabel,
  deliveryStates,
  receiptFileFormat,
  validateReceiptFile,
} from './delivery-helpers';

describe('交付回执输入与核验文案', () => {
  it('只有中性 JSON / CSV 输入，大小边界明确', () => {
    expect(receiptFileFormat('客户回执.JSON')).toBe('json');
    expect(validateReceiptFile('客户回执.csv', 2_000_000)).toBe('csv');
    for (const name of ['reply.xlsx', 'invoice.pdf', 'reply'])
      expect(() => receiptFileFormat(name)).toThrow();
    for (const size of [0, -1, 2_000_001, NaN])
      expect(() => validateReceiptFile('reply.json', size)).toThrow();
  });
  it('明确区分回执数据一致、缺字段和真实差异', () => {
    expect(deliveryStates.verified).toBe('回执数据一致');
    expect(deliveryIssueLabel('multiple_external_vouchers')).toContain('不同外部凭证号');
    expect(deliveryIssueLabel('line_auxiliary_values_mismatch')).toContain('辅助核算');
    expect(deliveryIssueLabel('receipt_item_missing')).toContain('未列出');
    expect(deliveryIssueLabel('future_issue')).toBe('future_issue');
  });
  it('UTF-8回执原字节保持BOM和末尾换行，非法字节拒绝', () => {
    const raw = '\ufeffa,b\r\n1,2\r\n';
    const bytes = new TextEncoder().encode(raw);
    const decoded = decodeReceiptBytes(bytes.buffer as ArrayBuffer);
    expect(decoded).toBe(raw);
    expect([...new TextEncoder().encode(decoded)]).toEqual([...bytes]);
    expect(() => decodeReceiptBytes(new Uint8Array([0xff, 0xfe]).buffer)).toThrow('UTF-8');
  });
});
