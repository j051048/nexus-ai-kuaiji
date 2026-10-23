export type FirmClient = {
  company_id: string;
  company_name: string;
  book_id: string | null;
  period: string;
  period_status: string;
  stage: 'unopened' | 'intake' | 'accounting' | 'review' | 'delivery' | 'closed';
  tasks: { intake: number; review: number; exception: number; delivery: number; overdue: number };
  lead_user_id: string | null;
  lead_name: string;
  reviewer_name: string;
  due_on: string;
  risk: number;
};

export type FirmSnapshot = {
  period: string;
  summary: {
    clients: number;
    opened: number;
    closed: number;
    overdue: number;
    exceptions: number;
    risk_clients: number;
    projection_stale: number;
  };
  stages: Record<string, number>;
  companies: FirmClient[];
  workload: {
    user_id: string | null;
    name: string;
    clients: number;
    pending: number;
    exceptions: number;
    overdue: number;
    risk_clients: number;
  }[];
  risks: FirmClient[];
};

export const firmStageLabels: Record<FirmClient['stage'], string> = {
  unopened: '待开通',
  intake: '资料收集中',
  accounting: '凭证处理中',
  review: '待会计复核',
  delivery: '待确认交付',
  closed: '已完成月结',
};

export function coordinationReasons(row: FirmClient): string[] {
  const reasons: string[] = [];
  if (row.tasks.overdue > 0) reasons.push(`${row.tasks.overdue} 项待办已逾期`);
  if (row.tasks.exception > 0) reasons.push(`${row.tasks.exception} 项异常待处理`);
  if (!row.book_id) reasons.push('本月账套待开通');
  if (row.book_id && !row.lead_user_id) reasons.push('主办会计待安排');
  return reasons;
}

export function selectCoordinationClients(rows: FirmClient[]): FirmClient[] {
  return rows
    .filter((row) => coordinationReasons(row).length > 0)
    .sort(
      (a, b) =>
        b.tasks.overdue - a.tasks.overdue ||
        b.tasks.exception - a.tasks.exception ||
        a.company_name.localeCompare(b.company_name, 'zh-CN') ||
        a.company_id.localeCompare(b.company_id),
    );
}

export function dueLabel(value: string | null | undefined): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '期限待确认';
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    return '期限待确认';
  }
  return `${Number(value.slice(5, 7))}月${Number(value.slice(8, 10))}日`;
}
