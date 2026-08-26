/**
 * @fileoverview 提示输入组件
 *
 * Web 前端的用户输入组件，支持：
 * - 多行文本输入
 * - 命令自动补全（/ 前缀触发）
 * - 文件提及补全（@ 前缀触发；仅插入路径文本，内容由模型自行读取）
 * - 内联选项选择
 * - 快捷键支持（Enter 发送、Ctrl+Enter 换行、Esc 关闭）
 * - 忙碌状态下的停止按钮
 *
 * @module PromptInput
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import type { FileMentionCandidate, WebWorkspaceItem } from '../types/protocol';
import { highlightMentions } from '../utils/mention';
import { CheckIcon, FolderClosedIcon, GearIcon, PlusIcon, StopIcon } from './icons';

/**
 * Web 端允许的 B 类指令集合（自动补全只显示这些）
 *
 * A 类指令（new/resume/delete/model/effort/permissions/plan）已完全交由 UI 控件承载，
 * 输入框不识别；其余指令当作普通文本发给 LLM。因此自动补全只列出 B 类指令。
 *
 * 阻塞会话的指令：/compact、/goal（busy 时由 App 端 toast 提示不可用）；
 * 非阻塞指令：/export、/init、/rename、/agent（不改变 busy 状态）。
 * 回退功能由 user 气泡下方的回退按钮承担，/rewind 不再作为输入框指令。 */
// 自动补全列表：包含所有前端识别的斜杠指令
// 注意：'/agent' 虽在此列表中，但在 App.tsx 的 handleSubmit 中有特殊分支处理（分支选择器/创建向导/查看摘要）
// 因此 '/agent' 不在 B_COMMANDS 中，不会走 web_query 通道
// '/goal' 同理：在 App.tsx 中走 submit_line（A 通道命令注册表），后端执行 /goal 命令并驱动 goal 轮次
export const WEB_COMMANDS = [
  '/compact', '/export', '/init',
  '/agent', '/rename',
  '/goal',
];

/** 输入框最大高度（px）：内容超过后输入框内部滚动，不再继续撑大 */
const MAX_TEXTAREA_HEIGHT = 240;

/** @ 提及补全防抖间隔（ms）：连续输入时减少补全请求 */
const MENTION_DEBOUNCE_MS = 120;

/**
 * @ 提及 token（光标处正在输入的提及片段）
 */
interface MentionToken {
  /** '@' 字符在文本中的下标 */
  start: number;
  /** 光标位置（token 结束边界） */
  end: number;
  /** 查询串：@ 之后、光标之前的路径片段（引号形式为引号内内容） */
  query: string;
  /** 是否以 @" 开启的引号形式（路径含空格时使用） */
  quoted: boolean;
}

/**
 * 检测光标处是否处于 @ 提及输入中。
 *
 * 规则：'@' 必须位于输入开头或空白字符之后（邮箱等文本不触发）；
 * 未加引号时 token 内不允许空格；@" 开启的引号形式允许空格，
 * 出现闭合引号即视为提及结束。
 *
 * @param text - 输入框全文
 * @param pos - 光标位置
 * @returns 提及 token；不在提及上下文返回 null
 */
export function detectMentionToken(text: string, pos: number): MentionToken | null {
  const before = text.slice(0, pos);
  const at = before.lastIndexOf('@');
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(before[at - 1] ?? '')) return null;
  const rest = before.slice(at + 1);
  if (rest.startsWith('"')) {
    if (rest.indexOf('"', 1) !== -1 || rest.includes('\n')) return null; // 引号已闭合或跨行：提及结束
    return { start: at, end: pos, query: rest.slice(1), quoted: true };
  }
  if (/\s/.test(rest)) return null; // 未引号形式不允许空格
  return { start: at, end: pos, query: rest, quoted: false };
}

/**
 * 格式化提及插入文本：
 * 名称含空格或引号时用 @"..." 形式；目录保留尾部 / 继续下钻，
 * 文件与技能追加空格闭合 token。
 *
 * @param candidate - 选中的候选
 * @returns 插入到输入框的完整文本
 */
