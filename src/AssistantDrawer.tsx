import { useEffect, useRef, type ReactNode } from 'react';
import { PanelRightClose, PanelRightOpen, ShieldCheck, Sparkles, X } from 'lucide-react';
import './assistant-drawer.css';

export type AssistantDrawerMode = 'context' | 'global';

export function AssistantDrawer({
  mode,
  contextAvailable,
  contextLabel,
  scopeBadge,
  docked = false,
  onToggleDock,
  onModeChange,
  onClose,
  children,
}: {
  mode: AssistantDrawerMode;
  contextAvailable: boolean;
  contextLabel?: string;
  scopeBadge?: string;
  docked?: boolean;
  onToggleDock?: () => void;
  onModeChange: (mode: AssistantDrawerMode) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!docked) closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !docked) {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      }
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, [docked]);

  return (
    <div className={'assistant-unified-layer' + (docked ? ' docked' : '')}>
      <aside
        className={`assistant-unified-drawer ${mode}` + (docked ? ' docked' : '')}
        role={docked ? 'complementary' : 'dialog'}
        aria-modal="false"
        aria-label="智能财税副驾"
      >
        <header className="assistant-unified-header">
          <span className="assistant-unified-mark">
            <Sparkles size={17} />
          </span>
          <div>
            <strong>智能财税副驾</strong>
            <small>{mode === 'context' ? '当前凭证依据核对' : '按账号权限查库 · 随时提问'}</small>
          </div>
          <div className="assistant-unified-header-actions">
            {onToggleDock && (
              <button
                type="button"
                className="icon-button assistant-dock-btn"
                aria-label={docked ? '切换为浮层窗口' : '固定常驻在右侧'}
                title={docked ? '切换为浮层窗口' : '固定常驻在右侧'}
                onClick={onToggleDock}
              >
                {docked ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
              </button>
            )}
            <button
              ref={closeButton}
              type="button"
              className="icon-button"
              aria-label="收起智能财税副驾"
              title="收起为悬浮球"
              onClick={onClose}
            >
              <X size={18} />
            </button>
          </div>
        </header>
        {scopeBadge && (
          <div className="assistant-scope-strip" aria-label="当前数据查询权限范围">
            <ShieldCheck size={14} aria-hidden="true" />
            <span>{scopeBadge}</span>
          </div>
        )}
        {contextAvailable && (
          <div className="assistant-unified-tabs" role="tablist" aria-label="智能财税副驾模式">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'context'}
              className={mode === 'context' ? 'active' : ''}
              title={contextLabel}
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
              直接提问
            </button>
            {contextLabel && (
              <span className="assistant-context-pill" title={contextLabel}>
                {contextLabel}
              </span>
            )}
          </div>
        )}
        <div className="assistant-unified-body">{children}</div>
      </aside>
    </div>
  );
}
