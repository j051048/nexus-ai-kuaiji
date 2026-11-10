import { useCallback, useEffect, useRef, useState } from 'react';
import type { BusinessEvent, BusinessScope } from './business-types';
import type { Session } from './types';
import {
  taskMemoryKey,
  readTaskMemory,
  taskCheckpoint,
  type TaskFocus,
} from './business-task-memory';

export function useBusinessTaskMemory(
  session: Session,
  selected: BusinessEvent | null,
  filters: { scope: BusinessScope; status: string; offset: number },
) {
  const key = taskMemoryKey(session);
  const [bookmark, setBookmark] = useState(() => {
    try {
      return readTaskMemory(localStorage.getItem(key), session);
    } catch {
      return null;
    }
  });
  const focus = useRef<TaskFocus>(bookmark?.focus ?? 'actions');
  const latest = useRef({ selected, filters, session });
  latest.current = { selected, filters, session };
  const remember = useCallback((section?: TaskFocus) => {
    if (section) focus.current = section;
    const { selected: event, filters: currentFilters, session: identity } = latest.current;
    if (!event || event.firm_id !== identity.firm_id) return;
    try {
      localStorage.setItem(
        taskMemoryKey(identity),
        JSON.stringify(taskCheckpoint(identity, event, currentFilters, focus.current)),
      );
    } catch {
      /* Local storage may be unavailable; the authoritative flow still works. */
    }
  }, []);
  const forget = useCallback(() => {
    try {
      localStorage.removeItem(key);
    } catch {
      /* Nothing authoritative is stored here. */
    }
    setBookmark(null);
  }, [key]);
  useEffect(() => {
    remember();
  }, [selected, filters.scope, filters.status, filters.offset, remember]);
  useEffect(() => {
    const revoke = (event: Event) => {
      if ((event as CustomEvent).detail === session.scope_key) forget();
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key, forget]);
  return {
    bookmark:
      bookmark?.userId === session.user_id && bookmark.firmId === session.firm_id ? bookmark : null,
    remember,
    forget,
    consumed: () => setBookmark(null),
  };
}
