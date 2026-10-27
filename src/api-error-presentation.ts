import { ApiError } from './api';

/**
 * 统一的错误呈现：把状态码、错误码与网络故障翻译成「发生了什么 + 现在做什么」。
 * 原文照抄服务端说明会把内部措辞和可能含标识的细节带到界面上，所以只用受控文案。
 */
export type ErrorKind =
  | 'auth'
  | 'mfa'
  | 'forbidden'
  | 'conflict'
  | 'too_large'
  | 'invalid'
  | 'unavailable'
  | 'network'
  | 'unknown';

export type ErrorPresentation = {
  kind: ErrorKind;
  title: string;
  detail: string;
  nextStep: string;
  requestId: string;
  retryable: boolean;
  /** 需要重新登录（不是重试）。 */
  relogin: boolean;
  /** 原始技术信息，仅用于「复制给技术支持」。 */
  technical: string;
};

const DETAIL_LIMIT = 160;

function condense(message: string): string {
  const text = message.replace(/\s+/g, ' ').trim();
  return text.length > DETAIL_LIMIT ? `${text.slice(0, DETAIL_LIMIT - 1)}…` : text;
}

function technicalOf(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? `[${error.code}] ` : '';
    return condense(`${code}${error.status} ${error.message}`);
  }
  if (error instanceof Error) return condense(error.message);
  return '';
}

/**
 * AbortError 表示这次读取被主动放弃（作用域切换、连续重试），不是故障，
 * 调用方应据此不渲染任何错误态。
 */
export function presentApiError(error: unknown, requestId = ''): ErrorPresentation | null {
  if (error instanceof DOMException && error.name === 'AbortError') return null;
  if (error instanceof Error && error.name === 'AbortError') return null;

  if (error instanceof TypeError) {
    return {
      kind: 'network',
      title: '网络请求没有发出或没有回应',
      detail: '浏览器未能完成这次请求，通常与网络中断或地址不可达有关。',
      nextStep: '检查网络后重试；反复失败时把请求编号发给技术支持。',
      requestId,
      retryable: true,
      relogin: false,
      technical: technicalOf(error),
    };
  }

  if (!(error instanceof ApiError)) {
    return {
      kind: 'unknown',
      title: '出现了未预期的错误',
      detail: '界面无法判断这次失败的原因。',
      nextStep: '重试一次；仍然失败时把请求编号发给技术支持。',
      requestId,
      retryable: true,
      relogin: false,
      technical: technicalOf(error),
    };
  }

  const technical = technicalOf(error);
  const base = { requestId, technical };

  if (error.status === 401)
    return {
      ...base,
      kind: 'auth',
      title: '登录状态已失效',
      detail: '服务端没有接受当前凭证，需要重新登录后再继续。',
      nextStep: '重新登录；未保存的输入会被保留在当前页面。',
      retryable: false,
      relogin: true,
    };

  if (error.status === 403 && error.code === 'admin_mfa_required')
    return {
      ...base,
      kind: 'mfa',
      title: '这一步需要二次验证',
      detail: '管理员操作要求在最近一次强认证后重新验证身份。',
      nextStep: '完成二次验证后回到本页重试。',
      retryable: false,
      relogin: true,
    };

  if (error.status === 403)
    return {
      ...base,
      kind: 'forbidden',
      title: '当前岗位没有这项权限',
      detail: '服务端按帐套与岗位重新判定后拒绝了这次操作。',
      nextStep: '请联系本账套负责人确认岗位与授权范围。',
      retryable: false,
      relogin: true,
    };

  if (error.status === 409)
    return {
      ...base,
      kind: 'conflict',
      title: '数据已被其他操作更新',
      detail: '提交时的版本与当前版本不一致，本次操作没有生效。',
      nextStep: '重新读取当前数据，确认后再提交。',
      retryable: false,
      relogin: false,
    };

  if (error.status === 413)
    return {
      ...base,
      kind: 'too_large',
      title: '内容超过单次上限',
      detail: '单次提交或导出超过服务端允许的大小。',
      nextStep: '按提示压缩范围或分批处理后重试。',
      retryable: false,
      relogin: false,
    };

  if (error.status === 422)
    return {
      ...base,
      kind: 'invalid',
      title: '提交内容未通过校验',
      detail: condense(error.message) || '服务端判定提交内容不合法。',
      nextStep: '按提示修正字段后重新提交。',
      retryable: false,
      relogin: false,
    };

  if (error.status === 502 || error.status === 503 || error.status === 504)
    return {
      ...base,
      kind: 'unavailable',
      title: '服务暂时不可用',
      detail: '服务端没有完成这次请求，可能正在重连或维护。',
      nextStep: '稍后重试；持续失败时把请求编号发给技术支持。',
      retryable: true,
      relogin: false,
    };

  return {
    ...base,
    kind: 'unknown',
    title: '请求未成功',
    detail: '界面无法判断这次失败的原因。',
    nextStep: '重试一次；仍然失败时把请求编号发给技术支持。',
    retryable: true,
    relogin: false,
  };
}
