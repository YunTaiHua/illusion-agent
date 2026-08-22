/**
 * @fileoverview 单轮变更条组件
 *
 * 聊天气泡底部展示单轮对话内被变更工具（edit_file/write_file）修改过的
 * 文件列表：类似右栏 CollapsibleSection 的区块折叠样式，但带明显的卡片
 * 背景色与边框。默认折叠，点击头部展开文件列表。
 *
 * 展示统一绝对路径；每行附增删行数着色数字（+N 绿 / -N 红）。点击行为：
 * Git 内 added/modified 打开 diff 变更视图；deleted / 工作区外 / 非 Git
 * 降级为内容预览（deleted 收到 file_deleted 错误码展示"文件已被删除"，
 * 覆盖文件被用户手动删除或会话中被删除的场景）；统计未到达时用原始串
 * 兜底预览（后端白名单校验兜底安全性）。
 *
 * 数据流：挂载时按原始路径串批量请求行数统计（web_request_file_stats，
 * hook 内去重），响应合并进 fileStats 缓存后本组件随引用变化重渲染。
 * 增删行数语义为"该文件当前相对 Git HEAD 的差异"，非严格单轮增量。
 *
 * @module TurnFilesBar
 */

import { memo, useCallback, useEffect, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import type { FileStatItem } from '../types/protocol';

/**
 * TurnFilesBar 组件属性接口
 */
interface TurnFilesBarProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 该轮变更工具修改的原始路径串列表（TurnView useMemo 提供，引用稳定） */
  rawPaths: string[];
  /** 行数统计缓存：原始路径串 → 统计条目（未到达的条目缺失） */
  stats: Map<string, FileStatItem>;
  /** 批量拉取统计（hook 内已缓存/在途去重） */
  onRequestStats: (paths: string[]) => void;
  /** 点击文件打开预览：Git 内传绝对路径 + 'diff'，否则 + 'content' */
  onOpenFile: (path: string, kind: 'content' | 'diff') => void;
}

/**
 * 单轮变更条组件
 *
 * 仅在该轮包含成功变更调用且轮次完成时由 TurnView 渲染（作为最终回复
 * 气泡的 footer，位于复制/重新生成按钮上方）。
 *
 * @param props - 组件属性
 * @returns 返回变更条 JSX 元素
 */
