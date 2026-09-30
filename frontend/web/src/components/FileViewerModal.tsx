/**
 * @fileoverview 文件预览组件（弹窗 + 共享渲染体）
 *
 * 文件内容的两种查看形态：
 * - FileViewerModal：全屏遮罩弹窗（右栏停靠列不够看时的放大视图）
 * - FilePreviewBody：共享渲染体（加载/错误/二进制/行号 + hljs 高亮代码/
 *   diff 着色），供弹窗与右栏停靠列（FilePreviewPanel）复用
 *
 * 滚动结构：单一滚动容器承载行号列（sticky left）与代码，纵向滚动时
 * 行号与内容同步翻动，横向滚动时行号列固定可见。
 *
 * @module FileViewerModal
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import hljs from 'highlight.js/lib/common';
import { t, type UiLanguage } from '../i18n';
import { FileIcon as FileGlyphIcon } from './icons';
import type { FileContentPayload } from '../types/protocol';

/** 本地化会话文件预览错误码：后端下发错误码（如 session_not_found），
 *  前端经 i18n 文案展示；未知/系统错误原文（raw）原样返回。 */
function locPreviewError(lang: UiLanguage, raw: string): string {
  const key = `session_file_${raw}`;
  const localized = t(lang, key);
  return localized === key ? raw : localized;
}

/** 扩展名 → hljs 语言名（common 子集内） */
const EXT_LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  py: 'python', pyi: 'python',
  json: 'json', jsonc: 'json',
  md: 'markdown', mdx: 'markdown',
  css: 'css', scss: 'scss', less: 'less',
  html: 'xml', xml: 'xml', svg: 'xml',
  rs: 'rust', go: 'go', java: 'java',
  c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', cxx: 'cpp',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  yml: 'yaml', yaml: 'yaml', toml: 'ini', ini: 'ini',
  sql: 'sql', rb: 'ruby', php: 'php', swift: 'swift',
  kt: 'kotlin', r: 'r', lua: 'lua',
};

/** 拆分文件相对路径：[目录(含尾斜杠), 文件名] */
export function splitFilePath(path: string): [string, string] {
  const sep = path.lastIndexOf('/');
  return sep >= 0 ? [path.slice(0, sep + 1), path.slice(sep + 1)] : ['', path];
}

/**
 * 文件预览弹窗组件（遮罩态）
 *
 * 关闭行为由 onClose 决定（App 接线：返回停靠列）。
 *
 * @param props - 组件属性
 * @returns 返回预览弹窗的 JSX 元素（payload 为 null 时返回 null）
 */
export default function FileViewerModal({ lang, payload, loading, onClose, rootDirLabel }: {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 预览载荷（null = 关闭） */
  payload: FileContentPayload | null;
  /** 内容读取中 */
  loading: boolean;
  /** 关闭预览 */
  onClose: () => void;
  /** 工作区根目录标识：路径无目录前缀（根目录文件）时头部补全路径展示 */
  rootDirLabel?: string | null;
}) {
  // Esc 关闭
  useEffect(() => {
    if (!payload) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [payload, onClose]);

  if (!payload) return null;

  const [dir, filename] = splitFilePath(payload.path);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative bg-surface-card border border-border-light rounded-2xl shadow-card w-full max-w-5xl h-[82vh] flex flex-col overflow-hidden modal-origin-center animate-scale-in"
      >
        {/* 头部：路径 + 关闭 */}
        <div className="px-5 py-3 border-b border-border-light flex items-center gap-3 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-content-primary truncate">
              {(dir || rootDirLabel) && <span className="text-content-disabled font-normal">{dir || rootDirLabel}</span>}
              {filename}
            </div>
            <PreviewMetaLine lang={lang} payload={payload} />
          </div>
          <CopyButton lang={lang} payload={payload} />
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

        {/* 主体：共享渲染体（滚动条贴住弹窗边缘，不再留间隙；允许自由选中文本） */}
        <div className="flex-1 min-h-0 overflow-hidden select-text">
          <FilePreviewBody lang={lang} payload={payload} loading={loading} />
        </div>
      </div>
    </div>
  );
}

