/**
 * @fileoverview 浏览器标签页对账（顶栏预览标签页 ↔ 后端浏览器 tab）
 *
 * 纯函数：给定当前预览标签页列表与最新 browser_state，产出对账后的列表与
 * 「应激活的键」。的模型——每个浏览器 tab 是面板顶栏的一个
 * 标签页（`browser|<tabId>`，各自挂载 guest），而不是嵌套在浏览器面板内部
 * 的第二层 tab 条。
 *
 * @module browserPreviewTabs
 */

import type { BrowserState, PreviewTab } from '../types/protocol';

/** 零 tab 时的占位标签页键（用户打开浏览器但后端尚无 tab：显示引导层） */
export const BROWSER_PLACEHOLDER_KEY = 'browser|builtin';

/** 浏览器标签页键 */
export function browserTabKey(browserTabId: string): string {
  return `browser|${browserTabId}`;
}

/** 标签页展示名（页面标题优先，回退 URL） */
function tabLabel(title: string, url: string): string {
  return (title || url || 'about:blank').trim() || 'about:blank';
}

export interface ReconcileInput {
  /** 当前预览标签页（含文件标签页） */
  tabs: readonly PreviewTab[];
  /** 最新浏览器状态（null = 未推送） */
  state: BrowserState | null;
  /** 在途关闭的浏览器 tab id（本地已移除、后端状态尚未回推） */
  closingIds: ReadonlySet<string>;
  /** 是否把「最新浏览器标签页」置为应激活（+ 面板显式新建） */
  activateNewest: boolean;
  /** 当前激活的预览标签页键 */
  activeKey: string | null;
  /** 标记置位时刻已知的后端 tab id（+ 新建基准：只有出现集合之外的
   *  tab 才算新 tab 落地；undefined = 视全部已知） */
  knownBrowserTabIds?: ReadonlySet<string>;
  /** 占位标签页（零 tab 引导层）是否可保留：浏览器 open 或启动中。
   *  浏览器已关闭（open=false 且不在启动中）时占位页必须让位——
   *  否则关掉浏览器后卡片仍停留为"清空网址"的引导层，看起来关不掉 */
  placeholderHeld?: boolean;
}

export interface ReconcileResult {
  /** 对账后的标签页列表（未变化时返回原引用） */
  tabs: readonly PreviewTab[];
  /** 应激活的键（undefined = 不改变当前激活） */
  activateKey?: string | null;
  /** 消费后的「在途关闭」集合（已从后端列表消失的 id 被清理） */
  closingIds: Set<string>;
  /** 消费后的 activateNewest 标记 */
  activateNewest: boolean;
}

/**
 * 对账：后端 tab 列表 → 顶栏浏览器标签页
 *
 * - 新 tab → 追加标签页（不自动激活：agent 新建 tab 不抢前台）
 * - 标题/URL 变化 → 原地更新展示名（保持标签页顺序）
 * - 后端已关闭 → 移除标签页
 * - 在途关闭的 id 不补回
 * - 零 tab 占位页在新 tab 到达后由真实标签页接管（并激活真实标签页）
 * - activateNewest（+ 面板显式新建）→ 激活最新浏览器标签页
 * - 正在查看的浏览器 tab 被关闭 → 顺延最后一个浏览器标签页
 *
 * @param input - 当前标签页、后端状态与激活上下文
 * @returns 对账结果
 */
