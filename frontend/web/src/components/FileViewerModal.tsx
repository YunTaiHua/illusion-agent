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

import { useEffect, useMemo, useState } from 'react';
import hljs from 'highlight.js/lib/common';
import { t, type UiLanguage } from '../i18n';
import type { FileContentPayload } from '../types/protocol';

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
export default function FileViewerModal({ lang, payload, loading, onClose }: {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 预览载荷（null = 关闭） */
  payload: FileContentPayload | null;
  /** 内容读取中 */
  loading: boolean;
  /** 关闭预览 */
  onClose: () => void;
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
        {/* 头部：文件名 + 元信息 + 关闭 */}
        <div className="px-5 py-3 border-b border-border-light flex items-center gap-3 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-content-primary truncate">
              {dir && <span className="text-content-disabled font-normal">{dir}</span>}
              {filename}
            </div>
            <PreviewMetaLine lang={lang} payload={payload} loading={loading} />
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
    return <div className="h-full flex items-center justify-center text-sm text-danger px-6 text-center">{payload.error}</div>;
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

  return (
    <div className="h-full preview-scroll overflow-auto">
      <div className="flex min-w-max min-h-full">
        {/* 行号列：sticky 固定在左侧，随内容纵向同步滚动（字号/行高与代码严格一致） */}
        <div
          className="sticky left-0 z-10 shrink-0 select-none text-right py-3 pl-3 pr-3 text-content-disabled bg-surface-card border-r border-border-light font-mono text-[13px] leading-[1.7]"
          aria-hidden="true"
        >
          {lines.map((_, i) => (
            <div key={i} className="tabular-nums">{i + 1}</div>
          ))}
        </div>
        {/* 代码区：右缘留白避免内容贴住纵向滚动条 */}
        <div className="py-3 pl-4 pr-8 font-mono text-[13px] leading-[1.7]">
          <pre className="whitespace-pre text-content-primary">
            <code className="hljs" dangerouslySetInnerHTML={{ __html: highlightedHtml ?? escapeHtml(content) }} />
          </pre>
          {truncated && (
            <div className="text-xs text-content-disabled mt-2 font-sans">{truncatedLabel}</div>
          )}
        </div>
      </div>
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
function DiffView({ content, emptyHint, truncated, truncatedLabel }: {
  content: string;
  emptyHint: string;
  truncated: boolean;
  truncatedLabel: string;
}) {
  const hunks = useMemo(() => parseUnifiedDiff(content), [content]);
  if (hunks.length === 0) {
    return <div className="h-full flex items-center justify-center text-sm text-content-secondary">{emptyHint}</div>;
  }
  return (
    <div className="h-full preview-scroll overflow-auto">
      {hunks.map((h, hi) => (
        <div key={hi} className={`min-w-max ${hi > 0 ? 'mt-3' : ''}`}>
          <div className="border border-border-light">
            <div className="flex font-mono text-[13px] leading-[1.7]">
              {/* 行号列：sticky 固定；删除行旧号、新增/上下文行新号 */}
              <div className="sticky left-0 z-10 shrink-0 select-none text-right bg-surface-card border-r border-border-light" aria-hidden="true">
                {h.lines.map((r, i) => (
                  <div key={i} className="pl-3 pr-3 tabular-nums text-content-disabled">
                    {r.type === 'del' ? r.oldNo : r.newNo}
                  </div>
                ))}
              </div>
              {/* 内容列：变更行整行着色（+/- 标记 + 底色） */}
              <div className="pr-8">
                {h.lines.map((r, i) => (
                  <div key={i} className={`flex whitespace-pre ${r.type === 'add' ? 'bg-success/10' : r.type === 'del' ? 'bg-danger/10' : ''}`}>
                    <span className={`w-4 shrink-0 select-none text-center ${r.type === 'add' ? 'text-success' : r.type === 'del' ? 'text-danger' : 'text-transparent'}`}>
                      {r.type === 'add' ? '+' : r.type === 'del' ? '-' : ' '}
                    </span>
                    <span className={`pr-4 ${r.type === 'add' ? 'text-diff-add' : r.type === 'del' ? 'text-diff-del' : 'text-content-primary'}`}>
                      {r.text || ' '}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      ))}
      {truncated && (
        <div className="text-xs text-content-disabled mt-2 font-sans">{truncatedLabel}</div>
      )}
    </div>
  );
}

/** 元信息行：内容视图（大小 · 行数 · 截断）/ diff 视图（相对 HEAD）等 */
export function PreviewMetaLine({ lang, payload, loading }: {
  lang: UiLanguage;
  payload: FileContentPayload;
  loading: boolean;
}) {
  const content = payload.content ?? '';
  if (payload.binary && payload.kind !== 'diff') return <div className="text-xs text-content-secondary mt-0.5">{t(lang, 'binary_file')}</div>;
  if (payload.error) return <div className="text-xs text-danger mt-0.5 truncate">{payload.error}</div>;
  if (payload.kind === 'diff') {
    const lines = content ? content.split('\n').length : 0;
    return (
      <div className="text-xs text-content-secondary tabular-nums mt-0.5">
        {content ? `${t(lang, 'diff_vs_head')} · ${lines} ${t(lang, 'lines_label')}` : t(lang, 'loading')}
      </div>
    );
  }
  const lines = content ? content.split('\n').length : 0;
  return (
    <div className="text-xs text-content-secondary tabular-nums mt-0.5">
      {loading && !content
        ? t(lang, 'loading')
        : [
            formatSize(payload.size ?? 0),
            lines > 0 ? `${lines} ${t(lang, 'lines_label')}` : '',
            payload.truncated ? t(lang, 'truncated_label') : '',
          ].filter(Boolean).join(' · ')}
    </div>
  );
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

/** 字节数人性化格式 */
function formatSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}
