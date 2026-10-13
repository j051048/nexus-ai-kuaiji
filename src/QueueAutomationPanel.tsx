import { useCallback, useEffect, useRef, useState } from 'react';
import { Clock3, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import { automationReason, durationLabel, resumeReasonLabels } from './queue-automation-helpers';
import type { QueueAutomationPolicy, JobMetricStage } from './queue-automation-helpers';
import './queue-automation.css';

interface ResumeEvent {
  job_id: string;
  generation: number;
  created_at: string;
  reason: {
    blocker: string;
    old_fact_version: number;
    new_fact_version: number;
    accepted_response_ids: string[];
  };
}
const message = (failure: unknown) =>
  failure instanceof Error ? failure.message : '续跑配置暂不可用';

export function QueueAutomationPanel({
  session,
  bookId,
  companyId,
  paused = false,
}: {
  session: Session;
  bookId: string;
  companyId: string;
  paused?: boolean;
}) {
  const [policy, setPolicy] = useState<QueueAutomationPolicy | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [supplements, setSupplements] = useState(true);
  const [metrics, setMetrics] = useState<JobMetricStage[]>([]);
  const [events, setEvents] = useState<ResumeEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const target = `${session.scope_key}:${companyId}:${bookId}`;
  const targetRef = useRef(target);
  targetRef.current = target;
  const epoch = useRef(0);
  const lock = useRef(false);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const value = ++epoch.current;
      try {
        const settings = await api<QueueAutomationPolicy>(
          `/intelligence/companies/${encodeURIComponent(companyId)}/automation-policy`,
          session,
          { signal },
        );
        const evidence = await api<{ events: ResumeEvent[] }>(
          `/intelligence/books/${encodeURIComponent(bookId)}/resume-events?limit=10`,
          session,
          { signal },
        );
        const timing = settings.permissions.manage
          ? await api<{ stages: JobMetricStage[] }>(
              `/intelligence/companies/${encodeURIComponent(companyId)}/job-metrics?days=7`,
              session,
              { signal },
            )
          : { stages: [] };
        if (signal?.aborted || epoch.current !== value) return;
        setPolicy(settings);
        setEnabled(settings.enabled);
        setSupplements(settings.allow_verified_supplements);
        setEvents(evidence.events);
        setMetrics(timing.stages);
        setError('');
      } catch (failure) {
        if (!signal?.aborted && epoch.current === value) {
          setPolicy(null);
          setMetrics([]);
          setEvents([]);
          setError(message(failure));
        }
      }
    },
    [session, companyId, bookId],
  );
  useEffect(() => {
    epoch.current += 1;
    setPolicy(null);
    setMetrics([]);
    setEvents([]);
    setReason('');
    setError('');
    setNotice('');
    setBusy(false);
    setOpen(false);
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current += 1;
    };
  }, [load]);
  async function save() {
    if (!policy?.permissions.manage || lock.current || paused) return;
    const issue = automationReason(reason);
    if (issue) {
      setError(issue);
      return;
    }
    const startedTarget = target;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await api<QueueAutomationPolicy>(
        `/intelligence/companies/${encodeURIComponent(companyId)}/automation-policy`,
        session,
        {
          method: 'PUT',
          body: {
            expected_version: policy.version,
            enabled,
            allow_verified_supplements: supplements,
            reason: reason.trim(),
          },
        },
      );
      if (targetRef.current !== startedTarget) return;
      setPolicy(result);
      setReason('');
      setNotice(
        result.enabled
          ? '已启用受控续跑。后台按企业公平巡检，恢复前重新检查原执行人当前权限、补件、期间和额度。'
          : '已关闭自动续跑，保留既有任务与恢复记录。',
      );
    } catch (failure) {
      if (targetRef.current === startedTarget) setError(message(failure));
    } finally {
      lock.current = false;
      if (targetRef.current === startedTarget) setBusy(false);
    }
  }
  return (
    <div className="queue-automation" aria-label="后台续跑与耗时">
      <div className="queue-automation-heading">
        <span>
          <ShieldCheck size={15} />
          条件自动续跑 · {policy ? (policy.enabled ? '已启用' : '默认关闭') : '读取中'}
        </span>
        <button
          className="text-button"
          disabled={busy || paused}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? '收起续跑配置' : '续跑配置与耗时'}
        </button>
      </div>
      {error && (
        <>
          <p className="inline-error" role="alert">
            <TriangleAlert size={14} />
            {error}
          </p>
          <button className="text-button" disabled={busy || paused} onClick={() => void load()}>
            重新读取续跑配置
          </button>
        </>
      )}
      {open && policy && (
        <div className="queue-automation-body">
          <p>
            配置恢复、积压释放、额度补齐或补件独立核验后，可恢复符合条件的未发送分录任务。人工修改、取消、已审核、来源替代和结果不确定的请求仍须人工处理。
          </p>
          {policy.permissions.manage ? (
            <div className="queue-automation-controls">
              <label>
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={busy || paused}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
                允许本企业条件自动续跑
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={supplements}
                  disabled={busy || paused}
                  onChange={(event) => setSupplements(event.target.checked)}
                />
                允许独立核验且已关闭的补件更新事实版本
              </label>
              <textarea
                rows={2}
                maxLength={1000}
                aria-label="自动续跑配置依据"
                value={reason}
                disabled={busy || paused}
                onChange={(event) => setReason(event.target.value)}
                placeholder="填写企业启用范围及配置依据"
              />
              <button
                className="button secondary small"
                disabled={busy || paused || !!automationReason(reason)}
                onClick={() => void save()}
              >
                保存续跑配置
              </button>
            </div>
          ) : (
            <p className="field-help">企业管理员配置续跑范围；会计仍需复核生成的草稿。</p>
          )}
          {notice && (
            <p className="form-success" role="status">
              {notice}
            </p>
          )}
          <div className="queue-automation-subhead">
            <strong>
              <Clock3 size={14} />
              最近续跑依据
            </strong>
            <button className="text-button" disabled={busy || paused} onClick={() => void load()}>
              <RefreshCw size={12} />
              刷新续跑与耗时
            </button>
          </div>
          {events.length ? (
            events.map((item) => (
              <div className="queue-resume-event" key={`${item.job_id}:${item.generation}`}>
                <strong>
                  {resumeReasonLabels[item.reason.blocker] ?? '已核验阻断条件'} · 第{' '}
                  {item.generation} 轮
                </strong>
                <span>
                  事实 v{item.reason.old_fact_version} → v{item.reason.new_fact_version} ·{' '}
                  {item.reason.accepted_response_ids.length} 份核验补件 ·{' '}
                  {new Date(item.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}
                </span>
              </div>
            ))
          ) : (
            <p className="field-help">本账套尚无自动续跑记录。</p>
          )}
          {policy.permissions.manage && (
            <>
              <strong className="queue-automation-subhead">本企业最近 7 日后台耗时</strong>
              {metrics.length ? (
                <div className="queue-metric-grid">
                  {metrics.map((row) => (
                    <div key={row.stage}>
                      <span>{row.label}</span>
                      <strong>
                        {row.stage === 'ocr_cache_reuse'
                          ? `${row.samples} 次`
                          : durationLabel(row.mean_ms)}
                      </strong>
                      <small>
                        {row.stage === 'ocr_cache_reuse'
                          ? '成功复用原件识别结果'
                          : `${row.samples} 次 · 最长 ${durationLabel(row.max_ms)}`}
                      </small>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="field-help">尚无阶段样本，后台任务执行后持续保存。</p>
              )}
              <p className="field-help">
                统计汇总所有
                worker。领取包含连接和锁等待；执行段包含校验、原件准备及落库，未计事务提交耗时。业务证据与模型输入不会出现在耗时指标中。
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
