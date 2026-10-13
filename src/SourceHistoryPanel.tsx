import { useEffect, useRef, useState } from 'react';
import { Archive, Download, LoaderCircle } from 'lucide-react';
import { api, apiBlob, errorMessage } from './api';
import { formatMoney } from './csv';
import type { BusinessPayload } from './business-types';
import type { Session } from './types';
import './revisions.css';

export type SourceHistory = {
  revision_id: string;
  event_id: string;
  book_id: string;
  source_id: string;
  draft_id: string;
  fact_version: number;
  payload: BusinessPayload;
  submitted_snapshot: { event_version: number; payload: BusinessPayload; created_at: string };
  attachments: {
    id: string;
    filename: string;
    media_type: string;
    sha256: string;
    size_bytes: number;
  }[];
  reason: string;
  created_at: string;
  state: 'superseded';
};

export function SourceHistoryPanel({
  session,
  bookId,
  sourceId,
}: {
  session: Session;
  bookId: string;
  sourceId: string;
}) {
  const [history, setHistory] = useState<SourceHistory | null>(null),
    [error, setError] = useState(''),
    [downloading, setDownloading] = useState('');
  const epoch = useRef(0),
    active = useRef(true);
  const scope = `${session.scope_key}:${bookId}:${sourceId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const base = `/books/${encodeURIComponent(bookId)}/sources/${encodeURIComponent(sourceId)}/history`;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController(),
      generation = ++epoch.current;
    setHistory(null);
    setError('');
    setDownloading('');
    void api<SourceHistory>(base, session, { signal: controller.signal })
      .then((value) => {
        if (active.current && !controller.signal.aborted && generation === epoch.current)
          setHistory(value);
      })
      .catch((failure) => {
        if (active.current && !controller.signal.aborted && generation === epoch.current)
          setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [base, session]);
  async function download(item: SourceHistory['attachments'][number]) {
    if (downloading) return;
    const started = scope;
    setDownloading(item.id);
    setError('');
    try {
      const blob = await apiBlob(
        `${base}/attachments/${encodeURIComponent(item.id)}/content`,
        session,
      );
      if (!active.current || scopeRef.current !== started) return;
      const url = URL.createObjectURL(blob),
        anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = item.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (failure) {
      if (active.current && scopeRef.current === started) setError(errorMessage(failure));
    } finally {
      if (active.current && scopeRef.current === started) setDownloading('');
    }
  }
  return (
    <section className="source-history-panel" aria-label="被替代来源的历史事实与原件">
      <h3>
        <Archive size={17} />
        历史事实与原件
      </h3>
      <p className="field-help">
        此处只显示该来源替代前留存的事实和原件。新期间的业务事项及后续补件按当前期间权限访问。
      </p>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {!history && !error && (
        <p>
          <LoaderCircle size={15} className="spinner" />
          读取冻结快照…
        </p>
      )}
      {history && (
        <>
          <dl className="source-history-facts">
            <div>
              <dt>原发生日期</dt>
              <dd>{history.payload.occurred_on}</dd>
            </div>
            <div>
              <dt>原事实金额</dt>
              <dd>{formatMoney(history.payload.amount)} 元</dd>
            </div>
            <div>
              <dt>原交易对方</dt>
              <dd>{history.payload.counterparty}</dd>
            </div>
            <div>
              <dt>事实版本</dt>
              <dd>{history.fact_version}</dd>
            </div>
          </dl>
          <p>{history.payload.description}</p>
          <p className="field-help">替代依据：{history.reason}</p>
          <p className="field-help">
            原审批提交快照版本 {history.submitted_snapshot.event_version} · 金额{' '}
            {formatMoney(history.submitted_snapshot.payload.amount)} 元
          </p>
          <ul className="source-history-files">
            {history.attachments.map((item) => (
              <li key={item.id}>
                <div>
                  <strong>{item.filename}</strong>
                  <small>
                    SHA-256 {item.sha256.slice(0, 16)}… · {item.size_bytes} 字节
                  </small>
                </div>
                <button
                  className="button secondary small"
                  disabled={!!downloading}
                  onClick={() => void download(item)}
                >
                  <Download size={14} />
                  下载历史原件
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
