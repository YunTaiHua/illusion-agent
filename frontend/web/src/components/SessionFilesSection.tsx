/**
 * @fileoverview 会话文件区块组件
 *
 * 右栏会话文件区块：展示当前会话内被变更工具（edit_file/write_file 等）
 * 修改过的文件列表。该列表独立于 Git 与工作区边界，可包含未纳入 Git
 * 追踪、项目目录之外、以及无 Git 环境下的文件，点击即打开内容预览
 * （web_read_session_file 由上层接线）。
 *
 * @module SessionFilesSection
 */

import { useCallback, useEffect, useRef } from 'react';
import { t, type UiLanguage } from '../i18n';
import { CollapsibleSection } from './RightPanel';
import type { SessionFileItem } from '../types/protocol';

/**
 * SessionFilesSection 组件属性接口
 */
interface SessionFilesSectionProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 会话内修改文件列表（随会话隔离，null 未拉取时为 []） */
  files: SessionFileItem[];
  /** 拉取中（区块加载态） */
  loading: boolean;
  /** 拉取/刷新会话文件列表 */
  onRefresh: () => void;
  /** 点击文件（打开内容预览） */
  onOpenFile: (path: string) => void;
  /** 是否显示区块顶部分隔线（可选，默认显示） */
  topBorder?: boolean;
}

/**
 * 会话文件区块组件
 *
 * @param props - 组件属性
 * @returns 返回会话文件区块的 JSX 元素（空列表显示占位提示）
 */
export default function SessionFilesSection({ lang, files, loading, onRefresh, onOpenFile, topBorder = true }: SessionFilesSectionProps) {
  // 自动拉取/恢复：区块展开或缓存被清空（切换会话/工作区）后无需用户操作自动刷新；
  // 与 GitSection 一致的 pending 守卫，避免响应异常/空列表时反复重试
  const pendingRef = useRef(false);
  useEffect(() => {
    if (files.length === 0 && !pendingRef.current) {
      pendingRef.current = true;
      onRefresh();
    }
    if (files.length > 0) pendingRef.current = false;
  }, [files.length, onRefresh]);

  return (
    <CollapsibleSection
      title={t(lang, 'session_files_title')}
      count={files.length}
      defaultCollapsed={true}
      onExpand={onRefresh}
      onRefresh={onRefresh}
      refreshLabel={t(lang, 'refresh')}
      topBorder={topBorder}
      icon={<ModifiedFileIcon className="w-3.5 h-3.5" />}
    >
      {files.length === 0 && loading ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'loading')}</div>
      ) : files.length === 0 ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_session_files')}</div>
      ) : (
        files.map((f) => (
          <SessionFileRow key={f.path} file={f} onOpenFile={onOpenFile} />
        ))
      )}
    </CollapsibleSection>
  );
}

/** 单个会话文件行：文件图标 + 路径（目录弱化 + 文件名正常）+ 修改工具徽标 */
function SessionFileRow({ file, onOpenFile }: { file: SessionFileItem; onOpenFile: (path: string) => void }) {
  const handleClick = useCallback(() => {
    onOpenFile(file.path);
  }, [file.path, onOpenFile]);

  // 展示路径拆分：目录弱化 + 文件名正常，窄栏下优先保住文件名（工作区外用绝对路径）
  const sep = file.display.lastIndexOf('/');
  const dir = sep >= 0 ? file.display.slice(0, sep + 1) : '';
  const name = sep >= 0 ? file.display.slice(sep + 1) : file.display;

  return (
    <button
      onClick={handleClick}
      className="w-[calc(100%_+_2.5rem)] flex items-center gap-1.5 -mx-5 pl-7 pr-5 py-1 rounded-md text-xs transition-colors glass-option-hover cursor-pointer"
      title={file.display}
    >
      {/* 编辑/修改文件图标：精简编辑铅笔造型 + 主色，与目录树的普通文件图标明显区分 */}
      <ModifiedFileIcon className="w-3.5 h-3.5 shrink-0 text-primary" />
      <span className="flex-1 min-w-0 truncate text-left">
        {dir && <span className="text-content-disabled">{dir}</span>}
        <span className="text-content-primary">{name}</span>
      </span>
      <span className="shrink-0 text-[10px] text-primary/80 bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full font-medium">
        {file.tool.replace(/_/g, ' ')}
      </span>
    </button>
  );
}

/**
 * 编辑/修改文件图标（精简线性，默认继承当前文本色）
 *
 * 会话文件区块的专属图标：一枚精简的编辑铅笔，传达"被修改/编辑的文件"
 * 语义。造型与目录树的普通文件夹/扩展名着色文件完全不同，用于在视觉上
 * 区分「目录树」与「会话文件」两类区块（含展开后的文件行）。通过
 * className 传 text-primary 等即可突出强调。
 */
function ModifiedFileIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12.4l.4-1.9 7-7a1.06 1.06 0 0 1 1.5 0l.6.6a1.06 1.06 0 0 1 0 1.5l-7 7-1.9.4z" />
      <path d="M10.7 5.7l.6.6" />
    </svg>
  );
}