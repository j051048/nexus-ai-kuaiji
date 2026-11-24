export type PwaPlatform =
  | 'wecom'
  | 'wechat'
  | 'dingtalk'
  | 'huawei'
  | 'xiaomi'
  | 'oppo'
  | 'vivo'
  | 'ios_safari'
  | 'ios_other'
  | 'android_other'
  | 'desktop';

export interface PwaEnvironment {
  platform: PwaPlatform;
  isMobile: boolean;
  standalone: boolean;
  needsExternalBrowser: boolean;
  isDomesticAndroid: boolean;
}

export interface PwaGuideContent {
  label: string;
  summary: string;
  steps: readonly string[];
  permissionTip?: string;
}

export const PWA_DISMISS_STORAGE_KEY = 'hengzhang-pwa-banner-dismissed-at';
export const PWA_DISMISS_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export function detectPwaEnvironment(userAgent: string, standalone: boolean): PwaEnvironment {
  const ua = userAgent || '';
  const isIos = /iPhone|iPad|iPod/i.test(ua);
  const isHarmony = /HarmonyOS|OpenHarmony/i.test(ua);
  const isAndroid = /Android/i.test(ua) || isHarmony;
  const isMobile = isIos || isAndroid || /Mobile|MicroMessenger|wxwork|DingTalk/i.test(ua);

  let platform: PwaPlatform = 'desktop';
  if (/wxwork/i.test(ua)) {
    platform = 'wecom';
  } else if (/MicroMessenger/i.test(ua)) {
    platform = 'wechat';
  } else if (/DingTalk/i.test(ua)) {
    platform = 'dingtalk';
  } else if (isIos) {
    const isOtherIosBrowser = /CriOS|FxiOS|EdgiOS|OPiOS|Quark|UCBrowser|MQQBrowser/i.test(ua);
    platform = isOtherIosBrowser ? 'ios_other' : 'ios_safari';
  } else if (isAndroid) {
    if (/HuaweiBrowser|HarmonyOS|HMSCore|HUAWEI|HONOR/i.test(ua)) {
      platform = 'huawei';
    } else if (/MiuiBrowser|XiaoMi|Redmi/i.test(ua)) {
      platform = 'xiaomi';
    } else if (/HeyTapBrowser|OPPO|OnePlus|realme|PCHM|PEGM|PGEM/i.test(ua)) {
      platform = 'oppo';
    } else if (/VivoBrowser|vivo|iQOO/i.test(ua)) {
      platform = 'vivo';
    } else {
      platform = 'android_other';
    }
  }

  return {
    platform,
    isMobile,
    standalone,
    needsExternalBrowser: platform === 'wechat' || platform === 'wecom' || platform === 'dingtalk',
    isDomesticAndroid:
      platform === 'huawei' ||
      platform === 'xiaomi' ||
      platform === 'oppo' ||
      platform === 'vivo' ||
      platform === 'android_other',
  };
}

const DOMESTIC_PERMISSION_TIP =
  '若点击添加后桌面未出现图标，请到手机「设置 → 应用管理 → 当前浏览器 → 权限」中开启「创建桌面快捷方式」后重试。';

