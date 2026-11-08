import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import type { FollowupState } from './delivery-followups';
import type { MonthlyDestination } from './monthly-workbench-types';
import { MonthlyPackagePanel } from './MonthlyPackagePanel';
import { DeliveryNotifications } from './DeliveryNotifications';

/** Thin task entry. Commands, independent review and package checks remain in their domain services. */
export function DeliveryFollowupPanel({
  session,
  value,
  onBack,
  onOpen,
  onBusyChange,
  onChanged,
  onDirtyChange,
}: {
  session: Session;
  value: FollowupState;
  onBack: () => void;
  onOpen?: (target: MonthlyDestination) => void;
  onBusyChange: (busy: boolean) => void;
  onChanged: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [recipient, setRecipient] = useState(value.recipient.user_id ?? '');
  const [reason, setReason] = useState(''),
    [error, setError] = useState('');
  const kind = value.action.kind;
  const pinnedPackage = useMemo(
    () =>
      value.event.package_id
        ? {
            id: value.event.package_id,
            version: value.event.package_version!,
            fingerprint: value.event.manifest_sha256!,
          }
        : undefined,
    [value.event],
  );
  const changeBusy = useCallback(
    (next: boolean) => {
      setBusy(next);
      onBusyChange(next);
    },
    [onBusyChange],
  );
  const dirty = !!reason || recipient !== (value.recipient.user_id ?? '');
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onBusyChange(false), [onBusyChange]);
  async function save() {
    if (busy || !recipient || reason.trim().length < 3) return;
    changeBusy(true);
    setError('');
    try {
      await api(
        `/books/${encodeURIComponent(value.book_id)}/delivery/monthly-followup/recipient`,
        session,
        {
          method: 'POST',
          body: {
            period: value.period,
            recipient_user_id: recipient,
            reason: reason.trim(),
            expected_source_fingerprint: value.source_fingerprint,
          },
        },
      );
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      changeBusy(false);
    }
  }
  return (
    <section className="panel task-followup-panel" aria-label="交付待办办理">
      <header>
        <h2>{value.action.label}</h2>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => {
            if (!dirty || window.confirm('尚有未保存的说明，放弃编辑并返回待办？')) onBack();
          }}
        >
          返回待办列表
        </button>
      </header>
      <p>
        {value.company_name} · {value.book_name} · {value.period}
      </p>
      <p>办完后返回列表，系统会重新核对进度。查看、领取和指派都不代表办结。</p>
      {value.action.reason && <p role="status">{value.action.reason}</p>}
      {error && <p role="alert">{error}</p>}
      {kind === 'close_period' && (
        <button
          className="button primary"
          disabled={!onOpen || busy || !value.action.can_act}
          onClick={() =>
            onOpen?.({
              view: 'closing',
              companyId: value.company_id,
              bookId: value.book_id,
              period: value.period,
            })
          }
        >
          去处理月结阻断
        </button>
      )}
      {['configure_recipient', 'fix_recipient'].includes(kind) && (
        <>
          <p>
            配置提醒收件人不会更改冻结包的联系人或授权。若包中联系人已失效，请联系服务负责人更正服务单并生成新版本，保留旧版签收记录。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label>
              本企业签收人
              <select
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
                disabled={busy}
                required
              >
                <option value="">请选择当前企业成员</option>
                {value.recipient_candidates.map((c) => (
                  <option key={c.user_id} value={c.user_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              变更说明
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                disabled={busy}
                required
                minLength={3}
              />
            </label>
            <button
              className="button primary"
              disabled={
                busy ||
                !value.action.can_act ||
                !value.recipient_candidates.some((c) => c.user_id === recipient) ||
                reason.trim().length < 3
              }
            >
              保存收件人并重新核对
            </button>
          </form>
          {!value.recipient_candidates.length && (
            <p role="status">暂无可选择的客户成员，请事务所管理员先核对本企业成员与签收授权。</p>
          )}
        </>
      )}
      {[
        'publish_package',
        'await_customer_signature',
        'review',
        'configure_recipient',
        'fix_recipient',
      ].includes(kind) && (
        <MonthlyPackagePanel
          session={session}
          bookId={value.book_id}
          expectedSource={pinnedPackage}
          onBusyChange={changeBusy}
        />
      )}
      {kind === 'reconcile_notification' && (
        <DeliveryNotifications
          session={session}
          bookId={value.book_id}
          period={value.period}
          onChanged={onChanged}
          onBusyChange={changeBusy}
        />
      )}
    </section>
  );
}
