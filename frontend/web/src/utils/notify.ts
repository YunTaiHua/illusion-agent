/**
 * Toast 透传辅助工具
 * ==================
 *
 * 为 toast 事件提供三块与呈现解耦的能力：
 *   1. 监管状态判定：用户是否正在应用界面内（可见且获得焦点）。
 *      监管中的用户能直接看到界面内的模态框/运行状态，无需 toast 打扰；
 *   2. 系统级通知透传：页面不可见时把 toast 转发出去——桌面壳内走
 *      Electron 主进程系统通知（点击可聚焦应用窗口），纯浏览器走
 *      Web Notification API，让用户不盯着页面也能看到任务结果；
 *   3. 提示音效：Web Audio 合成短提示音（无需音频资源文件），
 *      按 toast 级别区分音色；受 settings.json notifications.sound
 *      控制（后端已把联动结果写进 play_sound，这里只负责发声）。
 */

/** toast 展示级别（与 ToastPayload.level 对齐） */
export type NotifyLevel = 'success' | 'error' | 'info';

/**
 * 判断用户是否正在监管应用界面
 *
 * 「监管中」= 页面可见且窗口获得焦点。此时任务进度、结果和待处理
 * 的权限/提问模态框都直接呈现在界面上，不再叠加 toast / 音效 /
 * 系统通知，避免重复打扰。
 */
export function isAppSupervised(): boolean {
  if (typeof document === 'undefined') return true;
  return document.visibilityState === 'visible' && document.hasFocus();
}

/**
 * 页面是否完全不可见（最小化 / 切到其他标签页）
 *
 * 该状态下应用内 toast 卡片无法被看见也不再显示（提醒职责由
 * 提示音 + 系统级通知独自承担，回看不再二次打扰）。
 */
export function isPageHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

// === 系统级通知透传 ===

let notificationPermissionPrimed = false;

/**
 * 借用户手势提前申请通知权限（纯浏览器模式）
 *
 * Chromium 会拦截"来自后台标签页/无手势上下文"的 requestPermission——
 * 若等页面隐藏后 toast 事件到达时才申请，权限弹窗永远不会出现，
 * 系统通知静默失败。因此在外层首个 pointerdown/keydown 时调用本函数
 * 一次性拿到授权；桌面壳内不走此路径（Electron 通知无需网页权限）。
 */
export function primeNotificationPermission(): void {
  if (typeof window !== 'undefined' && window.illusionDesktop?.showNotification) return;
  if (typeof Notification === 'undefined') return;
  if (notificationPermissionPrimed || Notification.permission !== 'default') {
    notificationPermissionPrimed = true;
    return;
  }
  notificationPermissionPrimed = true;
  try {
    void Notification.requestPermission().catch(() => undefined);
  } catch {
    // 部分平台无 Promise 返回或请求被拒，静默处理
  }
}

/** 是否仍在等待（可通过手势推进的）通知权限申请 */
export function notificationNeedsPriming(): boolean {
  if (typeof window !== 'undefined' && window.illusionDesktop?.showNotification) return false;
  if (typeof Notification === 'undefined') return false;
  return !notificationPermissionPrimed && Notification.permission === 'default';
}

// === 提示音效（Web Audio 合成）===

let audioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    if (!audioCtx || audioCtx.state === 'closed') audioCtx = new Ctor();
    // 自动播放策略下（页面从未发生用户手势）context 可能处于 suspended，
    // 尽力恢复；失败则本次静默（下次调用再尝试）
    if (audioCtx.state === 'suspended') void audioCtx.resume().catch(() => undefined);
    return audioCtx;
  } catch {
    return null;
  }
}

/** 播放单个正弦提示音（指数衰减包络，避免爆音） */
function tone(ctx: AudioContext, freq: number, startAt: number, duration: number, peak: number): void {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  const endAt = startAt + duration;
  gain.gain.setValueAtTime(0.0001, startAt);
  gain.gain.exponentialRampToValueAtTime(peak, startAt + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, endAt);
  osc.connect(gain).connect(ctx.destination);
  osc.start(startAt);
  osc.stop(endAt + 0.05);
}

/**
 * 播放 toast 提示音效
 *
 * 成功为上行的两连音，错误为下行的低沉双音，信息为单音。
 * 浏览器后台节流可能延迟后台标签页中的播放，属于尽力而为的提示。
 */
export function playToastSound(level: NotifyLevel): void {
  const ctx = getAudioContext();
  if (!ctx) return;
  try {
    const now = ctx.currentTime;
    if (level === 'success') {
      tone(ctx, 659.25, now, 0.12, 0.18); // E5
      tone(ctx, 880.0, now + 0.13, 0.16, 0.16); // A5
    } else if (level === 'error') {
      tone(ctx, 329.63, now, 0.14, 0.2); // E4
      tone(ctx, 220.0, now + 0.15, 0.22, 0.2); // A3
    } else {
      tone(ctx, 523.25, now, 0.15, 0.15); // C5
    }
  } catch {
    // 音频设备不可用等异常直接忽略
  }
}

