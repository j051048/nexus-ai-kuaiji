import { useId, useMemo, useState } from 'react';
import { Eye, X } from 'lucide-react';
import { EvidenceViewer, type ReviewAttachment } from './EvidenceViewer';
import type { BusinessEvent } from './business-types';
import type { Session } from './types';

/** Reuse the existing authenticated, abortable blob viewer; no read occurs until opened. */
export function BusinessOriginalPreview({
  event,
  session,
  busy = false,
}: {
  event: BusinessEvent;
  session: Session;
  busy?: boolean;
}) {
  const contentId = useId();
  const [openedScope, setOpenedScope] = useState('');
  const scope = `${session.scope_key}:${event.id}:${event.version}`;
  const opened = openedScope === scope;
  const attachments = useMemo<ReviewAttachment[]>(
    () =>
      event.attachments.map((attachment) => ({
        ...attachment,
        contentPath: `/business/attachments/${encodeURIComponent(attachment.id)}/content`,
      })),
    [event.attachments],
  );
  if (!attachments.length) return null;
  return (
    <div className="business-original-preview">
      <button
        type="button"
        className="button secondary small"
        disabled={busy}
        aria-label={opened ? '收起原件预览' : '预览原件'}
        aria-expanded={opened}
        aria-controls={contentId}
        onClick={() => setOpenedScope(opened ? '' : scope)}
      >
        {opened ? <X size={14} /> : <Eye size={14} />}
        {opened ? '收起原件预览' : '预览原件'}
      </button>
      {opened && (
        <div id={contentId}>
          <EvidenceViewer
            key={scope}
            session={session}
            attachments={attachments}
            reference="当前业务事项留存原件；预览不代表真实性或会计处理已获确认。"
          />
        </div>
      )}
    </div>
  );
}
