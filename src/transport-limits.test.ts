import { afterEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_ATTACHMENT_BYTES,
  directUploadLimitLabel,
  directUploadSizeError,
  getTransportLimits,
  maxDirectUploadBytes,
  maxUploadBytes,
  setTransportLimits,
  transportBodyError,
  uploadLimitLabel,
  uploadSizeError,
} from './transport-limits';
import { validateAttachment } from './business-forms';
import { validateReceiptFile } from './delivery-helpers';
import { MAX_CSV_BYTES, parseCsv } from './csv';

const gatewayLimits = {
  max_file_bytes: 3 * 1024 * 1024,
  max_request_bytes: 4 * 1024 * 1024,
  max_response_bytes: 4 * 1024 * 1024,
  direct_upload_max_bytes: 50 * 1024 * 1024,
};
afterEach(() => setTransportLimits());

describe('公开运行配置中的上传限制', () => {
  it('普通后端未提供 metadata 时保留原本的 10 MB 与 2 MB 上限', () => {
    expect(getTransportLimits()).toBeNull();
    expect(maxUploadBytes()).toBe(DEFAULT_ATTACHMENT_BYTES);
    expect(uploadSizeError(DEFAULT_ATTACHMENT_BYTES)).toBe('');
    expect(uploadSizeError(DEFAULT_ATTACHMENT_BYTES + 1)).toContain('10 MB');
    expect(maxUploadBytes(MAX_CSV_BYTES)).toBe(MAX_CSV_BYTES);
    expect(validateReceiptFile('回执.json', 2_000_000)).toBe('json');
  });

  it('普通请求保持 3 MB，两段式直传由独立上限和业务上限共同约束', () => {
    setTransportLimits(gatewayLimits);
    expect(maxUploadBytes()).toBe(3 * 1024 * 1024);
    expect(maxDirectUploadBytes()).toBe(DEFAULT_ATTACHMENT_BYTES);
    expect(uploadLimitLabel()).toBe('3 MB');
    expect(directUploadLimitLabel()).toBe('10 MB');
    expect(uploadSizeError(3 * 1024 * 1024 + 1)).toContain('3 MB');
    expect(directUploadSizeError(DEFAULT_ATTACHMENT_BYTES)).toBe('');
    expect(directUploadSizeError(DEFAULT_ATTACHMENT_BYTES + 1)).toContain('10 MB');
    expect(
      validateAttachment(
        { name: '发票.pdf', type: 'application/pdf', size: 9 * 1024 * 1024 },
        0,
        0,
      ),
    ).toBe('');
    expect(
      validateAttachment(
        { name: '发票.pdf', type: 'application/pdf', size: DEFAULT_ATTACHMENT_BYTES + 1 },
        0,
        0,
      ),
    ).toContain('10 MB');
  });

  it('普通请求、直传附件与固定小文件导入分别使用各自边界', () => {
    setTransportLimits(gatewayLimits);
    expect(maxUploadBytes()).toBe(3 * 1024 * 1024);
    expect(maxDirectUploadBytes()).toBe(10 * 1024 * 1024);
    expect(uploadSizeError(3 * 1024 * 1024)).toBe('');
    expect(uploadSizeError(3 * 1024 * 1024 + 1)).toContain('拆分');
    expect(uploadSizeError(2 * 1024 * 1024, MAX_CSV_BYTES)).toBe('');
    expect(uploadSizeError(2 * 1024 * 1024 + 1, MAX_CSV_BYTES)).toContain('2 MB');
    expect(() => validateReceiptFile('回执.json', 2_000_001)).toThrow('2 MB');
  });

  it('更严格的部署限制也作用于原 CSV 字节校验与回执', () => {
    setTransportLimits({ ...gatewayLimits, max_file_bytes: 1024 });
    expect(uploadLimitLabel(MAX_CSV_BYTES)).toBe('1 KB');
    expect(() => parseCsv('账'.repeat(342))).toThrow('1 KB');
    expect(() => validateReceiptFile('回执.csv', 1025)).toThrow('1 KB');
  });

  it.each([
    null,
    {},
    { ...gatewayLimits, max_file_bytes: -1 },
    { ...gatewayLimits, max_request_bytes: Infinity },
    { ...gatewayLimits, max_file_bytes: '3145728' },
    { ...gatewayLimits, max_response_bytes: 1.5 },
    { ...gatewayLimits, max_request_bytes: 100 },
  ])('忽略不完整或无效 metadata：%j', (value) => {
    setTransportLimits(gatewayLimits);
    setTransportLimits(value);
    expect(getTransportLimits()).toBeNull();
    expect(maxUploadBytes()).toBe(DEFAULT_ATTACHMENT_BYTES);
  });

  it('上限不能放宽领域既有限制，并给 multipart 请求预留余量', () => {
    setTransportLimits({
      max_file_bytes: 30 * 1024 * 1024,
      max_request_bytes: 50 * 1024 * 1024,
      max_response_bytes: 50 * 1024 * 1024,
    });
    expect(maxUploadBytes()).toBe(DEFAULT_ATTACHMENT_BYTES);
    setTransportLimits({ ...gatewayLimits, max_file_bytes: gatewayLimits.max_request_bytes });
    expect(maxUploadBytes()).toBe(gatewayLimits.max_request_bytes - 128 * 1024);
    const snapshot = getTransportLimits()!;
    (snapshot as { max_file_bytes: number }).max_file_bytes = 1;
    expect(maxUploadBytes()).toBe(gatewayLimits.max_request_bytes - 128 * 1024);
  });

  it.each([0, -1, NaN, Infinity, 1.5])('拒绝无效文件大小 %s', (bytes) =>
    expect(uploadSizeError(bytes)).toContain('大于零'),
  );
});

describe('实际提交之前的正文便捷校验', () => {
  it('直连后端无 metadata 时不增加网关请求限制', () => {
    expect(transportBodyError('x'.repeat(5 * 1024 * 1024))).toBe('');
    const form = new FormData();
    form.append('file', new Blob([new Uint8Array(5 * 1024 * 1024)]), '原件.pdf');
    expect(transportBodyError(form)).toBe('');
  });

  it('按 UTF-8 字节而非字符长度校验 JSON', () => {
    setTransportLimits(gatewayLimits);
    expect(transportBodyError('x'.repeat(gatewayLimits.max_request_bytes))).toBe('');
    expect(
      transportBodyError('账'.repeat(Math.floor(gatewayLimits.max_request_bytes / 3) + 1)),
    ).toContain('减少每批记录');
  });

  it('3 MB 单文件可以上传，超出 3 MB 在读取文件正文前拒绝', () => {
    setTransportLimits(gatewayLimits);
    const form = new FormData();
    form.append('file', new Blob([new Uint8Array(3 * 1024 * 1024)]), '原件.pdf');
    expect(transportBodyError(form)).toBe('');
    const oversized = new FormData();
    oversized.append('file', new Blob([new Uint8Array(3 * 1024 * 1024 + 1)]), '原件.pdf');
    expect(transportBodyError(oversized)).toContain('3 MB');
  });

  it('多个文件加字段合计与 multipart 余量不能超过请求上限', () => {
    setTransportLimits(gatewayLimits);
    const form = new FormData();
    for (let i = 0; i < 2; i++)
      form.append('file', new Blob([new Uint8Array(2 * 1024 * 1024)]), `原件${i}.pdf`);
    expect(transportBodyError(form)).toContain('超过大小限制');
    const fields = new FormData();
    fields.append('content', 'x'.repeat(4 * 1024 * 1024));
    expect(transportBodyError(fields)).toContain('超过大小限制');
  });
});
