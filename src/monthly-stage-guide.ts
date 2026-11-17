import {
  monthlyClosingLabel,
  monthlyCount,
  monthlyRowDestination,
  type MonthlyClientRow,
  type MonthlyDestination,
} from './monthly-workbench-types';

export type MonthlyStageKey =
  'intake' | 'reconcile' | 'prepare' | 'review' | 'delivery' | 'archive';

export interface MonthlyStage {
  key: MonthlyStageKey;
  label: string;
  primary: string;
  secondary: string;
  attention: boolean;
  done: boolean;
  current: boolean;
  onOpen?: () => void;
}

function zero(value: number | null | undefined): boolean {
  return value != null && Number.isSafeInteger(value) && value === 0;
}

function positive(value: number | null | undefined): boolean {
  return value != null && Number.isSafeInteger(value) && value > 0;
}

/**
 * Projects the six monthly stages from the same server-owned counts the
 * workbench already uses. This is a navigation projection only: it does not
 * create a second business state machine or grant any permission.
 */
export function monthlyStages(
  row: MonthlyClientRow,
  open: (destination: MonthlyDestination) => void,
): MonthlyStage[] {
  const sources = row.materials.sources;
  const pending = row.drafts.pending;
  const approved = row.drafts.approved;
  const exported = row.drafts.exported;
  const delivery = row.delivery;
  const stages: Omit<MonthlyStage, 'current' | 'onOpen'>[] = [
    {
      key: 'intake',
      label: '资料收集',
      primary: `${monthlyCount(sources)} 份来源`,
      secondary: `待补资料 ${monthlyCount(row.materials.missing)} · 待验收 ${monthlyCount(row.material_requests?.submitted)}`,
      attention: positive(row.materials.missing),
      done: positive(sources) && zero(row.materials.missing),
    },
    {
      key: 'reconcile',
      label: '差异核对',
      primary: `${monthlyCount(row.exceptions.open)} 未结`,
      secondary: `差异 ${monthlyCount(delivery.differences)} · 待补件 ${monthlyCount(row.exceptions.awaiting_info)}`,
      attention: positive(row.exceptions.open) || positive(delivery.differences),
      done: zero(row.exceptions.open) && zero(delivery.differences),
    },
    {
      key: 'prepare',
      label: '凭证准备',
      primary: `${monthlyCount(row.materials.business_pending)} 待移交`,
      secondary: `待审凭证 ${monthlyCount(pending)}`,
      attention: positive(row.materials.business_pending),
      done: positive(sources) && zero(row.materials.business_pending) && zero(pending),
    },
    {
      key: 'review',
      label: '会计复核',
      primary: `${monthlyCount(pending)} 待审`,
      secondary: `已审待导出 ${monthlyCount(approved)} · 已导出 ${monthlyCount(exported)}`,
      attention: positive(pending),
      done: zero(pending) && (positive(approved) || positive(exported)),
    },
    {
      key: 'delivery',
      label: '交付核验',
      primary: `${monthlyCount(delivery.unverified)} 待核验`,
      secondary: `未知 ${monthlyCount(delivery.unknown)} · 差异 ${monthlyCount(delivery.differences)}`,
      attention:
        positive(delivery.unverified) ||
        positive(delivery.unknown) ||
        positive(delivery.differences),
      done:
        zero(delivery.unverified) &&
        zero(delivery.unknown) &&
        zero(delivery.differences) &&
        positive(delivery.verified),
    },
    {
      key: 'archive',
      label: '确认归档',
      primary: monthlyClosingLabel(row.closing.state),
      secondary: `未结阻断 ${monthlyCount(row.closing.blockers)}`,
      attention: row.closing.state === 'recheck' || row.closing.state === 'restricted',
      done: row.closing.state === 'closed',
    },
  ];
  const preferred =
    row.work?.target === 'service'
      ? 'intake'
      : row.work?.target === 'review'
        ? 'review'
        : row.work?.target === 'delivery'
          ? 'delivery'
          : undefined;
  const current =
    preferred ??
    stages.find((stage) => stage.attention)?.key ??
    stages.find((stage) => !stage.done)?.key ??
    'archive';
  const destinations: Record<
    MonthlyStageKey,
    [MonthlyDestination['view'], MonthlyDestination['filter']?]
  > = {
    intake: ['business'],
    reconcile: ['cases'],
    prepare: ['business'],
    review: ['review', 'pending'],
    delivery: ['delivery'],
    archive: ['closing'],
  };
  return stages.map((stage) => {
    const [view, filter] = destinations[stage.key];
    const destination = monthlyRowDestination(row, view, filter);
    return {
      ...stage,
      current: stage.key === current,
      ...(destination ? { onOpen: () => open(destination) } : {}),
    };
  });
}
