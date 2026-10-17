import type { TransportLimits } from './types';

export const DEFAULT_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const MULTIPART_MARGIN_BYTES = 128 * 1024;
let limits: TransportLimits | null = null;

/** Deployment limits come from the public runtime config, never a build-time internal URL. */
export function setTransportLimits(value?: unknown): void {
  if (!value || typeof value !== 'object') {
    limits = null;
    return;
  }
  const candidate = value as Record<string, unknown>;
  const fields = ['max_file_bytes', 'max_request_bytes', 'max_response_bytes'] as const;
  if (
    fields.some(
      (field) =>
        typeof candidate[field] !== 'number' ||
        !Number.isSafeInteger(candidate[field]) ||
        Number(candidate[field]) <= 0,
    ) ||
    Number(candidate.max_request_bytes) <= MULTIPART_MARGIN_BYTES
  ) {
    limits = null;
    return;
  }
  limits = {
    max_file_bytes: Math.min(
      Number(candidate.max_file_bytes),
      Number(candidate.max_request_bytes) - MULTIPART_MARGIN_BYTES,
    ),
    max_request_bytes: Number(candidate.max_request_bytes),
    max_response_bytes: Number(candidate.max_response_bytes),
    direct_upload_max_bytes:
      typeof candidate.direct_upload_max_bytes === 'number' &&
      Number.isSafeInteger(candidate.direct_upload_max_bytes) &&
      candidate.direct_upload_max_bytes > 0
        ? Number(candidate.direct_upload_max_bytes)
        : Number(candidate.max_file_bytes),
  };
}

export function getTransportLimits(): Readonly<TransportLimits> | null {
  return limits ? { ...limits } : null;
}

/** Keep each domain's existing, potentially smaller, limit and read deployment limits at use time. */
export function maxUploadBytes(domainLimit = DEFAULT_ATTACHMENT_BYTES): number {
  return Math.min(domainLimit, limits?.max_file_bytes ?? domainLimit);
}

/** Streaming uploads bypass the inline request limit but remain domain-capped. */
export function maxDirectUploadBytes(domainLimit = DEFAULT_ATTACHMENT_BYTES): number {
  return Math.min(
    domainLimit,
    limits?.direct_upload_max_bytes ?? limits?.max_file_bytes ?? domainLimit,
  );
}

export function uploadLimitLabel(domainLimit = DEFAULT_ATTACHMENT_BYTES): string {
  const bytes = maxUploadBytes(domainLimit);
  // Existing 2,000,000-byte receipt imports use the established decimal MB label.
  const megabytes = bytes === 2_000_000 ? 2 : bytes / (1024 * 1024);
  return megabytes >= 1 ? `${Number(megabytes.toFixed(2))} MB` : `${Math.floor(bytes / 1024)} KB`;
}

export function directUploadLimitLabel(domainLimit = DEFAULT_ATTACHMENT_BYTES): string {
  const bytes = maxDirectUploadBytes(domainLimit);
  return bytes >= 1024 * 1024
    ? `${Number((bytes / (1024 * 1024)).toFixed(2))} MB`
    : `${Math.floor(bytes / 1024)} KB`;
}

export function uploadSizeError(bytes: number, domainLimit = DEFAULT_ATTACHMENT_BYTES): string {
  if (!Number.isSafeInteger(bytes) || bytes <= 0) return '文件大小须大于零。';
  if (bytes > maxUploadBytes(domainLimit))
    return `单份文件不能超过 ${uploadLimitLabel(domainLimit)}，请压缩票据图片或按历史期间拆分后上传。`;
  return '';
}

export function directUploadSizeError(
  bytes: number,
  domainLimit = DEFAULT_ATTACHMENT_BYTES,
): string {
  if (!Number.isSafeInteger(bytes) || bytes <= 0) return '文件大小须大于零。';
  if (bytes > maxDirectUploadBytes(domainLimit))
    return `单份文件不能超过 ${directUploadLimitLabel(domainLimit)}，请压缩文件或按期间拆分后上传。`;
  return '';
}

export function transportBodyError(body: string | FormData | undefined): string {
  if (!limits || body === undefined) return '';
  let bytes: number;
  if (typeof body === 'string') bytes = new TextEncoder().encode(body).length;
  else {
    bytes = MULTIPART_MARGIN_BYTES;
    for (const [key, item] of body.entries()) {
      bytes += new TextEncoder().encode(key).length + 256;
      if (typeof item === 'string') bytes += new TextEncoder().encode(item).length;
      else {
        const invalid = uploadSizeError(item.size);
        if (invalid) return invalid;
        bytes += item.size + new TextEncoder().encode(item.name).length;
      }
    }
  }
  return bytes > limits.max_request_bytes
    ? '本次提交内容超过大小限制，请减少每批记录、压缩附件或按期间拆分后重新提交。'
    : '';
}

export function transportTooLargeMessage(): string {
  return '本次提交或下载内容超过服务的大小限制。请压缩附件、减少每批记录或按期间拆分后重试；大文件原件请联系管理员协助获取。';
}
