import { useState } from 'react';
import { api, ApiError } from './api';
import { useScopedQuery } from './useScopedQuery';
import { parseDeliveryPage, type DeliveryPageScope } from './assistant-delivery-page';
import type { AssistantDeliveryTarget } from './assistant-delivery-target';
import type { Session } from './types';
import './assistant-delivery-details.css';

interface Props {
  session: Session;
  conversationId: string;
  turnId: string;
  target: AssistantDeliveryTarget;
}
type Position = { cursor?: string; start: number; snapshot?: string };

function Page({
  session,
  conversationId,
  turnId,
  target,
  kind,
  position,
  move,
}: Props & {
  kind: 'bank' | 'control';
  position: Position;
  move: (p: Position) => void;
}) {
  const scope: DeliveryPageScope = {
    ...target,
    conversation_id: conversationId,
    turn_id: turnId,
    kind,
    limit: 10,
    start: position.start,
    snapshot_fingerprint: position.snapshot,
  };
  const query = useScopedQuery(
    async (signal) => {
      try {
        const params = new URLSearchParams({ kind, limit: '10' });
        if (position.cursor) params.set('after', position.cursor);
        const raw = await api<unknown>(
          `/assistant/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/delivery-details?${params}`,
          session,
          { signal },
        );
        return parseDeliveryPage(raw, scope);
      } catch (error) {
        if (error instanceof ApiError) {
          if (error.status === 409)
            throw new Error(
              '本次诊断依据已变化。请关闭明细，在当前企业重新提问；系统不会自动重发。',
            );
          if (['admin_mfa_required', 'mfa_required', 'step_up_required'].includes(error.code ?? ''))
            throw new Error('需要二次验证。请先完成身份验证，再重新打开明细。');
          if (error.status === 403 || error.status === 404)
            throw new Error('当前无权查看这份明细，数据已清空。请联系本事务所管理员核对授权。');
          if (error.status >= 500)
            throw new Error('暂时无法读取明细。请稍后重试读取；不会重新调用模型。');
        }
        throw error;
      }
    },
    JSON.stringify([session.scope_key, scope, position.cursor]),
  );
  return (
    <div aria-live="polite" aria-busy={query.loading}>
      {query.loading && <p>正在核验权限与诊断版本…</p>}
      {query.error && (
        <div role="alert">
          <p>{query.error}</p>
          <button onClick={query.reload}>重试读取</button>
        </div>
      )}
      {query.data && (
        <>
          <p className="ga-detail-coverage">
            {target.period} · 已展示第 {query.data.total ? query.data.start + 1 : 0}–
            {query.data.end} 条 / 共 {query.data.total} 条
          </p>
          {!query.data.rows.length && (
            <p>本次诊断没有此类账户明细。请同时查看上方的配置、资料与跨期提示。</p>
          )}
          <ul className="ga-detail-rows">
            {query.data.rows.map((row) => (
              <li key={row.key}>
                <strong>{row.label}</strong>
                <span>{row.passed ? '本项核对一致' : '本项需处理'}</span>
                {row.issues.map((issue, index) => (
                  <div key={`${issue.code}:${index}`}>
                    <p>{issue.reason}</p>
                    <p>处理人：{issue.owner_role}</p>
                    <p>下一步：{issue.next_action}</p>
                  </div>
                ))}
                {!row.passed && !row.issues.length && (
                  <p>尚未通过完整性检查，请打开正式账簿核对配置与原件。</p>
                )}
              </li>
            ))}
          </ul>
          <div className="ga-detail-actions">
            {position.start > 0 && <button onClick={() => move({ start: 0 })}>回到第一页</button>}
            {query.data.has_more && (
              <button
                onClick={() =>
                  move({
                    cursor: query.data!.cursor,
                    start: query.data!.end,
                    snapshot: query.data!.snapshot_fingerprint,
                  })
                }
              >
                下一页明细
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Parent keys this component by identity. A page is also keyed, so old rows
 * disappear in the same render as a scope/cursor change (before effects). */
export function AssistantDeliveryDetails(props: Props) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<'bank' | 'control'>('bank');
  const [position, setPosition] = useState<Position>({ start: 0 });
  return (
    <section className="ga-delivery-details" aria-label="诊断明细分页">
      <button
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setPosition({ start: 0 });
        }}
      >
        {open ? '收起核对明细' : '继续查看核对明细'}
      </button>
      {open && (
        <>
          <p>按本次诊断的版本读取。查看完明细不代表已复核、已关期或可交付。</p>
          <div className="ga-detail-actions" aria-label="明细类型">
            {(['bank', 'control'] as const).map((value) => (
              <button
                key={value}
                aria-pressed={kind === value}
                onClick={() => {
                  setKind(value);
                  setPosition({ start: 0 });
                }}
              >
                {value === 'bank' ? '银行账户' : '子账科目'}
              </button>
            ))}
          </div>
          <Page
            key={JSON.stringify([kind, position])}
            {...props}
            kind={kind}
            position={position}
            move={setPosition}
          />
        </>
      )}
    </section>
  );
}