/**
 * 文件预览共享渲染体
 *
 * 按载荷状态渲染：读取中 / 错误 / 二进制提示 / diff 着色视图 /
 * 行号 + 语法高亮代码。弹窗与右栏停靠列共用，填充父容器
 * （父级需提供确定高度并自带滚动条间隙内边距）。
 */
export function FilePreviewBody({ lang, payload, loading }: {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 预览载荷 */
  payload: FileContentPayload;
  /** 内容读取中 */
  loading: boolean;
}) {
  const content = payload.content ?? '';
  const isDiff = payload.kind === 'diff';

  if (loading && !payload.error && payload.binary === undefined) {
    return <div className="h-full flex items-center justify-center text-sm text-content-secondary">{t(lang, 'loading')}</div>;
  }
  if (payload.error) {
    // 文件已删除/不存在：美化空态（图标 + 标题 + 路径 + 提示），替代裸错误文案；
    // file_not_found（按路径打开但路径无对应文件）标题区分于"已被删除"
    if (payload.error === 'file_deleted' || payload.error === 'file_not_found') {
      return (
        <DeletedFileView
          path={payload.path}
          lang={lang}
          title={t(lang, payload.error === 'file_deleted' ? 'file_deleted_title' : 'session_file_file_not_found')}
        />
      );
    }
    return <div className="h-full flex items-center justify-center text-sm text-danger px-6 text-center">{locPreviewError(lang, payload.error)}</div>;
  }
  if (payload.binary) {
    return <div className="h-full flex items-center justify-center text-sm text-content-secondary">{t(lang, 'binary_file')}</div>;
  }

  if (isDiff) {
    return <DiffView content={content} emptyHint={t(lang, 'git_no_changes')} truncated={payload.truncated === true} truncatedLabel={t(lang, 'truncated_label')} />;
  }

  return <CodeView content={content} path={payload.path} truncated={payload.truncated === true} truncatedLabel={t(lang, 'truncated_label')} />;
}

/**
 * 文件已删除/不存在的预览空态
 *
 * 居中卡片式布局：文件剪影图标 + 标题 + 完整路径（目录弱化）+ 提示文案，
 * 替代裸错误文本。
 */
function DeletedFileView({ path, lang, title }: {
  path: string;
  lang: UiLanguage;
  title: string;
}) {
  const [dir, name] = splitFilePath(path);
  return (
    <div className="h-full flex flex-col items-center justify-center gap-1.5 text-center px-8 select-none">
      <FileGlyphIcon className="w-9 h-9 text-content-disabled mb-1" />
      <div className="text-sm font-semibold text-content-primary">{title}</div>
      <div className="text-xs text-content-secondary max-w-md truncate" title={path}>
        {dir && <span className="text-content-disabled">{dir}</span>}
        {name}
      </div>
      <div className="text-xs text-content-disabled mt-1">{t(lang, 'file_deleted_hint')}</div>
    </div>
  );
}

/**
 * 纵向滚动条存在性检测（代码/diff 内容区共用）
 *
 * 溢出裁剪发生在 padding box，scrollbar-gutter 拦不住横向溢出的内容——
 * 无纵向滚动条时文字会一直画进预留槽。渐隐层必须知道自己该让位
 * （有滚动条：right 8px）还是贴边（无滚动条：right 0 盖住预留槽）。
 */
function useHasVScrollbar(ref: React.RefObject<HTMLDivElement | null>): boolean {
  const [has, setHas] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHas(el.scrollHeight > el.clientHeight + 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // 内容增减（首帧高亮/字体加载/截断标记）同样改变 scrollHeight
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [ref]);
  return has;
}

/**
 * 右缘渐隐层（代码/diff 内容区共用）
 *
 * 绝对定位的渐变层盖在内容右缘，提示"横向还有更多内容"；常驻渲染——
 * 内容不超宽时该区域只有代码列的右内边距空白，渐变不可见。
 * right 由调用方按纵向滚动条存在性传入：有滚动条让位 8px（滚动条宽度，
 * .preview-scroll ::-webkit-scrollbar width）；无滚动条贴 0，把预留槽
 * 一起盖进渐变，避免"渐隐结束后文字又在槽里回显"。
 * bottom 10px = 横向滚动条高度（横向溢出必然伴随横向滚动条，渐隐可见时
 * 该值恒正确）。
 */