export function formatMentionInsertion(candidate: FileMentionCandidate): string {
  const needsQuote = /[\s"]/.test(candidate.path);
  if (candidate.kind === 'dir') return needsQuote ? `@"${candidate.path}/` : `@${candidate.path}/`;
  return needsQuote ? `@"${candidate.path}" ` : `@${candidate.path} `;
}

/**
 * 内联选项接口
 */
interface InlineOption {
  /** 选项值 */
  value: string;
  /** 显示标签 */
  label: string;
  /** 选项描述 */
  description?: string;
  /** 是否为当前活跃选项 */
  active?: boolean;
}

/**
 * 内联选项配置接口
 */
interface InlineOptions {
  /** 关联的命令名称 */
  command: string;
  /** 选项列表标题 */
  title: string;
  /** 选项列表 */
  options: InlineOption[];
}

/**
 * PromptInput 组件属性接口
 */
interface PromptInputProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 是否忙碌 */
  busy: boolean;
  /** 停止请求已发送、等待后端终止确认（终止过程可能有 1-2s 延迟，按钮显示旋转动画） */
  stopping?: boolean;
  /** 是否有运行中的后台任务（agent / bash / powershell 等，空闲时也可停止） */
  hasActiveTasks?: boolean;
  /** 是否已连接 */
  connected: boolean;
  /** 可用命令列表 */
  commands: string[];
  /** 提交回调 */
  onSubmit: (line: string) => void;
  /** 停止回调 */
  onStop: () => void;
  /** 内联选项配置（可选） */
  inlineOptions?: InlineOptions | null;
  /** 内联选项选择回调（可选） */
  onInlineSelect?: (command: string, value: string) => void;
  /** 内联选项关闭回调（可选） */
  onInlineClose?: () => void;
  /** 注册的工作区列表（目录按钮弹层数据源） */
  workspaces?: WebWorkspaceItem[];
  /** 当前活跃会话所属工作区目录（null 表示未知） */
  activeCwd?: string | null;
  /** 欢迎界面可见（无任何会话内容）：目录按钮常显，可直接选目录新建 */
  welcomeVisible?: boolean;
  /** 选择目录：立即在该目录新建会话并切换（选目录即新建） */
  onPickWorkspace?: (cwd: string) => void;
  /** 添加目录（弹层内联输入，后端校验并注册） */
  onAddWorkspace?: (path: string) => void;
  /** 打开设置弹窗的目录空间管理页 */
  onManageWorkspaces?: () => void;
  /** 底部工具行注入内容（Mode/Model/Effort 下拉，右对齐由发送按钮区隔离） */
  children?: React.ReactNode;
  /** 挂载时的初始草稿（rewind 回退到欢迎界面时输入框重挂载，用此回填被回退的 user 消息） */
  initialDraft?: string;
  /** 消费初始草稿后的回调（父组件据此清空持久化草稿，避免残留影响下次会话） */
  onConsumeInitialDraft?: (draft: string) => void;
  /** @ 提及补全最近一次结果（requestId 不匹配视为过期丢弃；null = 尚无结果） */
  fileMentionResult?: { requestId: string; query: string; candidates: FileMentionCandidate[] } | null;
  /** 拉取 @ 提及补全候选（query 为 @ 后路径片段） */
  onRequestFileMentions?: (query: string, requestId: string) => void;
  /** 当前展开的唯一下拉标识（plus/ws 或 Toolbar 的 mode/model/effort），null 表示全部收起 */
  activeMenu: string | null;
  /** 菜单展开/收起回调（打开时传 key，收起时传 null），用于和 Toolbar 下拉互斥收起 */
  onMenuOpen: (key: string | null) => void;
}

/**
 * 提示输入组件
 *
 * Web 前端的用户输入组件。
 *
 * @param props - 组件属性
 * @returns 返回提示输入的 JSX 元素
 */
export interface PromptInputHandle {
  /** 设置输入框内容（用于 rewind 回填被回退的 user 消息） */
  setDraft: (text: string) => void;
}