export function pwaGuideFor(platform: PwaPlatform): PwaGuideContent {
  switch (platform) {
    case 'wecom':
      return {
        label: '企业微信内打开',
        summary: '企业微信内置浏览器不支持直接生成桌面图标，需先用手机系统浏览器打开。',
        steps: [
          '点击页面右上角「···」菜单按钮。',
          '选择「在默认浏览器中打开」（或先点击下方复制网址，粘贴到手机浏览器）。',
          '在手机浏览器中打开后，再按提示将「衡账」添加到桌面。',
        ],
      };
    case 'wechat':
      return {
        label: '微信内打开',
        summary: '微信内置浏览器不支持直接生成桌面图标，需先切到手机自带浏览器。',
        steps: [
          '点击页面右上角「···」菜单按钮。',
          '选择「在浏览器打开」（苹果手机选择「在默认浏览器打开」）。',
          '跳转打开后，点击页面底部的「添加到桌面」引导即可完成。',
        ],
      };
    case 'dingtalk':
      return {
        label: '钉钉内打开',
        summary: '钉钉内置窗口不支持直接放桌面图标，请先在系统浏览器中打开。',
        steps: [
          '点击右上角菜单，选择「使用浏览器打开」。',
          '在系统浏览器打开后，通过菜单中的「添加到桌面」创建图标。',
        ],
      };
    case 'huawei':
      return {
        label: '华为 / 荣耀手机',
        summary: '添加到桌面后，从桌面点击「衡账」图标即可全屏直达工作台。',
        steps: [
          '点击浏览器底部右下角「四个点 / 三条横线」菜单按钮。',
          '在菜单中点击「添加书签」或「工具箱 → 添加到桌面 / 主屏幕」。',
          '确认名称为「衡账」并点击「添加」，图标就会出现在手机桌面。',
        ],
        permissionTip: DOMESTIC_PERMISSION_TIP,
      };
    case 'xiaomi':
      return {
        label: '小米 / 红米手机',
        summary: '在澎湃或系统浏览器中添加后，可像原生应用一样一键打开。',
        steps: [
          '点击浏览器底部中间的「≡」菜单按钮。',
          '点击「添加书签」或向右滑动工具栏找到「发送到桌面 / 添加到桌面」。',
          '在弹窗中选择「手机桌面」并确认即可。',
        ],
        permissionTip: DOMESTIC_PERMISSION_TIP,
      };
    case 'oppo':
      return {
        label: 'OPPO / 一加 / 真我手机',
        summary: '通过系统浏览器添加到桌面，日常报销与审批无需再找网址。',
        steps: [
          '点击浏览器底部「≡」菜单按钮。',
          '选择「添加书签」或「工具箱 → 添加到桌面」。',
          '在弹窗中确认名称为「衡账」并点击「添加」。',
        ],
        permissionTip: DOMESTIC_PERMISSION_TIP,
      };
    case 'vivo':
      return {
        label: 'vivo / iQOO 手机',
        summary: '将「衡账」添加到桌面后，可全屏快速打开工作台。',
        steps: [
          '点击浏览器底部「≡」菜单按钮。',
          '选择「添加书签 → 手机桌面」或「工具箱 → 添加到桌面」。',
          '点击确认完成添加。',
        ],
        permissionTip: DOMESTIC_PERMISSION_TIP,
      };
    case 'ios_safari':
      return {
        label: '苹果手机浏览器',
        summary: '添加至主屏幕后将以全屏独立应用样式打开，体验更流畅。',
        steps: [
          '点击屏幕底部的「分享」图标（方框带向上箭头）。',
          '向下滑动菜单，点击「添加到主屏幕」。',
          '点击右上角「添加」，即可在桌面看到「衡账」图标。',
        ],
      };
    case 'ios_other':
      return {
        label: '苹果手机（非系统浏览器）',
        summary: '苹果系统需通过自带浏览器添加到主屏幕。',
        steps: [
          '点击下方「复制当前网址」，打开苹果系统自带浏览器粘贴访问。',
          '点击底部「分享」按钮，选择「添加到主屏幕」即可生成桌面图标。',
        ],
      };
    case 'android_other':
      return {
        label: '安卓手机浏览器',
        summary: '添加到手机桌面后，下次点击图标即可全屏直达。',
        steps: [
          '如上方出现「立即安装到桌面」按钮，直接点击即可一步完成。',
          '否则点击浏览器右上角或底部的「⋮ / ≡」菜单，选择「安装应用」或「添加到主屏幕 / 桌面」。',
        ],
        permissionTip: DOMESTIC_PERMISSION_TIP,
      };
    default:
      return {
        label: '电脑或手机桌面安装',
        summary:
          '电脑浏览器也可将「衡账」安装为独立桌面窗口；手机扫码或访问当前网址即可放到手机桌面。',
        steps: [
          '电脑端：若支持一键安装请点上方按钮，或点击地址栏右侧「安装」图标。',
          '手机端：用华为、小米、OPPO、vivo 或苹果自带浏览器打开当前网址，按提示添加到桌面。',
        ],
      };
  }
}

export function isBannerDismissed(now: number, storage?: Pick<Storage, 'getItem'> | null): boolean {
  try {
    const raw = storage?.getItem(PWA_DISMISS_STORAGE_KEY);
    if (!raw) return false;
    const saved = Number(raw);
    return Number.isFinite(saved) && now - saved < PWA_DISMISS_TTL_MS;
  } catch {
    return false;
  }
}

export function dismissBanner(now: number, storage?: Pick<Storage, 'setItem'> | null): void {
  try {
    storage?.setItem(PWA_DISMISS_STORAGE_KEY, String(now));
  } catch {
    /* 隐私浏览模式可能禁止写入本地存储，忽略即可。 */
  }
}

export function shouldShowInstallBanner(env: PwaEnvironment, dismissed: boolean): boolean {
  return env.isMobile && !env.standalone && !dismissed;
}
