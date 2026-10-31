import type { TurnEventFeed } from './assistant-events';

export function AssistantTurnProgress({
  feed,
  notice,
}: {
  feed: TurnEventFeed | null;
  notice: string;
}) {
  if (!feed && !notice) return null;
  return (
    <section className="ga-turn-progress" aria-label="助手执行记录">
      {feed && (
        <>
          <p role="status" aria-label="助手执行进度" aria-live="polite">
            {feed.events.at(-1)?.message}
          </p>
          <details>
            <summary>查看处理过程</summary>
            <ol>
              {feed.events.map((event) => (
                <li key={event.seq}>
                  <span>{event.message}</span>
                  {event.at && (
                    <time dateTime={event.at}>
                      {new Date(event.at).toLocaleTimeString('zh-CN', {
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                      })}
                    </time>
                  )}
                </li>
              ))}
            </ol>
          </details>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
