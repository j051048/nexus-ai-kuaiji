const RECOVERY_PARAM = '_deploy_reload';
const RECOVERY_KEY = 'nexus.preload-recovery-at';
const RETRY_WINDOW_MS = 15_000;
const CHUNK_ERROR_PATTERNS = [
  /unable to preload css/i,
  /failed to fetch dynamically imported module/i,
  /importing a module script failed/i,
  /loading chunk .* failed/i,
  /dynamically imported module/i,
];

export function isChunkLoadError(error: unknown) {
  const message =
    error instanceof Error
      ? `${error.name}: ${error.message}`
      : typeof error === 'string'
        ? error
        : '';
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

export function recoverFromChunkError(error: unknown) {
  if (typeof window === 'undefined' || !isChunkLoadError(error)) return false;
  try {
    const now = Date.now();
    const previous = Number(window.sessionStorage.getItem(RECOVERY_KEY) ?? 0);
    if (Number.isFinite(previous) && now - previous < RETRY_WINDOW_MS) return false;
    window.sessionStorage.setItem(RECOVERY_KEY, String(now));
    const url = new URL(window.location.href);
    url.searchParams.set(RECOVERY_PARAM, String(now));
    window.location.replace(url.toString());
    return true;
  } catch {
    return false;
  }
}

export function installPreloadRecovery() {
  if (typeof window === 'undefined') return;
  window.addEventListener('vite:preloadError', (event) => {
    const payload = (event as Event & { payload?: unknown }).payload;
    if (
      recoverFromChunkError(payload) ||
      recoverFromChunkError(new Error('Failed to fetch dynamically imported module'))
    ) {
      event.preventDefault();
    }
  });
  window.addEventListener('unhandledrejection', (event) => {
    if (recoverFromChunkError(event.reason)) event.preventDefault();
  });
}

export function cleanupRecoveryParam() {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(RECOVERY_PARAM)) return;
    url.searchParams.delete(RECOVERY_PARAM);
    window.history.replaceState(window.history.state, '', url.toString());
  } catch {
    // URL cleanup is optional; the reload itself is the recovery step.
  }
}
