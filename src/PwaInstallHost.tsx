import { useEffect, useState } from 'react';
import { CheckCircle2, Copy, Download, Smartphone, X } from 'lucide-react';
import {
  clearDeferredInstallPrompt,
  getDeferredInstallPrompt,
  isStandalonePwa,
  onPwaGuideOpen,
  onPwaInstalled,
  onPwaPromptChange,
  type BeforeInstallPromptEvent,
} from './pwa-install-trigger';
import {
  detectPwaEnvironment,
  dismissBanner,
  isBannerDismissed,
  pwaGuideFor,
  shouldShowInstallBanner,
  type PwaPlatform,
} from './pwa-install';
import './pwa-install.css';

const OTHER_BRANDS: readonly PwaPlatform[] = ['huawei', 'xiaomi', 'oppo', 'vivo', 'ios_safari'];

export function PwaInstallHost() {
  const [standalone, setStandalone] = useState(() => isStandalonePwa());
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(() =>
    getDeferredInstallPrompt(),
  );
  const [guideOpen, setGuideOpen] = useState(false);
  const [dismissed, setDismissed] = useState(() =>
    isBannerDismissed(Date.now(), typeof localStorage !== 'undefined' ? localStorage : null),
  );
  const [installing, setInstalling] = useState(false);
  const [feedback, setFeedback] = useState('');

  const env = detectPwaEnvironment(
    typeof navigator !== 'undefined' ? navigator.userAgent : '',
    standalone,
  );
  const guide = pwaGuideFor(env.platform);

  useEffect(() => {
    const offOpen = onPwaGuideOpen(() => {
      setFeedback('');
      setGuideOpen(true);
    });
    const offPrompt = onPwaPromptChange(() => {
      setPromptEvent(getDeferredInstallPrompt());
    });
    const offInstalled = onPwaInstalled(() => {
      setStandalone(true);
      setGuideOpen(false);
      setPromptEvent(null);
    });
    return () => {
      offOpen();
      offPrompt();
      offInstalled();
    };
  }, []);

  useEffect(() => {
    if (!guideOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setGuideOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [guideOpen]);

  async function triggerNativeInstall() {
    if (!promptEvent || installing) return;
    setInstalling(true);
    setFeedback('');
    try {
      await promptEvent.prompt();
      const choice = await promptEvent.userChoice;
      clearDeferredInstallPrompt();
      setPromptEvent(null);
      if (choice.outcome === 'accepted') {
        setFeedback('已发起安装，稍等片刻即可在桌面看到「衡账」图标。');
        setDismissed(true);
      } else {
        setFeedback('您已取消本次安装，后续可随时在菜单或登录页再次添加。');
      }
    } catch {
      setFeedback('当前浏览器未唤起系统安装弹窗，请按下方步骤手动添加到桌面。');
    } finally {
      setInstalling(false);
    }
  }

  async function copyCurrentUrl() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setFeedback('已复制当前网址，请切换到系统浏览器粘贴打开。');
    } catch {
      setFeedback('请长按浏览器地址栏手动复制当前网址。');
    }
  }

  function handleDismissBanner() {
    dismissBanner(Date.now(), typeof localStorage !== 'undefined' ? localStorage : null);
    setDismissed(true);
  }

  const showBanner = !guideOpen && shouldShowInstallBanner(env, dismissed);

  if (!showBanner && !guideOpen) return null;

  return (
    <>
      {showBanner && (
        <aside className="pwa-banner" aria-label="添加到手机桌面提示">
          <img
            className="pwa-banner-icon"
            src="/icons/icon-192.png"
            alt=""
            width={36}
            height={36}
          />
          <div className="pwa-banner-copy">
            <strong>把「衡账」放到手机桌面</strong>
            <span>一键全屏打开，拍照报销与审单更顺手</span>
          </div>
          <div className="pwa-banner-actions">
            {promptEvent ? (
              <button
                type="button"
                className="button primary small"
                disabled={installing}
                onClick={() => void triggerNativeInstall()}
              >
                {installing ? '安装中…' : '一键安装'}
              </button>
            ) : (
              <button
                type="button"
                className="button primary small"
                onClick={() => {
                  setFeedback('');
                  setGuideOpen(true);
                }}
              >
                添加到桌面
              </button>
            )}
            <button
              type="button"
              className="pwa-banner-close"
              aria-label="30 天内不再提示安装到桌面"
              onClick={handleDismissBanner}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </aside>
      )}
      {guideOpen && (
        <div
          className="pwa-modal-backdrop"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) setGuideOpen(false);
          }}
        >
          <section
            className="pwa-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="pwa-modal-title"
          >
            <header className="pwa-modal-header">
              <div className="pwa-modal-brand">
                <img src="/icons/icon-192.png" alt="衡账桌面图标" width={44} height={44} />
                <div>
                  <h2 id="pwa-modal-title">将「衡账」添加到桌面</h2>
                  <p>当前环境：{guide.label}</p>
                </div>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭添加到桌面引导"
                onClick={() => setGuideOpen(false)}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </header>

            <div className="pwa-modal-body">
              {standalone ? (
                <p className="pwa-modal-status" role="status">
                  <CheckCircle2 size={16} aria-hidden="true" />
                  您当前已通过桌面图标独立打开「衡账」，无需重复安装。
                </p>
              ) : (
                <>
                  <p className="pwa-modal-summary">{guide.summary}</p>
                  {promptEvent && (
                    <button
                      type="button"
                      className="button primary pwa-native-button"
                      disabled={installing}
                      onClick={() => void triggerNativeInstall()}
                    >
                      <Download size={16} aria-hidden="true" />
                      {installing ? '正在唤起系统安装…' : '立即一键安装到桌面'}
                    </button>
                  )}
                  {(env.needsExternalBrowser || env.platform === 'ios_other') && (
                    <button
                      type="button"
                      className="button secondary pwa-copy-button"
                      onClick={() => void copyCurrentUrl()}
                    >
                      <Copy size={15} aria-hidden="true" />
                      复制当前网址，去系统浏览器打开
                    </button>
                  )}
                  <ol className="pwa-steps">
                    {guide.steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                  {guide.permissionTip && (
                    <p className="pwa-permission-tip">
                      <Smartphone size={15} aria-hidden="true" />
                      <span>{guide.permissionTip}</span>
                    </p>
                  )}
                </>
              )}

              {feedback && (
                <p className="pwa-modal-feedback" role="status">
                  {feedback}
                </p>
              )}

              <details className="pwa-other-brands">
                <summary>查看华为、小米、OPPO、vivo、苹果通用安装方法</summary>
                <div className="pwa-brand-grid">
                  {OTHER_BRANDS.filter((item) => item !== env.platform).map((brand) => {
                    const info = pwaGuideFor(brand);
                    return (
                      <article key={brand}>
                        <h3>{info.label}</h3>
                        <ol>
                          {info.steps.map((step) => (
                            <li key={step}>{step}</li>
                          ))}
                        </ol>
                      </article>
                    );
                  })}
                </div>
              </details>
            </div>

            <footer className="pwa-modal-footer">
              <p>数据安全说明：桌面图标仅方便快捷打开，不会在本地离线缓存企业账务数据。</p>
              <button
                type="button"
                className="button secondary small"
                onClick={() => setGuideOpen(false)}
              >
                我知道了
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
