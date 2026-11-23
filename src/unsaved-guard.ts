/**
 * Unsaved-edit guard registry (Batch 02 · 1.3).
 *
 * Editors register themselves while they hold uncommitted changes. The shell
 * asks this registry before it changes the working company or period — the two
 * switches that silently replace the data under an editor's feet. It reuses the
 * one confirmation dialog the app already owns; no `window.confirm` anywhere.
 */

export interface UnsavedLeaveRequest {
  title: string;
  description: string;
  /** Period/company being left, shown to the user as the object at stake. */
  period?: string;
  company?: string;
  confirmLabel?: string;
  cancelLabel?: string;
}

const sources = new Map<string, string>();

/** Register an editor's unsaved state; returns the unregister function. */
export function registerUnsavedGuard(id: string, label: string): () => void {
  const key = String(id || '').trim();
  if (!key) throw new Error('未保存守卫必须有标识');
  sources.set(key, String(label || key).trim() || key);
  return () => {
    sources.delete(key);
  };
}

export function unsavedLabels(): string[] {
  return [...sources.values()].sort();
}

export function hasUnsavedEdits(): boolean {
  return sources.size > 0;
}

export function resetUnsavedGuards(): void {
  sources.clear();
}

/**
 * Ask before leaving when any editor is dirty. Returns `true` when the caller
 * may proceed (clean, or the user confirmed discarding); `false` keeps the
 * current company/period and leaves every editor's text untouched.
 */
export async function confirmLeaveIfDirty(request: UnsavedLeaveRequest): Promise<boolean> {
  if (!hasUnsavedEdits()) return true;
  const labels = unsavedLabels();
  // 动态引入确认对话框：它的 CSS 不能被拉进首屏入口包（homeCss 余量只有几百字节）。
  const { requestConfirm } = await import('./confirm-dialog');
  return requestConfirm({
    title: request.title,
    description: `${request.description}（未保存：${labels.join('、')}）`,
    period: request.period,
    company: request.company,
    consequence: '确认离开将丢弃这些未保存的修改，且不能撤销。',
    confirmLabel: request.confirmLabel ?? '丢弃修改并切换',
    cancelLabel: request.cancelLabel ?? '留在当前单位/期间',
  });
}
