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
import { CollapsibleSection } from './RightPanel';
import { GitBranchIcon } from './icons';
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

/** 变更状态 → 展示字母与配色 */
const STATUS_META: Record<string, { letter: string; cls: string }> = {
  added: { letter: 'A', cls: 'text-success bg-success/15' },
  modified: { letter: 'M', cls: 'text-warning bg-warning/15' },
  deleted: { letter: 'D', cls: 'text-danger bg-danger/15' },
  renamed: { letter: 'R', cls: 'text-info bg-info/15' },
  untracked: { letter: 'U', cls: 'text-content-secondary bg-[var(--badge-bg-subtle)]' },
  unmerged: { letter: '!', cls: 'text-danger bg-danger/15' },
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

/** 单个变更文件行：状态字母徽标 + 路径 + 行级增删 */
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
      className="w-[calc(100%_+_2.5rem)] flex items-center gap-1.5 -mx-5 pl-7 pr-5 py-1 rounded-md text-xs transition-colors glass-option-hover cursor-pointer"
      title={title}
    >
      <span className={`shrink-0 w-4 h-4 flex items-center justify-center rounded text-[10px] font-bold font-mono ${meta.cls} ${file.staged ? '' : 'opacity-70'}`}>
        {meta.letter}
      </span>
      <span className="flex-1 min-w-0 truncate text-left">
        {dir && <span className="text-content-disabled">{dir}</span>}
        <span className="text-content-primary">{name}</span>
      </span>
      {(file.insertions != null || file.deletions != null) && (
        <span className="shrink-0 font-mono text-[10px] tabular-nums">
          {file.insertions != null && file.insertions > 0 && <span className="text-success">+{file.insertions}</span>}
          {file.deletions != null && file.deletions > 0 && (
            <span className="text-danger">{file.insertions != null && file.insertions > 0 ? ' ' : ''}-{file.deletions}</span>
          )}
        </span>
      )}
    </button>
  );
}
