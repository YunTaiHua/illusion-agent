/**
 * @fileoverview 目录树区块组件
 *
 * 右栏目录树区块：以工作区根为起点懒加载的目录树。
 * 目录展开时按需拉取单层条目（web_request_file_tree），
 * 点击文件触发预览（web_read_file 由上层接线）。
 *
 * @module FileTreeSection
 */

import { useCallback, useEffect, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { CollapsibleSection } from './RightPanel';
import type { FileTreeNode } from '../types/protocol';
import { FolderClosedIcon, FolderOpenIcon } from './icons';

/**
 * FileTreeSection 组件属性接口
 */
interface FileTreeSectionProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 目录相对路径 → 子条目缓存（'' 为根） */
  fileTree: Record<string, FileTreeNode[]>;
  /** 正在加载的目录路径列表 */
  loadingPaths: string[];
  /** 拉取目录条目（path 为相对目录；force 强制刷新） */
  onRequestDir: (path: string, force?: boolean) => void;
  /** 点击文件（打开预览） */
  onOpenFile: (path: string) => void;
}

/** 常见扩展名 → 图标颜色（无映射时用次级文本色） */
const EXT_COLORS: Record<string, string> = {
  ts: '#3178c6', tsx: '#3178c6',
  js: '#b58900', jsx: '#b58900', mjs: '#b58900', cjs: '#b58900',
  py: '#3572a5', pyi: '#3572a5',
  json: '#a0752a', jsonc: '#a0752a',
  md: '#6f42c1', mdx: '#6f42c1',
  css: '#563d7c', scss: '#563d7c', less: '#563d7c',
  html: '#e34c26', xml: '#e34c26', svg: '#e34c26', vue: '#41b883',
  rs: '#b7410e', go: '#008080', java: '#b07219',
  c: '#555555', h: '#555555', cpp: '#f34b7d', hpp: '#f34b7d',
  sh: '#4e9a3d', bash: '#4e9a3d', ps1: '#012456',
  yml: '#6f42c1', yaml: '#6f42c1', toml: '#6f42c1', ini: '#6f42c1',
  sql: '#e38c00', rb: '#701516', php: '#4f5d95', swift: '#f05138',
  txt: '#8b949e', lock: '#8b949e',
};

/**
 * 文件树区块组件
 *
 * @param props - 组件属性
 * @returns 返回文件树区块的 JSX 元素
 */
export default function FileTreeSection({ lang, fileTree, loadingPaths, onRequestDir, onOpenFile }: FileTreeSectionProps) {
  // 展开的目录集合（相对路径 → 是否展开）
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // 自动拉取/恢复：首次挂载或缓存被清空（切换工作区）后，根目录与
  // 已展开目录自动按需重拉，无需用户手动刷新（无痕切换）
  useEffect(() => {
    const dirs = ['', ...Object.keys(expanded).filter((d) => expanded[d])];
    for (const dir of dirs) {
      if (fileTree[dir] === undefined && !loadingPaths.includes(dir)) onRequestDir(dir);
    }
  }, [fileTree, loadingPaths, expanded, onRequestDir]);

  const toggleDir = useCallback((path: string) => {
    const willOpen = expanded[path] !== true;
    if (willOpen && fileTree[path] === undefined) onRequestDir(path);
    setExpanded((prev) => ({ ...prev, [path]: willOpen }));
  }, [expanded, fileTree, onRequestDir]);

  // 刷新：根目录 + 所有已展开目录强制重拉
  const handleRefresh = useCallback(() => {
    onRequestDir('', true);
    for (const dir of Object.keys(expanded)) {
      if (expanded[dir]) onRequestDir(dir, true);
    }
  }, [expanded, onRequestDir]);

  const rootEntries = fileTree[''] ?? [];
  const rootLoading = loadingPaths.includes('');

  return (
    <CollapsibleSection
      title={t(lang, 'files_title')}
      count={rootEntries.length}
      defaultCollapsed={true}
      onExpand={() => { if (fileTree[''] === undefined) onRequestDir(''); }}
      onRefresh={handleRefresh}
      refreshLabel={t(lang, 'refresh')}
      icon={
        <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M1.5 4.5A1.5 1.5 0 0 1 3 3h2.6a1.5 1.5 0 0 1 1.1.5l.9 1H13a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 13 12.5H3a1.5 1.5 0 0 1-1.5-1.5v-6.5z" />
        </svg>
      }
    >
      {rootLoading && rootEntries.length === 0 ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'loading')}</div>
      ) : rootEntries.length === 0 && fileTree[''] !== undefined ? (
        <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_files')}</div>
      ) : (
        <TreeRows
          dirPath=""
          depth={0}
          fileTree={fileTree}
          loadingPaths={loadingPaths}
          expanded={expanded}
          onToggleDir={toggleDir}
          onOpenFile={onOpenFile}
        />
      )}
    </CollapsibleSection>
  );
}

