/**
 * @fileoverview 提示输入组件
 *
 * Web 前端的用户输入组件，支持：
 * - 多行文本输入
 * - 工作区资源和 Web 内置命令补全（光标处 / 触发）
 * - @ 提及补全（会话引用 / 文件；仅插入提及文本，文件内容由模型
 *   自行读取，会话内容由引擎在提交边界以只读快照注入）
 * - 内联选项选择
 * - 快捷键支持（Enter 发送、Ctrl+Enter 换行、Esc 关闭）
 * - 忙碌状态下的停止按钮
 *
 * @module PromptInput
 */

import {
  forwardRef,
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { t, type UiLanguage } from "../i18n";
import type { FileMentionCandidate, WebWorkspaceItem } from "../types/protocol";
import { highlightMentions, SlashCatalogContext } from "../utils/mention";
import {
  builtinForLine, filterSlashChoices, insertSlashChoice, scanPromptTokens,
  slashAtCaret, slashSection, standaloneToken, type SlashChoice,
} from "../utils/slashTokens";
import type { WebElementContextComposerAttachment } from "../lib/webElementContext";
import WebElementContextAttachmentChip from "./WebElementContextAttachmentChip";
import {
  CheckIcon,
  FolderClosedIcon,
  GearIcon,
  ListChecksIcon,
  McpIcon,
  PlusIcon,
  SparkleIcon,
  StopIcon,
  TreeFileIcon,
} from "./icons";

const PICKER_ICON_CLASS = "w-3.5 h-3.5 shrink-0 text-content-secondary";

/** 输入框最大高度（px）：内容超过后输入框内部滚动，不再继续撑大 */
const MAX_TEXTAREA_HEIGHT = 240;

/** @ 提及补全防抖间隔（ms）：连续输入时减少补全请求 */
const MENTION_DEBOUNCE_MS = 120;

/** @ 提及请求序号（模块级）：跨组件实例单调递增，避免重挂载后 m1 与旧实例在途响应撞号 */
let mentionReqSeq = 0;

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
 * 出现闭合引号即视为提及结束。会话引用文法（@[label](illusion-session:id)，
 * '[' 开头）由引擎在提交边界解析，不触发文件补全菜单。
 *
 * @param text - 输入框全文
 * @param pos - 光标位置
 * @returns 提及 token；不在提及上下文返回 null
 */
export function detectMentionToken(
  text: string,
  pos: number,
): MentionToken | null {
  const before = text.slice(0, pos);
  const at = before.lastIndexOf("@");
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(before[at - 1] ?? "")) return null;
  const rest = before.slice(at + 1);
  if (rest.startsWith("[")) return null; // 会话引用文法：光标处于 @[... 提及内
  if (rest.startsWith('"')) {
    if (rest.indexOf('"', 1) !== -1 || rest.includes("\n")) return null; // 引号已闭合或跨行：提及结束
    return { start: at, end: pos, query: rest.slice(1), quoted: true };
  }
  if (/\s/.test(rest)) return null; // 未引号形式不允许空格
  return { start: at, end: pos, query: rest, quoted: false };
}

/**
 * 格式化提及插入文本：
 * 会话引用为规范形式 @[label](illusion-session:id)（label 转义 \ 与 ]，
 * 与后端 session_reference 文法一致——转义规则修改需同步后端与终端端）；
 * 名称含空格或引号时用 @"..." 形式；目录保留尾部 / 继续下钻，
 * 文件追加空格闭合 token。
 *
 * @param candidate - 选中的候选
 * @returns 插入到输入框的完整文本
 */
