import { emptyWorkspaceRoute, workspaceRouteUrl } from './workspace-route';

/** Server-declared destinations for a blocked month-close check. The list is a
 * whitelist: an unexpected kind is explained to the user, never guessed into a
 * page or turned into an arbitrary URL. */
export const closeActionKinds = [
  'periodic',
  'review',
  'manual_vouchers',
  'subledger',
  'bank_reconciliation',
  'formal_books',
  'reports',
] as const;
export type CloseActionKind = (typeof closeActionKinds)[number];

export interface CloseActionTarget {
  kind: string;
  book_id: string;
  period: string;
}

export interface CloseActionContext {
  bookId: string;
  period: string;
  companyId?: string;
  canReview: boolean;
}

export type CloseDestination =
  | { mode: 'inline'; panel: 'subledger' | 'bank'; label: string }
  | { mode: 'link'; href: string; label: string }
  | { mode: 'manual'; label: string; reason: string };

const labels: Record<CloseActionKind, string> = {
  periodic: '去复核期末结转',
  review: '去复核待审凭证',
  manual_vouchers: '去处理手工凭证',
  subledger: '去配置控制科目',
  bank_reconciliation: '去补银行账户与覆盖',
  formal_books: '查看账簿差额明细',
  reports: '查看报表复核',
};

function manual(reason: string): CloseDestination {
  return { mode: 'manual', label: '需要人工办理', reason };
}

function route(
  view: 'review' | 'formal-books',
  context: CloseActionContext,
  bookId: string,
): string {
  return workspaceRouteUrl(
    {
      ...emptyWorkspaceRoute,
      view,
      bookId: view === 'formal-books' ? bookId : '',
      reviewBookId: view === 'review' ? bookId : '',
      companyId: context.companyId ?? '',
      period: context.period,
    },
    undefined,
  );
}

/** Resolve one blocked check into an actionable destination.
 *
 * Rules: the target must be a known kind, must carry both book and period, and
 * must match the scope currently on screen. A mismatch is explained instead of
 * being followed, because a stale link must never edit another ledger. Write
 * entries are only offered when the server said this user may review. */
export function resolveCloseAction(
  target: CloseActionTarget | undefined,
  context: CloseActionContext,
): CloseDestination {
  if (!target)
    return manual(
      '该检查项由历史预检生成，未附带可直达的办理入口；请按“下一步”处理后重新预检。',
    );
  const kind = target.kind;
  if (!closeActionKinds.includes(kind as CloseActionKind))
    return manual(
      `该检查项要求的办理入口（${kind || '未提供'}）不在支持范围内；系统不会猜测页面，请按“下一步”处理。`,
    );
  if (!target.book_id || !target.period)
    return manual('该检查项缺少账套或月份，不能直接跳转；请重新生成月结预检后再办理。');
  if (target.book_id !== context.bookId || target.period !== context.period)
    return manual(
      '该检查项属于其他账套或月份；请切换到对应账套与月份后办理，本页不会跨账套操作。',
    );
  switch (kind as CloseActionKind) {
    case 'subledger':
    case 'bank_reconciliation':
      if (!context.canReview)
        return manual(
          '办理需要本账套复核权限；你当前只能查看，请联系有复核权限的同事办理。',
        );
      return {
        mode: 'inline',
        panel: kind === 'subledger' ? 'subledger' : 'bank',
        label: labels[kind as CloseActionKind],
      };
    case 'reports':
      return { mode: 'link', href: '#formal-close-report', label: labels.reports };
    case 'formal_books':
      return {
        mode: 'link',
        href: `${route('formal-books', context, target.book_id)}#formal-close`,
        label: labels.formal_books,
      };
    default:
      return {
        mode: 'link',
        href: route('review', context, target.book_id),
        label: labels[kind as CloseActionKind],
      };
  }
}
