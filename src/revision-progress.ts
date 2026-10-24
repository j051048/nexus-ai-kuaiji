import type { BusinessPayload } from './business-types';
import type { RevisionContext } from './revision-types';
import { revisionChanges } from './revision-types';

/** Unsaved inputs only; this does not apply or approve a fact revision. */
export function revisionIsDirty({
  context,
  payload,
  targetBook,
  reason,
  reviewReason,
}: {
  context: Pick<RevisionContext, 'payload' | 'mapping'> | null;
  payload: BusinessPayload | null;
  targetBook: string;
  reason: string;
  reviewReason: string;
}): boolean {
  return (
    !!(context && payload && revisionChanges(context.payload, payload).length) ||
    !!reason.trim() ||
    !!reviewReason.trim() ||
    (!!context && targetBook !== context.mapping.book_id)
  );
}