export function formatMentionInsertion(
  candidate: FileMentionCandidate,
): string {
  if (candidate.kind === "session" && candidate.sessionId) {
    const label = candidate.path.replace(/\\/g, "\\\\").replace(/\]/g, "\\]");
    return `@[${label}](illusion-session:${candidate.sessionId}) `;
  }
  const needsQuote = /[\s"]/.test(candidate.path);
  if (candidate.kind === "dir")
    return needsQuote ? `@"${candidate.path}/` : `@${candidate.path}/`;
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
 * 本地规范化 @ 提及查询串：与后端 normalize_mention_query 一致，
 * 避免 @./src、@\path 等原始串过滤候选时全部落空。
 */
function normalizeMentionQuery(query: string): string {
  let q = (query ?? "").trim().replace(/\\/g, "/");
  while (q.startsWith("./")) q = q.slice(2);
  if (q.startsWith("/")) q = q.replace(/^\/+/, "");
  return q.trim();
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
  /** @ 提及补全候选订阅：响应由 PromptInput 本地持有（不进 App 状态，避免整页重渲染） */
  subscribeFileMentions?: (
    cb: (result: {
      requestId: string;
      query: string;
      candidates: FileMentionCandidate[];
    }) => void,
  ) => () => void;
  /** 拉取 @ 提及补全候选（query 为 @ 后路径片段） */
  onRequestFileMentions?: (query: string, requestId: string) => void;
  /** 当前展开的唯一下拉标识（plus/ws 或 Toolbar 的 mode/model/effort），null 表示全部收起 */
  activeMenu: string | null;
  /** 菜单展开/收起回调（打开时传 key，收起时传 null），用于和 Toolbar 下拉互斥收起 */
  onMenuOpen: (key: string | null) => void;
  /** 网页元素拾取附件（pill 行数据源：浏览器元素选择结果） */
  webElementContexts?: readonly WebElementContextComposerAttachment[];
  /** 移除单条拾取附件 */
  onRemoveWebElementContext?: (id: string) => void;
  /** 整组移除拾取附件 */
  onClearWebElementContexts?: () => void;
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
  /** 在当前草稿末尾追加文本（空格分隔；元素拾取/提及类插入用） */
  appendText: (text: string) => void;
}

function slashChoiceId(choice: SlashChoice): string {
  return `${choice.kind}:${choice.name.toLowerCase()}`;
}

const PromptInput = forwardRef<PromptInputHandle, PromptInputProps>(
  function PromptInput(
    {
      lang,
      busy,
      stopping,
      hasActiveTasks,
      connected,
      onSubmit,
      onStop,
      inlineOptions,
      onInlineSelect,
      onInlineClose,
      workspaces,
      activeCwd,
      welcomeVisible,
      onPickWorkspace,
      onAddWorkspace,
      onManageWorkspaces,
      children,
      initialDraft,
      onConsumeInitialDraft,
      subscribeFileMentions,
      onRequestFileMentions,
      activeMenu,
      onMenuOpen,
      webElementContexts,
      onRemoveWebElementContext,
      onClearWebElementContexts,
    },
    ref,
  ) {
    // 草稿统一为规范文本（rewind 回填的 user 消息即库内原样 text，
    // 输入框与提交内容一致，无需任何转换）
    const [value, setValue] = useState(initialDraft ?? "");
    // 光标位置：高度贴底判定与 @ 提及 token 检测共用（须先于 height layout effect 声明）
    const [caret, setCaret] = useState(initialDraft?.length ?? 0);

    // 挂载时若携带初始草稿（欢迎界面重挂载回填），通知父组件消费清空，避免残留
    useEffect(() => {
      if (initialDraft) onConsumeInitialDraft?.(initialDraft);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    // + 号与目录弹层开关：由父组件 activeMenu 统一管理（与 Toolbar 下拉互斥展开）
    const plusOpen = activeMenu === "plus";
    const wsOpen = activeMenu === "ws";
    // 目录选择弹层（选目录即新建会话）状态
    const [wsAddMode, setWsAddMode] = useState(false);
    const [wsAddValue, setWsAddValue] = useState("");
    const wsInputRef = useRef<HTMLInputElement>(null);

    useImperativeHandle(
      ref,
      () => ({
        appendText: (text: string) => {
          setValue((prev) => {
            const needsSpace = prev.length > 0 && !/\s$/.test(prev);
            return prev + (needsSpace ? ' ' : '') + text;
          });
          requestAnimationFrame(() => {
            const ta = textareaRef.current;
            if (ta) {
              ta.focus();
              const end = ta.value.length;
              ta.setSelectionRange(end, end);
            }
          });
        },
        setDraft: (text: string) => {
          setValue(text);
          setCaret(text.length);
          // 回填草稿：聚焦、光标移到末尾；高度由镜像层撑开，这里滚到底并同步镜像
          requestAnimationFrame(() => {
            const ta = textareaRef.current;
            if (ta) {
              ta.focus();
              ta.setSelectionRange(text.length, text.length);
              ta.scrollTop = ta.scrollHeight;
              if (mirrorRef.current) mirrorRef.current.scrollTop = ta.scrollTop;
            }
          });
        },
      }),
      [],
    );
    const catalog = useContext(SlashCatalogContext);
    const [focused, setFocused] = useState(false);
    const [composing, setComposing] = useState(false);
    const [slashDismissed, setSlashDismissed] = useState(false);
    const tokens = useMemo(() => scanPromptTokens(value), [value]);
    const slashToken = useMemo(() => slashAtCaret(value, tokens, caret), [value, tokens, caret]);
    const allowBuiltins = standaloneToken(value, slashToken);
    const query = plusOpen ? '' : slashToken?.query ?? '';
    const menuCommands = useMemo(() => filterSlashChoices(catalog, query, allowBuiltins), [catalog, query, allowBuiltins]);
    const showCommands = focused && !composing && !activeMenu && !inlineOptions && !slashDismissed && !!slashToken;
    const showMenu = (plusOpen || showCommands) && menuCommands.length > 0;
    const [inlineIndex, setInlineIndex] = useState(0);
    const menuScope = showMenu ? JSON.stringify([activeCwd, plusOpen, slashToken?.start, query, allowBuiltins]) : '';
    const [selection, setSelection] = useState({ scope: '', id: '' });
    const selectedIndex = selection.scope === menuScope && selection.id
      ? Math.max(0, menuCommands.findIndex(cmd => slashChoiceId(cmd) === selection.id)) : 0;
    const selectedId = showMenu ? slashChoiceId(menuCommands[selectedIndex]!) : '';
    // 默认/兜底行也要锚定。新查询从其最佳匹配开始；
    // 同一查询的新快照保持选中名称在原位置不动。
    if (selection.scope !== menuScope || selection.id !== selectedId) {
      setSelection({ scope: menuScope, id: selectedId });
    }
    const listRef = useRef<HTMLDivElement>(null);
    const selectedCommandRef = useRef<HTMLButtonElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    // 欢迎界面目录为空：必须先在目录按钮中选定目录（新建会话）才能开始对话，
    // 此时禁用发送，避免在未指定工作区时发空会话消息（handleKeyDown/发送按钮共用）
    const noWorkspaceOnWelcome = welcomeVisible === true && !activeCwd;

    /**
     * 镜像层按行渲染：用 <br> 显式画出换行，空行用 NBSP 撑高。
     *
     * 不能只靠 textarea 的 scrollHeight——Chrome 在末尾 `\n` 时会少算一行，
     * 而 pre-wrap 文本节点的尾部换行在部分布局下也不稳定，导致「内容已换行
     * 但框没长高」。镜像层负责撑高后，高度完全由我们控制。
     */
    const mirrorNodes = useMemo(() => highlightMentions(value, catalog, { tokens, mirror: true }), [value, catalog, tokens]);

    /**
     * 镜像层内容变化后对齐 DOM 与滚动。
     * - 强制 ta.value === value：setRangeText/受控组件偶发不同步时以 state 为准，
     *   避免「删了换行又被旧 DOM 值顶回去」的自动再换行
     * - pendingCaret 在同一次 layout 提交里恢复光标，不用 rAF（rAF 会与后续
     *   Backspace 抢同一帧，造成光标/内容错位）
     */
    const pendingCaretRef = useRef<number | null>(null);

    useLayoutEffect(() => {
      const ta = textareaRef.current;
      const mirror = mirrorRef.current;
      if (!ta || !mirror) return;
      // React 提交期 autoFocus 可能先于欢迎页 composer 的 onFocus 触发。
      setFocused(ta.ownerDocument.activeElement === ta);
      if (!composing && ta.value !== value) ta.value = value;
      if (pendingCaretRef.current != null) {
        const c = pendingCaretRef.current;
        pendingCaretRef.current = null;
        ta.setSelectionRange(c, c);
      }
      const stickToEnd = caret >= value.length;
      if (stickToEnd && ta.scrollHeight > ta.clientHeight) {
        ta.scrollTop = ta.scrollHeight;
      }
      mirror.scrollTop = ta.scrollTop;
    }, [value, caret, composing]);

    // 点击外部关闭内联选项
    useEffect(() => {
      if (!inlineOptions || inlineOptions.options.length === 0) return;
      const handler = (e: MouseEvent) => {
        if (
          containerRef.current &&
          !containerRef.current.contains(e.target as Node)
        ) {
          onInlineClose?.();
        }
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, [inlineOptions, onInlineClose]);

    // 点击外部关闭命令补全（含 + 号菜单与目录弹层）；点击输入框同样收起
    // plus/目录非输入型下拉（与 Toolbar 下拉一致），保持体验统一
    useEffect(() => {
      if (!showCommands && !plusOpen && !wsOpen) return;
      const handler = (e: MouseEvent) => {
        const target = e.target as Node;
        if (containerRef.current && !containerRef.current.contains(target)) {
          setSlashDismissed(true);
          onMenuOpen(null);
          setWsAddMode(false);
        } else if (textareaRef.current && target === textareaRef.current) {
          // 点击输入框（对焦）即收起 + 号与目录弹层
          onMenuOpen(null);
          setWsAddMode(false);
        }
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, [showCommands, plusOpen, wsOpen, onMenuOpen]);

    useEffect(() => {
      setInlineIndex(0);
    }, [inlineOptions, plusOpen]);

    useEffect(() => {
      if (!showMenu || !listRef.current) return;
      // 在顶部时保持分节标题可见；否则滚动到选中的候选项本身。
      if (selectedIndex === 0) listRef.current.scrollTop = 0;
      else selectedCommandRef.current?.scrollIntoView({ block: 'nearest' });
    }, [selectedIndex, showMenu, menuCommands]);

    // === @ 提及补全（仅插入路径文本，不读内容） ===
    // caret 状态已在组件顶部声明（高度贴底判定依赖），此处仅补全提及索引
    const [mentionIndex, setMentionIndex] = useState(0);
    const [mentionDismissed, setMentionDismissed] = useState(false);
    // 最新已发出请求 id（仅更新 ref 不触发渲染，请求在途期间保留旧候选继续显示）
    const latestMentionReqIdRef = useRef<string | null>(null);
    // 最近一次采纳的候选缓存；新响应到达后整批替换，请求在途时旧缓存继续渲染
    const [mentionCache, setMentionCache] = useState<
      FileMentionCandidate[] | null
    >(null);
    const mentionListRef = useRef<HTMLDivElement>(null);
    const mirrorRef = useRef<HTMLDivElement>(null);

    // 光标处提及 token 检测（输入/点击/方向键移动光标都会触发重算）
    const mentionToken = useMemo(
      () => detectMentionToken(value, caret),
      [value, caret],
    );
    const tokenKey = mentionToken
      ? `${mentionToken.start}:${mentionToken.query}`
      : null;

    // token 变化时防抖拉取候选；token 消失自动收起菜单；Esc 关闭后继续输入自动重新武装
    useEffect(() => {
      setMentionDismissed(false);
    }, [tokenKey]);
    useEffect(() => {
      if (!tokenKey || !onRequestFileMentions) return;
      const timer = setTimeout(() => {
        const rid = `m${++mentionReqSeq}`;
        latestMentionReqIdRef.current = rid;
        onRequestFileMentions(tokenKey.slice(tokenKey.indexOf(":") + 1), rid);
      }, MENTION_DEBOUNCE_MS);
      return () => clearTimeout(timer);
    }, [tokenKey, onRequestFileMentions]);

    // 订阅会话层的提及响应：requestId 与最新已发出请求一致才采纳写入缓存
    // （迟到响应天然丢弃）；结果不经 App 状态，避免每次响应整页重渲染造成卡顿
    useEffect(() => {
      if (!subscribeFileMentions) return;
      return subscribeFileMentions((r) => {
        if (
          latestMentionReqIdRef.current &&
          r.requestId === latestMentionReqIdRef.current
        ) {
          setMentionCache(r.candidates);
        }
      });
    }, [subscribeFileMentions]);

    // 切换工作区（目录空间）时作废本地缓存与在途请求归属：
    // 上一工作区的路径候选不应在新工作区串显，旧响应也不再采纳
    useEffect(() => {
      setMentionCache(null);
      latestMentionReqIdRef.current = null;
    }, [activeCwd]);

    useEffect(() => {
      if (!mentionToken) setMentionIndex(0);
    }, [mentionToken]);

    // 候选基于缓存 + 当前 token 查询串实时过滤（新请求在途时旧候选按当前输入过滤，
    // 菜单保持稳定不闪没；查询串本地规范化后再过滤，与后端 normalize 口径一致）
    const normalizedTokenQuery = useMemo(
      () => normalizeMentionQuery(mentionToken?.query ?? "").toLowerCase(),
      [mentionToken?.query],
    );
    const mentionCandidates = useMemo(() => {
      if (!mentionCache || !mentionToken) return [] as FileMentionCandidate[];
      if (!normalizedTokenQuery) return mentionCache;
      // 会话候选额外按 sessionId 匹配（后端支持 id 命中，本地过滤口径对齐）
      return mentionCache.filter(
        (c) =>
          c.path.toLowerCase().includes(normalizedTokenQuery) ||
          (c.kind === "session" &&
            c.sessionId?.toLowerCase().includes(normalizedTokenQuery)),
      );
    }, [mentionCache, mentionToken, normalizedTokenQuery]);
    const showMentionMenu =
      mentionToken !== null &&
      !mentionDismissed &&
      mentionCandidates.length > 0 &&
      focused && !composing && !activeMenu && !inlineOptions;

    // 会话在文件之前；标题不占键盘导航序号。
    const filesStart = mentionCandidates.filter(c => c.kind === 'session').length;

    useEffect(() => {
      // 索引 0 直接回顶，以免 Sessions/Files 标题滚出可视区
      if (showMentionMenu && mentionListRef.current) {
        if (mentionIndex === 0) {
          mentionListRef.current.scrollTop = 0;
        } else {
          mentionListRef.current
            .querySelectorAll("[data-mention-row]")
            [mentionIndex]?.scrollIntoView({ block: "nearest" });
        }
      }
    }, [mentionIndex, showMentionMenu]);

    // 候选列表变化时钳制选中索引（防抖响应可能缩窄列表，避免索引越界产生死高亮）
    useEffect(() => {
      setMentionIndex((i) =>
        mentionCandidates.length === 0
          ? 0
          : Math.min(i, mentionCandidates.length - 1),
      );
    }, [mentionCandidates.length]);

    // 点击输入卡片外部时收起 @ 提及菜单（点击内部交由光标/token 检测自然处理）
    useEffect(() => {
      if (!showMentionMenu) return;
      const handler = (e: MouseEvent) => {
        if (
          containerRef.current &&
          !containerRef.current.contains(e.target as Node)
        ) {
          setMentionDismissed(true);
        }
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, [showMentionMenu]);

    /** 应用选中的提及候选：替换 @token 为提及文本；目录保留尾部 / 继续下钻 */
    const applyMention = useCallback(
      (candidate: FileMentionCandidate) => {
        const ta = textareaRef.current;
        if (!ta || !mentionToken) return;
        const insertion = formatMentionInsertion(candidate);
        const nextCaret = mentionToken.start + insertion.length;
        pendingCaretRef.current = nextCaret;
        setValue(
          (prev) =>
            prev.slice(0, mentionToken.start) +
            insertion +
            prev.slice(mentionToken.end),
        );
        setCaret(nextCaret);
        ta.focus();
      },
      [mentionToken],
    );

    const handleChange = useCallback(
      (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const newValue = e.target.value;
        // 用户编辑（含 Backspace 删换行）作废待恢复光标，避免与旧换行位置打架
        pendingCaretRef.current = null;
        setFocused(true);
        setValue(newValue);
        setCaret(e.target.selectionStart ?? newValue.length);
        onMenuOpen(null);
        setSlashDismissed(false);
      },
      [onMenuOpen],
    );

    /**
     * 多行粘贴：接管原生粘贴，统一换行符后一次性写入。
     *
     * 原生粘贴会先改 textarea 再等 React 提交镜像层，中间帧两者内容不一致；
     * 加上滚动条占宽变化导致换行点漂移，就会出现残影/重影。这里 preventDefault
     * 后同步写 value + caret，由 useLayoutEffect 在同一次提交中完成高度与滚动对齐。
     */
    const handlePaste = useCallback(
      (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
        if (composing) return;
        const ta = e.currentTarget;
        const raw = e.clipboardData.getData("text/plain");
        if (!raw) return;
        e.preventDefault();
        // 统一为 \n，避免 CRLF 在镜像层与 textarea 换行次数不一致
        const text = raw.replace(/\r\n?/g, "\n");
        const start = ta.selectionStart ?? value.length;
        const end = ta.selectionEnd ?? start;
        const next = value.slice(0, start) + text + value.slice(end);
        const nextCaret = start + text.length;
        pendingCaretRef.current = nextCaret;
        setValue(next);
        setCaret(nextCaret);
        onMenuOpen(null);
        setSlashDismissed(false);
      },
      [value, onMenuOpen, composing],
    );

    const selectCommand = useCallback(
      (cmd: SlashChoice) => {
        const ta = textareaRef.current;
        if (!ta || composing || !connected) return;
        if (cmd.kind === 'builtin') {
          if (!allowBuiltins || noWorkspaceOnWelcome) return;
          onSubmit(cmd.name);
          setValue('');
          setCaret(0);
        } else {
          const range = slashToken ?? { start: ta.selectionStart, end: ta.selectionEnd };
          const next = insertSlashChoice(value, range, cmd.name, !!slashToken);
          ta.focus();
          pendingCaretRef.current = next.caret;
          setValue(next.value);
          setCaret(next.caret);
        }
        setSlashDismissed(true);
        onMenuOpen(null);
      },
      [allowBuiltins, composing, connected, noWorkspaceOnWelcome, onMenuOpen, onSubmit, slashToken, value],
    );

    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // 输入法转换未提交前，Enter/Tab/方向键/Escape 均归 IME 处理。
        if (composing || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
        // 内联选项模式
        if (inlineOptions && !plusOpen) {
          const opts = inlineOptions.options;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setInlineIndex((i) => Math.min(i + 1, opts.length - 1));
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setInlineIndex((i) => Math.max(i - 1, 0));
            return;
          }
          if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            if (opts[inlineIndex] && onInlineSelect) {
              onInlineSelect(inlineOptions.command, opts[inlineIndex].value);
            }
            return;
          }
          if (e.key === "Escape") {
            e.preventDefault();
            onInlineClose?.();
            return;
          }
          return;
        }

        // @ 提及补全模式（优先级在内联选项之后、斜杠补全之前）
        if (showMentionMenu) {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setMentionIndex((i) =>
              Math.min(i + 1, mentionCandidates.length - 1),
            );
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setMentionIndex((i) => Math.max(i - 1, 0));
            return;
          }
          if (
            e.key === "Tab" ||
            (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey)
          ) {
            e.preventDefault();
            const picked = mentionCandidates[mentionIndex];
            if (picked) applyMention(picked);
            return;
          }
          if (e.key === "Escape") {
            e.preventDefault();
            setMentionDismissed(true);
            return;
          }
        }

        // 「+」与「/」两种模式都只在下方已渲染的候选数组内导航。
        if (showMenu) {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            const delta = e.key === 'ArrowDown' ? 1 : -1;
            const next = Math.max(0, Math.min(menuCommands.length - 1, selectedIndex + delta));
            setSelection({ scope: menuScope, id: slashChoiceId(menuCommands[next]!) });
            return;
          }
          if (
            e.key === "Tab" ||
            (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey)
          ) {
            e.preventDefault();
            const picked = menuCommands[selectedIndex];
            if (picked) selectCommand(picked);
            return;
          }
          if (e.key === "Escape") {
            e.preventDefault();
            setSlashDismissed(true);
            onMenuOpen(null);
            return;
          }
        }

        // 普通输入模式
        if (e.key === "Enter") {
          // Ctrl/Shift+Enter 换行（Shift 为常见聊天习惯；两者都不触发发送/补全采纳）
          if (e.ctrlKey || e.metaKey || e.shiftKey) {
            e.preventDefault();
            const target = e.currentTarget;
            const start = target.selectionStart ?? value.length;
            const end = target.selectionEnd ?? start;
            // 纯 React 写入，不用 setRangeText：避免与受控 value 抢状态，
            // 后续 Backspace 才不会出现「删了换行又自动长回来」
            const newValue = value.slice(0, start) + "\n" + value.slice(end);
            const nextCaret = start + 1;
            pendingCaretRef.current = nextCaret;
            setValue(newValue);
            setCaret(nextCaret);
            return;
          }
          e.preventDefault();
          if (!connected) return;
          // 忙碌时仅放行"已识别指令"（交由 App 判定：阻塞→toast，非阻塞→放行），
          // 未知斜杠（如 /resume）与普通文本仍禁止，避免向忙碌中的会话注入正文
          if (busy && !builtinForLine(value, catalog)) return;
          const line = value.trim();
          if (!line) return;
          if (noWorkspaceOnWelcome) return; // 欢迎界面未选目录时禁止发送
          onSubmit(line);
          // 始终清空输入框（包括 B 指令触发 inline popup 的情况）
          setValue("");
          setCaret(0);
          setSlashDismissed(true);
          onMenuOpen(null);
        }
      },
      [
        value,
        busy,
        connected,
        onSubmit,
        showMenu,
        plusOpen,
        menuCommands,
        menuScope,
        selectedIndex,
        inlineIndex,
        selectCommand,
        inlineOptions,
        onInlineSelect,
        onInlineClose,
        onMenuOpen,
        noWorkspaceOnWelcome,
        showMentionMenu,
        mentionCandidates,
        mentionIndex,
        applyMention,
        catalog,
        composing,
      ],
    );

    const handleSend = () => {
      // 忙碌时：已识别指令转发给 App 判定（阻塞→toast/非阻塞→放行，不停止任务）；
      // 未知斜杠（如 /resume）与普通文本/空白按停止处理，避免向忙碌中的会话注入正文
      if (busy) {
        const line = value.trim();
        if (builtinForLine(line, catalog)) {
          onSubmit(line);
          setValue("");
          setCaret(0);
          setSlashDismissed(true);
          onMenuOpen(null);
          return;
        }
        onStop();
        return;
      }
      if (hasActiveTasks && !value.trim()) {
        onStop();
        return;
      }
      if (!connected) return;
      const line = value.trim();
      if (!line) return;
      if (noWorkspaceOnWelcome) return; // 欢迎界面未选目录时禁止发送
      onSubmit(line);
      setValue("");
      setCaret(0);
      setSlashDismissed(true);
      onMenuOpen(null);
    };

    const showInline = !plusOpen && inlineOptions && inlineOptions.options.length > 0;
    // 发送按钮状态：停止（busy 或后台任务+空输入）｜空输入灰色（不可发送）｜正常发送
    const isStopState = busy || (hasActiveTasks && !value.trim());
    const isIdleEmpty = !value.trim() && !busy && !stopping && !isStopState;

    return (
      <div className="relative px-3 pt-3" ref={containerRef}>
        {/* 内联选项 */}
        {showInline && (
          <div className="absolute bottom-full left-3 right-3 mb-1 bg-surface-card-alt border border-border-medium rounded-2xl max-h-64 overflow-y-auto p-1 z-20 dropdown-scroll dropdown-panel">
            <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest">
              {inlineOptions.title}
            </div>
            {inlineOptions.options.map((opt, idx) => (
              <button
                key={opt.value}
                onClick={() =>
                  onInlineSelect?.(inlineOptions.command, opt.value)
                }
                className={`w-full text-left px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer flex flex-col gap-0.5 ${
                  idx === inlineIndex
                    ? "glass-option-active text-content-primary glass-option-hover"
                    : opt.active
                      ? "text-primary/70 glass-option-hover"
                      : "text-content-secondary glass-option-hover"
                }`}
              >
                <span className="font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="text-xs text-content-disabled">
                    {opt.description}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* @ 提及补全弹窗（与斜杠命令弹窗同位置同样式；分区标题沿用英文 uppercase 惯例，
          分区顺序 Sessions → Files 与候选顺序一致） */}
        {showMentionMenu && (
          <div
            ref={mentionListRef}
            className="absolute bottom-full left-0 right-0 mb-1 bg-surface-card-alt border border-border-medium rounded-3xl max-h-56 overflow-y-auto p-1 z-20 scrollbar-hidden dropdown-panel"
          >
            {mentionCandidates.map((c, idx) => {
              const name = c.path.split("/").pop() || c.path;
              const dir = c.kind === "dir";
              const session = c.kind === "session";
              return (
                <Fragment key={`${c.kind}:${c.sessionId ?? c.path}`}>
                  {/* 分区标题与行同级渲染（标题不占 data-mention-row 序号），
                    DOM 序号与候选/选中序号一致，高亮、Enter 采纳与滚动定位才不会错位 */}
                  {session && idx === 0 && (
                    <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1">
                      Sessions
                    </div>
                  )}
                  {!session && idx === filesStart && (
                    <div
                      className={`px-3 pt-2 pb-1 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center ${filesStart > 0 ? "border-t border-border-light mt-1" : ""} border-b border-border-light mb-1`}
                    >
                      Files
                    </div>
                  )}
                  <button
                    data-mention-row
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => applyMention(c)}
                    title={session ? (c.description ?? c.path) : c.path}
                    className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                      idx === mentionIndex
                        ? "glass-option-active text-content-primary glass-option-hover"
                        : "text-content-secondary glass-option-hover"
                    }`}
                  >
                    <span aria-hidden="true" className="shrink-0">
                      {session ? <ListChecksIcon className={PICKER_ICON_CLASS} />
                        : dir ? <FolderClosedIcon className={PICKER_ICON_CLASS} />
                        : <TreeFileIcon name={name} />}
                    </span>
                    <span
                      className={`truncate flex-1 text-left ${dir ? "font-mono" : ""}`}
                    >
                      {c.path}
                    </span>
                    {session ? (
                      c.description && (
                        <span className="text-xs text-content-disabled shrink-0 max-w-[45%] truncate">
                          {c.description}
                        </span>
                      )
                    ) : (
                      <span className="text-xs text-content-disabled shrink-0">
                        {name}
                      </span>
                    )}
                  </button>
                </Fragment>
              );
            })}
          </div>
        )}

        {/* + 号 / 斜杠共用的命令弹窗（同一位置、同一样式） */}
        {showMenu && (
          <div
            ref={listRef}
            onMouseDown={(e) => e.preventDefault()}
            className="absolute bottom-full left-0 right-0 mb-1 bg-surface-card-alt border border-border-medium rounded-3xl max-h-56 overflow-y-auto p-1 z-20 scrollbar-hidden dropdown-panel"
          >
            {menuCommands.map((cmd, idx) => {
              const section = slashSection(cmd);
              const startsSection = idx === 0 || slashSection(menuCommands[idx - 1]!) !== section;
              const selected = idx === selectedIndex;
              const ChoiceIcon = cmd.kind === 'skill' ? SparkleIcon : McpIcon;
              const description = cmd.kind === 'builtin' ? t(lang, cmd.descriptionKey) : cmd.description;
              return (
                <Fragment key={cmd.name}>
                  {startsSection && (
                    <h3 className={`px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1 ${idx > 0 ? 'mt-1 border-t' : ''}`}>
                      {section}
                    </h3>
                  )}
                  <button
                    data-command-row
                    ref={selected ? selectedCommandRef : undefined}
                    onClick={() => selectCommand(cmd)}
                    aria-label={description ? `${cmd.name} ${description}` : cmd.name}
                    title={description}
                    className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                      selected
                        ? "glass-option-active text-content-primary glass-option-hover"
                        : "text-content-secondary glass-option-hover"
                    }`}
                  >
                    <span aria-hidden="true" className="shrink-0"><ChoiceIcon className={PICKER_ICON_CLASS} /></span>
                    <span className="font-mono truncate flex-1 text-left">{cmd.name.slice(1)}</span>
                    {description && <span className="text-xs text-content-disabled shrink-0 max-w-[45%] truncate">{description}</span>}
                  </button>
                </Fragment>
              );
            })}
          </div>
        )}

        {/* 网页元素拾取附件 pill 行（浏览器元素选择结果；悬停展开详情，
            上下文附件行） */}
        {webElementContexts && webElementContexts.length > 0 && (
          <div className="flex max-w-full flex-wrap items-center gap-2 pb-2">
            <WebElementContextAttachmentChip
              contexts={webElementContexts}
              onRemove={onRemoveWebElementContext}
              onRemoveAll={onClearWebElementContexts}
              lang={lang}
            />
          </div>
        )}

        {/* 输入区：镜像层在文档流中撑高（按行 <br> + 空行 NBSP），textarea 绝对覆盖。
          正文透明只留光标/选区；隐藏滚动条避免超限后占宽变化导致换行漂移 */}
        <div className="relative">
          <div
            ref={mirrorRef}
            aria-hidden
            className={`pointer-events-none select-none overflow-hidden whitespace-pre-wrap break-words text-base text-content-primary leading-[1.8] py-2 pl-3 pr-2 box-border min-h-[36px] ${connected ? "" : "opacity-50"}`}
            style={{ maxHeight: `${MAX_TEXTAREA_HEIGHT}px` }}
          >
            {mirrorNodes}
          </div>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={handleChange}
            onPaste={handlePaste}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={(e) => {
              setComposing(false);
              setCaret(e.currentTarget.selectionStart);
            }}
            onFocus={(e) => {
              setFocused(true);
              setSlashDismissed(false);
              setCaret(e.currentTarget.selectionStart);
            }}
            onBlur={() => setFocused(false)}
            onClick={() => { setSlashDismissed(false); setMentionDismissed(false); }}
            // 点击/方向键移动光标时同步 caret 状态，驱动提及 token 重算（移出 token 自动收起菜单）
            onSelect={(e) => {
              const pos = e.currentTarget.selectionStart;
              if (pos !== caret) setSlashDismissed(false);
              setCaret(pos);
            }}
            // 内容超限内部滚动时镜像层同步偏移，保证高亮与光标对齐
            onScroll={(e) => {
              if (mirrorRef.current)
                mirrorRef.current.scrollTop = e.currentTarget.scrollTop;
            }}
            placeholder={
              connected
                ? activeCwd
                  ? t(lang, "input_placeholder")
                  : t(lang, "input_placeholder_no_cwd")
                : t(lang, "disconnected")
            }
            rows={1}
            disabled={!connected}
            // 欢迎界面挂载时自动聚焦：删除会话/新建会话后输入框重新挂载，
            // 显式聚焦避免焦点悬空导致用户无法直接输入
            autoFocus={welcomeVisible}
            className="prompt-ta absolute inset-0 w-full resize-none bg-transparent text-base [caret-color:var(--text-primary)] placeholder-content-disabled focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed leading-[1.8] py-2 pl-3 pr-2 box-border whitespace-pre-wrap break-words scrollbar-hidden"
            style={{ maxHeight: `${MAX_TEXTAREA_HEIGHT}px`, overflowY: "auto" }}
          />
        </div>

        {/* 底部工具行：+ 号 + 目录选择 + Mode/Model/Effort（左），发送按钮（右）；pt-8 与输入区留出更高视觉空白行 */}
        <div className="flex items-center justify-between gap-2 px-1 pt-8 pb-2">
          <div className="flex items-center gap-2 min-w-0">
            {/* + 号：打开快捷指令菜单（与斜杠同一弹窗） */}
            <button
              onClick={() => {
                if (!plusOpen) textareaRef.current?.focus({ preventScroll: true });
                onMenuOpen(plusOpen ? null : "plus");
                setSlashDismissed(true);
                setWsAddMode(false);
              }}
              title="Commands"
              aria-label="Commands"
              aria-expanded={plusOpen && showMenu}
              className={`pill-badge w-8 h-8 flex items-center justify-center rounded-full transition-colors cursor-pointer select-none ${
                plusOpen
                  ? "text-primary"
                  : "text-content-secondary hover:text-content-primary"
              }`}
              style={{ borderColor: "var(--border-medium)" }}
            >
              <PlusIcon className="w-4 h-4" />
            </button>
            {/* 目录选择：欢迎界面可见（点选目录即在该目录新建会话）；无三角指示器。
              弹层标题与 ToolBar 下拉（Mode/Model/Effort）同风格：英文、uppercase */}
            {welcomeVisible && (
              <div className="relative shrink-0">
                <button
                  onClick={() => {
                    onMenuOpen(wsOpen ? null : "ws");
                    setSlashDismissed(true);
                    setWsAddMode(false);
                  }}
                  disabled={!connected || !onPickWorkspace}
                  title={
                    activeCwd
                      ? `${t(lang, "workspace_new_in")}\n${activeCwd}`
                      : t(lang, "workspace_select")
                  }
                  className={`pill-badge flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none ${
                    wsOpen
                      ? "text-primary"
                      : "text-content-secondary hover:text-content-primary"
                  }`}
                  style={{ borderColor: "var(--border-medium)" }}
                >
                  <FolderClosedIcon className="w-3.5 h-3.5 shrink-0" />
                  <span
                    className={`max-w-[110px] truncate ${activeCwd ? "" : "text-content-disabled"}`}
                  >
                    {activeCwd
                      ? workspaces?.find((w) => w.path === activeCwd)?.name ||
                        activeCwd.split(/[\\/]/).filter(Boolean).pop() ||
                        activeCwd
                      : t(lang, "workspace_label")}
                  </span>
                </button>
                {wsOpen && (
                  <div className="absolute bottom-full left-0 mb-1 bg-surface-card-alt border border-border-medium rounded-2xl z-20 min-w-[260px] max-w-[380px] p-1 max-h-[40vh] overflow-y-auto dropdown-scroll dropdown-panel">
                    {/* 标题与 ToolBar 下拉一致：英文、10px、uppercase、居中，无截断 */}
                    <div className="px-3 py-1.5 text-[10px] text-content-disabled font-semibold uppercase tracking-widest text-center border-b border-border-light mb-1">
                      New session in
                    </div>
                    {(workspaces ?? []).map((ws) => {
                      const isActive = ws.path === activeCwd;
                      return (
                        <button
                          key={ws.path}
                          onClick={() => {
                            onMenuOpen(null);
                            onPickWorkspace?.(ws.path);
                          }}
                          title={ws.path}
                          className={`w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm transition-colors cursor-pointer animate-fade ${
                            isActive
                              ? "text-primary font-medium glass-option-hover"
                              : "text-content-secondary glass-option-hover"
                          } ${!ws.available ? "opacity-50" : ""}`}
                        >
                          <FolderClosedIcon className="w-3.5 h-3.5 shrink-0" />
                          <span className="truncate flex-1 text-left">
                            {ws.name}
                          </span>
                          {ws.is_default && (
                            <span className="text-[10px] text-content-disabled shrink-0">
                              {t(lang, "workspace_default_badge")}
                            </span>
                          )}
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
                              if (e.key === "Enter") {
                                const v = wsAddValue.trim();
                                if (v) {
                                  onAddWorkspace?.(v);
                                  setWsAddValue("");
                                  setWsAddMode(false);
                                }
                              } else if (e.key === "Escape") {
                                setWsAddMode(false);
                                setWsAddValue("");
                              }
                            }}
                            autoFocus
                            placeholder={t(lang, "workspace_add_placeholder")}
                            className="flex-1 min-w-0 bg-transparent text-sm text-content-primary placeholder-content-disabled outline-none border border-border-light rounded-md px-2 py-1.5 focus:border-primary/40"
                          />
                          <button
                            onClick={() => {
                              const v = wsAddValue.trim();
                              if (v) {
                                onAddWorkspace?.(v);
                                setWsAddValue("");
                                setWsAddMode(false);
                              }
                            }}
                            disabled={!wsAddValue.trim()}
                            className="shrink-0 px-2.5 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {t(lang, "workspace_add_confirm")}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setWsAddMode(true);
                          requestAnimationFrame(() =>
                            wsInputRef.current?.focus(),
                          );
                        }}
                        className="w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm text-content-secondary glass-option-hover transition-colors cursor-pointer"
                      >
                        <PlusIcon className="w-3.5 h-3.5 shrink-0" />
                        <span>{t(lang, "workspace_add")}</span>
                      </button>
                    )}
                    {onManageWorkspaces && (
                      <button
                        onClick={() => {
                          onMenuOpen(null);
                          onManageWorkspaces();
                        }}
                        className="w-full flex items-center gap-2 px-3 py-2 border border-transparent hover:border-border-light text-sm text-content-secondary glass-option-hover transition-colors cursor-pointer"
                      >
                        <GearIcon className="w-3.5 h-3.5 shrink-0" />
                        <span>{t(lang, "workspace_manage")}</span>
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
              disabled={
                (!connected && !busy) || stopping || noWorkspaceOnWelcome
              }
              className={`shrink-0 w-8 h-8 flex items-center justify-center rounded-full transition-colors duration-150 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
                stopping
                  ? "bg-danger/10 text-danger hover:bg-danger/20"
                  : noWorkspaceOnWelcome
                    ? "bg-black/10 text-content-disabled cursor-not-allowed pointer-events-none"
                    : isStopState
                      ? "bg-danger/10 text-danger hover:bg-danger/20 animate-pulse"
                      : isIdleEmpty
                        ? "bg-black/10 text-content-disabled cursor-not-allowed pointer-events-none"
                        : "bg-primary text-white hover:bg-primary-hover hover:shadow-glow"
              }`}
              title={
                stopping
                  ? t(lang, "task_stopping")
                  : noWorkspaceOnWelcome
                    ? t(lang, "welcome_need_workspace")
                    : isStopState
                      ? t(lang, "task_stopped")
                      : t(lang, "send")
              }
            >
              {stopping ? (
                // 停止请求已发出、等待后端确认：旋转圆圈缓冲动画（终止可能延迟 1-2s）
                <svg
                  className="w-4 h-4 animate-spin"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <circle
                    cx="8"
                    cy="8"
                    r="6"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeOpacity="0.25"
                  />
                  <path
                    d="M14 8a6 6 0 0 0-6-6"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
              ) : isStopState ? (
                <StopIcon className="w-[10px] h-[10px]" />
              ) : (
                "↑"
              )}
            </button>
          </div>
        </div>
      </div>
    );
  },
);

export default PromptInput;
