/**
 * @fileoverview 网页元素拾取附件 pill
 *
 * composer 输入框上方的常驻附件胶囊：指针图标 + 「N 个网页元素」计数标签，
 * 悬停展开详情浮层（每条元素：标题 / tag·role / 页面标题，可单条移除），
 * 尾部 X 整组移除。提交时由 buildPromptWithWebElementContexts 序列化。
 *
 * @module WebElementContextAttachmentChip
 */

import { GlobeIcon, MousePointerIcon, CloseIcon } from "./icons";
import type { WebElementContextComposerAttachment } from "../lib/webElementContext";
import { t, type UiLanguage } from "../i18n";

/** 元素标题（标题取值：可访问名 → 文本 → 选择器 → tag） */
function getElementTitle(context: WebElementContextComposerAttachment): string {
  return (
    context.accessibleName || context.text || context.selector || context.tagName.toLowerCase()
  );
}

/** 元素元信息（元信息：tag · role） */
function getElementMeta(context: WebElementContextComposerAttachment): string {
  const role = context.role ? `role=${context.role}` : null;
  const tagName = context.tagName.toLowerCase();
  return [tagName, role].filter(Boolean).join(" · ");
}

/** 附件 pill 属性 */
interface WebElementContextAttachmentChipProps {
  /** 驻留的拾取附件列表 */
  contexts: readonly WebElementContextComposerAttachment[];
  /** 移除单条 */
  onRemove?: (id: string) => void;
  /** 整组移除 */
  onRemoveAll?: () => void;
  /** 当前 UI 语言 */
  lang: UiLanguage;
}

/**
 * 网页元素拾取附件 pill
 *
 * @param props - 组件属性
 * @returns pill JSX（无附件时返回 null）
 */
export default function WebElementContextAttachmentChip({
  contexts, onRemove, onRemoveAll, lang,
}: WebElementContextAttachmentChipProps) {
  if (contexts.length === 0) return null;

  const label = contexts.length === 1
    ? t(lang, "browser_pick_chip_one").replace("{count}", "1")
    : t(lang, "browser_pick_chip_many").replace("{count}", String(contexts.length));
  const removeLabel = t(lang, "browser_pick_chip_remove");
  const removeAllLabel = t(lang, "browser_pick_chip_remove_all");

  return (
    <div className="group/pill relative flex max-w-full items-center" data-composer-context-attachments-row="true">
      {/* pill 本体（悬停展开详情） */}
      <div
        role="button"
        tabIndex={0}
        aria-label={label}
        title={label}
        className="pill-badge flex h-8 max-w-full cursor-pointer select-none items-center gap-1.5 rounded-full py-1.5 pl-3 pr-1.5 text-xs font-medium text-content-secondary"
        style={{ borderColor: "var(--border-medium)" }}
      >
        <MousePointerIcon className="w-4 h-4 shrink-0 text-content-disabled" />
        <span className="truncate">{label}</span>
        {onRemoveAll && (
          <button
            type="button"
            aria-label={removeAllLabel}
            title={removeAllLabel}
            onClick={(e) => { e.stopPropagation(); onRemoveAll(); }}
            className="shrink-0 w-5 h-5 flex items-center justify-center rounded-full text-content-disabled opacity-0 transition-opacity hover:text-content-primary group-hover/pill:opacity-100 cursor-pointer"
          >
            <CloseIcon className="w-3 h-3" />
          </button>
        )}
      </div>
      {/* 详情浮层（悬停/聚焦展开；纯 CSS 驱动，无需弹窗基础设施） */}
      <div className="pointer-events-none absolute bottom-full left-0 z-30 mb-2 hidden w-80 max-w-[calc(100vw-2rem)] group-hover/pill:block">
        <div className="max-h-64 overflow-y-auto rounded-xl border border-[var(--border-medium)] bg-surface-card-alt p-1 shadow-card dropdown-scroll">
          {contexts.map((context) => (
            <div
              key={context.id}
              className="group/context flex min-h-7 cursor-default gap-2 rounded-lg px-2 py-1 text-xs text-content-secondary hover:bg-[var(--badge-bg-subtle)]"
            >
              <GlobeIcon className="mt-0.5 w-4 h-4 shrink-0 text-content-disabled" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-content-primary">{getElementTitle(context)}</div>
                <div className="truncate font-mono text-[11px] text-content-disabled">{getElementMeta(context)}</div>
                <div className="truncate text-[11px] text-content-disabled">{context.pageTitle || context.pageUrl}</div>
              </div>
              {onRemove && (
                <button
                  type="button"
                  aria-label={removeLabel}
                  title={removeLabel}
                  onClick={(e) => { e.stopPropagation(); onRemove(context.id); }}
                  className="mt-0.5 shrink-0 w-5 h-5 flex items-center justify-center rounded-sm text-content-disabled opacity-0 transition-opacity hover:text-danger group-hover/context:opacity-100 cursor-pointer"
                >
                  <CloseIcon className="w-3 h-3" />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
