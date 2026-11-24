export interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

const OPEN_EVENT = 'hengzhang:pwa-open-guide';
const PROMPT_EVENT = 'hengzhang:pwa-prompt-changed';
const INSTALLED_EVENT = 'hengzhang:pwa-installed';

let deferredPrompt: BeforeInstallPromptEvent | null = null;
let runtimeInstalled = false;

export function isStandalonePwa(): boolean {
  if (typeof window === 'undefined') return false;
  const nav = window.navigator as Navigator & { standalone?: boolean };
  return (
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    window.matchMedia?.('(display-mode: minimal-ui)').matches === true ||
    nav.standalone === true
  );
}

export function getDeferredInstallPrompt(): BeforeInstallPromptEvent | null {
  return deferredPrompt;
}

export function clearDeferredInstallPrompt(): void {
  deferredPrompt = null;
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(PROMPT_EVENT));
}

export function openPwaInstallGuide(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(OPEN_EVENT));
}

export function onPwaGuideOpen(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(OPEN_EVENT, listener);
  return () => window.removeEventListener(OPEN_EVENT, listener);
}

export function onPwaPromptChange(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(PROMPT_EVENT, listener);
  return () => window.removeEventListener(PROMPT_EVENT, listener);
}

export function onPwaInstalled(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(INSTALLED_EVENT, listener);
  return () => window.removeEventListener(INSTALLED_EVENT, listener);
}

export function registerPwaRuntime(): void {
  if (typeof window === 'undefined' || runtimeInstalled) return;
  runtimeInstalled = true;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event as BeforeInstallPromptEvent;
    window.dispatchEvent(new CustomEvent(PROMPT_EVENT));
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    window.dispatchEvent(new CustomEvent(PROMPT_EVENT));
    window.dispatchEvent(new CustomEvent(INSTALLED_EVENT));
  });

  if (
    'serviceWorker' in navigator &&
    (window.location.protocol === 'https:' ||
      window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1')
  ) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
        /* 浏览器禁用 Service Worker 时静默回退，不影响正常财税功能。 */
      });
    });
  }
}
