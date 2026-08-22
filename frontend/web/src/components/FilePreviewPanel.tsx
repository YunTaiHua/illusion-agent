/**
 * @fileoverview 文件预览停靠列组件
 *
 * 右栏右侧的文件内容停靠区（默认查看形态）：头部显示文件名/元信息，
 * 提供"内容 / Diff"视图切换（Git 变更默认进 diff，文件树默认进内容）、
 * "弹窗查看"（放大为全屏弹窗）、复制、关闭操作；主体复用
 * FilePreviewBody（行号 + 语法高亮 / diff 着色）。与右栏之间由 App
 * 渲染的分隔条支持鼠标拖拽调整宽度。
 *
 * @module FilePreviewPanel
 */

import { t, type UiLanguage } from '../i18n';
import { CopyButton, FilePreviewBody, PreviewMetaLine, splitFilePath } from './FileViewerModal';
import type { FileContentPayload } from '../types/protocol';

/**
 * FilePreviewPanel 组件属性接口
 */
interface FilePreviewPanelProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 预览载荷（非空时由父级渲染本组件） */
  payload: FileContentPayload;
  /** 内容读取中 */
  loading: boolean;
  /** 列宽（px，由 App 拖拽管理） */
  width: number;
  /** 当前文件是否有 Git 变更（true/null=可展示 diff；false=无变更，隐藏"Diff"切换） */
  hasDiff?: boolean | null;
  /** 切换到内容视图（web_read_file） */
  onOpenContent: (path: string) => void;
  /** 切换到 diff 视图（web_file_diff） */
  onOpenDiff: (path: string) => void;
  /** 弹窗查看（放大为全屏弹窗） */
  onPopOut: () => void;
  /** 关闭预览 */
  onClose: () => void;
}

/**
 * 文件预览停靠列组件
 *
 * @param props - 组件属性
 * @returns 返回停靠列的 JSX 元素
 */
export default function FilePreviewPanel({ lang, payload, loading, width, hasDiff, onOpenContent, onOpenDiff, onPopOut, onClose }: FilePreviewPanelProps) {
  const [dir, filename] = splitFilePath(payload.path);
  const isDiff = payload.kind === 'diff';
  // 视图切换按钮：diff 视图始终保留"内容"入口以退出；内容视图仅在存在 Git
  // 变更（或未知，宽限处理）时展示"Diff"，无变更文件不显示以免展示空的 diff
  const showToggle = !payload.error && !payload.binary && (isDiff || hasDiff !== false);

  return (
    <aside
      className="glass-panel panel-below-titlebar flex flex-col h-full shrink-0 overflow-hidden select-none"
      style={{ width: `${width}px` }}
    >
      {/* 头部：文件名 + 元信息 + 视图切换/复制/弹窗/关闭 */}
      <div className="px-4 pt-3 pb-2.5 border-b border-border-light shrink-0">
        <div className="flex items-center gap-1.5">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-content-primary truncate" title={payload.path}>
              {dir && <span className="text-content-disabled font-normal">{dir}</span>}
              {filename}
            </div>
          </div>
          {/* 左侧文字按钮：内容 / Diff 视图切换 + 复制（字体样式统一） */}
          {showToggle && (
            <button
              onClick={() => (isDiff ? onOpenContent(payload.path) : onOpenDiff(payload.path))}
              title={isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
              className="px-2 py-1 text-[11px] font-semibold rounded-md text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
            >
              {isDiff ? t(lang, 'content_view') : t(lang, 'diff_view')}
            </button>
          )}
          <CopyButton lang={lang} payload={payload} />
          {/* 右侧图标按钮：弹窗查看 + 关闭（风格一致） */}
          <button
            onClick={onPopOut}
            title={t(lang, 'popout_preview')}
            aria-label={t(lang, 'popout_preview')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9.5 1.5h5v5" />
              <path d="M14.5 1.5L8 8" />
              <path d="M13 9.5v3A1.5 1.5 0 0 1 11.5 14h-7A1.5 1.5 0 0 1 3 12.5v-7A1.5 1.5 0 0 1 4.5 4h3" />
            </svg>
          </button>
          <button
            onClick={onClose}
            title={t(lang, 'image_preview_close')}
            aria-label={t(lang, 'image_preview_close')}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer shrink-0"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <PreviewMetaLine lang={lang} payload={payload} loading={loading} />
      </div>

      {/* 主体：共享渲染体（滚动条贴住面板边缘，不再留间隙；允许自由选中文本） */}
      <div className="flex-1 min-h-0 overflow-hidden select-text">
        <FilePreviewBody lang={lang} payload={payload} loading={loading} />
      </div>
    </aside>
  );
}