const PromptInput = forwardRef<PromptInputHandle, PromptInputProps>(function PromptInput({ lang, busy, stopping, hasActiveTasks, connected, commands, onSubmit, onStop, inlineOptions, onInlineSelect, onInlineClose, workspaces, activeCwd, welcomeVisible, onPickWorkspace, onAddWorkspace, onManageWorkspaces, children, initialDraft, onConsumeInitialDraft, fileMentionResult, onRequestFileMentions, activeMenu, onMenuOpen }, ref) {
  const [value, setValue] = useState(initialDraft ?? '');

  // 挂载时若携带初始草稿（欢迎界面重挂载回填），通知父组件消费清空，避免残留
  useEffect(() => {
    if (initialDraft) onConsumeInitialDraft?.(initialDraft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // + 号与目录弹层开关：由父组件 activeMenu 统一管理（与 Toolbar 下拉互斥展开）
  const plusOpen = activeMenu === 'plus';
  const wsOpen = activeMenu === 'ws';
  // 目录选择弹层（选目录即新建会话）状态
  const [wsAddMode, setWsAddMode] = useState(false);
  const [wsAddValue, setWsAddValue] = useState('');
  const wsInputRef = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    setDraft: (text: string) => {
      setValue(text);
      setCaret(text.length);
      // 回填草稿：聚焦、光标移到末尾，按内容撑高并滚动到底部
      requestAnimationFrame(() => {
        const ta = textareaRef.current;
        if (ta) {
          ta.focus();
          ta.setSelectionRange(text.length, text.length);
          // 高度由 useLayoutEffect 按 value 自动撑高，此处只需保证视口在底部
          ta.scrollTop = ta.scrollHeight;
        }
      });
    },
  }), []);
  const [showCommands, setShowCommands] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 欢迎界面目录为空：必须先在目录按钮中选定目录（新建会话）才能开始对话，
  // 此时禁用发送，避免在未指定工作区时发空会话消息（handleKeyDown/发送按钮共用）
  const noWorkspaceOnWelcome = welcomeVisible === true && !activeCwd;

  // 输入内容（含回填/清空等程序化赋值）变化时，按 scrollHeight 自动撑高并限高
  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
    // 高度变化可能改变内部滚动位置，镜像层同步偏移保持高亮对齐
    if (mirrorRef.current) mirrorRef.current.scrollTop = ta.scrollTop;
  }, [value]);

  // 点击外部关闭内联选项
  useEffect(() => {
    if (!inlineOptions || inlineOptions.options.length === 0) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onInlineClose?.();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [inlineOptions, onInlineClose]);

  // 点击外部关闭命令补全（含 + 号菜单与目录弹层）；点击输入框同样收起
  // plus/目录非输入型下拉（与 Toolbar 下拉一致），保持体验统一
  useEffect(() => {
    if (!showCommands && !plusOpen && !wsOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current && !containerRef.current.contains(target)) {
        setShowCommands(false);
        onMenuOpen(null);
        setWsAddMode(false);
      } else if (textareaRef.current && target === textareaRef.current) {
        // 点击输入框（对焦）即收起 + 号与目录弹层
        onMenuOpen(null);
        setWsAddMode(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showCommands, plusOpen, wsOpen, onMenuOpen]);

  // 自动补全仅显示 B 类指令（与后端 ready 推送的完整命令列表取交集，无交集则用静态集合）
  const webCommands = useMemo(() => {
    return commands.length > 0
      ? WEB_COMMANDS.filter((c) => commands.some((cmd) => cmd === c || cmd === c.slice(1)))
      : WEB_COMMANDS;
  }, [commands]);

  const filteredCommands = webCommands.filter((cmd) => {
    const query = value.toLowerCase();
    return cmd.toLowerCase().startsWith(query) || cmd.toLowerCase().includes(query.slice(1));
  });

  useEffect(() => {
    // 只在命令行元素（data-command-row）中定位，跳过标题等非行子元素——
    // children[selectedIndex] 会把标题算进索引导致滚动与选中错位（选中项漂出可视区）；
    // 索引 0 直接回顶：nearest 会把首行顶到滚动区上缘，标题被推出可视区
    if (showCommands && listRef.current) {
      if (selectedIndex === 0) {
        listRef.current.scrollTop = 0;
      } else {
        listRef.current.querySelectorAll('[data-command-row]')[selectedIndex]?.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [selectedIndex, showCommands]);

  // === @ 提及补全（仅插入路径文本，不读内容） ===
  const [caret, setCaret] = useState(0);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [acceptedMentionReqId, setAcceptedMentionReqId] = useState<string | null>(null);
  const [mentionDismissed, setMentionDismissed] = useState(false);
  const mentionReqIdRef = useRef(0);
  const mentionListRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);

  // 光标处提及 token 检测（输入/点击/方向键移动光标都会触发重算）
  const mentionToken = useMemo(() => detectMentionToken(value, caret), [value, caret]);
  const tokenKey = mentionToken ? `${mentionToken.start}:${mentionToken.query}` : null;

  // token 变化时防抖拉取候选；token 消失自动收起菜单；Esc 关闭后继续输入自动重新武装
  useEffect(() => { setMentionDismissed(false); }, [tokenKey]);
  useEffect(() => {
    if (!tokenKey || !onRequestFileMentions) return;
    const timer = setTimeout(() => {
      const rid = `m${++mentionReqIdRef.current}`;
      setAcceptedMentionReqId(rid);
      onRequestFileMentions(tokenKey.slice(tokenKey.indexOf(':') + 1), rid);
    }, MENTION_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [tokenKey, onRequestFileMentions]);

  useEffect(() => { if (!mentionToken) setMentionIndex(0); }, [mentionToken]);

  // 仅采纳最新请求的响应，并按响应回显的规范化 query 二次过滤（防抖窗口内的旧结果避免闪烁；
  // 用服务端规范化后的 query，避免 @./src、@\path 等原始串过滤全部落空）
  const mentionCandidates = useMemo(() => {
    if (!fileMentionResult || fileMentionResult.requestId !== acceptedMentionReqId) return [] as FileMentionCandidate[];
    const q = fileMentionResult.query.toLowerCase();
    return q ? fileMentionResult.candidates.filter((c) => c.path.toLowerCase().includes(q)) : fileMentionResult.candidates;
  }, [fileMentionResult, acceptedMentionReqId]);
  const showMentionMenu = mentionToken !== null && !mentionDismissed && mentionCandidates.length > 0 && !inlineOptions;

  // 菜单分区：技能在前、文件在后（候选顺序即导航顺序）；行元素带 data-mention-row 供滚动定位
  const mentionFileRows = mentionCandidates.filter((c) => c.kind !== 'skill');
  const mentionSkillCount = mentionCandidates.length - mentionFileRows.length;

  useEffect(() => {
    // 索引 0 直接回顶：nearest 会把首行顶到滚动区上缘，标题（Skills/Files）被推出可视区
    if (showMentionMenu && mentionListRef.current) {
      if (mentionIndex === 0) {
        mentionListRef.current.scrollTop = 0;
      } else {
        mentionListRef.current.querySelectorAll('[data-mention-row]')[mentionIndex]?.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [mentionIndex, showMentionMenu]);

  // 候选列表变化时钳制选中索引（防抖响应可能缩窄列表，避免索引越界产生死高亮）
  useEffect(() => {
    setMentionIndex((i) => (mentionCandidates.length === 0 ? 0 : Math.min(i, mentionCandidates.length - 1)));
  }, [mentionCandidates.length]);

  // 点击输入卡片外部时收起 @ 提及菜单（点击内部交由光标/token 检测自然处理）
  useEffect(() => {
    if (!showMentionMenu) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setMentionDismissed(true);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showMentionMenu]);

  /** 应用选中的提及候选：替换 @token 为提及文本；目录保留尾部 / 继续下钻 */
  const applyMention = useCallback((candidate: FileMentionCandidate) => {
    const ta = textareaRef.current;
    if (!ta || !mentionToken) return;
    const insertion = formatMentionInsertion(candidate);
    const nextCaret = mentionToken.start + insertion.length;
    setValue((prev) => prev.slice(0, mentionToken.start) + insertion + prev.slice(mentionToken.end));
    setCaret(nextCaret);
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(nextCaret, nextCaret);
    });
  }, [mentionToken]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    setValue(newValue);
    setCaret(e.target.selectionStart ?? newValue.length);
    onMenuOpen(null);
    setShowCommands(newValue.startsWith('/') && newValue.length > 0 && filteredCommands.length > 0 && !inlineOptions);
  }, [filteredCommands.length, inlineOptions, onMenuOpen]);

  const selectCommand = useCallback((cmd: string) => {
    if (noWorkspaceOnWelcome) return; // 欢迎界面未选目录时禁止发送（含指令补全）
    setValue('');
    setShowCommands(false);
    onMenuOpen(null);
    onSubmit(cmd);
  }, [onSubmit, onMenuOpen, noWorkspaceOnWelcome]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // 内联选项模式
      if (inlineOptions) {
        const opts = inlineOptions.options;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedIndex((i) => Math.min(i + 1, opts.length - 1));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedIndex((i) => Math.max(i - 1, 0));
          return;
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (opts[selectedIndex] && onInlineSelect) {
            onInlineSelect(inlineOptions.command, opts[selectedIndex].value);
          }
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onInlineClose?.();
          return;
        }
        return;
      }

      // @ 提及补全模式（优先级在内联选项之后、斜杠补全之前）
      if (showMentionMenu) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionIndex((i) => Math.min(i + 1, mentionCandidates.length - 1));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionIndex((i) => Math.max(i - 1, 0));
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
          e.preventDefault();
          const picked = mentionCandidates[mentionIndex];
          if (picked) applyMention(picked);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setMentionDismissed(true);
          return;
        }
      }

      // 自动补全模式
      if (showCommands) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedIndex((i) => Math.min(i + 1, filteredCommands.length - 1));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedIndex((i) => Math.max(i - 1, 0));
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
          e.preventDefault();
          if (filteredCommands[selectedIndex]) {
            selectCommand(filteredCommands[selectedIndex]);
          }
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setShowCommands(false);
          return;
        }
      }

      // 普通输入模式
      if (e.key === 'Enter') {
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          const target = e.currentTarget;
          const start = target.selectionStart;
          const end = target.selectionEnd;
          const newValue = value.slice(0, start) + '\n' + value.slice(end);
          setValue(newValue);
          setCaret(start + 1);
          requestAnimationFrame(() => {
            target.selectionStart = target.selectionEnd = start + 1;
            // 高度由 useLayoutEffect 撑高；光标在末尾附近时滚到底部，便于看到新行
            if (start + 1 >= newValue.length - 1) {
              target.scrollTop = target.scrollHeight;
            }
          });
          return;
        }
        e.preventDefault();
        if (!connected) return;
        // 忙碌时仅放行"已识别指令"（交由 App 判定：阻塞→toast，非阻塞→放行），
        // 未知斜杠（如 /resume）与普通文本仍禁止，避免向忙碌中的会话注入正文
        if (busy) {
          const pending = value.trim();
          if (!(pending && webCommands.some((c) => pending === c || pending.startsWith(`${c} `)))) return;
        }
        const line = value.trim();
        if (!line) return;
        if (noWorkspaceOnWelcome) return; // 欢迎界面未选目录时禁止发送
        onSubmit(line);
        // 始终清空输入框（包括 B 指令触发 inline popup 的情况）
        setValue('');
        setShowCommands(false);
        onMenuOpen(null);
      }
    },
    [value, busy, connected, onSubmit, showCommands, filteredCommands, selectedIndex, selectCommand, inlineOptions, onInlineSelect, onInlineClose, onMenuOpen, noWorkspaceOnWelcome, showMentionMenu, mentionCandidates, mentionIndex, applyMention, webCommands],
  );

  const handleSend = () => {
    // 忙碌时：已识别指令转发给 App 判定（阻塞→toast/非阻塞→放行，不停止任务）；
    // 未知斜杠（如 /resume）与普通文本/空白按停止处理，避免向忙碌中的会话注入正文
    if (busy) {
      const line = value.trim();
      if (line && webCommands.some((c) => line === c || line.startsWith(`${c} `))) {
        onSubmit(line);
        setValue('');
        setShowCommands(false);
        onMenuOpen(null);
        return;
      }
      onStop();
      return;
    }
    if (hasActiveTasks && !value.trim()) { onStop(); return; }
    if (!connected) return;
    const line = value.trim();
    if (!line) return;
    if (noWorkspaceOnWelcome) return; // 欢迎界面未选目录时禁止发送
    onSubmit(line);
    setValue('');
    setShowCommands(false);
    onMenuOpen(null);
  };

  const showInline = inlineOptions && inlineOptions.options.length > 0;
  const showAutocomplete = showCommands && filteredCommands.length > 0 && !showInline;
  // + 号与斜杠共用同一命令弹窗（+ 展示全部 WEB_COMMANDS，斜杠展示过滤结果）
  const showMenu = plusOpen || showAutocomplete;
  const menuCommands = plusOpen ? webCommands : filteredCommands;
  // 发送按钮状态：停止（busy 或后台任务+空输入）｜空输入灰色（不可发送）｜正常发送
  const isStopState = busy || (hasActiveTasks && !value.trim());
  const isIdleEmpty = !value.trim() && !busy && !stopping && !isStopState;

  return (
    <div className="relative px-3 pt-3" ref={containerRef}>
      {/* 内联选项 */}
      {showInline && (
        <div className="absolute bottom-full left-3 right-3 mb-1 bg-surface-card-alt border border-border-medium rounded-2xl max-h-64 overflow-y-auto p-1 z-20 dropdown-scroll dropdown-panel">
          <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest">{inlineOptions.title}</div>
          {inlineOptions.options.map((opt, idx) => (
            <button
              key={opt.value}
              onClick={() => onInlineSelect?.(inlineOptions.command, opt.value)}
              className={`w-full text-left px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer flex flex-col gap-0.5 ${
                idx === selectedIndex ? 'glass-option-active text-content-primary glass-option-hover' : opt.active ? 'text-primary/70 glass-option-hover' : 'text-content-secondary glass-option-hover'
              }`}
            >
              <span className="font-medium">{opt.label}</span>
              {opt.description && <span className="text-xs text-content-disabled">{opt.description}</span>}
            </button>
          ))}
        </div>
      )}

      {/* @ 提及补全弹窗（与斜杠命令弹窗同位置同样式；分区标题沿用英文 uppercase 惯例） */}
      {showMentionMenu && (
        <div
          ref={mentionListRef}
          className="absolute bottom-full left-0 right-0 mb-1 bg-surface-card-alt border border-border-medium rounded-3xl max-h-56 overflow-y-auto p-1 z-20 scrollbar-hidden dropdown-panel"
        >
          {mentionSkillCount > 0 && (
            <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1">Skills</div>
          )}
          {mentionCandidates.map((c, idx) => {
            // 文件区标题在首个文件行处渲染（有技能区时在其后，无技能区时在列表头）
            if (c.kind !== 'skill' && idx === (mentionSkillCount > 0 ? mentionSkillCount : 0)) {
              return (
                <div key="section-files" className={`px-3 pt-2 pb-1 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center ${mentionSkillCount > 0 ? 'border-t border-border-light mt-1' : ''} border-b border-border-light mb-1`}>Files</div>
              );
            }
            // 行索引与渲染 idx 解耦：分区标题占掉 idx 但不算行，否则标题后所有行
            // 的 idx !== mentionIndex，高亮/滚动定位全部失效（对应"上移异常"）
            const rowIdx = c.kind === 'skill' ? idx : idx - (mentionSkillCount > 0 ? 1 : 0);
            const name = c.path.split('/').pop() || c.path;
            const dir = c.kind === 'dir';
            const skill = c.kind === 'skill';
            return (
              <button
                key={`${c.kind}:${c.path}`}
                data-mention-row
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => applyMention(c)}
                title={c.path}
                className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                  rowIdx === mentionIndex ? 'glass-option-active text-content-primary glass-option-hover' : 'text-content-secondary glass-option-hover'
                }`}
              >
                {dir ? (
                  <svg className="w-3.5 h-3.5 shrink-0 text-primary/70" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M1.5 4.5v7a1.5 1.5 0 001.5 1.5h10a1.5 1.5 0 001.5-1.5V6.5a1.5 1.5 0 00-1.5-1.5H8L6.4 3.1a1.5 1.5 0 00-1.1-.6H3a1.5 1.5 0 00-1.5 1.5v.5z" />
                  </svg>
                ) : skill ? (
                  <svg className="w-3.5 h-3.5 shrink-0 text-primary/70" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                    {/* 技能：四点网格（与右栏 Skills 图标同语义） */}
                    <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
                    <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
                    <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
                    <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5 shrink-0 text-primary/70" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 1.5h5L13 5v9a1 1 0 01-1 1H4a1 1 0 01-1-1V2.5a1 1 0 011-1z" />
                    <path d="M9 1.5V5h4" />
                  </svg>
                )}
                <span className={`truncate flex-1 text-left font-mono ${dir ? '' : 'text-content-primary'}`}>{c.path}</span>
                {skill
                  ? c.description && <span className="text-xs text-content-disabled shrink-0 max-w-[45%] truncate">{c.description}</span>
                  : <span className="text-xs text-content-disabled shrink-0">{name}</span>}
              </button>
            );
          })}
        </div>
      )}

      {/* + 号 / 斜杠共用的命令弹窗（同一位置、同一样式） */}
      {showMenu && (
        <div
          ref={listRef}
          className="absolute bottom-full left-0 right-0 mb-1 bg-surface-card-alt border border-border-medium rounded-3xl max-h-56 overflow-y-auto p-1 z-20 scrollbar-hidden dropdown-panel"
        >
          <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1">Commands</div>
          {menuCommands.map((cmd, idx) => (
            <button
              key={cmd}
              data-command-row
              onClick={() => selectCommand(cmd)}
              className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                (!plusOpen && idx === selectedIndex) ? 'glass-option-active text-content-primary glass-option-hover' : 'text-content-secondary glass-option-hover'
              }`}
            >
              <span className="font-mono truncate flex-1 text-left">{cmd}</span>
              <span className="text-xs text-content-disabled shrink-0 max-w-[45%] truncate">{t(lang, `cmd_${cmd.slice(1).replace(/-/g, '_')}`)}</span>
            </button>
          ))}
        </div>
      )}

      {/* 输入区：镜像层渲染 @token 主题色，textarea 文字透明只留光标/选区（两者排版参数完全一致） */}
      <div className="flex items-end relative">
        <div
          ref={mirrorRef}
          aria-hidden
          className={`absolute inset-0 pointer-events-none select-none overflow-hidden whitespace-pre-wrap break-words text-base text-content-primary leading-[1.8] py-2 pl-3 pr-2 [scrollbar-gutter:stable] ${connected ? '' : 'opacity-50'}`}
        >
          {highlightMentions(value)}
        </div>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          // 点击/方向键移动光标时同步 caret 状态，驱动提及 token 重算（移出 token 自动收起菜单）
          onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          // 内容超限内部滚动时镜像层同步偏移，保证高亮与光标对齐
          onScroll={(e) => { if (mirrorRef.current) mirrorRef.current.scrollTop = e.currentTarget.scrollTop; }}
          placeholder={connected ? (activeCwd ? t(lang, 'input_placeholder') : t(lang, 'input_placeholder_no_cwd')) : t(lang, 'disconnected')}
          rows={1}
          disabled={!connected}
          // 欢迎界面挂载时自动聚焦：删除会话/新建会话后输入框重新挂载，
          // 显式聚焦避免焦点悬空导致用户无法直接输入
          autoFocus={welcomeVisible}
          className="flex-1 relative resize-none bg-transparent text-base [color:transparent] [caret-color:var(--text-primary)] placeholder-content-disabled min-h-[36px] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed leading-[1.8] py-2 pl-3 pr-2 [scrollbar-gutter:stable]"
          style={{ height: 'auto', maxHeight: `${MAX_TEXTAREA_HEIGHT}px`, overflowY: 'auto' }}
        />
      </div>

      {/* 底部工具行：+ 号 + 目录选择 + Mode/Model/Effort（左），发送按钮（右）；pt-8 与输入区留出更高视觉空白行 */}
      <div className="flex items-center justify-between gap-2 px-1 pt-8 pb-2">
        <div className="flex items-center gap-2 min-w-0">
          {/* + 号：打开快捷指令菜单（与斜杠同一弹窗） */}
          <button
            onClick={() => { onMenuOpen(plusOpen ? null : 'plus'); setShowCommands(false); setWsAddMode(false); }}
            title="Commands"
            aria-label="Commands"
            className={`pill-badge w-8 h-8 flex items-center justify-center rounded-full transition-colors cursor-pointer select-none ${
              plusOpen ? 'text-primary' : 'text-content-secondary hover:text-content-primary'
            }`}
            style={{ borderColor: 'var(--border-medium)' }}
          >
            <PlusIcon className="w-4 h-4" />
          </button>
          {/* 目录选择：欢迎界面可见（点选目录即在该目录新建会话）；无三角指示器。
              弹层标题与 ToolBar 下拉（Mode/Model/Effort）同风格：英文、uppercase */}
          {welcomeVisible && (
          <div className="relative shrink-0">
            <button
              onClick={() => { onMenuOpen(wsOpen ? null : 'ws'); setShowCommands(false); setWsAddMode(false); }}
              disabled={!connected || !onPickWorkspace}
              title={activeCwd ? `${t(lang, 'workspace_new_in')}\n${activeCwd}` : t(lang, 'workspace_select')}
              className={`pill-badge flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none ${
                wsOpen ? 'text-primary' : 'text-content-secondary hover:text-content-primary'
              }`}
              style={{ borderColor: 'var(--border-medium)' }}
            >
              <FolderClosedIcon className="w-3.5 h-3.5 shrink-0" />
              <span className={`max-w-[110px] truncate ${activeCwd ? '' : 'text-content-disabled'}`}>
                {activeCwd ? (workspaces?.find((w) => w.path === activeCwd)?.name || activeCwd.split(/[\\/]/).filter(Boolean).pop() || activeCwd) : t(lang, 'workspace_label')}
              </span>
            </button>
            {wsOpen && (
              <div className="absolute bottom-full left-0 mb-1 bg-surface-card-alt border border-border-medium rounded-2xl z-20 min-w-[260px] max-w-[380px] p-1 max-h-[40vh] overflow-y-auto dropdown-scroll dropdown-panel">
                {/* 标题与 ToolBar 下拉一致：英文、10px、uppercase、居中，无截断 */}
                <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1">New session in</div>
                {(workspaces ?? []).map((ws) => {
                  const isActive = ws.path === activeCwd;
                  return (
                    <button
                      key={ws.path}
                      onClick={() => { onMenuOpen(null); onPickWorkspace?.(ws.path); }}
                      title={ws.path}
                      className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                        isActive ? 'text-primary font-medium glass-option-hover' : 'text-content-secondary glass-option-hover'
                      } ${!ws.available ? 'opacity-50' : ''}`}
                    >
                      <FolderClosedIcon className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate flex-1 text-left">{ws.name}</span>
                      {ws.is_default && <span className="text-[10px] text-content-disabled shrink-0">{t(lang, 'workspace_default_badge')}</span>}
                      {isActive && (
                        <CheckIcon className="w-4 h-4 shrink-0" />
                      )}
                    </button>
                  );
                })}
                {wsAddMode ? (
                  <div className="px-2.5 py-2">
                    <div className="flex items-center gap-1.5">
                      <input
                        ref={wsInputRef}
                        type="text"
                        value={wsAddValue}
                        onChange={(e) => setWsAddValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            const v = wsAddValue.trim();
                            if (v) { onAddWorkspace?.(v); setWsAddValue(''); setWsAddMode(false); }
                          } else if (e.key === 'Escape') {
                            setWsAddMode(false); setWsAddValue('');
                          }
                        }}
                        autoFocus
                        placeholder={t(lang, 'workspace_add_placeholder')}
                        className="flex-1 min-w-0 bg-transparent text-sm text-content-primary placeholder-content-disabled outline-none border border-border-light rounded-md px-2 py-1.5 focus:border-primary/40"
                      />
                      <button
                        onClick={() => { const v = wsAddValue.trim(); if (v) { onAddWorkspace?.(v); setWsAddValue(''); setWsAddMode(false); } }}
                        disabled={!wsAddValue.trim()}
                        className="shrink-0 px-2.5 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {t(lang, 'workspace_add_confirm')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => { setWsAddMode(true); requestAnimationFrame(() => wsInputRef.current?.focus()); }}
                    className="w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm text-content-secondary glass-option-hover transition-colors cursor-pointer"
                  >
                    <PlusIcon className="w-3.5 h-3.5 shrink-0" />
                    <span>{t(lang, 'workspace_add')}</span>
                  </button>
                )}
                {onManageWorkspaces && (
                  <button
                    onClick={() => { onMenuOpen(null); onManageWorkspaces(); }}
                    className="w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm text-content-secondary glass-option-hover transition-colors cursor-pointer"
                  >
                    <GearIcon className="w-3.5 h-3.5 shrink-0" />
                    <span>{t(lang, 'workspace_manage')}</span>
                  </button>
                )}
              </div>
            )}
          </div>
          )}
          {children}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={handleSend}
            disabled={(!connected && !busy) || stopping || noWorkspaceOnWelcome}
            className={`shrink-0 w-8 h-8 flex items-center justify-center rounded-full transition-colors duration-150 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
              stopping
                ? 'bg-danger/10 text-danger hover:bg-danger/20'
                : noWorkspaceOnWelcome
                  ? 'bg-black/10 text-content-disabled cursor-not-allowed pointer-events-none'
                  : isStopState
                    ? 'bg-danger/10 text-danger hover:bg-danger/20 animate-pulse'
                    : isIdleEmpty
                      ? 'bg-black/10 text-content-disabled cursor-not-allowed pointer-events-none'
                      : 'bg-primary text-white hover:bg-primary-hover hover:shadow-glow'
            }`}
            title={stopping ? t(lang, 'task_stopping') : noWorkspaceOnWelcome ? t(lang, 'welcome_need_workspace') : isStopState ? t(lang, 'task_stopped') : t(lang, 'send')}
          >
            {stopping ? (
              // 停止请求已发出、等待后端确认：旋转圆圈缓冲动画（终止可能延迟 1-2s）
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none">
                <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
                <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            ) : isStopState
              ? <StopIcon className="w-[10px] h-[10px]" />
              : '↑'}
          </button>
        </div>
      </div>
    </div>
  );
});

export default PromptInput;
