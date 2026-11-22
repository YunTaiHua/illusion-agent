/**
 * @fileoverview Git 变更区块组件
 *
 * 右栏 Git 区块：当前分支/上游/领先落后计数与工作区变更文件列表
 * 点击变更文件打开该文件相对 HEAD 的 diff 预览（已删除文件同样可查看）。
 *
 * @module GitSection
 */

import { useCallback, useEffect, useRef } from 'react';
import { t, type UiLanguage } from '../i18n';
import { fileIconColor } from '../utils/fileIcon';
import AutoScrollText from './AutoScrollText';
import { CollapsibleSection } from './RightPanel';
import { FileIcon, GitBranchIcon } from './icons';
import type { GitFileStatus, GitStatusSnapshot } from '../types/protocol';

/**
 * GitSection 组件属性接口
 */
interface GitSectionProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** Git 状态快照（null = 未拉取；is_repo=false 表示非仓库，区块常驻显示占位） */
  status: GitStatusSnapshot | null;
  /** 拉取中（区块头部加载态） */
  loading: boolean;
  /** 拉取/刷新 Git 状态 */
  onRefresh: () => void;
  /** 点击文件（打开 diff 预览） */
  onOpenDiff: (path: string) => void;
  /** 是否显示区块顶部分隔线（可选，默认显示） */
  topBorder?: boolean;
}

/** 变更状态 → 展示字母与文字配色（底色统一为中性 --badge-bg-subtle，
 *  与 write/edit / 技能 P / 插件 OFF 徽标同一底色；A/M/D/R/U/! 状态
 *  语义保留在文字颜色上，直接取主题协调色：A 用 write/edit 徽标同款
 *  的青绿主色（不再用薄荷绿 success——两种绿打架），M/D/! 用主题自带
 *  的琥珀黄/柔和红，不做透明度修饰） */
const STATUS_META: Record<string, { letter: string; cls: string }> = {
  added: { letter: 'A', cls: 'text-primary/80' },
  modified: { letter: 'M', cls: 'text-warning' },
  deleted: { letter: 'D', cls: 'text-danger' },
  renamed: { letter: 'R', cls: 'text-info' },
  untracked: { letter: 'U', cls: 'text-content-secondary' },
  unmerged: { letter: '!', cls: 'text-danger' },
};

/**
 * Git 变更区块组件
 *
 * @param props - 组件属性
 * @returns 返回 Git 区块的 JSX 元素（非仓库时区块常驻，显示占位提示）
 */
export default function GitSection({ lang, status, loading, onRefresh, onOpenDiff, topBorder = true }: GitSectionProps) {
  // 自动拉取/恢复：首次挂载或缓存被清空（切换工作区）后无需用户操作自动刷新；
  // pending 守卫防响应异常（error 事件不落地快照）时反复重试
  const pendingRef = useRef(false);
  useEffect(() => {
    if (status === null && !loading && !pendingRef.current) {
      pendingRef.current = true;
      onRefresh();
    }
    if (status !== null) pendingRef.current = false;
  }, [status, loading, onRefresh]);

  const files = status?.files ?? [];
  // 非仓库（含未装 git）：区块常驻，内容显示占位提示
  const notRepo = status !== null && status.is_repo === false;

  return (
    <CollapsibleSection
      title={t(lang, 'git_title')}
      count={files.length}
      defaultCollapsed={true}
      onExpand={onRefresh}
      onRefresh={onRefresh}
      refreshLabel={t(lang, 'refresh')}
      topBorder={topBorder}
      icon={
        <GitBranchIcon className="w-3.5 h-3.5" />
      }
    >
      {notRepo ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'git_not_repo')}</div>
      ) : loading && files.length === 0 ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'loading')}</div>
      ) : files.length === 0 ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'git_no_changes')}</div>
      ) : (
        <>
          {files.map((f) => (
            <GitFileRow key={`${f.staged ? 's' : 'w'}-${f.path}`} file={f} onOpenDiff={onOpenDiff} />
          ))}
        </>
      )}
    </CollapsibleSection>
  );
}

/** 单个变更文件行：文件类型图标 + 路径 + 行级增删 + 状态字母徽标（右端） */
function GitFileRow({ file, onOpenDiff }: { file: GitFileStatus; onOpenDiff: (path: string) => void }) {
  const handleClick = useCallback(() => {
    onOpenDiff(file.path);
  }, [file.path, onOpenDiff]);

  const meta = STATUS_META[file.status] ?? STATUS_META.modified!;
  // 路径拆分：目录弱化 + 文件名正常，窄栏下优先保住文件名
  const sep = file.path.lastIndexOf('/');
  const dir = sep >= 0 ? file.path.slice(0, sep + 1) : '';
  const name = sep >= 0 ? file.path.slice(sep + 1) : file.path;
  const title = [
    file.path,
    file.orig_path ? `${file.orig_path} → ${file.path}` : '',
    file.staged ? 'staged' : 'unstaged',
    file.insertions != null || file.deletions != null ? `+${file.insertions ?? 0} -${file.deletions ?? 0}` : '',
  ].filter(Boolean).join(' · ');

  return (
    <button
      onClick={handleClick}
      className="as-host w-[calc(100%_+_1rem)] flex items-center gap-1.5 -mx-2 pl-4 pr-2 py-1 rounded-lg text-xs transition-colors glass-option-hover cursor-pointer"
      title={title}
    >
      <FileIcon className="w-3.5 h-3.5 shrink-0" color={fileIconColor(file.path)} />
      {/* 截断时静态渐隐、悬浮单向滚动展示完整路径 */}
      <AutoScrollText trigger="parent" className="flex-1 min-w-0 text-left">
        {dir && <span className="text-content-disabled">{dir}</span>}
        <span className="text-content-primary">{name}</span>
      </AutoScrollText>
      {(file.insertions != null || file.deletions != null) && (
        <span className="shrink-0 font-mono text-[10px] tabular-nums">
          {file.insertions != null && file.insertions > 0 && <span className="text-success">+{file.insertions}</span>}
          {file.deletions != null && file.deletions > 0 && (
            <span className="text-danger">{file.insertions != null && file.insertions > 0 ? ' ' : ''}-{file.deletions}</span>
          )}
        </span>
      )}
      {/* 状态字母徽标：与其他子区块徽标（write/edit、技能 P、插件 OFF）
          完全同款——中性 --badge-bg-subtle 底色、宽度随内容（px-1.5）、
          rounded-full、font-medium；A/M/D/R/U/! 状态语义保留在文字颜色上。
          未暂存的区分只降字母透明度：整枚徽标降透明会让中性底色比其他
          徽标明显偏浅（底色不一致的来源） */}
      <span
        className={`shrink-0 text-[10px] bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full font-medium ${meta.cls}`}
        title={file.staged ? 'staged' : 'unstaged'}
      >
        <span className={file.staged ? '' : 'opacity-70'}>{meta.letter}</span>
      </span>
    </button>
  );
}
