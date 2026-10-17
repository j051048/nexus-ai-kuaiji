import { describe, expect, test } from 'vitest';
import { DIRECT_UPLOAD_THRESHOLD, attachmentForm } from './upload-client';

describe('attachmentForm 字段契约', () => {
  test('普通文件继续使用 multipart file 字段', () => {
    const file = new File([new Uint8Array([1, 2, 3])], '发票.png', { type: 'image/png' });
    const form = attachmentForm(file, null);
    expect(form.get('file')).toBe(file);
    expect(form.get('upload_id')).toBeNull();
  });

  test('直传凭证只携带 upload_id，不再重复提交文件', () => {
    const file = new File([new Uint8Array([4])], '大文件.pdf', { type: 'application/pdf' });
    const form = attachmentForm(file, 'a'.repeat(32));
    expect(form.get('upload_id')).toBe('a'.repeat(32));
    expect(form.get('file')).toBeNull();
  });

  test('两段式直传阈值保持 2 MiB', () => {
    expect(DIRECT_UPLOAD_THRESHOLD).toBe(2 * 1024 * 1024);
  });
});
