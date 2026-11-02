import { useState } from 'react';
import { api } from './api';
import { useOperationRequests } from './useOperationRequests';
import {
  conclusionLabels,
  investigationCommand,
  type InvestigationConclusion,
} from './dcp-evidence';
import type { NotificationItem } from './delivery-notification-state';
import type { Session } from './types';

export function NotificationInvestigation({
  item,
  session,
  bookId,
  onSaved,
}: {
  item: NotificationItem;
  session: Session;
  bookId: string;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false),
    [evidence, setEvidence] = useState(''),
    [reason, setReason] = useState('');
  const [conclusion, setConclusion] = useState<InvestigationConclusion>('unresolved');
  const [key] = useState(() => crypto.randomUUID());
  const requests = useOperationRequests(session);
  if (requests.denied) return <p role="alert">{requests.error}</p>;
  return (
    <div>
      {item.investigation && (
        <p>
          {conclusionLabels[item.investigation.conclusion]} · 第 {item.investigation.revision}{' '}
          次核查 · {item.investigation.reason}
        </p>
      )}
      {item.can_investigate && (
        <button
          className="button secondary"
          disabled={requests.busy}
          onClick={() => setOpen(!open)}
        >
          登记人工核查
        </button>
      )}
      {open && (
        <form
          className="dcp-evidence-form"
          onSubmit={(e) => {
            e.preventDefault();
            void requests.run(
              (signal) =>
                api(
                  `/books/${encodeURIComponent(bookId)}/delivery/notifications/${encodeURIComponent(item.id)}/investigations`,
                  session,
                  {
                    method: 'POST',
                    signal,
                    body: investigationCommand(item, conclusion, evidence, reason, key),
                  },
                ),
              () => {
                setOpen(false);
                onSaved();
              },
              true,
            );
          }}
        >
          <p>仅记录人工核查意见，保留原未知状态；不代签收、不触发重发。</p>
          <label>
            核查结论
            <select
              value={conclusion}
              onChange={(e) => setConclusion(e.target.value as InvestigationConclusion)}
            >
              {Object.entries(conclusionLabels).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            核查证据编号或说明
            <input
              required
              minLength={3}
              maxLength={1000}
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
            />
          </label>
          <label>
            核查原因
            <textarea
              required
              minLength={3}
              maxLength={1000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <button className="button primary" disabled={requests.busy}>
            保存核查记录
          </button>
        </form>
      )}
      {requests.error && <p role="alert">{requests.error}</p>}
    </div>
  );
}
