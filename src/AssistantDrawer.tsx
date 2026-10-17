import { useEffect, useRef, type ReactNode } from 'react';
import { Sparkles, X } from 'lucide-react';
import './assistant-drawer.css';

export type AssistantDrawerMode = 'context' | 'global';

export function AssistantDrawer({
  mode,
  contextAvailable,
  contextLabel,
  onModeChange,
  onClose,
  children,
}: {
  mode: AssistantDrawerMode;
  contextAvailable: boolean;
  contextLabel?: string;
  onModeChange: (mode: AssistantDrawerMode) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      }
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, []);
  return (
    <div className="assistant-unified-layer">
      <aside
        className={`assistant-unified-drawer ${mode}`}
        role="dialog"
        aria-modal="false"
        aria-label="会计助手"
      >
        <header className="assistant-unified-header">
          <span className="assistant-unified-mark">
            <Sparkles size={17} />
          </span>
          <div>
            <strong>会计助手</strong>
            <small>{mode === 'context' ? '当前凭证依据' : '全账套与跨期对话'}</small>
          </div>
          <button
            ref={closeButton}
            type="button"
            className="icon-button"
            aria-label="关闭会计助手"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        <div className="assistant-unified-tabs" role="tablist" aria-label="会计助手模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'context'}
            className={mode === 'context' ? 'active' : ''}
            disabled={!contextAvailable}
            title={contextAvailable ? contextLabel : '先打开一张凭证再切换'}
            onClick={() => onModeChange('context')}
          >
            当前凭证
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'global'}
            className={mode === 'global' ? 'active' : ''}
            onClick={() => onModeChange('global')}
          >
            全账套对话
          </button>
          {contextAvailable && contextLabel && (
            <span className="assistant-context-pill" title={contextLabel}>
              {contextLabel}
            </span>
          )}
        </div>
        <div className="assistant-unified-body">{children}</div>
      </aside>
    </div>
  );
}
