import { useEffect, useRef, useState } from 'react';
import { Download, FileText, LoaderCircle, TriangleAlert } from 'lucide-react';
import { apiBlob, errorMessage } from './api';
import { previewMediaType } from './review-session';
import type { Session } from './types';

export interface ReviewAttachment { id: string; filename: string; media_type: string; sha256: string; size_bytes: number; contentPath: string; superseded?: boolean }

export function EvidenceViewer({ session, attachments, reference, historical = false }: {
  session: Session; attachments: ReviewAttachment[]; reference: string; historical?: boolean;
}) {
  const [selected, setSelected] = useState(attachments[0]?.id ?? '');
  const [url, setUrl] = useState(''), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const urlRef = useRef('');
  const file = attachments.find(row => row.id === selected) ?? attachments[0];
  const path = file?.contentPath;
  useEffect(() => { setSelected(attachments[0]?.id ?? ''); }, [attachments]);
  useEffect(() => {
    const controller = new AbortController();
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = ''; setUrl(''); setError('');
    if (!path) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    void apiBlob(path, session, { signal: controller.signal }).then(blob => {
      if (controller.signal.aborted) return;
      const next = URL.createObjectURL(blob); urlRef.current = next; setUrl(next);
    }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); if (urlRef.current) URL.revokeObjectURL(urlRef.current); urlRef.current = ''; };
  }, [path, session]);
  return <section className="inline-evidence" aria-label="同屏原件预览"><div className="inline-evidence-heading"><h3><FileText size={17} />{historical ? '冻结历史原件' : '原始附件'}</h3>{file && <span>{attachments.findIndex(row => row.id === file.id) + 1} / {attachments.length}</span>}</div>
    {attachments.length > 0 ? <><label className="inline-file-select"><span>选择原件</span><select aria-label="选择复核原件" value={file?.id ?? ''} onChange={event => setSelected(event.target.value)}>{attachments.map(row => <option key={row.id} value={row.id}>{row.filename}{row.superseded ? '（已由补件替代）' : ''}</option>)}</select></label>
      {file && <div className="inline-file-meta"><span>{(file.size_bytes / 1024).toFixed(0)} KB · SHA-256 {file.sha256.slice(0, 12)}…</span>{url && <a className="text-button" href={url} download={file.filename}><Download size={14} />下载原件</a>}</div>}
      {error && <p className="inline-error" role="alert"><TriangleAlert size={16} />{error}</p>}
      {loading ? <div className="inline-evidence-placeholder"><LoaderCircle size={24} className="spinner" /><p>正在核验权限并读取原件…</p></div> : url && file ? <div className="inline-document-frame">{previewMediaType(file.media_type) === 'image' ? <img src={url} alt={`原始票据 ${file.filename}`} /> : previewMediaType(file.media_type) === 'pdf' ? <iframe title={`原始 PDF ${file.filename}`} src={url} /> : <p>此格式不支持同屏展示，请下载原件后核对。</p>}</div> : null}
      {historical && <p className="field-help">仅显示此历史来源替代前冻结的附件；不展示其他期间的后续资料。</p>}</> : <div className="inline-evidence-placeholder"><FileText size={32} /><strong>当前来源只有文字引用</strong><p>{reference || '尚未补充凭据引用'}</p><p>尚未关联可预览的原件，请按引用核对归档资料。</p></div>}
    <p className="inline-evidence-reference">凭据引用：{reference || '未填写'}</p>
  </section>;
}
