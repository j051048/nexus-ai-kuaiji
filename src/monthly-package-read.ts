/**
 * Acceptance rules for one monthly delivery package response.
 *
 * The client portal shows numbers a customer may sign for, so a response is
 * only accepted when it proves it belongs to the package that was requested.
 * Nothing here decides authorisation: the server remains the only judge; this
 * is the last check that the bytes on screen are the bytes we asked for.
 */

import type { MonthlyPackage } from './monthly-package-types';
import { validatePackage } from './monthly-package-types';

export interface PackageIdentity {
  id: string;
  bookId: string;
  companyId?: string;
  period: string;
  manifestSha256?: string;
}

export interface PackageSelection {
  kind: 'exact' | 'superseded' | 'missing';
  item: MonthlyPackage | null;
  message: string;
}

export function acceptPackage(updated: unknown, expected: PackageIdentity): MonthlyPackage | null {
  if (
    !validatePackage(updated, {
      bookId: expected.bookId,
      companyId: expected.companyId,
      period: expected.period,
    })
  )
    return null;
  const item = updated as MonthlyPackage;
  if (item.id !== expected.id) return null;
  if (expected.manifestSha256 && item.manifest_sha256 !== expected.manifestSha256) return null;
  return item;
}

export function selectPackage(packages: MonthlyPackage[], wantedId: string): PackageSelection {
  const item = packages.find((pack) => pack.id === wantedId) ?? null;
  if (!item)
    return {
      kind: 'missing',
      item: null,
      message: '该资料包不在当前企业或月份可见范围内，可能已被撤回；请从当前页面重新选择。',
    };
  if (item.superseded)
    return {
      kind: 'superseded',
      item,
      message: `这是第 ${item.version} 版，已被更新版本取代；内容仍可查看，签收前请确认版本。`,
    };
  return { kind: 'exact', item, message: '' };
}

export function packageFromSearch(search: string): string {
  const wanted = new URLSearchParams(search).get('package') ?? '';
  return /^[0-9a-fA-F-]{8,64}$/.test(wanted) ? wanted : '';
}

export interface ReadRecovery {
  retryable: boolean;
  message: string;
}

export function readRecovery(status?: number): ReadRecovery {
  if (status === 503)
    return {
      retryable: true,
      message: '交付包暂时读取失败，签收状态没有改变；请稍后重试，或刷新后确认版本。',
    };
  if (status === 403 || status === 401)
    return { retryable: false, message: '当前账号不能再查看这个资料包，请联系事务所重新授权。' };
  if (status === 404)
    return {
      retryable: false,
      message: '这个资料包不存在或已不在可见范围内，请从当前页面重新选择。',
    };
  return { retryable: true, message: '读取交付包失败，签收状态没有改变；请重试一次。' };
}
