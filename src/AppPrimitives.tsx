import { useEffect, useId, useRef, type ReactNode } from 'react';
import { FolderOpen, LoaderCircle, TriangleAlert, X } from 'lucide-react';
export function Spinner() {
  return <LoaderCircle className="spinner" size={16} aria-hidden="true" />;
}

export function PageControls({
  label,
  page,
  busy,
  onPage,
}: {
  label: string;
  page?: import('./types').Pagination;
  busy: boolean;
  onPage: (offset: number) => void;
}) {
  if (!page || page.total <= page.limit) return null;
  return (
    <div className="pagination-controls" aria-label={`${label}分页`}>
      <span>
        共 {page.total} 项 · 第 {Math.floor(page.offset / page.limit) + 1} 页
      </span>
      <button
        type="button"
        className="button secondary small"
        disabled={busy || page.offset === 0}
        onClick={() => onPage(Math.max(0, page.offset - page.limit))}
      >
        上一页
      </button>
      <button
        type="button"
        className="button secondary small"
        disabled={busy || !page.has_more}
        onClick={() => onPage(page.offset + page.limit)}
      >
        下一页
      </button>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  icon = <FolderOpen size={28} />,
}: {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Modal({
  title,
  subtitle,
  onClose,
  children,
  busy = false,
  error,
  describedBy,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  busy?: boolean;
  error?: string;
  describedBy?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const errorId = useId();
  useEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-labelledby={titleId}
      aria-describedby={[describedBy, error ? errorId : ''].filter(Boolean).join(' ') || undefined}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button
          type="button"
          className="icon-button"
          disabled={busy}
          onClick={onClose}
          aria-label="关闭弹窗"
        >
          <X size={20} />
        </button>
      </div>
      {error && (
        <div className="dialog-error" id={errorId} role="alert">
          <TriangleAlert size={16} />
          {error}
        </div>
      )}
      {children}
    </dialog>
  );
}

export function StatCard({
  label,
  value,
  unit,
  icon,
  hint,
  warning = false,
}: {
  label: string;
  value: number;
  unit: string;
  icon: ReactNode;
  hint: string;
  warning?: boolean;
}) {
  return (
    <article className={`stat-card ${warning ? 'warning' : ''}`}>
      <div className="stat-label">
        {label}
        <span>{icon}</span>
      </div>
      <div className="stat-value">
        {value.toLocaleString('zh-CN')}
        <span>{unit}</span>
      </div>
      <p>
        {warning && value > 0 ? <span className="warning-dot" /> : null}
        {hint}
      </p>
    </article>
  );
}
