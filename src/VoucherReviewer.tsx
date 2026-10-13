import { useEffect, useState } from 'react';
import {
  ArrowRight,
  Check,
  CheckCircle2,
  FileSearch,
  FileText,
  FolderOpen,
  Plus,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { formatCents, formatMoney, moneyToCents } from './csv';
import { JournalEntryGrid } from './JournalEntryGrid';
import { Spinner } from './AppPrimitives';
import { IntelligencePanel } from './IntelligencePanel';
import { BankDuplicatePanel } from './BankDuplicatePanel';
import { SourceHistoryPanel } from './SourceHistoryPanel';
import type { Session, Draft, BookDetail, JournalLine } from './types';
const statusLabels: Record<Draft['status'], string> = {
  draft: '待审核',
  approved: '已审核',
  exported: '已导出',
};
export function Reviewer({
  session,
  draft,
  detail,
  busy,
  externalBusy,
  onIntelligenceBusyChange,
  onIntelligenceChanged,
  onDirtyChange,
  onOpenCase,
  onSave,
  onApprove,
  onEvidence,
  onBusinessEvidence,
  onOpenBatch,
  onAssistant,
}: {
  session: Session;
  draft: Draft;
  detail: BookDetail;
  busy: string;
  externalBusy: boolean;
  onIntelligenceBusyChange: (busy: boolean) => void;
  onIntelligenceChanged: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onOpenCase: (target: {
    eventId?: string;
    bookId: string;
    draftId: string;
    title: string;
  }) => void;
  onSave: (
    draft: Draft,
    description: string,
    lines: JournalLine[],
    correctionReason?: string,
  ) => Promise<void>;
  onApprove: (draft: Draft) => Promise<void>;
  onEvidence: (evidenceRef: string) => Promise<void>;
  onBusinessEvidence: (eventId: string) => void;
  onOpenBatch: (batchId: string) => void;
  onAssistant: () => void;
}) {
  const [description, setDescription] = useState(draft.description);
  const [correctionReason, setCorrectionReason] = useState('');
  const [intelligenceOpen, setIntelligenceOpen] = useState(true);
  const [lines, setLines] = useState<JournalLine[]>(draft.lines.map((line) => ({ ...line })));
  const source = detail.sources.find((item) => item.id === draft.source_id);
  const [evidenceRef, setEvidenceRef] = useState(source?.evidence_ref ?? '');
  const locked = draft.status !== 'draft' || !!draft.source_inactive;
  const readOnly = locked || !detail.book.permissions.edit;
  useEffect(() => {
    setDescription(draft.description);
    setCorrectionReason('');
    setLines(draft.lines.map((line) => ({ ...line })));
  }, [draft.id, draft.version, draft.description, draft.lines]);
  useEffect(() => {
    setEvidenceRef(source?.evidence_ref ?? '');
  }, [source?.evidence_ref]);
  const dirty =
    description !== draft.description || JSON.stringify(lines) !== JSON.stringify(draft.lines);
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  let debit = 0n;
  let credit = 0n;
  let amountError = '';
  try {
    for (const line of lines) {
      debit += moneyToCents(line.debit);
      credit += moneyToCents(line.credit);
    }
  } catch {
    amountError = '金额须为非负数字，最多保留两位小数。';
  }
  const balanced = !amountError && debit === credit && debit > 0n;
  return (
    <div className="review-content">
      {!locked && detail.book.permissions.review && (
        <button
          type="button"
          className="button secondary small"
          disabled={!!busy || dirty}
          onClick={() =>
            onOpenCase({
              eventId: draft.business_origin?.event_id,
              bookId: detail.book.id,
              draftId: draft.id,
              title: draft.description,
            })
          }
        >
          <TriangleAlert size={14} />
          指派异常 / 请求补件
        </button>
      )}
      <div className="review-status">
        <span className={`badge ${draft.status}`}>{statusLabels[draft.status]}</span>
        <span>
          版本 {draft.version}
          <span className="inline-dot">·</span>
          {detail.book.period}
        </span>
      </div>
      {!locked && !detail.book.permissions.review && (
        <p className="permission-note">
          <ShieldCheck size={14} />
          {detail.book.permissions.edit
            ? '你可以准备草稿，审核与导出由获授权复核员完成。'
            : '当前账套为只读权限。'}
        </p>
      )}
      <section className="review-section source-card">
        <h3>
          <FileText size={14} />
          原始流水<span>来源可追溯</span>
        </h3>
        {source ? (
          <>
            <div className="source-card-title">{source.description}</div>
            <div className="source-main-amount">
              <span>¥</span>
              {formatMoney(source.amount)}
              <small>{source.direction === 'expense' ? '支出' : '收入'}</small>
            </div>
            <dl>
              <div>
                <dt>交易对方</dt>
                <dd>{source.counterparty || '未填写'}</dd>
              </div>
              <div>
                <dt>交易日期</dt>
                <dd>{source.date}</dd>
              </div>
              <div>
                <dt>流水编号</dt>
                <dd className="mono">{source.external_id}</dd>
              </div>
              <div>
                <dt>凭据引用</dt>
                <dd>{source.evidence_ref || <span className="text-warning">待补充</span>}</dd>
              </div>
            </dl>
            {!source.evidence_ref && !readOnly && (
              <div className="evidence-editor">
                <label htmlFor="evidence-ref">补充凭据引用</label>
                <div>
                  <input
                    id="evidence-ref"
                    value={evidenceRef}
                    maxLength={500}
                    placeholder="例如：发票号码或归档文件编号"
                    onChange={(event) => setEvidenceRef(event.target.value)}
                    disabled={!!busy}
                  />
                  <button
                    type="button"
                    className="button secondary small"
                    disabled={!!busy || !evidenceRef.trim()}
                    onClick={() => void onEvidence(evidenceRef.trim())}
                  >
                    {busy === 'evidence' ? <Spinner /> : '补充'}
                  </button>
                </div>
              </div>
            )}
            <button
              className="text-button"
              type="button"
              disabled={!!busy || dirty}
              onClick={() =>
                onBusinessEvidence(
                  source.evidence_ref.startsWith('business:')
                    ? source.evidence_ref.split(':')[1]
                    : '',
                )
              }
            >
              <FolderOpen size={14} />
              同屏查看原件与凭证
            </button>
            <p className="evidence-note">凭据引用待人工核对，不代表附件已验证。</p>
          </>
        ) : (
          <p>来源记录暂不可用，请重新加载账套。</p>
        )}
      </section>
      {source?.evidence_ref.startsWith('business:') && !intelligenceOpen && (
        <button
          className="text-button"
          type="button"
          disabled={!!busy}
          onClick={() => setIntelligenceOpen(true)}
        >
          <FileSearch size={14} />
          查看票据核对与模型建议
        </button>
      )}
      {source?.evidence_ref.startsWith('business:') && intelligenceOpen && (
        <IntelligencePanel
          key={source.evidence_ref.split(':')[1]}
          session={session}
          eventId={source.evidence_ref.split(':')[1]}
          book={detail.book}
          draft={draft}
          accounts={detail.accounts}
          externalBusy={externalBusy}
          onBusyChange={onIntelligenceBusyChange}
          onChanged={onIntelligenceChanged}
          onDismiss={() => setIntelligenceOpen(false)}
          onOpenBatch={onOpenBatch}
          disableProposal={dirty}
        />
      )}
      {source && !source.evidence_ref.startsWith('business:') && (
        <BankDuplicatePanel
          session={session}
          bookId={detail.book.id}
          draft={draft}
          disabled={!!busy || dirty || externalBusy}
          onBusyChange={onIntelligenceBusyChange}
          onChanged={onIntelligenceChanged}
        />
      )}
      <section className="review-section rationale">
        <h3>
          <Sparkles size={14} />
          建议依据
        </h3>
        <p>{draft.explanation || '请根据原始流水核对会计处理。'}</p>
        <span className="rule-label">建议依据 · 会计确认</span>
      </section>
      <button
        className="agent-review-assistant-button"
        disabled={!!busy || dirty}
        onClick={onAssistant}
      >
        <span>
          <Sparkles size={15} /> 会计助手 · 解释当前资料
        </span>
        <ArrowRight size={16} />
      </button>
      {draft.issues.length > 0 && (
        <section className="issues-box" aria-label="待处理问题">
          <h3>
            <TriangleAlert size={14} />
            {draft.issues.length} 项待处理
          </h3>
          <ul>
            {draft.issues.map((issue, index) => (
              <li key={`${index}-${issue}`}>{issue}</li>
            ))}
          </ul>
        </section>
      )}
      <section className="review-section journal-section">
        <div className="journal-heading">
          <h3>凭证分录</h3>
          <span>示例科目 · CNY</span>
        </div>
        <label className="field-label" htmlFor="journal-description">
          凭证摘要
        </label>
        <textarea
          id="journal-description"
          className="description-input"
          value={description}
          maxLength={500}
          rows={2}
          onChange={(event) => setDescription(event.target.value)}
          disabled={readOnly || !!busy}
        />
        <JournalEntryGrid
          lines={lines}
          accounts={detail.accounts}
          readonly={readOnly}
          busy={!!busy}
          onChange={setLines}
        />
        {!readOnly && (
          <button
            type="button"
            className="add-line"
            disabled={!!busy || lines.length >= 20}
            onClick={() =>
              setLines((current) => [
                ...current,
                { account_code: '', debit: '0.00', credit: '0.00' },
              ])
            }
          >
            <Plus size={14} />
            添加分录
          </button>
        )}
        <div className={`journal-totals ${balanced ? 'balanced' : 'unbalanced'}`}>
          <div>
            <span>借方合计</span>
            <strong>{amountError ? '—' : formatCents(debit)}</strong>
          </div>
          <div>
            <span>贷方合计</span>
            <strong>{amountError ? '—' : formatCents(credit)}</strong>
          </div>
          <p>
            {balanced ? (
              <>
                <CheckCircle2 size={13} />
                借贷平衡
              </>
            ) : (
              <>
                <TriangleAlert size={13} />
                {amountError ||
                  (debit === 0n && credit === 0n
                    ? '请填写有效金额'
                    : `借贷差额 ¥ ${formatCents(debit - credit)}`)}
              </>
            )}
          </p>
        </div>
      </section>
      {!readOnly && dirty && (
        <section className="review-section">
          <label htmlFor="correction-reason">会计修正说明（可选）</label>
          <textarea
            id="correction-reason"
            maxLength={1000}
            rows={2}
            value={correctionReason}
            disabled={!!busy}
            placeholder="记录改科目或分录的依据，审核后保留为本企业样例。"
            onChange={(event) => setCorrectionReason(event.target.value)}
          />
        </section>
      )}
      {draft.source_inactive && draft.business_origin && (
        <SourceHistoryPanel session={session} bookId={detail.book.id} sourceId={draft.source_id} />
      )}
      <div className="review-actions">
        {locked ? (
          <div className="approved-note">
            <ShieldCheck size={18} />
            <p>
              {draft.source_inactive
                ? '此来源已被替代或隔离，历史凭据保留供追溯。'
                : draft.status === 'exported'
                  ? '此凭证已导出，修改入口已锁定。'
                  : '此凭证已审核，选中后即可导出。'}
              <span>如需更正，请在后续调整流程中处理。</span>
            </p>
          </div>
        ) : (
          <>
            <p className="review-action-note">
              {dirty ? '有未保存的修改，请先保存后审核。' : '审核前，请核对凭据、金额与会计科目。'}
            </p>
            <div>
              <button
                className="button secondary"
                disabled={readOnly || !!busy || !dirty || !description.trim() || !!amountError}
                onClick={() =>
                  void onSave(draft, description.trim(), lines, correctionReason.trim())
                }
              >
                {busy === 'save' ? <Spinner /> : <FileText size={15} />}保存草稿
              </button>
              <button
                className="button primary"
                disabled={
                  !detail.book.permissions.review ||
                  !!busy ||
                  dirty ||
                  !balanced ||
                  draft.issues.length > 0 ||
                  !source?.evidence_ref
                }
                onClick={() => void onApprove(draft)}
              >
                {busy === 'approve' ? <Spinner /> : <Check size={16} />}确认审核
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
