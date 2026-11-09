import type { ServicePlanOverview } from './service-plan-types';

export type ServiceJourneyView = 'materials' | 'review' | 'delivery';
export type ServiceJourneyTarget = {
  view: ServiceJourneyView | 'formal-books';
  finance?: 'aging' | 'periodic';
  bookId: string;
  companyId: string;
  period: string;
};
export type ServiceJourneyStep = {
  view: ServiceJourneyView;
  title: string;
  description: string;
  action: string;
  disabled: boolean;
  reason: string;
  target: ServiceJourneyTarget;
};

/** Navigation only. Each destination re-reads its live grants and source facts. */
export function serviceJourney(
  overview: ServicePlanOverview | null,
  scope: Omit<ServiceJourneyTarget, 'view'>,
  state: {
    loading?: boolean;
    busy?: boolean;
    dirty?: boolean;
    revoked?: boolean;
    enabled?: boolean;
  } = {},
): ServiceJourneyStep[] {
  if (
    !overview ||
    !scope.bookId ||
    !scope.companyId ||
    !/^20\d{2}-(0[1-9]|1[0-2])$/.test(scope.period) ||
    overview.book.id !== scope.bookId ||
    overview.book.company_id !== scope.companyId ||
    overview.book.period !== scope.period
  )
    return [];
  const reason = state.revoked
    ? '当前授权已变更，请重新读取服务单。'
    : state.loading || state.busy
      ? '正在读取或保存，请稍后继续。'
      : state.dirty
        ? '服务单或资料说明尚未保存，请先保存或取消编辑。'
        : state.enabled === false
          ? '当前页面暂不能打开办理入口。'
          : '';
  return [
    {
      view: 'materials' as const,
      title: '准备资料',
      description: '补齐本期原件与业务说明，提交后仍需独立验收。',
      action: '打开本期资料',
    },
    {
      view: 'review' as const,
      title: '会计复核',
      description: '对照原件核对分录和金额，审核通过后才能导出。',
      action: '打开会计复核',
    },
    {
      view: 'delivery' as const,
      title: '核验交付',
      description: '导出不代表外部已接收；核对已有回执与差异。',
      action: '核验交付回执',
    },
  ].map((step) => {
    const denied = step.view !== 'materials' && !overview.permissions.review;
    const blocked = reason || (denied ? '当前账号没有会计复核与交付权限，请联系获授权会计。' : '');
    return {
      ...step,
      target: { ...scope, view: step.view },
      disabled: !!blocked,
      reason: blocked,
    };
  });
}