export function reconcileBrowserPreviewTabs(input: ReconcileInput): ReconcileResult {
  const { tabs, state, activateNewest, activeKey } = input;
  const backendTabsAll = state?.open ? state.tabs : [];
  const closingIds = new Set(input.closingIds);
  if (closingIds.size > 0) {
    const present = new Set(backendTabsAll.map((tb) => tb.id));
    for (const id of [...closingIds]) if (!present.has(id)) closingIds.delete(id);
  }
  const backendTabs = backendTabsAll.filter((tb) => !closingIds.has(tb.id));
  const backendById = new Map(backendTabs.map((tb) => [tb.id, tb]));

  const keepPlaceholder = (input.placeholderHeld ?? false)
    && backendTabs.length === 0
    && tabs.some((tb) => tb.key === BROWSER_PLACEHOLDER_KEY);
  const next: PreviewTab[] = [];
  let droppedActiveBrowserIndex = -1;
  for (const [index, tab] of tabs.entries()) {
    if (tab.kind !== 'browser') { next.push(tab); continue; }
    if (tab.key === BROWSER_PLACEHOLDER_KEY) {
      if (keepPlaceholder) next.push(tab);
      continue; // 有真实 tab 时占位页让位
    }
    const backend = tab.browserTabId ? backendById.get(tab.browserTabId) : undefined;
    if (!backend) {
      if (tab.key === activeKey) droppedActiveBrowserIndex = index;
      continue;
    }
    const nextLabel = tabLabel(backend.title, backend.url);
    next.push(tab.path === nextLabel ? tab : { ...tab, path: nextLabel });
  }
  for (const backend of backendTabs) {
    const key = browserTabKey(backend.id);
    if (next.some((tb) => tb.key === key)) continue;
    next.push({
      key, path: tabLabel(backend.title, backend.url), kind: 'browser',
      payload: null, loading: false, synthetic: true, browserTabId: backend.id,
    });
  }

  const changed = next.length !== tabs.length
    || next.some((tab, i) => tab.key !== tabs[i]?.key || tab.path !== tabs[i]?.path);
  const browserNext = next.filter((tb) => tb.kind === 'browser' && tb.browserTabId);

  let activateKey: string | null | undefined;
  let nextActivateNewest = activateNewest;
  if (activateNewest) {
    // + 面板显式新建：激活后端列表的最新 tab（新 tab 若已在对账结果里就
    // 立即激活并消费标记；未落地则保留标记等下一次 browser_state）。
    // 注意判定基准是后端 tabs（新增 id 是否出现），不是本地列表——
    // openBrowserPreview 会先把激活键设回旧 tab，以本地"最后一个"判定
    // 会永远命中旧 tab，导致 + 新建后焦点不迁移（多开失灵根因）。
    const known = input.knownBrowserTabIds;
    const backendIds = (state?.open ? state.tabs : []).map((tb) => tb.id)
      .filter((id) => !closingIds.has(id));
    // 落地判定：出现已知集合之外的 tab（否则 open 的状态回推会先命中
    // 旧 tab、提前消费标记，+ 新建的焦点迁移失效）
    const landed = known
      ? backendIds.filter((id) => !known.has(id))
      : backendIds;
    const newestId = landed[landed.length - 1];
    if (newestId !== undefined) {
      const newest = browserNext.find((tb) => tb.browserTabId === newestId) ?? null;
      if (newest) {
        nextActivateNewest = false;
        activateKey = newest.key;
      }
    }
    // 未落地：标记保留，激活占位页（引导层）等待新 tab 接管。
    // 占位页承担"正在启动"的可视状态，绝不能清空激活键——激活键为空会让预览列
    // 判定为"无激活 tab"而整列离屏，表现为面板展开后瞬间塌陷；且激活键一旦为空，
    // 真实 tab 到达时既不匹配占位键也没有 dropped 标记，面板将永久停留在离屏态。
    activateKey = activateKey === undefined
      ? (next.some((tb) => tb.key === BROWSER_PLACEHOLDER_KEY) ? BROWSER_PLACEHOLDER_KEY : undefined)
      : activateKey;
  } else if (activeKey === BROWSER_PLACEHOLDER_KEY) {
    // 占位页已被让位（浏览器关闭）时回落到最后一个真实标签页
    activateKey = browserNext[0]?.key ?? next[next.length - 1]?.key ?? null;
  } else if (droppedActiveBrowserIndex >= 0) {
    // 正在查看的浏览器 tab 被关闭：与 closePreviewTab 同一顺延规则
    // （优先同位置的下一个，末尾则取前一个），且不限种类——文件 tab 也可接管，
    // 避免"关掉最后一个浏览器 tab 后预览列整列离屏"
    activateKey = next[Math.min(droppedActiveBrowserIndex, next.length - 1)]?.key ?? null;
  } else if (activeKey === null || !next.some((tb) => tb.key === activeKey)) {
    // 激活键为空或指向已消失的标签页（如"关闭全部"后 agent 又建了 tab）：
    // 选中最后一个——面板恒有激活项，卡片不会停在无内容态
    activateKey = next[next.length - 1]?.key ?? null;
  }

  return {
    tabs: changed ? next : tabs,
    ...(activateKey === undefined ? {} : { activateKey }),
    closingIds,
    activateNewest: nextActivateNewest,
  };
}