const TurnFilesBar = memo(function TurnFilesBar({ lang, rawPaths, stats, onRequestStats, onOpenFile }: TurnFilesBarProps) {
  // 默认折叠：与右栏区块一致的浏览习惯，展开看增量细节
  const [open, setOpen] = useState(false);

  // 缺失统计时批量拉取：以拼接 key 为依赖避免 rawPaths 数组引用抖动重复触发
  const statsKey = rawPaths.join('\u0000');
  useEffect(() => {
    if (statsKey) onRequestStats(statsKey.split('\u0000'));
  }, [statsKey, onRequestStats]);

  return (
    /* 明显背景色 + 边框的卡片容器：区别于透明底的正文章节 */
    <div className="my-4 rounded-lg border border-border-medium bg-surface-card-alt overflow-hidden">
      {/* 区块头：仿右栏 CollapsibleSection —— 图标槽位 hover 时淡出为三角指示器，
          标题 + 右侧计数徽标；整行为点击目标 */}
      <button
        onClick={() => setOpen(!open)}
        className="group/head w-full px-3 py-2.5 flex items-center gap-1.5 transition-colors cursor-pointer glass-option-hover"
      >
        {/* 16px 图标槽位：常显铅笔图标；hover 时图标淡出、三角指示器淡入 */}
        <span className="relative w-4 h-4 shrink-0 flex items-center justify-center">
          <svg
            className="absolute inset-0 m-auto w-4 h-4 text-primary transition-opacity duration-100 group-hover/head:opacity-0"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"
          >
            <path d="M4 12.4l.4-1.9 7-7a1.06 1.06 0 0 1 1.5 0l.6.6a1.06 1.06 0 0 1 0 1.5l-7 7-1.9.4z" />
            <path d="M10.7 5.7l.6.6" />
          </svg>
          <svg
            className={`absolute inset-0 m-auto w-3.5 h-3.5 text-content-secondary opacity-0 group-hover/head:opacity-100 transition-[opacity,transform] duration-100 group-hover/head:duration-150 ${open ? 'rotate-90' : ''}`}
            viewBox="0 0 14 14" fill="none"
          >
            <path d="M4.25 2.82782L4.25 11.1722C4.25 11.6622 4.84243 11.9076 5.18891 11.5611L9.36109 7.38891C9.57588 7.17412 9.57588 6.82588 9.36109 6.61109L5.18891 2.43891C4.84243 2.09243 4.25 2.33782 4.25 2.82782Z" fill="currentColor" />
          </svg>
        </span>
        {/* 标题：普通正文大小 + 加粗；py-2.5 与侧栏目录项同高（text-sm 行高 20px + 20px = 40px） */}
        <span className="text-sm font-bold text-content-primary tracking-wide">{t(lang, 'turn_files_title')}</span>
        <span className="ml-auto shrink-0 text-[10px] text-content-secondary bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full tabular-nums">
          {rawPaths.length}
        </span>
      </button>
      {/* 展开/折叠微动画（简洁 fade：纯透明度 150ms）；容器无垂直内边距，
          文件行紧贴分隔线与卡片底边，hover 高亮连续不留间隙 */}
      {open && (
        <div className="animate-fade">
          <div className="flex flex-col border-t border-border-light">
            {rawPaths.map((raw) => (
              <TurnFileRow key={raw} raw={raw} stat={stats.get(raw)} onOpenFile={onOpenFile} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

/**
 * 单个变更文件行
 *
 * 展示绝对路径（目录弱化 + 文件名正常）+ 增删行数着色数字；统计未到达
 * 时仍可点击（内容预览兜底，后端白名单校验保证安全）。前缀以与触发器
 * 图标槽位同宽的空占位缩进，文字与触发器文本左对齐。
 *
 * @param props.raw - 原始路径串（缓存键）
 * @param props.stat - 统计条目（可选，null 表示尚未到达）
 * @param props.onOpenFile - 打开预览回调（path + 视图类型）
 */
function TurnFileRow({ raw, stat, onOpenFile }: { raw: string; stat?: FileStatItem; onOpenFile: (path: string, kind: 'content' | 'diff') => void }) {
  // 展示路径：优先后端回填的绝对路径；统计未到达时退化为原始串
  const display = stat?.display || raw;
  const sep = display.lastIndexOf('/');
  const dir = sep >= 0 ? display.slice(0, sep + 1) : '';
  const name = sep >= 0 ? display.slice(sep + 1) : display;

  const handleClick = useCallback(() => {
    if (!stat) {
      // 统计未到达的兜底：直接内容预览（后端 web_read_session_file 白名单校验兜底安全性）
      onOpenFile(raw, 'content');
      return;
    }
    if (stat.status === 'added' || stat.status === 'modified') {
      // Git 内 added/modified：diff 变更视图（后端支持绝对路径）
      onOpenFile(stat.path, 'diff');
    } else {
      // deleted/工作区外/非 Git/解析失败：内容视图（deleted 会收到 file_deleted
      // 错误码由 FileViewerModal 本地化展示"文件已被删除"）
      onOpenFile(stat.path, 'content');
    }
  }, [stat, raw, onOpenFile]);

  // added/modified 可开 diff（需 path）；其余需有效绝对路径；统计未到达用原串兜底
  const canOpen =
    !stat || (stat.status === 'added' || stat.status === 'modified' ? !!stat.display : !!stat.path);

  return (
    <button
      onClick={handleClick}
      disabled={!canOpen}
      className="w-full flex items-center gap-1.5 py-2.5 px-3 text-sm transition-colors glass-option-hover disabled:cursor-default disabled:hover:bg-transparent"
      title={display}
    >
      {/* 空占位：与触发器图标槽位同宽，文件名缩进对齐触发器文本 */}
      <span className="w-4 h-4 shrink-0" />
      <span className="flex-1 min-w-0 truncate text-left">
        {dir && <span className="text-content-disabled">{dir}</span>}
        <span className="text-content-primary">{name}</span>
      </span>
      {/* 增删行数着色（无法统计时不显示数字） */}
      {typeof stat?.insertions === 'number' && stat.insertions > 0 && (
        <span className="shrink-0 font-mono text-xs text-diff-add">+{stat.insertions}</span>
      )}
      {typeof stat?.deletions === 'number' && stat.deletions > 0 && (
        <span className="shrink-0 font-mono text-xs text-diff-del">-{stat.deletions}</span>
      )}
    </button>
  );
}

export default memo(TurnFilesBar);
