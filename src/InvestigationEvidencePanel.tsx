import { useEffect, useState } from 'react';
import { api } from './api';
import { useOperationRequests } from './useOperationRequests';
import { investigationLabels, readInvestigation, type InvestigationEvidence } from './dcp-evidence';
import type { Session } from './types';
import './dcp-evidence.css';

export function InvestigationEvidencePanel(props: {
  session: Session;
  bookId: string;
  period: string;
  companyId: string;
}) {
  return (
    <Panel
      key={[props.session.scope_key, props.bookId, props.period, props.companyId].join(':')}
      {...props}
    />
  );
}
function Panel({
  session,
  bookId,
  period,
  companyId,
}: {
  session: Session;
  bookId: string;
  period: string;
  companyId: string;
}) {
  const [available, setAvailable] = useState(false),
    [result, setResult] = useState<InvestigationEvidence | null>(null);
  const request = useOperationRequests(session);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ enabled: boolean; book_id: string }>(
      `/assistant/investigations/capability?book_id=${encodeURIComponent(bookId)}`,
      session,
      { signal: controller.signal },
    )
      .then((r) => {
        if (!controller.signal.aborted && r.book_id === bookId) setAvailable(r.enabled === true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setAvailable(false);
      });
    return () => controller.abort();
  }, [session, bookId]);
  if (!available || request.denied) return null;
  return (
    <section className="dcp-investigation" aria-label="交付依据调查">
      <h3>为什么本月还不能交付</h3>
      <p>按当前授权核对凭证、月结和交付状态。只读查询，不调用模型，不办理复核或导出。</p>
      <button
        className="button secondary"
        disabled={request.busy}
        onClick={() => {
          setResult(null);
          void request.run(
            async (signal) =>
              readInvestigation(
                await api('/assistant/investigations', session, {
                  method: 'POST',
                  signal,
                  body: { book_id: bookId, period },
                }),
                bookId,
                period,
                companyId,
                session.firm_id ?? '',
              ),
            setResult,
          );
        }}
      >
        核对交付依据
      </button>
      {request.error && <p role="alert">{request.error}</p>}
      {request.busy && <p role="status">正在核对三类依据…</p>}
      {result && (
        <div>
          <p>
            已核对 {result.coverage.read} / {result.coverage.of} 类依据 ·{' '}
            {result.status === 'complete'
              ? '查询完成，不代表已具备交付条件'
              : '未完成，请会计人工核对'}
          </p>
          {result.missing.length > 0 && (
            <p>未检查：{result.missing.map((k) => investigationLabels[k]).join('、')}</p>
          )}
          {result.execution && (
            <p role="status">
              {result.execution.stop_reason === 'timeout'
                ? '核对已超时，迟到结果不会补入，请稍后由会计重新核对。'
                : result.execution.stop_reason === 'busy'
                  ? '当前核对任务较多，请稍后重试。'
                  : result.execution.stop_reason
                    ? '依据或授权未能确认，本次结果不用于办理。'
                    : '三类依据已重新核对，仍须按原流程人工办理。'}{' '}
              未调用模型。
            </p>
          )}
          {Object.entries(result.findings).map(([kind, facts]) => (
            <details key={kind}>
              <summary>{investigationLabels[kind]}</summary>
              <Evidence kind={kind} facts={facts} />
            </details>
          ))}
          {result.evidence && (
            <p className="dcp-fingerprint">本次证据摘要：{result.evidence.fingerprint}</p>
          )}
        </div>
      )}
    </section>
  );
}
function Evidence({ kind, facts }: { kind: string; facts: Record<string, unknown> }) {
  if (kind === 'voucher_status')
    return (
      <p>
        已过账凭证 {String(facts.posted_vouchers ?? '未知')} 张；待处理草稿{' '}
        {String(facts.draft_rows ?? '未知')} 张。
      </p>
    );
  if (kind === 'close_blockers')
    return (
      <>
        <p>
          {facts.status === 'no_run'
            ? '还没有月结预检，请先由会计完成预检。'
            : `月结记录版本 ${String(facts.version ?? '未知')}，以最新预检为准。`}
        </p>
        {Array.isArray(facts.blockers) &&
          facts.blockers.map((b, i) => (
            <p key={i}>
              {b && typeof b === 'object'
                ? String((b as Record<string, unknown>).next_action ?? '请会计核对该阻断项')
                : '请会计核对'}
            </p>
          ))}
        {facts.unknown === true && <p>月结明细无法读取，请人工核对。</p>}
      </>
    );
  return (
    <>
      <p>
        {facts.package ? '已有冻结交付包；不代表客户已签收。' : '尚无冻结交付包。'}
        {facts.opened === true ? ' 客户曾打开资料包。' : ''}
      </p>
      {facts.notifications &&
        typeof facts.notifications === 'object' &&
        Object.entries(facts.notifications).map(([state, count]) => (
          <p key={state}>
            {state === 'unknown'
              ? '发送结果未知，需管理员人工核查'
              : state === 'sink_written'
                ? '通道接受，送达未确认'
                : '其他通知状态'}
            ：{String(count)} 条
          </p>
        ))}
    </>
  );
}
