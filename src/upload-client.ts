import { api, ApiError } from './api';
import { currentAccessToken } from './auth';
import type { Session } from './types';

/** 超过该阈值的原件改走两段式直传，绕开网关的小请求限制（网关上限 4 MiB）。 */
export const DIRECT_UPLOAD_THRESHOLD = 2 * 1024 * 1024;

export type DirectUploadPurpose =
  | 'intake_attachment'
  | 'event_attachment'
  | 'case_attachment'
  | 'material_attachment'
  | 'invoice_import'
  | 'bank_import'
  | 'tax_filing_attachment';

export function sameOriginUploadHeaders(
  firmId: string | null,
  token: string,
  mediaType: string,
): Record<string, string> {
  return {
    'Content-Type': mediaType || 'application/octet-stream',
    Authorization: `Bearer ${token}`,
    ...(firmId ? { 'X-Firm-Id': firmId } : {}),
  };
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * 大文件两段式上传：先申请一次性短效凭证，直传私有存储，再回到后端校验。
 * 返回 null 表示文件不大，调用方继续使用原来的 multipart 请求。
 */
export async function prepareDirectUpload(
  session: Session,
  file: File,
  purpose: DirectUploadPurpose,
): Promise<string | null> {
  if (file.size <= DIRECT_UPLOAD_THRESHOLD) return null;
  return prepareUploadIntent(session, file, purpose);
}

/** 固定走两段式上传并返回凭证 id（用于没有 multipart 通道的导入接口）。 */
export async function prepareUploadIntent(
  session: Session,
  file: File,
  purpose: DirectUploadPurpose,
): Promise<string> {
  const intent = await api<{
    id: string;
    method: string;
    url: string;
    headers: Record<string, string>;
  }>('/uploads', session, {
    method: 'POST',
    body: {
      purpose,
      filename: file.name,
      media_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      sha256: await sha256Hex(await file.arrayBuffer()),
    },
  });
  const sameOrigin = intent.url.startsWith('/');
  const headers: Record<string, string> = sameOrigin
    ? sameOriginUploadHeaders(
        session.firm_id,
        await currentAccessToken(session),
        file.type || 'application/octet-stream',
      )
    : { ...intent.headers };
  let response: Response;
  try {
    response = await fetch(intent.url, {
      method: intent.method,
      headers,
      body: file,
      credentials: 'omit',
      cache: 'no-store',
    });
  } catch {
    throw new ApiError('文件直传失败，请检查网络后重试。', 0);
  }
  if (!response.ok) throw new ApiError('文件直传失败，请重试或缩小文件后重传。', 502);
  await api(`/uploads/${encodeURIComponent(intent.id)}/complete`, session, { method: 'POST' });
  return intent.id;
}

/** 附件写入的字段契约：直传凭证走 upload_id，普通文件走 file。 */
export function attachmentForm(file: File, directUploadId: string | null): FormData {
  const data = new FormData();
  if (directUploadId) data.append('upload_id', directUploadId);
  else data.append('file', file);
  return data;
}
