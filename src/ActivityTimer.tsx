import { useEffect, useRef, useState } from 'react';
import { Clock3 } from 'lucide-react';
import { api } from './api';
import { canAcknowledgeActivity, durationLabel, workActivities, type WorkSession } from './efficiency-helpers';
import type { Session } from './types';

/** Only a deliberately started session in the visible book can receive activity. */
export function ActivityTimer({ session, bookId, enabled, onOpen }: { session: Session; bookId?: string; enabled: boolean; onOpen: () => void }) {
  const [current, setCurrent] = useState<WorkSession | null>(null), [error, setError] = useState('');
  const lastInput = useRef(-Infinity);
  useEffect(() => {
    let active = true, inflight = false;
    const controller = new AbortController();
    const interacted = () => { lastInput.current = performance.now(); };
    lastInput.current = -Infinity;
    window.addEventListener('pointerdown', interacted, { passive: true }); window.addEventListener('keydown', interacted);
    async function tick(acknowledge: boolean) {
      if (!enabled || inflight) return;
      inflight = true;
      try {
        const result = await api<{ sessions: WorkSession[] }>('/efficiency/sessions?limit=100', session, { signal: controller.signal });
        if (!active) return;
        let row = result.sessions.find(s => s.state === 'active') ?? null;
        if (acknowledge && row && row.book_id === bookId && bookId && canAcknowledgeActivity(document.visibilityState === 'visible', document.hasFocus(), performance.now() - lastInput.current)) {
          row = await api<WorkSession>(`/efficiency/sessions/${encodeURIComponent(row.id)}/commands`, session, { method: 'POST', body: { action: 'heartbeat', expected_version: row.version, idempotency_key: crypto.randomUUID() }, signal: controller.signal });
        }
        if (active) { setCurrent(row); setError(''); }
      } catch { if (active) { setCurrent(null); setError('计时状态待确认，请进入人工效率页核对'); } }
      finally { inflight = false; }
    }
    if (!enabled) { setCurrent(null); setError(''); }
    else void tick(false);
    const timer = window.setInterval(() => { void tick(true); }, 20_000);
    return () => { active = false; controller.abort(); clearInterval(timer); window.removeEventListener('pointerdown', interacted); window.removeEventListener('keydown', interacted); };
  }, [session, enabled, bookId]);
  if (!enabled || (!current && !error)) return null;
  return <div className="activity-timer" aria-label="已开启的人工工作计时"><Clock3 size={14} /><span>{error || `${workActivities[current!.activity]} · 已确认 ${durationLabel(current!.credited_seconds)} · ${current?.book_id === bookId ? '当前账套有操作时记录' : '其他范围，当前不累计'}`}</span>{bookId && <button type="button" className="button secondary small" onClick={onOpen}>管理计时</button>}</div>;
}
