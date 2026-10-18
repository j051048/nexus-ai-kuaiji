import { useState } from 'react';
import { ArrowLeftRight, SearchCheck, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from './api';
import { Modal, Spinner } from './AppPrimitives';
import type { Member, Session } from './types';

type HandoverPlan = {
  from_user_id: string;
  to_user_id: string;
  plan_fingerprint: string;
  executed: boolean;
  books: { book_id: string; name: string; period: string; role: string; action: string }[];
  plans: {
    book_id: string;
    book_name: string;
    period: string;
    version: number;
    changes: Record<string, string>;
  }[];
  cases: { case_id: string; kind: string; title: string }[];
  skipped: { kind: string; ref: string; title: string; reason: string }[];
  counts: { books: number; plans: number; cases: number; skipped: number };
};

const changeLabels: Record<string, string> = {
  lead_user_id: '主办',
  reviewer_user_id: '独立复核',
  backup_user_id: '备援',
  contact_user_id: '资料联系人',
};

/** 受审计的工作交接向导：预览→填写依据→原子化转派并逐项报告。 */
export function MemberHandoverDialog({
  session,
  from,
  candidates,
  onClose,
  onDone,
}: {
  session: Session;
  from: Member<string>;
  candidates: Member<string>[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<HandoverPlan | null>(null);
  const [done, setDone] = useState<HandoverPlan | null>(null);
  const current = done ?? plan;

  async function preview() {
    if (!target) {
      setError('请先选择接替人。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const value = await api<HandoverPlan>(
        `/firm/members/${encodeURIComponent(from.user_id)}/handover?to=${encodeURIComponent(target)}`,
        session,
      );
      setPlan(value);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function execute() {
    if (!plan) return;
    if (reason.trim().length < 3) {
      setError('请填写至少 3 个字的交接依据。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const value = await api<HandoverPlan>(
        `/firm/members/${encodeURIComponent(from.user_id)}/handover`,
        session,
        {
          method: 'POST',
          body: {
            to_user_id: target,
            reason: reason.trim(),
            plan_fingerprint: plan.plan_fingerprint,
          },
        },
      );
      setDone(value);
      setPlan(null);
      onDone();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  const targetLabel = (user_id: string) => {
    const member = candidates.find((item) => item.user_id === user_id);
    return member ? member.name || member.email : user_id;
  };

  return (
    <Modal
      title="工作交接"
      subtitle={`${from.name || from.email} 名下未结束的账套职责、服务单分工与待处理任务`}
      onClose={onClose}
      busy={busy}
      error={error}
    >
      {done ? (
        <div className="batch-dialog">
          <p role="status" className="batch-summary">
            已完成交接：账套职责 {done.counts.books} 项 · 服务单分工 {done.counts.plans} 项 ·
            待处理任务 {done.counts.cases} 项 · 需人工处理 {done.counts.skipped} 项
          </p>
          <HandoverLists plan={done} targetLabel={targetLabel} />
          <div className="batch-actions">
            <button className="button primary" type="button" onClick={onClose}>
              <ShieldCheck size={15} />
              完成交接
            </button>
          </div>
        </div>
      ) : (
        <div className="batch-dialog">
          <p className="batch-info">
            移交只转派"未结束"的工作：开放期间的账套主办／独立复核职责、最新服务单分工与未关闭任务；
            主办与复核不得同人，接替人必须仍具备对应权限，逐项跳过会说明原因。整个转派一次事务完成并留存审计快照。
          </p>
          <label>
            接替人
            <select
              aria-label="交接接替人"
              value={target}
              disabled={busy}
              onChange={(event) => {
                setTarget(event.target.value);
                setPlan(null);
              }}
            >
              <option value="">选择接替人</option>
              {candidates.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {(member.name || member.email) + ' · ' + member.role}
                </option>
              ))}
            </select>
          </label>
          <div className="batch-actions">
            <button
              className="button secondary"
              type="button"
              disabled={busy || !target}
              onClick={() => void preview()}
            >
              {busy && !plan ? <Spinner /> : <SearchCheck size={15} />}
              预览交接范围
            </button>
          </div>
          {plan && (
            <>
              <p role="status" className="batch-summary">
                待交接：账套职责 {plan.counts.books} 项 · 服务单分工 {plan.counts.plans} 项 ·
                待处理任务 {plan.counts.cases} 项 · 需人工处理 {plan.counts.skipped} 项
              </p>
              <HandoverLists plan={plan} targetLabel={targetLabel} />
              <label>
                操作依据
                <textarea
                  aria-label="交接操作依据"
                  minLength={3}
                  maxLength={1000}
                  required
                  value={reason}
                  disabled={busy}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <div className="batch-actions">
                <button
                  className="button secondary"
                  type="button"
                  disabled={busy}
                  onClick={onClose}
                >
                  取消
                </button>
                <button
                  className="button primary"
                  type="button"
                  disabled={busy || reason.trim().length < 3}
                  onClick={() => void execute()}
                >
                  {busy ? <Spinner /> : <ArrowLeftRight size={15} />}
                  执行交接
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

function HandoverLists({
  plan,
  targetLabel,
}: {
  plan: HandoverPlan;
  targetLabel: (user_id: string) => string;
}) {
  return (
    <div className="batch-rows" aria-label="工作交接范围">
      {plan.books.map((item) => (
        <article key={'book-' + item.book_id} className="batch-row">
          <header>
            <strong>
              {item.name} · {item.period}
            </strong>
            <span className="batch-badge">
              {item.action === 'merge' ? '接替人已持有' : '账套职责'}
            </span>
          </header>
          <p>
            以「{item.role === 'reviewer' ? '独立复核' : '主办'}」职责移交给{' '}
            {targetLabel(plan.to_user_id)}。
          </p>
        </article>
      ))}
      {plan.plans.map((item) => (
        <article key={'plan-' + item.book_id} className="batch-row">
          <header>
            <strong>
              {item.book_name} · {item.period}
            </strong>
            <span className="batch-badge">服务单分工 v{item.version}</span>
          </header>
          <p>
            {Object.keys(item.changes)
              .map((field) => changeLabels[field] ?? field)
              .join('、')}{' '}
            将转派给 {targetLabel(plan.to_user_id)}。
          </p>
        </article>
      ))}
      {plan.cases.map((item) => (
        <article key={'case-' + item.case_id} className="batch-row">
          <header>
            <strong>{item.title}</strong>
            <span className="batch-badge">待处理任务</span>
          </header>
          <p>移交后由 {targetLabel(plan.to_user_id)} 继续处理。</p>
        </article>
      ))}
      {plan.skipped.map((item) => (
        <article key={'skip-' + item.kind + item.ref} className="batch-row blocked">
          <header>
            <strong>{item.title || item.ref}</strong>
            <span className="batch-badge blocked">需人工处理</span>
          </header>
          <p className="batch-message">{item.reason}</p>
        </article>
      ))}
      {!plan.books.length && !plan.plans.length && !plan.cases.length && !plan.skipped.length && (
        <p className="field-help">该成员没有需要移交的未结束工作。</p>
      )}
    </div>
  );
}