/**
 * 把 Markdown 正文降为适合系统横幅的单行纯文本摘要
 *
 * 系统通知（Windows 横幅 / macOS 通知中心）不渲染任何格式——
 * `**粗体**`、表格竖线、列表短横线原样堆在横幅里正是"系统 toast 与
 * 应用内样式不协调"的主要观感来源。这里做轻量去格式 + 截断：
 *   - 剥离代码围栏/行内标记/链接语法，取链接文字
 *   - 扔掉标题记号、引用符、列表与表格装饰符号
 *   - 压缩空白后按句读截到 maxChars 内
 * 应用内卡片仍渲染完整 Markdown，两通道从此各司其职。
 */
export function plainTextSummary(markdown: string, maxChars = 120): string {
  if (!markdown) return '';
  // 输入护栏：后端已把正文截到 400 字符，这里再兜一层——若将来被复用于
  // 无界文本，避免链接等正则在大输入上的平方级扫描
  let input = markdown;
  if (input.length > 2000) input = input.slice(0, 2000);
  const text = input
    .replace(/```[\s\S]*?```/g, ' ') // 围栏代码块整体去除
    .replace(/`([^`]*)`/g, '$1') // 行内代码留内容
    .replace(/!\[([^\]]*)]\([^)]*\)/g, '$1') // 图片 alt 文本
    .replace(/\[([^\]]*)]\(([^)]+)\)/g, '$1 ($2)') // 链接降为 文字 (url)
    .replace(/^\s{0,3}#{1,6}\s+/gm, '') // 标题记号
    .replace(/^\s{0,3}>\s?/gm, '') // 引用符
    .replace(/^\s*[-*+]\s+\[[ xX]]\s+/gm, '') // 任务列表复选框
    .replace(/^\s*[-*+]\s+/gm, '') // 无序列表符
    .replace(/^\s*\d+[.)]\s+/gm, '') // 有序列表符
    .replace(/\|/g, ' ') // 表格竖线
    .replace(/^(-{3,}\s*)+$/gm, ' ') // 表格分隔行残留（|---|---| 去竖线后）
    .replace(/[*_~]{1,3}/g, '') // 强调记号
    .replace(/\r/g, '');
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  let summary = lines.join(' ');
  const sentenceEnd = /[。！？.!?\s]/;
  if (summary.length > maxChars) {
    summary = summary.slice(0, maxChars);
    // 尽量在句读处收尾，避免单词被拦腰截断
    for (let i = summary.length - 1; i > maxChars - 30 && i > 0; i--) {
      if (sentenceEnd.test(summary.charAt(i))) {
        summary = summary.slice(0, i).trimEnd();
        break;
      }
    }
    summary = summary.trimEnd() + '…';
  }
  return summary;
}

/**
 * 发送系统级通知（透传）——极简两段式：固定短标题 + 一行纯文本摘要。
 *
 * 桌面壳内经 preload 桥接到主进程创建系统通知（Electron Notification），
 * 主进程负责点击聚焦应用窗口；纯浏览器环境回退到 Web Notification API
 * （权限未授予时先异步请求，被拒绝则静默放弃）。两头都不可用时为 no-op。
 *
 * 全程经 console 诊断输出（前缀 [notify]），便于排查"听到声音但没看到
 * 系统通知"的场景：permission 状态、环境不支持、构造异常都有明确日志。
 */
export function notifyDesktop(title: string, body: string): void {
  const summary = plainTextSummary(body);
  const bridge = window.illusionDesktop;
  if (bridge?.showNotification) {
    console.info('[notify] pass-through via Electron IPC:', title);
    bridge.showNotification(title, summary);
    return;
  }
  if (typeof Notification === 'undefined') {
    console.warn('[notify] Notification API unavailable — 仅应用内提醒可用');
    return;
  }
  try {
    if (Notification.permission === 'granted') {
      console.info('[notify] show Web Notification:', title);
      const notification = new Notification(title, { body: summary });
      // 点击通知回到应用（尽力而为：各浏览器支持度不一，失败不影响显示）
      notification.onclick = () => {
        try {
          window.focus();
        } catch {
          // 部分浏览器禁止后台页聚焦，静默忽略
        }
      };
    } else if (Notification.permission === 'default') {
      // default 通常意味着首次手势申请被忽略/被浏览器拦截：
      // 点击页面任意处可重新触发 priming，或在站点设置中手动允许
      console.warn(
        '[notify] 浏览器通知权限未授予（default）。' +
          '请点击页面一次触发授权弹窗，或在 chrome://settings/content/notifications 中允许本站点。',
      );
    } else {
      console.warn(
        '[notify] 浏览器通知权限已被拒绝（denied）——系统级通知不可用。' +
          '可在浏览器地址栏左侧图标或站点设置中重新允许。',
      );
    }
  } catch (err) {
    console.warn('[notify] 创建系统通知失败:', err);
  }
}
