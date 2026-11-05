import type { ReactNode } from 'react';
import type { BusinessEvent } from './business-types';

/** The service returns actions in chronological order. This is guidance for
 * the current returned record, never a permission or a guessed field mapping. */
export function ReturnedBusinessNotice({
  event,
  children,
}: {
  event: BusinessEvent;
  children?: ReactNode;
}) {
  if (event.status !== 'returned') return null;
  const action = [...event.actions].reverse().find((item) => item.decision === 'return');
  const stage =
    action?.stage === 'department'
      ? '部门审批退回'
      : action?.stage === 'finance'
        ? '财务核验退回'
        : '事项已退回';
  return (
    <section className="business-return-notice" aria-label="本次退回与补充说明">
      <strong>{stage}</strong>
      <p>{action?.reason?.trim() || '尚未取得具体退回原因，请先查看审批记录或联系处理人。'}</p>
      <small>原信息和附件已保留。按退回说明补充后，核对并重新提交同一事项。</small>
      {children && <div className="business-return-actions">{children}</div>}
    </section>
  );
}
