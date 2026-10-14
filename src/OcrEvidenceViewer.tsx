import { useEffect, useRef, useState } from 'react';
import { FileSearch, LoaderCircle, MapPin, ShieldCheck, TriangleAlert, ZoomIn } from 'lucide-react';
import { apiBlob, errorMessage } from './api';
import type { IndependentOcrEvidence } from './intelligence-types';
import type { Session } from './types';
import {
  bindingLabel,
  boundBlocks,
  fieldBinding,
  OCR_FIELD_LABELS,
  ocrPreviewPath,
  ocrScore,
  polygonPoints,
} from './ocr-evidence';
import './ocr-evidence.css';

export interface OcrEvidenceSelection {
  field?: string;
  page: number;
}

export function OcrEvidenceViewer({
  session,
  eventId,
  runId,
  documentIndex,
  evidence,
  selection,
  onSelect,
}: {
  session: Session;
  eventId: string;
  runId: string;
  documentIndex: number;
  evidence: IndependentOcrEvidence;
  selection: OcrEvidenceSelection | null;
  onSelect: (selection: OcrEvidenceSelection) => void;
}) {
  const page = evidence.pages.find((item) => item.page === selection?.page);
  const binding = selection?.field
    ? fieldBinding(evidence, documentIndex, selection.field)
    : undefined;
  const [preview, setPreview] = useState({ scope: '', url: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [verifiedImageUrl, setVerifiedImageUrl] = useState('');
  const [zoomed, setZoomed] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const [retry, setRetry] = useState(0);
  const request = useRef<AbortController | null>(null);
  const urlRef = useRef('');
  const viewerRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scope = `${session.scope_key}:${session.user_id}:${eventId}:${runId}:${page?.page ?? ''}`;
  const visibleUrl = preview.scope === scope && !revoked ? preview.url : '';
  const pageNumber = page?.page;
  const selectedBlocks = page ? boundBlocks(page, binding) : [];
  const selectedPoints = selectedBlocks.flatMap((block) =>
    page && polygonPoints(block, page) ? block.polygon : [],
  );
  const focusX = selectedPoints.length
    ? selectedPoints.reduce((sum, point) => sum + point[0], 0) / selectedPoints.length
    : 0;
  const focusY = selectedPoints.length
    ? selectedPoints.reduce((sum, point) => sum + point[1], 0) / selectedPoints.length
    : 0;
  useEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll || !page) return;
    scroll.scrollLeft = zoomed
      ? (focusX / page.width) * scroll.scrollWidth - scroll.clientWidth / 2
      : 0;
    scroll.scrollTop = zoomed
      ? (focusY / page.height) * scroll.scrollHeight - scroll.clientHeight / 2
      : 0;
  }, [zoomed, focusX, focusY, page, visibleUrl]);

  useEffect(() => {
    if (selection?.field) viewerRef.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }, [selection]);

  useEffect(() => {
    const revoke = () => {
      request.current?.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
      setPreview({ scope: '', url: '' });
      setRevoked(true);
      setLoading(false);
    };
    window.addEventListener('accounting:unauthorized', revoke);
    window.addEventListener('accounting:forbidden', revoke);
    return () => {
      window.removeEventListener('accounting:unauthorized', revoke);
      window.removeEventListener('accounting:forbidden', revoke);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = '';
    setPreview({ scope: '', url: '' });
    setError('');
    setLoading(!!pageNumber);
    if (!pageNumber || revoked) return () => controller.abort();
    void apiBlob(ocrPreviewPath(eventId, runId, pageNumber), session, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        if (!['image/jpeg', 'image/png'].includes(blob.type))
          throw new Error('未返回可校验的票据页面，请核对原始附件。');
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setPreview({ scope, url });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
    };
  }, [eventId, runId, pageNumber, session, scope, retry, revoked]);

  if (revoked)
    return (
      <section className="ocr-evidence-panel" aria-label="独立文字识别证据">
        <p className="permission-note" role="alert">
          <ShieldCheck size={16} />
          票据授权已更新，已清除预览；请重新读取事项后核对。
        </p>
      </section>
    );

  const fieldName = selection?.field ? (OCR_FIELD_LABELS[selection.field] ?? selection.field) : '';
  const lowOrMissing = !!selection?.field && (!binding || binding.status !== 'matched');
  return (
    <section className="ocr-evidence-panel" aria-label="独立文字识别证据" ref={viewerRef}>
      <div className="ocr-evidence-header">
        <strong>
          <FileSearch size={15} />
          独立文字识别证据
        </strong>
        <span>PaddleOCR · {evidence.model}</span>
      </div>
      <p className="ocr-evidence-caption">
        点选上方字段，对照文字位置核对。识别分数仅反映文字识别把握，不代表会计处理正确率。
      </p>
      <div className="ocr-evidence-controls">
        <label>
          <span>证据页面</span>
          <select
            aria-label="选择独立 OCR 证据页面"
            value={selection?.page ?? ''}
            onChange={(event) => onSelect({ page: Number(event.target.value) })}
          >
            <option value="" disabled>
              选择页面查看原文
            </option>
            {evidence.pages.map((item) => (
              <option key={item.page} value={item.page}>
                第 {item.page} 页{item.source === 'pdf_native' ? ' · PDF 文字层' : ''}
              </option>
            ))}
          </select>
        </label>
        <span>
          {evidence.recognized_pages} 页文字识别 · {evidence.native_pages} 页文字层
        </span>
      </div>
      {selection?.field && (
        <div className={`ocr-field-location ${lowOrMissing ? 'needs-review' : ''}`} role="status">
          <strong>
            <MapPin size={14} />
            {fieldName} · {bindingLabel(binding)}
          </strong>
          <p>
            {lowOrMissing
              ? '请人工核对原件；如金额、税额或交易对方冲突，在下方核对结果中记录处理。'
              : selectedPoints.length
                ? '文字位置已标出，请同时核对业务事项与流水，仍须会计复核。'
                : '已找到文字依据，但本页未保留可定位文字框。请对照原件、业务事项与流水，仍须会计复核。'}
          </p>
          {binding && page?.source !== 'pdf_native' && (
            <span>字段文字识别分数：{ocrScore(binding.score)}</span>
          )}
          {selectedBlocks.map((block) => (
            <blockquote key={block.id}>{block.text}</blockquote>
          ))}
        </div>
      )}
      {loading ? (
        <div className="ocr-preview-placeholder" role="status">
          <LoaderCircle className="spinner" size={19} />
          正在核验权限并读取票据页面…
        </div>
      ) : error ? (
        <div className="ocr-preview-error" role="alert">
          <p>
            <TriangleAlert size={15} />
            {error}
          </p>
          <button
            className="text-button"
            type="button"
            onClick={() => setRetry((value) => value + 1)}
          >
            重新读取页面
          </button>
        </div>
      ) : visibleUrl && page ? (
        <div className="ocr-page-preview">
          <button
            type="button"
            className="text-button ocr-zoom-toggle"
            aria-pressed={zoomed}
            onClick={() => setZoomed((value) => !value)}
          >
            <ZoomIn size={13} />
            {zoomed ? '适应页面宽度' : '放大票据文字'}
          </button>
          <div
            className={`ocr-page-scroll ${zoomed ? 'zoomed' : ''}`}
            ref={scrollRef}
            tabIndex={zoomed ? 0 : undefined}
            aria-label="票据页面，可放大后滚动核对"
          >
            <div className="ocr-page-canvas">
              <img
                src={visibleUrl}
                alt={`独立 OCR 证据第 ${page.page} 页`}
                onLoad={(event) => {
                  if (
                    event.currentTarget.naturalWidth === page.width &&
                    event.currentTarget.naturalHeight === page.height
                  )
                    setVerifiedImageUrl(visibleUrl);
                  else setError('页面尺寸与留存证据不一致，已停止定位。请人工核对原始附件。');
                }}
                onError={() => setError('票据页面无法展示，请重新读取或核对原始附件。')}
              />
              {verifiedImageUrl === visibleUrl && (
                <svg
                  aria-label={`${fieldName || '当前页面'}文字定位框`}
                  viewBox={`0 0 ${page.width} ${page.height}`}
                  preserveAspectRatio="none"
                >
                  {selectedBlocks.map((block) => {
                    const points = polygonPoints(block, page);
                    return points ? (
                      <polygon
                        key={block.id}
                        points={points}
                        className={lowOrMissing ? 'needs-review' : undefined}
                      >
                        <title>{block.text}</title>
                      </polygon>
                    ) : null;
                  })}
                </svg>
              )}
            </div>
          </div>
          <p>定位图经过方向校正与等比缩放；原始附件继续保留。</p>
        </div>
      ) : null}
      {page?.source === 'pdf_native' && (
        <p className="ocr-evidence-caption">
          本页使用 PDF 文字层校验，未生成文字定位框。请对照页面与字段引文核对。
        </p>
      )}
      {page?.source === 'paddleocr' && selection?.field && selectedBlocks.length === 0 && (
        <p className="ocr-evidence-caption text-warning">
          此字段没有可定位的文字框，请人工核对原件。
        </p>
      )}
      {page?.source === 'paddleocr' && (
        <details className="ocr-raw-text">
          <summary>第 {page.page} 页独立识别原文与分数</summary>
          <p>以下为文字识别引擎输出，可能存在漏字或错字。</p>
          <ol>
            {page.blocks.map((block) => (
              <li key={block.id}>
                <span>{block.text}</span>
                <small>文字分数 {ocrScore(block.score)}</small>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
