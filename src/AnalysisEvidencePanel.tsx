import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from './api';
import { useOperationRequests } from './useOperationRequests';
import type { Session } from './types';
import './analysis-evidence.css';
import { createReadGuard } from './scoped-read';
import {
  AnalysisContractError,
  parseAnalysis,
  type Analysis,
  type AnalysisScope,
} from './analysis-result';

const missingLabels: Record<string, string> = {
  previous_period: '上期账套',
  voucher_source: '凭证来源金额',
  account_identity: '跨期科目身份与版本',
};

export function AnalysisEvidencePanel(props: {
  session: Session;
  bookId: string;
  period: string;
  companyId: string;
  targetVoucherId?: string;
}) {
  const [available, setAvailable] = useState(false);
  const [kind, setKind] = useState<Analysis['kind']>(
    props.targetVoucherId ? 'invoice_voucher_diff' : 'expense_bridge',
  );
  const [accountCode, setAccountCode] = useState('6602.02');
  const voucherId = props.targetVoucherId ?? '';
  const [result, setResult] = useState<Analysis | null>(null);
  const [contractError, setContractError] = useState('');
  const request = useOperationRequests(props.session);
  const guard = useRef(createReadGuard());
  const scopeKey = JSON.stringify([
    props.session.scope_key,
    props.companyId,
    props.bookId,
    props.period,
    kind,
    accountCode,
    voucherId,
  ]);
  useLayoutEffect(() => {
    guard.current.cancel();
    setResult(null);
    setContractError('');
    return () => guard.current.cancel();
  }, [scopeKey]);
  useEffect(() => {
    if (request.denied) {
      guard.current.cancel();
      setResult(null);
    }
  }, [request.denied]);

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
        if (!controller.signal.aborted && r.book_id === props.bookId)
          setAvailable(r.enabled === true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setAvailable(false);
      });
    return () => controller.abort();
  }, [props.session, props.bookId, props.companyId, props.period]);

  if (!available || request.denied) return null;
  const run = (offset = 0, sourceVersion?: string) => {
    setResult(null);
    setContractError('');
    const scope: AnalysisScope = {
      firmId: props.session.firm_id,
      companyId: props.companyId,
      bookId: props.bookId,
      period: props.period,
      kind,
      accountCode,
      voucherId,
      offset,
      limit: 50,
      sourceVersion,
    };
    const ticket = guard.current.begin();
    void request.run(
      async (signal) => {
        signal.addEventListener('abort', () => guard.current.cancel(ticket), { once: true });
        try {
          const value = await api('/assistant/analyses', props.session, {
            method: 'POST',
            signal: ticket.signal,
            body: {
              book_id: props.bookId,
              period: props.period,
              kind,
              limit: scope.limit,
              offset,
              ...(sourceVersion ? { expected_source_version: sourceVersion } : {}),
              ...(kind === 'expense_bridge'
                ? { account_code: accountCode }
                : { target_voucher_id: voucherId }),
            },
          });
          if (!guard.current.accepts(ticket)) throw new DOMException('Scope changed', 'AbortError');
          return parseAnalysis(value, scope);
        } catch (failure) {
          if (!guard.current.accepts(ticket)) throw new DOMException('Scope changed', 'AbortError');
          if (failure instanceof AnalysisContractError) {
            setContractError(failure.message);
            return null;
          }
          throw failure;
        }
      },
      (value) => {
        if (guard.current.accepts(ticket)) setResult(value);
      },
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
          <p>
            {voucherId
              ? '已绑定当前打开的凭证。'
              : '请在正式账簿打开凭证，再从凭证详情核对票据来源。'}
          </p>
        )}
        <button
          type="button"
          className="button secondary"
          disabled={request.busy || (kind === 'invoice_voucher_diff' && !voucherId.trim())}
          onClick={() => run()}
        >
          {request.busy ? '核对中…' : '开始只读核对'}
        </button>
      </div>
      {request.error && <p role="alert">{request.error}</p>}
      {contractError && <p role="alert">{contractError}</p>}
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
            未检查：
            {[
              ...result.missing.map((k) => missingLabels[k] ?? k),
              ...(!result.coverage.detail_complete ? ['尚未查看的明细（总额按全部来源计算）'] : []),
            ].join('、') || '无'}
            。
          </p>
          {result.handoff && (
            <p>
              下一步：{result.handoff.reason}（{'会计'}
              ）。打开原记录核对后再办理，核对本身不构成复核。
            </p>
          )}
          <details>
            <summary>核对依据</summary>
            {['current_rows', 'previous_rows', 'sources'].map((key) => {
              const rows = result.facts[key] as Record<string, unknown>[] | undefined;
              return rows?.length ? (
                <div key={key}>
                  <p>
                    {key === 'current_rows'
                      ? '本期凭证明细'
                      : key === 'previous_rows'
                        ? '对比期凭证明细'
                        : '当前凭证的来源明细'}
                  </p>
                  <ul>
                    {rows.map((row, index) => (
                      <li key={index}>
                        {key === 'sources'
                          ? `来源 ${String(row.kind)} · 版本 ${String(row.version)}`
                          : `凭证 ${String(row.voucher_no)} · ${String(row.date)} · ${String(row.description)}`}
                        {' · '}
                        {(Number(row.amount_cents) / 100).toFixed(2)} 元
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null;
            })}
            <p className="analysis-evidence-fingerprint">证据摘要：{result.fingerprint}</p>
          </details>
          <div className="analysis-evidence-pages">
            {result.coverage.offset > 0 && (
              <button
                type="button"
                className="text-button"
                onClick={() => run(Math.max(0, result.coverage.offset - 50), result.source_version)}
              >
                上一页明细
              </button>
            )}
            {result.coverage.has_more && (
              <button
                type="button"
                className="text-button"
                onClick={() => run(result.coverage.offset + 50, result.source_version)}
              >
                下一页明细
              </button>
            )}
          </div>
          <a
            className="text-button"
            href={`/workspace/accounting?book=${encodeURIComponent(props.bookId)}&period=${encodeURIComponent(props.period)}&company=${encodeURIComponent(props.companyId)}`}
          >
            打开账套原记录核对
          </a>
        </div>
      )}
    </details>
  );
}
