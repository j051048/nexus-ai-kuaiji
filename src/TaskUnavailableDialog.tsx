import { AlertTriangle, ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react';

export type TaskUnavailableReason = 'stale' | 'forbidden' | 'missing' | 'completed';

export type TaskUnavailableTask = {
  kind: string;
  ref: string;
  company_id: string;
  company_name: string;
  period?: string | null;
  action_label: string;
  business_action?: string;
  current_handler?: string;
};

const COPY: Record<TaskUnavailableReason, { title: string; body: string }> = {
  stale: {
    title: '待办内容已更新',
    body: '这个待办在你打开前已经发生变化。为避免在旧版本上办理，页面没有进入原对象。',
  },
  forbidden: {
    title: '权限已撤销',
    body: '当前账号已无权访问或办理这个待办。列表已按最新授权刷新。',
  },
  missing: {
    title: '待办不可见',
    body: '对象不存在、已由他人办完，或不属于当前单位。为不泄露对象是否存在，统一显示此页。',
  },
  completed: {
    title: '待办已办完',
    body: '该事项已由他人办理。请返回队列查看最新状态；如需查看已审核记录，请从账簿进入。',
  },
};

export function TaskUnavailableDialog({
  task,
  reason,
  handler,
  detail,
  busy,
  onClose,
  onOpenLatest,
  onViewRecord,
}: {
  task: TaskUnavailableTask;
  reason: TaskUnavailableReason;
  handler?: string;
  detail?: string;
  busy?: boolean;
  onClose: () => void;
  onOpenLatest?: () => void;
  onViewRecord?: () => void;
}) {
  const copy = COPY[reason];
  return (
    <div className="task-unavailable-backdrop" role="presentation">
      <section
        className="task-unavailable-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-unavailable-title"
      >
        <div className="task-unavailable-icon" aria-hidden="true">
          <AlertTriangle size={22} />
        </div>
        <p className="task-unavailable-source">{task.business_action || task.action_label}</p>
        <h2 id="task-unavailable-title">{copy.title}</h2>
        <p>{detail || copy.body}</p>
        <dl>
          <div>
            <dt>企业</dt>
            <dd>{task.company_name}</dd>
          </div>
          <div>
            <dt>期间</dt>
            <dd>{task.period || '未指定'}</dd>
          </div>
          {handler && (
            <div>
              <dt>当前处理人</dt>
              <dd>{handler}</dd>
            </div>
          )}
        </dl>
        <div className="task-unavailable-actions">
          <button type="button" className="secondary-button" onClick={onClose} disabled={busy}>
            <ArrowLeft size={15} />
            返回队列
          </button>
          {reason === 'stale' && onOpenLatest && (
            <button type="button" className="primary-button" onClick={onOpenLatest} disabled={busy}>
              <RefreshCw size={15} className={busy ? 'spinner' : undefined} />
              打开最新内容
            </button>
          )}
          {reason === 'completed' && onViewRecord && (
            <button type="button" className="primary-button" onClick={onViewRecord} disabled={busy}>
              <ExternalLink size={15} />
              查看处理记录
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
