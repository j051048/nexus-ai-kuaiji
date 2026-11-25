/// <reference types="node" />
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  detectPwaEnvironment,
  dismissBanner,
  isBannerDismissed,
  PWA_DISMISS_TTL_MS,
  pwaGuideFor,
  shouldShowInstallBanner,
} from './pwa-install';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));

describe('PWA 国产手机与微信环境识别及安全边界', () => {
  it.each([
    [
      'Mozilla/5.0 (Linux; Android 14; ...) MicroMessenger/8.0.48 wxwork/4.1.20',
      'wecom',
      true,
      true,
    ],
    [
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) MicroMessenger/8.0.48',
      'wechat',
      true,
      true,
    ],
    ['Mozilla/5.0 (Linux; Android 13; ...) DingTalk/7.5.0', 'dingtalk', true, true],
    [
      'Mozilla/5.0 (Linux; Android 12; HarmonyOS; NOH-AN00) HuaweiBrowser/14.0',
      'huawei',
      true,
      false,
    ],
    [
      'Mozilla/5.0 (Linux; U; Android 14; zh-cn; 23116PN5BC) MiuiBrowser/18.2',
      'xiaomi',
      true,
      false,
    ],
    ['Mozilla/5.0 (Linux; Android 14; PJD110) HeyTapBrowser/45.10', 'oppo', true, false],
    ['Mozilla/5.0 (Linux; Android 14; V2324A) VivoBrowser/17.3', 'vivo', true, false],
    [
      'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1',
      'ios_safari',
      true,
      false,
    ],
    [
      'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) CriOS/128.0 Safari/604.1',
      'ios_other',
      true,
      false,
    ],
    [
      'Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/128.0 Mobile Safari/537.36',
      'android_other',
      true,
      false,
    ],
    [
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36',
      'desktop',
      false,
      false,
    ],
  ] as const)('准确识别 %s', (ua, expectedPlatform, expectedMobile, expectedExternal) => {
    const env = detectPwaEnvironment(ua, false);
    expect(env.platform).toBe(expectedPlatform);
    expect(env.isMobile).toBe(expectedMobile);
    expect(env.needsExternalBrowser).toBe(expectedExternal);
    expect(pwaGuideFor(env.platform).steps.length).toBeGreaterThanOrEqual(2);
  });

  it('国产安卓均附带系统桌面快捷方式权限排障提示', () => {
    for (const brand of ['huawei', 'xiaomi', 'oppo', 'vivo', 'android_other'] as const) {
      expect(pwaGuideFor(brand).permissionTip).toContain('创建桌面快捷方式');
    }
  });

  it('底部轻提示仅在非独立模式的真实手机端展示，并遵守 30 天免打扰记忆', () => {
    const mobileEnv = detectPwaEnvironment(
      'Mozilla/5.0 (Linux; Android 14; HUAWEI) HuaweiBrowser/14',
      false,
    );
    const desktopEnv = detectPwaEnvironment(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128',
      false,
    );
    const standaloneEnv = detectPwaEnvironment('Mozilla/5.0 (Linux; Android 14; HUAWEI)', true);

    expect(shouldShowInstallBanner(mobileEnv, false)).toBe(true);
    expect(shouldShowInstallBanner(desktopEnv, false)).toBe(false);
    expect(shouldShowInstallBanner(standaloneEnv, false)).toBe(false);

    const store = new Map<string, string>();
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
    };
    const t0 = 1_700_000_000_000;
    dismissBanner(t0, storage);
    expect(isBannerDismissed(t0 + 1000, storage)).toBe(true);
    expect(shouldShowInstallBanner(mobileEnv, isBannerDismissed(t0 + 1000, storage))).toBe(false);
    expect(isBannerDismissed(t0 + PWA_DISMISS_TTL_MS + 1, storage)).toBe(false);
  });

  it('清单文件、自适应遮罩图标与零业务数据缓存服务工作线程完整合规', () => {
    const manifestPath = resolve(root, 'public/manifest.webmanifest');
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as {
      short_name: string;
      display: string;
      icons: { src: string; purpose?: string }[];
    };
    expect(manifest.short_name).toBe('衡账');
    expect(manifest.display).toBe('standalone');
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
    for (const icon of manifest.icons) {
      expect(existsSync(resolve(root, 'public', icon.src.replace(/^\//, '')))).toBe(true);
    }

    const sw = readFileSync(resolve(root, 'public/sw.js'), 'utf8');
    expect(sw).toContain("url.pathname.startsWith('/api/')");
    // 严格只允许缓存带内容哈希指纹的 /assets/ 与 /icons/ 静态资源，禁止缓存任意接口或 HTML 文档
    expect(sw).toContain("pathname.startsWith('/assets/')");
    expect(sw).toContain("pathname.startsWith('/icons/')");
    expect(sw).toContain('isCacheableStaticPath(url.pathname)');
    expect(sw).not.toContain('cache.add');
  });

  it('PWA 引导样式仅使用设计令牌，不含硬编码十六进制颜色或过小字号', () => {
    const css = readFileSync(new URL('pwa-install.css', import.meta.url), 'utf8');
    expect(css).not.toMatch(/#[\da-f]{3,8}\b/i);
    expect(css).toContain('var(--focus-ring)');
    const small = [...css.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)]
      .map((m) => Number(m[1]))
      .filter((size) => size > 0 && size < 12);
    expect(small).toEqual([]);
  });
});