function RightFadeOverlay({ right }: { right: number }) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute top-0 bottom-[10px] w-7"
      style={{ right, background: 'linear-gradient(to right, transparent, var(--bg-card))' }}
    />
  );
}

/**
 * 代码视图：单一滚动容器（行号列 sticky left + 代码），
 * 纵向滚动行号同步翻动，横向滚动行号固定可见。
 * 字号/行高与全局 pre code.hljs 规则（13px / 1.7）严格一致，保证逐行对齐。
 */
function CodeView({ content, path, truncated, truncatedLabel }: {
  content: string;
  path: string;
  truncated: boolean;
  truncatedLabel: string;
}) {
  const lines = useMemo(() => (content ? content.split('\n') : []), [content]);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const hasVScrollbar = useHasVScrollbar(scrollerRef);

  const highlightedHtml = useMemo(() => {
    if (!content) return null;
    const ext = path.includes('.') ? path.split('.').pop()!.toLowerCase() : '';
    const language = EXT_LANG[ext];
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(content, { language, ignoreIllegals: true }).value;
      }
      return hljs.highlight(content, { language: 'plaintext', ignoreIllegals: true }).value;
    } catch {
      return null;
    }
  }, [content, path]);

  if (lines.length === 0) {
    return <div className="h-full" />;
  }

  // 行号槽宽度按最大行号位数自适应（千行文件 4 位、万行 5 位……）：
  // 全列统一同一宽度（恒定不突变），数字右缘留 12px 呼吸
  const gutterW = 24 + String(lines.length).length * 9;

  return (
    <div className="relative h-full">
      <div
        ref={scrollerRef}
        className="h-full preview-scroll overflow-auto"
        /* 恒定预留纵向滚动条位置：内容不满一屏时右侧同样留白，
            打开不同文件时内容右缘不左右跳动 */
        style={{ scrollbarGutter: 'stable' }}
      >
        <div className="flex min-w-max min-h-full">
          {/* 行号列：sticky 固定左侧；中性色阶（内容文字色 7% 混入卡片底，不偏绿）；
              宽度按最大行号位数自适应且全列统一，数字右缘留 12px 呼吸 */}
          <div
            className="sticky left-0 z-10 shrink-0 select-none text-right py-3 pr-3 text-content-disabled font-mono text-[13px] leading-[1.7]"
            style={{ backgroundColor: 'color-mix(in srgb, var(--text-primary) 7%, var(--bg-card))', width: gutterW }}
            aria-hidden="true"
          >
            {lines.map((_, i) => (
              <div key={i} className="tabular-nums">{i + 1}</div>
            ))}
          </div>
          {/* 代码区：左缘与行号列底色对齐（pl 改 3，与 diff 内容格一致） */}
          <div className="py-3 pl-3 pr-8 font-mono text-[13px] leading-[1.7]">
            <pre className="whitespace-pre text-content-primary">
              <code className="hljs" dangerouslySetInnerHTML={{ __html: highlightedHtml ?? escapeHtml(content) }} />
            </pre>
            {truncated && (
              <div className="text-xs text-content-disabled mt-2 font-sans">{truncatedLabel}</div>
            )}
          </div>
        </div>
      </div>
      <RightFadeOverlay right={hasVScrollbar ? 8 : 0} />
    </div>
  );
}

// ---- diff 视图（局部 hunk 结构：只显示增减 + 少量上下文） ----

/** diff 单行：删除行取旧文件行号 oldNo，新增/上下文行取新文件行号 newNo */
interface DiffLine {
  oldNo: number | null;
  newNo: number;
  type: 'ctx' | 'add' | 'del';
  text: string;
}

/** diff hunk：头标注新旧文件起点行号，块内为内容行 */
interface DiffHunk {
  oldStart: number;
  newStart: number;
  lines: DiffLine[];
}

