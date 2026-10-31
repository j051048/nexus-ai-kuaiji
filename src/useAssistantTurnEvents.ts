import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { acceptTurnEvents, type TurnEventFeed } from './assistant-events';
import type { Session } from './types';

/** Read-only polling companion. Cursors and data never leave this drawer. */
export function useAssistantTurnEvents({
  session,
  companyId,
  conversationId,
  turnId,
  active,
  scopeKey,
  onRevoked,
  onFinished,
}: {
  session: Session;
  companyId: string;
  conversationId: string | null;
  turnId?: string;
  active: boolean;
  scopeKey: string;
  onRevoked: () => void;
  onFinished: () => Promise<void>;
}) {
  const key = `${scopeKey}:${turnId ?? ''}`;
  const live = useRef(key);
  live.current = key;
  const callbacks = useRef({ onRevoked, onFinished, active });
  callbacks.current = { onRevoked, onFinished, active };
  const state = useRef<{
    key: string;
    feed: TurnEventFeed | null;
    unsupported: boolean;
    finishedRead: boolean;
  }>({ key, feed: null, unsupported: false, finishedRead: false });
  const request = useRef<AbortController | null>(null);
  const [view, setView] = useState<{
    key: string;
    feed: TurnEventFeed | null;
    notice: string;
  } | null>(null);
  const read = useCallback(async () => {
    if (!turnId || !conversationId) return false;
    if (state.current.key !== key)
      state.current = { key, feed: null, unsupported: false, finishedRead: false };
    const current = state.current;
    if (current.unsupported) return false;
    if (request.current) return true;
    const controller = new AbortController();
    request.current = controller;
    try {
      const query = new URLSearchParams({ limit: '3' });
      if (current.feed) query.set('after', current.feed.cursor);
      const value = await api<unknown>(
        `/assistant/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/events?${query}`,
        session,
        { signal: controller.signal },
      );
      if (controller.signal.aborted || live.current !== key) return true;
      let next: TurnEventFeed;
      try {
        next = acceptTurnEvents(current.feed, value, { companyId, conversationId, turnId });
      } catch {
        current.feed = null;
        setView({ key, feed: null, notice: '执行记录范围或顺序已变化，将重新读取。' });
        return true;
      }
      current.feed = next;
      setView({ key, feed: next, notice: '' });
      if (next.terminal && !next.has_more && callbacks.current.active && !current.finishedRead) {
        current.finishedRead = true;
        await callbacks.current.onFinished();
      }
    } catch (failure) {
      if (controller.signal.aborted || live.current !== key) return true;
      if (
        failure instanceof ApiError &&
        failure.status === 404 &&
        failure.message === 'Not Found'
      ) {
        current.unsupported = true;
        current.feed = null;
        setView(null);
        return false;
      }
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        current.feed = null;
        setView(null);
        callbacks.current.onRevoked();
      } else if (failure instanceof ApiError && failure.status === 409) {
        current.feed = null;
        setView({
          key,
          feed: null,
          notice: '执行记录已变化，将从头重新读取；不会重新发送模型请求。',
        });
      } else
        setView({
          key,
          feed: current.feed,
          notice: '进度暂时无法读取，将从上次位置继续；不会重新发送模型请求。',
        });
    } finally {
      if (request.current === controller) request.current = null;
    }
    return true;
  }, [key, companyId, conversationId, turnId, session]);
  useEffect(() => {
    void read();
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [read]);
  return {
    read,
    feed: view?.key === key ? view.feed : null,
    notice: view?.key === key ? view.notice : '',
  };
}
