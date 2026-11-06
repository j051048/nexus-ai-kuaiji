import { useEffect, useState } from 'react';
import { api } from './api';
import { useOperationRequests } from './useOperationRequests';
import type { Session } from './types';
import './analysis-evidence.css';

type Analysis = {
  kind: 'expense_bridge' | 'invoice_voucher_diff';
  status: 'complete' | 'partial';
  summary: string;
  facts: Record<string, unknown>;
  coverage: {
    scope: string;
    rows_read: number;
    detail_rows?: number;
    total_rows?: number;
    detail_complete?: boolean;
    has_more: boolean;
  };
  missing: string[];
  handoff: {
    reason_code?: string;
    reason: string;
    owner_role?: string;
    action_target?: string;
    deadline?: string | null;
  } | null;
  fingerprint: string;
};

const missingLabels: Record<string, string> = {
  previous_period: '上期账套',
  voucher_source: '凭证来源金额',
};

export function AnalysisEvidencePanel(props: {
  session: Session;
  bookId: string;
  period: string;
  companyId: string;
}) {
  const [available, setAvailable] = useState(false);
  const [kind, setKind] = useState<Analysis['kind']>('expense_bridge');
  const [accountCode, setAccountCode] = useState('6602.02');
  const [voucherId, setVoucherId] = useState('');
  const [result, setResult] = useState<Analysis | null>(null);
  const request = useOperationRequests(props.session);

  useEffect(() => {
    const controller = new AbortController();
    setAvailable(false);
    setResult(null);
    void api<{ enabled: boolean; book_id: string }>(
      `/assistant/analyses/capability?book_id=${encodeURIComponent(props.bookId)}`,
      props.session,
      { signal: controller.signal },
    )
      .then((r) => {
        if (!controller.signal.aborted && r.book_id === props.bookId) setAvailable(r.enabled === true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setAvailable(false);
      });
    return () => controller.abort();
  }, [props.session, props.bookId]);

  if (!available || request.denied) return null;
  const run = () => {
    setResult(null);
    void request.run(
      async (signal) =>
        (await api('/assistant/analyses', props.session, {
          method: 'POST',
          signal,
          body:
            kind === 'expense_bridge'
              ? {
                  book_id: props.bookId,
                  period: props.period,
                  kind,
                  account_code: accountCode,
                  limit: 50,
                }
              : {
                  book_id: props.bookId,
                  period: props.period,
                  kind,
                  target_voucher_id: voucherId,
                  limit: 50,
                },
        })) as Analysis,
      setResult,
    );
  };

  return (
    <details className="analysis-evidence" aria-label="只读差异核对">
      <summary>只读差异核对</summary>
      <p>
        按当前授权核对费用跨期或票据差额。结果由确定性代码计算，不调用模型，不修改凭证、复核或导出。
      </p>
      <div className="analysis-evidence-controls">
        <label>
          核对类型
          <select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as Analysis['kind']);
              setResult(null);
            }}
          >
            <option value="expense_bridge">费用跨期差额</option>
            <option value="invoice_voucher_diff">票据与凭证差额</option>
          </select>
        </label>
        {kind === 'expense_bridge' ? (
          <label>
            科目代码
            <input
              value={accountCode}
              inputMode="decimal"
              onChange={(event) => setAccountCode(event.target.value)}
              placeholder="6602.02"
            />
          </label>
        ) : (
          <label>
            凭证 ID
            <input
              value={voucherId}
              onChange={(event) => setVoucherId(event.target.value)}
              placeholder="从凭证详情复制"
            />
          </label>
        )}
        <button
          type="button"
          className="button secondary"
          disabled={request.busy || (kind === 'invoice_voucher_diff' && !voucherId.trim())}
          onClick={run}
        >
          {request.busy ? '核对中…' : '开始只读核对'}
        </button>
      </div>
      {request.error && <p role="alert">{request.error}</p>}
      {result && (
        <div className="analysis-evidence-result">
          <p>
            {result.summary} · 状态：{result.status === 'complete' ? '当前范围已核对' : '不完整'}
          </p>
          {result.kind === 'expense_bridge' ? (
            <p>
              本期 {String(result.facts.current_total ?? '未知')} 元，上期{' '}
              {String(result.facts.previous_total ?? '未知')} 元，差额{' '}
              {String(result.facts.difference ?? '未知')} 元。
            </p>
          ) : (
            <p>
              凭证 {String(result.facts.voucher_no ?? '未知')}，借方{' '}
              {String(result.facts.voucher_total ?? '未知')} 元，来源{' '}
              {String(result.facts.source_total ?? '未知')} 元，差额{' '}
              {String(result.facts.difference ?? '未知')} 元。
            </p>
          )}
          <p>
            总额 {result.coverage.total_rows ?? result.coverage.rows_read} 行，当前明细{' '}
            {result.coverage.detail_rows ?? result.coverage.rows_read} 行
            {result.coverage.has_more ? '，仍有下一页，当前为部分结果' : '，未发现更多分页'}。
            未检查：{result.missing.length ? result.missing.map((k) => missingLabels[k] ?? k).join('、') : '无'}。
          </p>
          {result.handoff && (
            <p>
              下一步：{result.handoff.reason}（{result.handoff.owner_role ?? 'accountant'}）。打开原记录核对后再办理，核对本身不构成复核。
            </p>
          )}
          <p className="analysis-evidence-fingerprint">证据摘要：{result.fingerprint}</p>
          <a
            className="text-button"
            href={
              result.handoff?.action_target ??
              `/workspace/accounting?book=${encodeURIComponent(props.bookId)}&period=${encodeURIComponent(props.period)}&company=${encodeURIComponent(props.companyId)}`
            }
          >
            打开账套原记录核对
          </a>
        </div>
      )}
    </details>
  );
}