/**
 * 解析 unified diff（后端输出少量上下文的局部 hunk）。
 * 按 @@ 块头切分为 hunk，块内行号从块头起点各自计数：
 * 上下文/新增推进新行号，删除推进旧行号——删除行与新增行各自对应正确的行号。
 */
function parseUnifiedDiff(diff: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let oldNo = 0;
  let newNo = 0;
  let cur: DiffHunk | null = null;
  for (const raw of diff.split('\n')) {
    if (raw.startsWith('@@')) {
      const m = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) {
        oldNo = parseInt(m[1]!, 10);
        newNo = parseInt(m[2]!, 10);
      }
      cur = { oldStart: oldNo, newStart: newNo, lines: [] };
      hunks.push(cur);
      continue;
    }
    if (
      raw.startsWith('diff --git') || raw.startsWith('index ') || raw.startsWith('--- ') ||
      raw.startsWith('+++ ') || raw.startsWith('new file') || raw.startsWith('deleted file') ||
      raw.startsWith('Binary files') || raw.startsWith('old mode') || raw.startsWith('new mode') ||
      raw.startsWith('\\')
    ) {
      continue;
    }
    if (!cur) continue; // 文件头之后的空行/结构行，未进入 hunk 时忽略
    const ch = raw.charAt(0);
    if (ch === '+') {
      cur.lines.push({ oldNo: null, newNo: newNo++, type: 'add', text: raw.slice(1) });
    } else if (ch === '-') {
      cur.lines.push({ oldNo: oldNo++, newNo, type: 'del', text: raw.slice(1) });
    } else {
      // 上下文行（前导空格；空串视为空上下文行）
      cur.lines.push({ oldNo: oldNo++, newNo: newNo++, type: 'ctx', text: ch === ' ' ? raw.slice(1) : raw });
    }
  }
  return hunks;
}

/**
 * diff 视图：以"变更分组"呈现局部增减。
 * 每个 hunk 以细边框矩形分组，组间留出间距，多处改动各自独立、易于区分。
 * 组内删除行显示旧文件行号、新增/上下文行显示新文件行号，行首 +/- 与
 * 底色区分增删。
 */
/** diff 行类型 → 渲染样式。
 *  分割线用色阶区分：行号槽底色加深一档（18% 且混入不透明卡片底色），
 *  内容区 14% 透明底——两档色差自然勾出行号槽右缘，无需边框线。
 *  行号槽为 sticky 悬浮层，底色必须不透明（与 --bg-card 混合而非
 *  transparent），否则横向滚动时内容会从行号格下穿透。 */
function diffLineStyles(type: DiffLine['type']): {
  rowStyle?: React.CSSProperties;
  gutterStyle?: React.CSSProperties;
  gutterTextCls: string;
  textCls: string;
} {
  if (type === 'add') {
    return {
      gutterStyle: {
        backgroundColor: 'color-mix(in srgb, var(--success) 38%, var(--bg-card))',
        boxShadow: 'inset 3px 0 0 var(--success)',
        color: 'var(--success)',
      },
      gutterTextCls: '',
      rowStyle: { backgroundColor: 'color-mix(in srgb, var(--success) 14%, transparent)' },
      textCls: 'text-diff-add',
    };
  }
  if (type === 'del') {
    return {
      gutterStyle: {
        backgroundColor: 'color-mix(in srgb, var(--error) 38%, var(--bg-card))',
        boxShadow: 'inset 3px 0 0 var(--error)',
        color: 'var(--error)',
      },
      gutterTextCls: '',
      rowStyle: { backgroundColor: 'color-mix(in srgb, var(--error) 14%, transparent)' },
      textCls: 'text-diff-del',
    };
  }
  return {
    // diff 上下文行：中性色阶（内容文字色 7% 混入卡片底，不偏绿），不透明防滚动穿透
    gutterStyle: { backgroundColor: 'color-mix(in srgb, var(--text-primary) 7%, var(--bg-card))' },
    gutterTextCls: 'text-content-disabled',
    textCls: 'text-content-primary',
  };
}

