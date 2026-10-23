import { useEffect, useRef, useState } from 'react';
import { Download, FileText, LoaderCircle, MapPin, ShieldCheck, TriangleAlert } from 'lucide-react';
import { apiBlob, ApiError, errorMessage } from './api';
import { evidencePage } from './accounting-context';
import { previewMediaType } from './review-session';
import type { Session } from './types';
import './evidence-viewer.css';

export interface ReviewAttachment {
  id: string;
  filename: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  contentPath: string;
  superseded?: boolean;
}
export interface EvidenceFocus {
  attachment_id?: string;
  page_number?: number;
  text_excerpt?: string;
  reference?: string;
}

export function EvidenceViewer({
  session,
  attachments,
  reference,
  historical = false,
  focus,
}: {
  session: Session;
  attachments: ReviewAttachment[];
  reference: string;
  historical?: boolean;
  focus?: EvidenceFocus;
}) {
  const [selected, setSelected] = useState(attachments[0]?.id ?? '');
  const [url, setUrl] = useState(''),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false);
  const [loadedScope, setLoadedScope] = useState(''),
    [revoked, setRevoked] = useState(false);
  const urlRef = useRef(''),
    request = useRef<AbortController | null>(null),
    viewer = useRef<HTMLElement>(null);
  const file = attachments.find((row) => row.id === selected) ?? attachments[0];
  const path = file?.contentPath;
  const scope = `${session.scope_key}:${session.user_id}:${path ?? ''}`;
  const visibleUrl = loadedScope === scope && !revoked ? url : '';
  const locationMatches = !!file && focus?.attachment_id === file.id;
  const page =
    locationMatches && previewMediaType(file.media_type) === 'pdf'
      ? evidencePage(focus?.page_number)
      : undefined;
  useEffect(() => {
    setSelected(attachments[0]?.id ?? '');
  }, [attachments]);
  useEffect(() => {
    if (focus?.attachment_id && attachments.some((row) => row.id === focus.attachment_id))
      setSelected(focus.attachment_id);
    if (focus) viewer.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
  }, [focus, attachments]);
  useEffect(() => {
    const revoke = () => {
      request.current?.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
      setUrl('');
      setLoadedScope('');
      setLoading(false);
      setRevoked(true);
      setError('当前原件授权已更新，请重新读取凭证后再打开。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = '';
    setUrl('');
    setLoadedScope('');
    setError('');
    setRevoked(false);
    if (!path) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    void apiBlob(path, session, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        const next = URL.createObjectURL(blob);
        urlRef.current = next;
        setUrl(next);
        setLoadedScope(scope);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(failure));
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            setUrl('');
            setLoadedScope('');
            setRevoked(true);
          }
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
    };
  }, [path, session, scope]);
  if (revoked)
    return (
      <section className="inline-evidence" aria-label="同屏原件预览">
        <p className="permission-note" role="alert">
          <ShieldCheck size={16} />
          {error || '原件授权已更新，请重新读取。'}
        </p>
      </section>
    );
  return (
    <section className="inline-evidence" aria-label="同屏原件预览" ref={viewer} tabIndex={-1}>
      <div className="inline-evidence-heading">
        <h3>
          <FileText size={17} />
          {historical ? '冻结历史原件' : '原始附件'}
        </h3>
        {file && (
          <span>
            {attachments.findIndex((row) => row.id === file.id) + 1} / {attachments.length}
          </span>
        )}
      </div>
      {attachments.length > 0 ? (
        <>
          <label className="inline-file-select">
            <span>选择原件</span>
            <select
              aria-label="选择复核原件"
              value={file?.id ?? ''}
              onChange={(event) => setSelected(event.target.value)}
            >
              {attachments.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.filename}
                  {row.superseded ? '（已由补件替代）' : ''}
                </option>
              ))}
            </select>
          </label>
          {file && (
            <div className="inline-file-meta">
              <strong className="inline-file-name">{file.filename}</strong>
              <span>
                {(file.size_bytes / 1024).toFixed(0)} KB · SHA-256 {file.sha256.slice(0, 12)}…
              </span>
              {visibleUrl && (
                <a className="text-button" href={visibleUrl} download={file.filename}>
                  <Download size={14} />
                  下载原件
                </a>
              )}
            </div>
          )}
          {error && (
            <p className="inline-error" role="alert">
              <TriangleAlert size={16} />
              <span>
                {error}
                <small className="evidence-error-help">
                  请重新打开凭证读取原件，再继续人工核对。
                </small>
              </span>
            </p>
          )}
          {locationMatches && (page || focus?.text_excerpt) && (
            <div className="evidence-location-note">
              <strong>
                <MapPin size={14} />
                留存证据位置{page && ` · 第 ${page} 页`}
              </strong>
              {focus?.text_excerpt && <blockquote>{focus.text_excerpt}</blockquote>}
              <p>位置来自已留存记录，请对照原件确认。</p>
            </div>
          )}
          {loading ? (
            <div className="inline-evidence-placeholder" role="status" aria-busy="true">
              <LoaderCircle size={24} className="spinner" />
              <p>正在核验权限并读取原件…</p>
            </div>
          ) : visibleUrl && file ? (
            <div className="inline-document-frame">
              {previewMediaType(file.media_type) === 'image' ? (
                <img src={visibleUrl} alt={`原始票据 ${file.filename}`} />
              ) : previewMediaType(file.media_type) === 'pdf' ? (
                <iframe
                  title={`原始 PDF ${file.filename}`}
                  src={`${visibleUrl}${page ? `#page=${page}` : ''}`}
                />
              ) : (
                <p className="inline-document-unsupported">
                  此格式不支持同屏展示，请下载原件后核对。
                </p>
              )}
            </div>
          ) : null}
          {historical && (
            <p className="field-help">
              仅显示此历史来源替代前冻结的附件；不展示其他期间的后续资料。
            </p>
          )}
        </>
      ) : (
        <div className="inline-evidence-placeholder">
          <FileText size={32} />
          <strong>当前来源只有文字引用</strong>
          <p>{reference || '尚未补充凭据引用'}</p>
          <p>尚未关联可预览的原件，请按引用核对归档资料。</p>
        </div>
      )}
      <p className="inline-evidence-reference">凭据引用：{reference || '未填写'}</p>
      {focus?.reference && (
        <p className="evidence-citation-reference">选中的资料出处：{focus.reference}</p>
      )}
    </section>
  );
}
