/**
 * @fileoverview 单轮变更条组件
 *
 * 聊天气泡底部展示单轮对话内被变更工具（edit_file/write_file）修改过的
 * 文件列表：类似右栏 CollapsibleSection 的区块折叠样式，但带明显的卡片
 * 背景色与边框。默认折叠，点击头部展开文件列表。
 *
 * 展示统一绝对路径；每行附增删行数着色数字（+N 绿 / -N 红）。增减行数
 * 语义为"本轮对话的增量"：由 computeTurnFileStats 从该轮转录的工具结果
 * 本地累计（同一文件多次编辑求和；直播条目优先用工具 structured_output
 * 的精确值，恢复条目回退 diff 文本解析），与 Git 状态无关。点击行为：
 * added/modified 打开 diff 变更视图（后端按会话白名单校验）；其余降级为
 * 内容预览（后端收到 file_deleted 错误码时展示"文件已被删除"）。
 *
 * @module TurnFilesBar
 */

import { memo, useCallback, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { normalizePathKey, type TurnFileStat } from '../utils/turnGrouping';
import { ChevronRightIcon, ModifiedFileIcon } from './icons';

/**
 * TurnFilesBar 组件属性接口
 */
interface TurnFilesBarProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 该轮变更工具修改的原始路径串列表（TurnView useMemo 提供，引用稳定） */
  rawPaths: string[];
  /** 本轮累计增减统计：原始路径串 → 条目（computeTurnFileStats 本地计算） */
  stats: Map<string, TurnFileStat>;
  /** 点击文件打开预览：added/modified 传 'diff'，否则 + 'content' */
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
const TurnFilesBar = memo(function TurnFilesBar({ lang, rawPaths, stats, onOpenFile }: TurnFilesBarProps) {
  // 默认折叠：与右栏区块一致的浏览习惯，展开看增量细节
  const [open, setOpen] = useState(false);

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
          <ModifiedFileIcon className="absolute inset-0 m-auto w-4 h-4 text-primary transition-opacity duration-100 group-hover/head:opacity-0" />
          <ChevronRightIcon
            className={`absolute inset-0 m-auto w-3.5 h-3.5 text-content-secondary opacity-0 group-hover/head:opacity-100 transition-[opacity,transform] duration-100 group-hover/head:duration-150 ${open ? 'rotate-90' : ''}`}
          />
        </span>
        {/* 标题：普通正文大小 + 加粗；py-2.5 与侧栏目录项同高（text-sm 行高 20px + 20px = 40px） */}
        <span className="text-sm font-bold text-content-primary tracking-wide">{t(lang, 'turn_files_title')}</span>
        <span className="ml-auto shrink-0 text-[10px] text-content-secondary bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full tabular-nums">
          {rawPaths.length}
        </span>
      </button>
      {/* 展开/折叠微动画（简洁 fade：纯透明度 150ms）；容器无垂直内边距，
          文件行紧贴分隔线与卡片底边，hover 高亮连续不留间隙。
          max-h 限制展开高度（大量文件时卡片不无限撑高聊天流），
          超出部分滚动查看；scrollbar-hidden 彻底隐藏滚动条且不占槽位，
          子项 w-full 悬浮选中可完整覆盖整行，也避免滚动条出现/消失
          引起的行宽与布局抖动 */}
      {open && (
        <div className="animate-fade overflow-y-auto max-h-56 scrollbar-hidden">
          <div className="flex flex-col border-t border-border-light">
            {rawPaths.map((raw) => (
              <TurnFileRow key={raw} raw={raw} stat={stats.get(normalizePathKey(raw))} onOpenFile={onOpenFile} />
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
 * 展示路径（目录弱化 + 文件名正常）+ 增删行数着色数字（本轮对话内该
 * 文件的累计增量）。前缀以与触发器图标槽位同宽的空占位缩进，文字与
 * 触发器文本左对齐。
 *
 * @param props.raw - 原始路径串（缓存键）
 * @param props.stat - 本轮累计统计（可选，缺失时不显示数字）
 * @param props.onOpenFile - 打开预览回调（path + 视图类型）
 */
function TurnFileRow({ raw, stat, onOpenFile }: { raw: string; stat?: TurnFileStat; onOpenFile: (path: string, kind: 'content' | 'diff') => void }) {
  const display = raw;
  const sep = display.lastIndexOf('/');
  const winSep = display.lastIndexOf('\\');
  const cut = Math.max(sep, winSep);
  const dir = cut >= 0 ? display.slice(0, cut + 1) : '';
  const name = cut >= 0 ? display.slice(cut + 1) : display;

  const handleClick = useCallback(() => {
    if (stat && (stat.status === 'added' || stat.status === 'modified')) {
      // 本轮创建/修改：diff 变更视图（后端按会话白名单校验安全性，
      // 文件已被删除时收到 file_deleted 错误码由 FileViewerModal 本地化展示）
      onOpenFile(raw, 'diff');
    } else {
      // 统计缺失的兜底：内容预览（后端 web_read_session_file 白名单校验兜底安全性）
      onOpenFile(raw, 'content');
    }
  }, [stat, raw, onOpenFile]);

  return (
    <button
      onClick={handleClick}
      className="w-full flex items-center gap-1.5 py-2.5 px-3 text-sm transition-colors glass-option-hover cursor-pointer"
      title={display}
    >
      {/* 空占位：与触发器图标槽位同宽，文件名缩进对齐触发器文本 */}
      <span className="w-4 h-4 shrink-0" />
      <span className="flex-1 min-w-0 truncate text-left">
        {dir && <span className="text-content-disabled">{dir}</span>}
        <span className="text-content-primary">{name}</span>
      </span>
      {/* 增删行数着色加粗（本轮对话累计增量；无法统计时不显示数字） */}
      {typeof stat?.insertions === 'number' && stat.insertions > 0 && (
        <span className="shrink-0 font-mono text-xs font-bold text-diff-add">+{stat.insertions}</span>
      )}
      {typeof stat?.deletions === 'number' && stat.deletions > 0 && (
        <span className="shrink-0 font-mono text-xs font-bold text-diff-del">-{stat.deletions}</span>
      )}
    </button>
  );
}

export default memo(TurnFilesBar);
