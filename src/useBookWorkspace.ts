import { approvalBody } from './review-policy';
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { api, errorMessage } from './api';
import { downloadCsv } from './csv';
import type { BookDetail, Draft, JournalLine, Session, Workbench } from './types';
import type { WorkspaceView } from './workspace-route';
import { reviewFailureNotice, type ReviewFeedbackNotice } from './review-operation-feedback';

export type BookFilter = 'all' | 'pending' | 'exception' | 'approved';
export type BookListTab = 'drafts' | 'sources' | 'exports';
export type BookNotice = ReviewFeedbackNotice;
type ExportRecord = { id: string; count: number; filename: string; created_at: string };

export interface UseBookWorkspaceOptions {
  session: Session | null;
  sessionScope: MutableRefObject<string | null>;
  routeView: WorkspaceView;
  routeUsesBook: boolean;
  businessBusy: boolean;
  setRouteView: (view: WorkspaceView) => void;
  setNotice: (notice: BookNotice) => void;
}

/**
 * Owns the accounting book list, selected book/draft, pagination and the
 * read/write lifecycle around them. Keeping these guards together prevents a
 * response started under an old firm, page or draft from becoming actionable
 * after the operator has already moved elsewhere.
 */
export function useBookWorkspace({
  session,
  sessionScope,
  routeView,
  routeUsesBook,
  businessBusy,
  setRouteView,
  setNotice,
}: UseBookWorkspaceOptions) {
  const [workbench, setWorkbench] = useState<Workbench | null>(null);
  const [bookId, setBookId] = useState('');
  const [storedDetail, setDetail] = useState<BookDetail | null>(null);
  const [draftId, setDraftId] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [filter, setFilter] = useState<BookFilter>('all');
  const [search, setSearch] = useState('');
  const [listTab, setListTab] = useState<BookListTab>('drafts');
  const [bookOffset, setBookOffset] = useState(0);
  const [draftOffset, setDraftOffset] = useState(0);
  const [sourceOffset, setSourceOffset] = useState(0);
  const [draftLoading, setDraftLoading] = useState(false);
  const [reviewNavigationLoading, setReviewNavigationLoading] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [reviewDirty, setReviewDirty] = useState(false);
  const [exportHistory, setExportHistory] = useState<ExportRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [operationBusy, setBusy] = useState('');
  const [intelligenceBusy, setIntelligenceBusy] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [bookLoading, setBookLoading] = useState(false);
  const [bootError, setBootError] = useState('');
  const [bookError, setBookError] = useState('');

  const bookIdRef = useRef(bookId);
  const actionLock = useRef(false);
  const workbenchReadEpoch = useRef(0);
  const bookReadEpoch = useRef(0);
  const draftReadEpoch = useRef(0);
  const exportRequest = useRef<{ signature: string; key: string } | null>(null);
  const focusBookRef = useRef('');
  const pendingReview = useRef<{ bookId: string; draftId: string } | null>(null);
  bookIdRef.current = bookId;

  const busy =
    operationBusy ||
    (reviewNavigationLoading
      ? 'navigation'
      : intelligenceBusy
        ? 'intelligence'
        : businessBusy
          ? 'business'
          : '');

  const changeReviewDirty = useCallback((value: boolean) => setReviewDirty(value), []);
  const changeIntelligenceBusy = useCallback((value: boolean) => setIntelligenceBusy(value), []);

  const workbenchPath = useCallback(
    () =>
      '/workbench?' +
      new URLSearchParams({
        limit: '20',
        offset: String(bookOffset),
        filter,
        search,
        ...(focusBookRef.current ? { focus_book_id: focusBookRef.current } : {}),
      }).toString(),
    [bookOffset, filter, search],
  );
  const bookPath = useCallback(
    (id: string) =>
      '/books/' +
      encodeURIComponent(id) +
      '?' +
      new URLSearchParams({
        limit: '20',
        offset: String(draftOffset),
        source_offset: String(sourceOffset),
        summaries: 'true',
        filter,
        ...(pendingReview.current?.bookId === id
          ? { focus_draft_id: pendingReview.current.draftId }
          : {}),
      }).toString(),
    [draftOffset, sourceOffset, filter],
  );

  useEffect(() => {
    setBookOffset(0);
    setDraftOffset(0);
    setSourceOffset(0);
    setSelectedIds([]);
  }, [filter, search]);

  const initialize = useCallback(
    async (signal?: AbortSignal, focusId?: string) => {
      if (!session?.firm_id || sessionScope.current !== session.scope_key) return;
      const scope = session.scope_key;
      const requestEpoch = ++workbenchReadEpoch.current;
      setBootLoading(true);
      setBootError('');
      try {
        const path = focusId
          ? '/workbench?' +
            new URLSearchParams({
              limit: '20',
              offset: '0',
              filter: 'all',
              focus_book_id: focusId,
            }).toString()
          : workbenchPath();
        const data = await api<Workbench>(path, session, { signal });
        if (
          signal?.aborted ||
          sessionScope.current !== scope ||
          workbenchReadEpoch.current !== requestEpoch
        )
          return;
        setWorkbench(data);
        setBookId(
          (current) =>
            pendingReview.current?.bookId ??
            (focusBookRef.current ||
              (data.books.some((book) => book.id === current)
                ? current
                : (data.books[0]?.id ?? ''))),
        );
      } catch (error) {
        if (
          !signal?.aborted &&
          sessionScope.current === scope &&
          workbenchReadEpoch.current === requestEpoch
        )
          setBootError(errorMessage(error));
      } finally {
        if (
          !signal?.aborted &&
          sessionScope.current === scope &&
          workbenchReadEpoch.current === requestEpoch
        )
          setBootLoading(false);
      }
    },
    [session, sessionScope, workbenchPath],
  );

  useEffect(() => {
    if (!routeUsesBook) {
      setBootLoading(false);
      return;
    }
    const controller = new AbortController();
    void initialize(controller.signal);
    return () => {
      controller.abort();
      workbenchReadEpoch.current++;
    };
  }, [initialize, routeUsesBook]);

  useEffect(() => {
    if (!routeUsesBook || !session || !bookId) {
      setDetail(null);
      setBookLoading(false);
      return;
    }
    const controller = new AbortController();
    const requestEpoch = ++bookReadEpoch.current;
    const scope = session.scope_key;
    draftReadEpoch.current++;
    setDetail(null);
    setDraftId(pendingReview.current?.bookId === bookId ? pendingReview.current.draftId : '');
    setSelectedIds([]);
    setBookLoading(true);
    setBookError('');
    exportRequest.current = null;
    void api<BookDetail>(bookPath(bookId), session, { signal: controller.signal })
      .then((data) => {
        if (
          controller.signal.aborted ||
          sessionScope.current !== scope ||
          bookReadEpoch.current !== requestEpoch ||
          bookIdRef.current !== bookId
        )
          return;
        setDetail(data);
        const requested =
          pendingReview.current?.bookId === data.book.id
            ? pendingReview.current.draftId
            : undefined;
        setDraftId(
          requested ??
            data.drafts.find((draft) => draft.status === 'draft')?.id ??
            data.drafts[0]?.id ??
            '',
        );
        setReviewNavigationLoading(false);
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          bookReadEpoch.current === requestEpoch
        ) {
          setBookError(errorMessage(error));
          setReviewNavigationLoading(false);
        }
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          bookReadEpoch.current === requestEpoch
        )
          setBookLoading(false);
      });
    return () => controller.abort();
  }, [bookId, session, sessionScope, bookPath, routeUsesBook]);

  useEffect(() => {
    setExportHistory([]);
    setHistoryError('');
    if (routeView !== 'accounting' || !session || !bookId || listTab !== 'exports') return;
    const controller = new AbortController();
    setHistoryLoading(true);
    void api<{ exports: ExportRecord[] }>(`/books/${encodeURIComponent(bookId)}/exports`, session, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setExportHistory(result.exports);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setHistoryError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [bookId, session, listTab, storedDetail, routeView]);

  const refresh = useCallback(
    async (id: string) => {
      if (!session || sessionScope.current !== session.scope_key) return;
      const scope = session.scope_key;
      const listEpoch = ++workbenchReadEpoch.current;
      const detailEpoch = ++bookReadEpoch.current;
      draftReadEpoch.current++;
      let nextWorkbench: Workbench;
      let nextDetail: BookDetail;
      try {
        [nextWorkbench, nextDetail] = await Promise.all([
          api<Workbench>(workbenchPath(), session),
          api<BookDetail>(bookPath(id), session),
        ]);
      } catch (error) {
        if (
          sessionScope.current === scope &&
          bookIdRef.current === id &&
          bookReadEpoch.current === detailEpoch
        ) {
          setDetail(null);
          setBookError(errorMessage(error));
          setBookLoading(false);
          setReviewNavigationLoading(false);
        }
        if (sessionScope.current === scope && workbenchReadEpoch.current === listEpoch)
          setBootLoading(false);
        throw error;
      }
      if (sessionScope.current !== scope) return;
      if (workbenchReadEpoch.current === listEpoch) {
        setWorkbench(nextWorkbench);
        setBootLoading(false);
      }
      if (bookIdRef.current !== id || bookReadEpoch.current !== detailEpoch) return;
      setDetail(nextDetail);
      setBookError('');
      const requested =
        pendingReview.current?.bookId === id ? pendingReview.current.draftId : undefined;
      setDraftId((current) => requested ?? (current || nextDetail.drafts[0]?.id || ''));
      setBookLoading(false);
      setReviewNavigationLoading(false);
      setSelectedIds((current) =>
        current.filter((value) =>
          nextDetail.drafts.some((draft) => draft.id === value && draft.status === 'approved'),
        ),
      );
    },
    [session, sessionScope, workbenchPath, bookPath],
  );

  const runAction = useCallback(
    async (
      name: string,
      action: () => Promise<void>,
      reviewTarget?: { bookId: string; draftId: string },
    ) => {
      if (
        actionLock.current ||
        reviewNavigationLoading ||
        intelligenceBusy ||
        businessBusy ||
        (reviewDirty && name !== 'save')
      )
        return;
      actionLock.current = true;
      workbenchReadEpoch.current++;
      bookReadEpoch.current++;
      draftReadEpoch.current++;
      const scope = sessionScope.current;
      setBusy(name);
      setNotice(null);
      try {
        await action();
      } catch (error) {
        if (
          sessionScope.current === scope &&
          !(error instanceof DOMException && error.name === 'AbortError')
        )
          setNotice(
            reviewTarget && scope
              ? reviewFailureNotice(errorMessage(error), { ...reviewTarget, scopeKey: scope })
              : { kind: 'error', text: errorMessage(error) },
          );
      } finally {
        if (sessionScope.current === scope) {
          actionLock.current = false;
          setBusy('');
        }
      }
    },
    [businessBusy, intelligenceBusy, reviewDirty, reviewNavigationLoading, sessionScope, setNotice],
  );

  const chooseBook = useCallback(
    (id: string) => {
      if (id === bookId || busy || reviewDirty) return;
      focusBookRef.current = '';
      pendingReview.current = null;
      setDetail(null);
      setDraftId('');
      setSelectedIds([]);
      setBookError('');
      setBookId(id);
      setListTab('drafts');
      setDraftOffset(0);
      setSourceOffset(0);
    },
    [bookId, busy, reviewDirty],
  );

  const openDraftInBook = useCallback(
    (id: string, targetDraftId: string) => {
      if (!session || sessionScope.current !== session.scope_key || !targetDraftId) return;
      workbenchReadEpoch.current++;
      bookReadEpoch.current++;
      draftReadEpoch.current++;
      focusBookRef.current = id;
      pendingReview.current = { bookId: id, draftId: targetDraftId };
      setDetail(null);
      setDraftId(targetDraftId);
      setDraftError('');
      setReviewNavigationLoading(true);
      setSelectedIds([]);
      setBookOffset(0);
      setDraftOffset(0);
      setSourceOffset(0);
      setFilter('all');
      setSearch('');
      setBookId(id);
      setRouteView('accounting');
      if (routeUsesBook && bookId === id)
        void refresh(id).catch((error) => {
          if (sessionScope.current === session.scope_key && bookIdRef.current === id) {
            setBookError(errorMessage(error));
            setReviewNavigationLoading(false);
          }
        });
      if (routeUsesBook) void initialize(undefined, id);
    },
    [bookId, initialize, refresh, routeUsesBook, session, sessionScope, setRouteView],
  );

  const acceptMutatedDraft = useCallback(
    (id: string, draft: Draft) => {
      if (!session || sessionScope.current !== session.scope_key || bookIdRef.current !== id)
        return;
      bookReadEpoch.current++;
      draftReadEpoch.current++;
      setDetail((current) =>
        current?.book.id === id
          ? {
              ...current,
              drafts: current.drafts.map((row) =>
                row.id === draft.id && row.version <= draft.version
                  ? { ...draft, detail_loaded: true }
                  : row,
              ),
            }
          : current,
      );
    },
    [session, sessionScope],
  );

  const generateSuggestions = useCallback(async () => {
    if (!session || !bookId) return;
    const id = bookId;
    await runAction('suggest', async () => {
      const result = await api<{ created: number }>(
        `/books/${encodeURIComponent(id)}/suggestions`,
        session,
        { method: 'POST', body: {} },
      );
      await refresh(id);
      setListTab('drafts');
      setNotice({
        kind: 'success',
        text: result.created
          ? `已生成 ${result.created} 张凭证草稿，请逐张核对依据后审核。`
          : '没有待生成建议的流水。已有草稿不会重复生成。',
      });
    });
  }, [bookId, refresh, runAction, session, setNotice]);

  const saveDraft = useCallback(
    async (draft: Draft, description: string, lines: JournalLine[], correctionReason = '') => {
      if (!session) return;
      const id = bookId;
      await runAction(
        'save',
        async () => {
          const saved = await api<Draft>(
            `/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}`,
            session,
            {
              method: 'PATCH',
              body: {
                expected_version: draft.version,
                description,
                lines,
                correction_reason: correctionReason,
              },
            },
          );
          acceptMutatedDraft(id, saved);
          await refresh(id);
          setNotice({ kind: 'success', text: '草稿已保存，金额和科目校验结果已更新。' });
        },
        { bookId: id, draftId: draft.id },
      );
    },
    [acceptMutatedDraft, bookId, refresh, runAction, session, setNotice],
  );

  const approveDraft = useCallback(
    async (draft: Draft, reviewReason = '') => {
      if (!session) return;
      const id = bookId;
      await runAction(
        'approve',
        async () => {
          const approved = await api<Draft>(
            `/books/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draft.id)}/approve`,
            session,
            {
              method: 'POST',
              body: approvalBody(
                draft.review_responsibility,
                draft.version,
                session.user_id,
                reviewReason,
              ),
            },
          );
          acceptMutatedDraft(id, approved);
          await refresh(id);
          setNotice({
            kind: 'success',
            text: '凭证已审核，可选中后导出。审核不会写入外部正式账本。',
          });
        },
        { bookId: id, draftId: draft.id },
      );
    },
    [acceptMutatedDraft, bookId, refresh, runAction, session, setNotice],
  );

  const exportDrafts = useCallback(async () => {
    if (!session || !selectedIds.length) return;
    const id = bookId;
    const ids = [...selectedIds].sort();
    const signature = `${id}:${ids.join(',')}`;
    if (exportRequest.current?.signature !== signature)
      exportRequest.current = { signature, key: crypto.randomUUID() };
    const key = exportRequest.current.key;
    await runAction('export', async () => {
      const result = await api<{ id: string; csv: string; count: number; filename: string }>(
        `/books/${encodeURIComponent(id)}/exports`,
        session,
        {
          method: 'POST',
          body: { draft_ids: ids, idempotency_key: key },
        },
      );
      downloadCsv(result.csv, result.filename);
      await refresh(id);
      setSelectedIds([]);
      exportRequest.current = null;
      setNotice({
        kind: 'success',
        text: `已导出 ${result.count} 张凭证。请在目标财务系统导入后核验回执与金额。`,
      });
    });
  }, [bookId, refresh, runAction, selectedIds, session, setNotice]);

  const stats = workbench?.stats;
  const filteredBooks = workbench?.books ?? [];
  const detail = filteredBooks.some((book) => book.id === bookId) ? storedDetail : null;

  useEffect(() => {
    if (!routeUsesBook || busy || bootLoading || !workbench) return;
    if (!filteredBooks.some((book) => book.id === bookId)) {
      setDetail(null);
      if (pendingReview.current?.bookId === bookId || focusBookRef.current === bookId) {
        setDraftId(pendingReview.current?.draftId ?? '');
        setSelectedIds([]);
        if (!bookLoading)
          setBookError(
            (current) =>
              current || '所选账套或凭证不存在或当前无权访问，请核对权限或手动选择另一账套。',
          );
        return;
      }
      setDraftId('');
      setSelectedIds([]);
      setBookId(filteredBooks[0]?.id ?? '');
    }
  }, [filter, search, workbench, bookId, busy, bootLoading, bookLoading, routeUsesBook]);

  const drafts = detail?.drafts ?? [];
  const activeDraft = draftId ? drafts.find((draft) => draft.id === draftId) : drafts[0];

  useEffect(() => {
    setDraftError('');
    if (
      routeView !== 'accounting' ||
      !session ||
      !activeDraft ||
      activeDraft.detail_loaded !== false
    ) {
      setDraftLoading(false);
      return;
    }
    const controller = new AbortController();
    const requestEpoch = ++draftReadEpoch.current;
    const scope = session.scope_key;
    const id = activeDraft.id;
    const version = activeDraft.version;
    setDraftLoading(true);
    void api<Draft>(
      '/books/' + encodeURIComponent(bookId) + '/drafts/' + encodeURIComponent(id),
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDetail((current) =>
            current?.book.id === bookId
              ? {
                  ...current,
                  drafts: current.drafts.map((row) =>
                    row.id === id &&
                    row.version === version &&
                    value.id === id &&
                    value.version >= row.version
                      ? value
                      : row,
                  ),
                }
              : current,
          );
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDraftError(errorMessage(error));
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          sessionScope.current === scope &&
          draftReadEpoch.current === requestEpoch
        )
          setDraftLoading(false);
      });
    return () => controller.abort();
  }, [
    session,
    sessionScope,
    bookId,
    activeDraft?.id,
    activeDraft?.version,
    activeDraft?.detail_loaded,
    storedDetail,
    routeView,
  ]);

  const approvedDraftIds = drafts
    .filter((draft) => draft.status === 'approved')
    .map((draft) => draft.id);

  const clearProtectedData = useCallback(() => {
    workbenchReadEpoch.current++;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    pendingReview.current = null;
    setWorkbench(null);
    setDetail(null);
    setSelectedIds([]);
    setExportHistory([]);
    setReviewDirty(false);
    setBookLoading(false);
    setBootLoading(false);
    setDraftLoading(false);
    setHistoryLoading(false);
    setReviewNavigationLoading(false);
    setBookError('权限不足或授权已变更，请确认权限后刷新。');
    // Keep route selectors for a visible denial/retry at the original location.
  }, []);

  const reset = useCallback(() => {
    workbenchReadEpoch.current++;
    bookReadEpoch.current++;
    draftReadEpoch.current++;
    pendingReview.current = null;
    focusBookRef.current = '';
    setWorkbench(null);
    setBookId('');
    setDetail(null);
    setDraftId('');
    setSelectedIds([]);
    setExportHistory([]);
    setHistoryError('');
    setHistoryLoading(false);
    setBookError('');
    setBookLoading(false);
    setFilter('all');
    setSearch('');
    setListTab('drafts');
    setBookOffset(0);
    setDraftOffset(0);
    setSourceOffset(0);
    setDraftLoading(false);
    setDraftError('');
    setBusy('');
    setReviewDirty(false);
    setReviewNavigationLoading(false);
    setIntelligenceBusy(false);
    actionLock.current = false;
    exportRequest.current = null;
  }, []);

  return {
    clearProtectedData,
    acceptMutatedDraft,
    activeDraft,
    approveDraft,
    approvedDraftIds,
    bookError,
    bookId,
    bookLoading,
    bookOffset,
    bootError,
    bootLoading,
    busy,
    changeIntelligenceBusy,
    changeReviewDirty,
    chooseBook,
    detail,
    draftError,
    draftId,
    draftLoading,
    draftOffset,
    drafts,
    exportDrafts,
    exportHistory,
    filter,
    filteredBooks,
    focusBookRef,
    generateSuggestions,
    historyError,
    historyLoading,
    initialize,
    intelligenceBusy,
    listTab,
    openDraftInBook,
    operationBusy,
    pendingReview,
    refresh,
    reset,
    reviewDirty,
    reviewNavigationLoading,
    runAction,
    saveDraft,
    search,
    selectedIds,
    setBookId,
    setBookError,
    setBookOffset,
    setDetail,
    setDraftId,
    setDraftOffset,
    setFilter,
    setListTab,
    setSearch,
    setSelectedIds,
    setSourceOffset,
    setReviewDirty,
    setWorkbench,
    sourceOffset,
    stats,
    workbench,
  };
}
