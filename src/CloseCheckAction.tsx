import {
  resolveCloseAction,
  type CloseActionContext,
  type CloseActionTarget,
} from './close-action-target';

interface Props {
  target: CloseActionTarget | undefined;
  context: CloseActionContext;
  ownerRole?: string;
  onOpenPanel: (panel: 'subledger' | 'bank') => void;
}

/** One blocked check rendered as a destination or an explanation. A manual
 * result always names the responsible role so a reader knows who to ask. */
export function CloseCheckAction({ target, context, ownerRole, onOpenPanel }: Props) {
  const destination = resolveCloseAction(target, context);
  if (destination.mode === 'manual')
    return (
      <p className="formal-close-guidance">
        负责岗位：{ownerRole ?? '待指派'} · {destination.reason}
      </p>
    );
  if (destination.mode === 'link')
    return (
      <a className="button secondary" href={destination.href}>
        {destination.label}
      </a>
    );
  return (
    <button
      type="button"
      className="button secondary"
      onClick={() => onOpenPanel(destination.panel)}
    >
      {destination.label}
    </button>
  );
}