function DiffView({ content, emptyHint, truncated, truncatedLabel }: {
  content: string;
  emptyHint: string;
  truncated: boolean;
  truncatedLabel: string;
}) {
  const hunks = useMemo(() => parseUnifiedDiff(content), [content]);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const hasVScrollbar = useHasVScrollbar(scrollerRef);
  // 行号格宽度按 hunk 内最大行号位数自适应（新旧行号取大），全 diff 统一
  const maxLineNo = hunks.reduce((m, h) => Math.max(
    m, ...h.lines.map((r) => Math.max(r.oldNo ?? 0, r.newNo)),
  ), 0);
  const gutterW = 24 + String(maxLineNo).length * 9;
  if (hunks.length === 0) {
    return <div className="h-full flex items-center justify-center text-sm text-content-secondary">{emptyHint}</div>;
  }
  return (
    <div className="relative h-full">
      <div
        ref={scrollerRef}
        className="h-full preview-scroll overflow-auto"
        style={{ scrollbarGutter: 'stable' }}
      >
        {/* 逐行 row 结构（每行横跨全部内容宽度并自带底色，行号格为行内
            sticky 单元）：底色长度天然等于最宽行，全局一致不长短不齐 */}
        <div className="w-max min-w-full min-h-full font-mono text-[13px] leading-[1.7]">
          {hunks.map((h, hi) => (
            <div key={hi} className={`border-y border-r border-border-light ${hi > 0 ? 'mt-3' : ''}`}>
              {h.lines.map((r, i) => {
                const s = diffLineStyles(r.type);
                return (
                  <div key={i} className={`flex w-full whitespace-pre ${s.textCls}`} style={s.rowStyle}>
                    {/* 行号格：宽度按最大行号位数自适应且全 diff 统一（位数增长不突变）；
                        sticky 固定在行内；底色比内容区深一档形成分割线；
                        唯一的增减指示位（状态条 + 行号着色） */}
                    <span
                      aria-hidden="true"
                      className={`sticky left-0 z-10 shrink-0 select-none text-right tabular-nums pr-3 ${s.gutterTextCls}`}
                      style={{ ...s.gutterStyle, width: gutterW }}
                    >
                      {r.type === 'del' ? r.oldNo : r.newNo}
                    </span>
                    {/* 内容格：flex-1 撑满行宽，底色由 row 统一提供；不渲染 +/- 协议符号 */}
                    <span className="block flex-1 pl-3 pr-8">{r.text || ' '}</span>
                  </div>
                );
              })}
            </div>
          ))}
          {truncated && (
            <div className="text-xs text-content-disabled mt-2 font-sans pl-3">{truncatedLabel}</div>
          )}
        </div>
      </div>
      <RightFadeOverlay right={hasVScrollbar ? 8 : 0} />
    </div>
  );
}

/** 元信息行：仅展示本地化的读取错误（大小/行数等元信息按需求不再展示） */
export function PreviewMetaLine({ lang, payload }: {
  lang: UiLanguage;
  payload: FileContentPayload;
}) {
  if (payload.error) return <div className="text-xs text-danger mt-0.5 truncate">{locPreviewError(lang, payload.error)}</div>;
  return null;
}

/** 复制全文按钮（内容为空/出错时隐藏） */
export function CopyButton({ lang, payload }: { lang: UiLanguage; payload: FileContentPayload }) {
  const [copied, setCopied] = useState(false);
  const content = payload.content ?? '';

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  if (payload.error || !content) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => setCopied(true)).catch(() => undefined);
  };

  return (
    <button
      onClick={handleCopy}
      title={copied ? t(lang, 'copied') : t(lang, 'copy')}
      className="px-2 py-1 text-[11px] font-semibold text-content-secondary glass-option-hover hover:text-content-primary rounded-md transition-colors cursor-pointer shrink-0"
    >
      {copied ? t(lang, 'copied') : t(lang, 'copy')}
    </button>
  );
}

/** 纯文本转义（高亮失败时的兜底渲染） */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