/**
 * 递归渲染一层目录条目
 */
function TreeRows({
  dirPath, depth, fileTree, loadingPaths, expanded, onToggleDir, onOpenFile,
}: {
  dirPath: string;
  depth: number;
  fileTree: Record<string, FileTreeNode[]>;
  loadingPaths: string[];
  expanded: Record<string, boolean>;
  onToggleDir: (path: string) => void;
  onOpenFile: (path: string) => void;
}) {
  const entries = fileTree[dirPath];
  if (!entries) return null;
  if (entries.length === 0) {
    return <div className="text-xs text-content-disabled" style={{ paddingLeft: 50 + depth * 12 }}>—</div>;
  }
  return (
    <>
      {entries.map((node) => {
        // 整行悬浮出血（-mx-5）后左缩进补偿 20px，保持原视觉层级缩进
        const pad = 28 + depth * 12;
        if (node.kind === 'dir') {
          const isOpen = expanded[node.path] === true;
          const dirLoading = loadingPaths.includes(node.path);
          return (
            <div key={node.path}>
              <button
                onClick={() => onToggleDir(node.path)}
                className="w-[calc(100%_+_2.5rem)] flex items-center gap-1.5 -mx-5 py-1 pr-5 rounded-md text-xs glass-option-hover transition-colors cursor-pointer"
                style={{ paddingLeft: pad }}
                title={node.path}
              >
                <svg
                  className={`w-3 h-3 shrink-0 text-content-disabled transition-transform duration-150 ${isOpen ? 'rotate-90' : ''}`}
                  viewBox="0 0 14 14" fill="none"
                >
                  <path d="M4.25 2.83v8.34c0 .49.59.74.94.39l4.17-4.17a.54.54 0 0 0 0-.78L5.19 2.44a.55.55 0 0 0-.94.39z" fill="currentColor" />
                </svg>
                <FolderIcon open={isOpen} />
                <span className="text-content-primary font-medium truncate flex-1 text-left">{node.name}</span>
                {dirLoading && <span className="text-[10px] text-content-disabled shrink-0">…</span>}
              </button>
              {isOpen && (
                <div className="animate-fade">
                  <TreeRows
                    dirPath={node.path}
                    depth={depth + 1}
                    fileTree={fileTree}
                    loadingPaths={loadingPaths}
                    expanded={expanded}
                    onToggleDir={onToggleDir}
                    onOpenFile={onOpenFile}
                  />
                </div>
              )}
            </div>
          );
        }
        return (
          <button
            key={node.path}
            onClick={() => onOpenFile(node.path)}
            className="w-[calc(100%_+_2.5rem)] flex items-center gap-1.5 -mx-5 py-1 pr-5 rounded-md text-xs glass-option-hover transition-colors cursor-pointer"
            style={{ paddingLeft: pad }}
            title={node.path}
          >
            {/* 与目录行的三角指示器等宽占位，保持图标纵向对齐 */}
            <span className="w-3 shrink-0" />
            <FileIcon name={node.name} />
            <span className="text-content-secondary truncate flex-1 text-left">{node.name}</span>
          </button>
        );
      })}
    </>
  );
}

/** 文件夹图标（开/合两种形态；与左栏 Sidebar 目录图标同款） */
function FolderIcon({ open }: { open: boolean }) {
  const cls = 'w-3.5 h-3.5 shrink-0 text-content-secondary';
  return open ? <FolderOpenIcon className={cls} /> : <FolderClosedIcon className={cls} />;
}

/** 文件图标（按扩展名着色） */
function FileIcon({ name }: { name: string }) {
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
  const color = EXT_COLORS[ext] ?? undefined;
  return (
    <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 16 16" fill="none" stroke={color ?? 'currentColor'} style={color ? { color } : undefined} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 1.5H4A1.5 1.5 0 0 0 2.5 3v10A1.5 1.5 0 0 0 4 14.5h8a1.5 1.5 0 0 0 1.5-1.5V6L9 1.5z" className={!color ? 'text-content-disabled' : ''} />
      <path d="M8.75 1.75V6h4.5" className={!color ? 'text-content-disabled' : ''} />
    </svg>
  );
}
