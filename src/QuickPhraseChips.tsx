import './quick-phrase-chips.css';

/**
 * 面向高频正向办理场景的常用意见芯片：点击即填充规范用语，仍允许手工补充或修改。
 * 不用于异常退回/驳回或非独立复核（单人办理），避免弱化审慎留痕。
 */
export function QuickPhraseChips({
  label,
  phrases,
  value,
  disabled = false,
  onSelect,
}: {
  label: string;
  phrases: readonly string[];
  value: string;
  disabled?: boolean;
  onSelect: (phrase: string) => void;
}) {
  if (!phrases.length) return null;
  const current = value.trim();
  return (
    <div className="quick-phrase-chips" role="group" aria-label={label}>
      <span className="quick-phrase-chips-label">常用意见</span>
      <div className="quick-phrase-chips-list">
        {phrases.map((phrase) => {
          const active = current === phrase;
          return (
            <button
              key={phrase}
              type="button"
              className={'quick-phrase-chip' + (active ? ' active' : '')}
              aria-pressed={active}
              disabled={disabled}
              onClick={() => onSelect(phrase)}
            >
              {phrase}
            </button>
          );
        })}
      </div>
    </div>
  );
}
