import { ClipboardCheck } from 'lucide-react';
import type { BusinessEvent } from './business-types';
import type { Book } from './types';

export type BusinessMappedBook = Pick<
  Book,
  'id' | 'company_id' | 'period' | 'period_status' | 'permissions'
>;

export function BusinessRecordLinks({
  event,
  book,
  busy = false,
  onOpenDraft,
  onOpenRevision,
}: {
  event: BusinessEvent;
  book?: BusinessMappedBook;
  busy?: boolean;
  onOpenDraft: (bookId: string, draftId: string) => void;
  onOpenRevision?: (eventId: string, scope?: { companyId: string; period: string }) => void;
}) {
  const mapping = event.mapping;
  if (
    !mapping ||
    !book ||
    !mapping.draft_id ||
    mapping.book_id !== book.id ||
    book.company_id !== event.company_id ||
    book.period !== event.payload.occurred_on.slice(0, 7)
  )
    return null;
  const canRevise = book.permissions.edit && book.period_status === 'open';
  const canReview =
    book.permissions.review && book.permissions.edit && book.period_status === 'open';
  return (
    <section className="business-record-links" aria-label="原事项关联凭证与纠错">
      <div>
        <h4>关联凭证与事实纠错</h4>
        <p>
          {canRevise
            ? '金额或日期有误时，从原事项提出修订，再由另一位会计独立审批；已审核记录保留历史。'
            : '当前关联记录只读；如需更正，请联系授权会计核对期间与后续调整方式。'}
        </p>
      </div>
      <div className="business-record-link-actions">
        <button
          className="button secondary"
          type="button"
          disabled={busy}
          onClick={() => onOpenDraft(mapping.book_id, mapping.draft_id)}
        >
          <ClipboardCheck size={15} />
          {canReview ? '打开会计复核' : '查看关联凭证'}
        </button>
        {canRevise && onOpenRevision && (
          <button
            className="button secondary"
            type="button"
            disabled={busy}
            onClick={() =>
              onOpenRevision(event.id, {
                companyId: event.company_id,
                period: event.payload.occurred_on.slice(0, 7),
              })
            }
          >
            申请事实修订
          </button>
        )}
      </div>
    </section>
  );
}
