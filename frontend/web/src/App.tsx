/**
 * @fileoverview Web 前端应用主组件
 *
 * 本模块是 IllusionAgent Web 前端的核心入口，负责：
 * 1. 整体应用布局与组件组合
 * 2. WebSocket 会话管理
 * 3. 处理用户提交的命令
 * 4. 管理侧边栏和右侧面板的折叠/展开状态
 * 5. Toast 通知显示
 * 6. 删除会话弹窗
 * 7. 权限和问答模态框响应
 *
 * @module App
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { normalizeLanguage, t, type UiLanguage } from './i18n';
import { settingsApi } from './api';
import { useWebSocketSession } from './hooks/useWebSocketSession';
import Sidebar, { SidebarControls } from './components/Sidebar';
import ChatArea from './components/ChatArea';
import PromptInput, { type PromptInputHandle } from './components/PromptInput';
import Toolbar from './components/Toolbar';
import RightPanel, { RightPanelControls } from './components/RightPanel';
import TitleBar from './components/TitleBar';
import ConnectingOverlay from './components/ConnectingOverlay';
import ImagePreview from './components/ImagePreview';
import { CustomInputModal } from './components/CustomInputModal';
import { AgentWizardForm } from './components/AgentWizardForm';
import { SetupForm } from './components/SetupForm';
import { GoalBar } from './components/GoalBar';
import type { GoalStatus } from './types/protocol';
import { FolderClosedIcon, FolderOpenIcon } from './components/icons';

/** WebSocket 连接地址 */
const WS_URL = `ws://${window.location.host}/ws`;

/** Toast 通知显示时长（毫秒） */
const TOAST_DURATION = 5000;

/** B 通道允许的指令集合（前端识别并走 web_query） */
const B_COMMANDS = ['rewind', 'compact', 'context', 'export', 'init', 'turns', 'output-style', 'language', 'max-tokens', 'rename'];

/**
 * 应用主组件
 *
 * Web 前端的根组件，负责组合所有子组件并管理全局状态。
 *
 * @returns 返回应用的 JSX 元素
 */
export default function App() {
  const session = useWebSocketSession(WS_URL);

  // Electron 桌面壳：首次后端连接成功（遮罩层消失）后自动最大化窗口
  // 仅触发一次（autoMaximizedRef 兜底），避免重连时反复最大化
  const autoMaximizedRef = useRef(false);
  useEffect(() => {
    if (session.connected && !autoMaximizedRef.current) {
      autoMaximizedRef.current = true;
      window.illusionDesktop?.maximize();
    }
  }, [session.connected]);
  const lang: UiLanguage = useMemo(
    () => normalizeLanguage(session.status?.ui_language),
    [session.status?.ui_language],
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // 输入框与工具栏展开的唯一下拉标识（plus/ws/mode/model/effort），null 表示全部收起；
  // 提升到 App 统一管理，保证点击其中一个时自动收起其他下拉
  const [activeMenu, setActiveMenu] = useState<string | null>(null);
  // 右栏默认折叠；折叠态下右栏整体隐藏，控制由顶部右侧按钮组（RightPanelControls）承载
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const [rightPanelWidth, setRightPanelWidth] = useState(260);
  const dragRef = useRef<{ side: 'left' | 'right'; startX: number; startW: number } | null>(null);

  // 内联选项状态：由 hook 按会话维护（session.inlineOptions / session.setInlineOptions），
  // 切换会话时选项随会话隔离，互不串扰

  // 自定义数字输入模态框状态（/max-tokens 与 /context-window 的 custom 分支触发）
  const [customInputModal, setCustomInputModal] = useState<{
    prompt: string;
    command: 'max-tokens' | 'context-window' | 'rename';
    invalidMessage?: string;
    targetSessionId?: string;
  } | null>(null);

  // Agent 摘要浮动卡片：查看已完成 agent 时以卡片形式展示
  const [agentResult, setAgentResult] = useState<string | null>(null);
  const agentRequestIdRef = useRef<string | null>(null); // 当前等待的 agent 请求 ID

  // 回退确认弹窗状态
  const [rewindConfirm, setRewindConfirm] = useState<{ turns: number } | null>(null);
  // 重新生成：存储待重发的 user 消息文本，rewind 完成后自动重发
  const pendingRegenerateRef = useRef<string | null>(null);
  const prevBusyRef = useRef(false);
  const promptInputRef = useRef<PromptInputHandle>(null);
  // rewind 回退到开头时欢迎界面重新挂载 PromptInput，用 state 持久化回退文本
  const [rewindDraft, setRewindDraft] = useState<string | null>(null);

  // Toast 状态
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [toastExiting, setToastExiting] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastHoverRef = useRef(false);
  const toastKeyRef = useRef(0);

  const closeToast = useCallback(() => {
    setToastExiting(true);
    setTimeout(() => { setToastMessage(null); setToastExiting(false); }, 200);
  }, []);

  const showToast = useCallback((text: string, type: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastKeyRef.current += 1;
    setToastExiting(false);
    setToastMessage({ text, type: type as 'success' | 'error' | 'info' });
    toastHoverRef.current = false;
    toastTimerRef.current = setTimeout(() => {
      if (!toastHoverRef.current) { closeToast(); }
      toastTimerRef.current = null;
    }, TOAST_DURATION);
  }, [closeToast]);

  const handleToastMouseEnter = useCallback(() => {
    toastHoverRef.current = true;
    if (toastTimerRef.current) { clearTimeout(toastTimerRef.current); toastTimerRef.current = null; }
  }, []);

  const handleToastMouseLeave = useCallback(() => {
    toastHoverRef.current = false;
    toastTimerRef.current = setTimeout(() => { closeToast(); toastTimerRef.current = null; }, TOAST_DURATION);
  }, [closeToast]);

  /**
   * 注册回调函数
   *
   * 将内联选项请求和指令结果回调注册到会话中。
   */
  useEffect(() => {
    // 注：select_request 内联选项已由 hook 按会话路由（session.inlineOptions），
    // 无需在此注册 onSelectRequest 回调
    session.setOnRewindRestored((text) => {
      // 持久化回退文本：回退到欢迎界面时输入框重挂载，靠 initialDraft 兜底回填；
      // 普通 rewind 时 ref 即时回填（两者不冲突）
      setRewindDraft(text);
      promptInputRef.current?.setDraft(text);
    });
    session.setOnCommandResult((text, type, requestId) => {
      // 使用 request_id 精确匹配 agent 摘要响应，避免竞态条件
      if (agentRequestIdRef.current && requestId === agentRequestIdRef.current) {
        agentRequestIdRef.current = null;
        setAgentResult(text);
      } else {
        showToast(text, type);
      }
    });
    // 版本更新提醒：连接建立后后端异步检查，有新版本时弹 toast
    session.setOnUpdateAvailable((version) => {
      showToast(t(lang, 'update_available').replace('{version}', version), 'info');
    });
    return () => {
      session.setOnSelectRequest(null);
      session.setOnRewindRestored(null);
      session.setOnCommandResult(null);
      session.setOnUpdateAvailable(null);
    };
  }, [session.setOnSelectRequest, session.setOnCommandResult, session.setOnUpdateAvailable, showToast, lang]);

  /**
   * 处理面板大小调整开始
   *
   * 当用户开始拖拽面板边缘时触发，用于调整侧边栏或右侧面板的宽度。
   *
   * @param side - 要调整的面板（'left' 或 'right'）
   * @param e - 鼠标事件
   */
  const handleResizeStart = useCallback((side: 'left' | 'right', e: React.MouseEvent) => {
    e.preventDefault();
    const startW = side === 'left' ? sidebarWidth : rightPanelWidth;
    dragRef.current = { side, startX: e.clientX, startW };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const maxW = window.innerWidth / 3;
      const dx = ev.clientX - dragRef.current.startX;
      if (dragRef.current.side === 'left') {
        setSidebarWidth(Math.min(maxW, Math.max(280, dragRef.current.startW + dx)));
      } else {
        setRightPanelWidth(Math.min(maxW, Math.max(260, dragRef.current.startW - dx)));
      }
    };
    const onUp = () => { dragRef.current = null; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [sidebarWidth, rightPanelWidth]);

  /**
   * 处理用户提交的命令（三通道有序判定）
   *
   * 通道隔离原则：
   * - B 通道（web_query）：输入框识别的精细化指令（rewind/compact/context/export/init/
   *   turns/output-style/language/max-tokens），走 web_query 结构化处理。
   * - 文本通道（submit_line）：普通文本，或未被识别的斜杠指令（A 类如 /resume /model
   *   以及已删除指令），全部当普通文本发给 LLM。
   *
   * A 类指令（new/resume/delete/model/effort/permissions/plan）已完全交由 UI 控件承载，
   * 输入框不识别，落入文本通道。
   *
   * @param line - 用户输入的命令
   */
  const handleSubmit = (line: string) => {
    if (!line.trim()) return;
    const trimmed = line.trim();

    // 通道 1：B 类斜杠指令 → web_query（精细化处理，不经过命令注册表）
    if (trimmed.startsWith('/')) {
      const cmdName = trimmed.slice(1).split(/\s+/)[0] ?? '';
      const args = trimmed.slice(1 + cmdName.length).trim();

      // /rename（无参数）→ 弹出会话选择器
      if (cmdName === 'rename' && !args) {
        session.setInlineOptions({
          command: 'rename_select',
          title: t(lang, 'rename_select_session'),
          options: session.sessions.map(s => ({
            value: s.value,
            label: s.label,
            active: s.active,
          })),
        });
        return;
      }
      // /language（无参数）→ 弹出语言选择框，不走 web_query
      if (cmdName === 'language' && !args) {
        const current = String(session.status?.ui_language ?? 'zh-CN');
        session.setInlineOptions({
          command: 'language',
          title: t(lang, 'language'),
          options: [
            { value: 'set zh-CN', label: '简体中文', description: '中文界面', active: current === 'zh-CN' },
            { value: 'set en', label: 'English', description: 'English UI', active: current === 'en' },
          ],
        });
        return;
      }
      // /agent create | /agent new → 打开 agent 创建向导
      // /agent（无参数）→ 分支选择器：查看已完成 agent / 创建新 agent
      // /agent <id> → 提交后端查看摘要
      if (cmdName === 'agent') {
        const sub = args.split(/\s+/)[0] ?? '';
        if (sub === 'create' || sub === 'new') {
          session.clearAgentWizardState();
          session.sendAgentWizardInit();
          setShowAgentWizard(true);
          return;
        }
        if (!sub) {
          session.setInlineOptions({
            command: 'agent_branch',
            title: t(lang, 'agentBranchTitle'),
            options: [
              { value: '__view__', label: t(lang, 'agentBranchView') },
              { value: '__create__', label: t(lang, 'agentBranchCreate') },
            ],
          });
          return;
        }
      // /agent <id> → 走命令注册表处理
      session.setBusyTrue();
      session.sendRequest({ type: 'submit_line', line: trimmed });
      return;
      }
      // /goal → 走命令注册表（A 通道，不带 treat_as_text）：后端执行 /goal 命令，
      // drive_goal 轮次正常流式，命令结果以 toast 呈现（创建目标的长任务入口）
      if (cmdName === 'goal') {
        session.setBusyTrue();
        session.sendRequest({ type: 'submit_line', line: trimmed });
        return;
      }
      if (B_COMMANDS.includes(cmdName)) {
        session.setBusyTrue();
        session.sendRequest({
          type: 'web_query',
          command: cmdName,
          args,
          request_id: `q-${Date.now()}`,
        });
        return;
      }
    }

    // 通道 2：所有其他输入（含 /resume、/model 等非 B 类指令）→ 当 user 消息发给 LLM
    // treat_as_text=true 告诉后端跳过命令注册表，直接当文本提交给 LLM
    session.setBusyTrue();
    session.optimisticSubmit(trimmed); // 乐观渲染 user 消息，后端回执按文本去重
    session.sendRequest({ type: 'submit_line', line: trimmed, treat_as_text: true });
    // 用户发送消息时清空持久化回退草稿，避免非欢迎态 rewind 残留影响后续
    setRewindDraft(null);
  };

  /**
   * 处理内联选项选择
   *
   * 当用户从内联选项列表中选择一个选项时触发。
   *
   * @param command - 命令名称
   * @param value - 选中的值
   */
  const handleInlineSelect = useCallback((command: string, value: string) => {
    // /rename 会话选择器 → 弹出文本输入模态框
    if (command === 'rename_select') {
      session.setInlineOptions(null);
      setCustomInputModal({
        prompt: t(lang, 'rename_enter_name'),
        command: 'rename',
        targetSessionId: value,
      });
      return;
    }
    // /agent 分支选择器
    if (command === 'agent_branch') {
      session.setInlineOptions(null);
      if (value === '__view__') {
        session.setBusyTrue();
        session.sendRequest({ type: 'select_command', command: 'agent' });
      } else if (value === '__create__') {
        session.clearAgentWizardState();
        session.sendAgentWizardInit();
        setShowAgentWizard(true);
      }
      return;
    }
    // max-tokens custom 分支：切换到数字输入模态框
    if (command === 'max-tokens' && value === 'custom') {
      setCustomInputModal({
        prompt: t(lang, 'maxTokensCustomPrompt'),
        command: 'max-tokens',
        invalidMessage: t(lang, 'maxTokensInvalid'),
      });
      session.setInlineOptions(null);
      return;
    }
    // context-window __custom__ 分支：切换到数字输入模态框
    if (command === 'context-window' && value === '__custom__') {
      setCustomInputModal({
        prompt: t(lang, 'contextWindowCustomPrompt'),
        command: 'context-window',
        invalidMessage: t(lang, 'contextWindowInvalid'),
      });
      session.setInlineOptions(null);
      return;
    }
    session.setInlineOptions(null);
    // language 走 web_query 通道（前端弹出选择框后提交）
    if (command === 'language') {
      session.sendRequest({
        type: 'web_query',
        command,
        args: value,
        request_id: `q-${Date.now()}`,
      });
    } else {
      // agent 摘要：生成唯一 request_id 并传递给后端，用于精确匹配响应
      if (command === 'agent') {
        agentRequestIdRef.current = `agent-${Date.now()}`;
      } else {
        agentRequestIdRef.current = null;
      }
      session.sendRequest({ type: 'apply_select_command', command, value, request_id: agentRequestIdRef.current ?? undefined });
    }
  }, [session.sendRequest, lang]);

  /**
   * 处理内联选项关闭
   *
   * 当用户关闭内联选项列表时触发。
   */
  const handleInlineClose = useCallback(() => session.setInlineOptions(null), []);

  /**
   * 处理自定义数字输入提交
   *
   * 由 CustomInputModal 触发，将用户输入的数字字符串通过 apply_select_command
   * 通道发回后端（与 rewind/context 等多步指令一致）。
   *
   * @param value - 用户输入的数字字符串
   */
  const handleCustomSubmit = useCallback((value: string) => {
    if (customInputModal) {
      // rename 走 web_query 通道（携带目标 session_id，路由到目标会话所在工作区）
      // 重命名是轻量元数据操作，且目标会话可能是非活跃会话；此处不调用
      // setBusyTrue——该函数固定对活跃会话置 busy，会误把活跃会话钉在运行态，
      // 而后端 web_query_result 只重置目标会话 busy，导致活跃会话永久显示运行中。
      if (customInputModal.command === 'rename') {
        const sid = customInputModal.targetSessionId;
        session.sendRequest({
          type: 'web_query',
          command: 'rename',
          args: sid ? `${sid} ${value}` : value,
          session_id: sid ?? undefined,
          request_id: `q-${Date.now()}`,
        });
        setCustomInputModal(null);
        return;
      }
      session.sendRequest({
        type: 'apply_select_command',
        command: customInputModal.command,
        value,
      });
    }
    setCustomInputModal(null);
  }, [customInputModal, session.sendRequest]);

  /**
   * 处理自定义数字输入取消
   *
   * 关闭自定义输入模态框，不做任何提交。
   */
  const handleCustomCancel = useCallback(() => {
    setCustomInputModal(null);
  }, []);

  // 删除会话弹窗状态（本地控制，数据源来自 session.sessions 主列表）
  const [deleteSelected, setDeleteSelected] = useState<Set<string>>(new Set());
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  // 删除弹窗退出动画阶段：关闭时先播放淡出，动画结束后再真正卸载
  const [deleteModalClosing, setDeleteModalClosing] = useState(false);
  // 单个会话删除确认（侧边栏会话项操作菜单触发）：存储待删除的会话 ID，
  // 用自定义 React 模态替代原生 window.confirm——原生 confirm 在 Electron
  // 桌面壳中会阻塞渲染进程并遗留焦点异常，导致后续输入框无法聚焦
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  // Agent 创建向导显示状态（/agent create 或 /agent new 触发）
  const [showAgentWizard, setShowAgentWizard] = useState(false);

  // 设置配置表单显示状态（首次登录自动弹出，或点击左栏 settings 齿轮手动打开）
  const [showSetupForm, setShowSetupForm] = useState(false);
  // 设置表单初始页（目录按钮"管理目录…"直达目录空间页）
  const [setupInitialTab, setSetupInitialTab] = useState<'settings' | 'workspaces' | 'channels' | 'cron' | 'sandbox'>('settings');
  // 欢迎界面可见（无任何会话内容且非忙碌）：输入框目录按钮常显，可直接选目录新建。
  // 忙碌（首条消息生成）时不算欢迎态，输入框回到底部，避免与"思考中"指示器并存
  const welcomeVisible = session.connected && !session.busy
    && !(session.staticItems.length > 0 || !!session.assistantBuffer || !!session.streamingReasoning
      || session.pendingToolCalls.length > 0 || !!session.modal);

  // 首次登录：后端 ready 且 first_login=true 时自动弹出配置表单（仅触发一次）
  const setupShownRef = useRef(false);
  useEffect(() => {
    if (session.ready && session.firstLogin && !setupShownRef.current) {
      setupShownRef.current = true;
      setShowSetupForm(true);
    }
  }, [session.ready, session.firstLogin]);

  /**
   * 处理关闭 agent 创建向导
   *
   * 关闭表单并清空所有向导状态（工具/模型列表、生成草稿、提交结果等），
   * 避免残留旧数据干扰下次打开。
   */
  const handleCloseAgentWizard = useCallback(() => {
    setShowAgentWizard(false);
    session.clearAgentWizardState();
  }, [session.clearAgentWizardState]);

  /**
   * 处理提交 agent 创建向导表单
   *
   * 直接转发给 session.sendAgentWizardSubmit，等待 agent_wizard_result 事件回填。
   *
   * @param fields - 表单字段（name/description/system_prompt 等，已由表单完成字段名映射）
   * @param scope - 写入范围：'user' 或 'project'
   */
  const handleSubmitAgentWizard = useCallback((fields: Record<string, unknown>, scope: 'user' | 'project') => {
    session.sendAgentWizardSubmit(fields, scope);
  }, [session.sendAgentWizardSubmit]);

  /**
   * 处理设置表单中界面语言变更
   *
   * 通过 WebSocket web_set_setting 即时同步到后端运行时，后端推送
   * web_setting_changed / state_snapshot 后前端 lang 自动更新。
   */
  const handleSetUiLanguage = useCallback((uiLang: 'zh-CN' | 'en-US') => {
    session.sendRequest({ type: 'web_set_setting', setting_key: 'ui_language', setting_value: uiLang });
  }, [session.sendRequest]);

  /**
   * 处理设置表单保存成功
   *
   * 配置已通过即时 REST API 写入 settings.json / channels.json / credentials.json，
   * 前端 state 已同步更新，无需整页刷新。静默关闭表单即可。
   */
  const handleSetupSaved = useCallback(() => {
    session.clearFirstLogin();
    setShowSetupForm(false);
  }, [session.clearFirstLogin]);

  /** 处理关闭设置表单 */
  const handleCloseSetupForm = useCallback(() => {
    setShowSetupForm(false);
  }, []);

  /** 处理停止当前任务（stopping 状态由 hook 管理：line_complete 清除 + 超时兜底） */
  const handleStop = useCallback(() => {
    session.sendStop();
  }, [session.sendStop]);

  /**
   * 处理回退到指定轮次
   *
   * 由 ChatArea 中 user 消息的撤销按钮触发，弹出模式选择弹窗。
   *
   * @param turnsToRewind - 需要回退的轮次数
   */
  const handleRewindToTurn = useCallback((turnsToRewind: number) => {
    setRewindConfirm({ turns: turnsToRewind });
  }, []);

  /**
   * 确认回退 —— 根据用户选择的模式执行 /rewind N mode
   *
   * 通过 submit_line 通道（treat_as_text 缺省=false）直接走命令注册表，
   * 绕过 web_query 的多步弹窗流程。
   *
   * @param mode - 回退模式：code / conversation / both
   */
  const handleConfirmRewind = useCallback((mode: string) => {
    const turns = rewindConfirm?.turns ?? 1;
    setRewindConfirm(null);
    session.setBusyTrue();
    session.sendRequest({ type: 'submit_line', line: `/rewind ${turns} ${mode}` });
  }, [rewindConfirm, session]);

  /**
   * 处理重新生成
   *
   * 找到最后一条 user 消息文本，先 /rewind 1 both 回退一轮，
   * rewind 完成后（busy→false）自动重发 user 消息。
   */
  const handleRegenerate = useCallback(() => {
    const lastUserMsg = [...session.staticItems].reverse().find((i) => i.role === 'user' && !i.is_command);
    if (!lastUserMsg) return;
    pendingRegenerateRef.current = lastUserMsg.text;
    session.setBusyTrue();
    session.sendRequest({ type: 'submit_line', line: '/rewind 1 both' });
  }, [session]);

  // 监听 busy 状态变化：rewind 完成后自动重发 user 消息（重新生成）
  useEffect(() => {
    if (prevBusyRef.current && !session.busy && pendingRegenerateRef.current) {
      const text = pendingRegenerateRef.current;
      pendingRegenerateRef.current = null;
      session.setBusyTrue();
      session.sendRequest({ type: 'submit_line', line: text, treat_as_text: true });
    }
    prevBusyRef.current = session.busy;
  }, [session.busy, session]);

  /** 处理新建会话：直接新建（当前活跃目录）；cwd 指定时在该目录新建。
   *  目录选择由欢迎界面常显的输入框目录按钮承担，不再弹出选择弹窗 */
  const handleNewSession = (cwd?: string) => {
    setRewindDraft(null); // 新建会话清空持久化回退草稿
    setActiveMenu(null); // 切换会话收起所有下拉，避免残留展开态
    session.newSession(cwd);
  };

  /** 切换右栏折叠/展开：展开时按需请求资源快照（缺省 = 活跃会话所在工作区） */
  const toggleRightPanel = useCallback(() => {
    if (rightPanelCollapsed) session.requestResources();
    setRightPanelCollapsed((c) => !c);
  }, [rightPanelCollapsed, session.requestResources]);

  /**
   * 处理选择会话（A 通道，零 suppress）
   *
   * 点击会话项 → 发送 web_restore_session（携带所属目录，跨工作区路由），
   * 前端立即进入 restoring 态显示加载动画，收到 web_restore_completed 后
   * 清除动画并替换转录。不再有 /resume 弹框副作用。
   *
   * @param id - 会话 ID
   * @param cwd - 会话所属工作区目录（可选，恢复请求路由依据）
   */
  const handleSelectSession = useCallback((id: string, cwd?: string) => {
    // 视图已就绪的会话纯本地切换（瞬时，无加载态）；未恢复的会话由
    // hook 自动发送 web_restore_session 并显示加载动画
    setRewindDraft(null); // 切换会话清空持久化回退草稿
    setActiveMenu(null); // 切换会话收起所有下拉，避免残留展开态
    session.activateSession(id, cwd);
  }, [session.activateSession]);

  /** 处理列出会话（A 通道，后端推送 web_sessions） */
  const handleListSessions = useCallback(() => {
    session.sendRequest({ type: 'web_request_sessions' });
  }, [session.sendRequest]);

  /** 处理删除会话：打开删除弹窗（数据源来自 session.sessions 主列表） */
  const handleDeleteSessions = useCallback(() => {
    setDeleteSelected(new Set());
    setDeleteModalClosing(false);
    setDeleteModalOpen(true);
  }, []);

  /**
   * 处理重命名单个会话（侧边栏会话项操作菜单触发）
   *
   * 直接打开文本输入模态框（复用 /rename 通道），携带目标会话 ID，
   * 提交后由 handleCustomSubmit 的 rename 分支发送 web_query。
   *
   * @param sid - 会话 ID
   */
  const handleRenameSession = useCallback((sid: string) => {
    setActiveMenu(null); // 收起输入框/工具栏下拉，避免遮挡
    setCustomInputModal({
      prompt: t(lang, 'rename_enter_name'),
      command: 'rename',
      targetSessionId: sid,
    });
  }, [lang]);

  /**
   * 处理删除单个会话（侧边栏会话项操作菜单触发）
   *
   * 打开自定义确认模态；确认后直接删除目标会话。若删除的是当前会话，
   * 后端原子化新建空会话，与删除弹窗的批量删除路径保持一致。
   * 注意：这里必须用 React 模态而非原生 window.confirm——原生 confirm
   * 在 Electron 桌面壳中会阻塞渲染进程，关闭后遗留焦点异常，导致
   * 跳转欢迎界面后输入框无法聚焦输入（最小化/最大化才恢复）。
   *
   * @param sid - 会话 ID
   */
  const handleDeleteOneSession = useCallback((sid: string) => {
    setDeleteConfirm(sid);
  }, []);

  /** 确认删除单个会话（自定义确认模态的确定按钮） */
  const handleConfirmDeleteOne = useCallback(() => {
    if (!deleteConfirm) return;
    setDeleteConfirm(null);
    // 运行中的会话后端会跳过删除（保持任务进行），若本地先行移除会导致
    // 会话"短暂消失又出现"，用户误以为删除失败——此处直接提示并中止
    if (session.sessions.some((s) => s.value === deleteConfirm && s.busy)) {
      showToast(t(lang, 'delete_session_busy'), 'info');
      return;
    }
    setRewindDraft(null); // 删除会话（可能新建空会话）清空持久化回退草稿
    session.deleteSessions([deleteConfirm]);
  }, [deleteConfirm, session.sessions, session.deleteSessions, showToast, lang]);

  /** 取消删除单个会话（自定义确认模态的取消/遮罩点击） */
  const handleCancelDeleteOne = useCallback(() => {
    setDeleteConfirm(null);
  }, []);

  // 删除确认模态键盘支持：Escape 取消（对齐 CustomInputModal 的键盘交互）
  useEffect(() => {
    if (!deleteConfirm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleCancelDeleteOne();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [deleteConfirm, handleCancelDeleteOne]);

  /** 触发删除弹窗退出动画（真正卸载由 handleDeleteModalAnimationEnd 完成） */
  const requestDeleteModalClose = useCallback(() => {
    setDeleteModalClosing(true);
  }, []);

  /** 退出动画结束：真正卸载弹窗并清空选中状态（仅响应弹窗自身动画） */
  const handleDeleteModalAnimationEnd = useCallback((e: React.AnimationEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return; // 忽略子元素冒泡的动画事件
    if (!deleteModalClosing) return;
    setDeleteModalOpen(false);
    setDeleteModalClosing(false);
    setDeleteSelected(new Set());
  }, [deleteModalClosing]);

  /**
   * 处理确认删除
   *
   * 删除所有选中的会话。删除全部时限定在当前活跃工作区目录
   * （多目录空间下互不影响）。
   */
  const handleConfirmDelete = useCallback(() => {
    const ids = Array.from(deleteSelected);
    if (ids.length > 0) {
      // 直接发送删除请求；若包含当前会话，后端会原子化地新建空会话，
      // 避免前端"先删后建"两阶段逻辑的竞态。
      setRewindDraft(null); // 删除会话（可能新建空会话）清空持久化回退草稿
      session.deleteSessions(ids);
    }
    requestDeleteModalClose();
  }, [deleteSelected, session.deleteSessions, requestDeleteModalClose]);

  /**
   * 处理关闭删除模态框
   *
   * 触发退出动画后关闭删除会话弹窗并清除选中状态。
   */
  const handleCloseDeleteModal = useCallback(() => {
    requestDeleteModalClose();
  }, [requestDeleteModalClose]);

  /**
   * 切换删除项选中状态
   *
   * @param v - 会话 ID
   */
  const toggleDeleteItem = useCallback((v: string) => {
    setDeleteSelected((prev) => { const n = new Set(prev); n.has(v) ? n.delete(v) : n.add(v); return n; });
  }, []);

  /** 待删除的普通会话列表（来自主会话列表 session.sessions） */
  const regularSessions = session.sessions;
  /** 总是提供"删除全部"入口 */
  const hasAllOption = session.sessions.length > 0;

  /** 目录 basename（删除弹窗分组显示用，与 Sidebar 分组一致） */
  const deleteGroupName = (path: string): string => {
    const parts = (path || '').split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || path || t(lang, 'workspace_unknown');
  };

  /** 删除弹窗按目录分组（保持会话列表顺序，同目录会话归组） */
  const deleteGroups = useMemo(() => {
    const byCwd = new Map<string, typeof regularSessions>();
    for (const s of regularSessions) {
      const key = s.cwd || '';
      const bucket = byCwd.get(key);
      if (bucket) bucket.push(s);
      else byCwd.set(key, [s]);
    }
    return Array.from(byCwd.entries()).map(([cwd, items]) => ({
      cwd,
      name: deleteGroupName(cwd),
      sessions: items,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regularSessions, lang]);

  /** 整组删除：删除该目录下的全部会话 */
  const handleDeleteGroup = useCallback((group: { name: string; sessions: { value: string }[] }) => {
    if (!window.confirm(t(lang, 'delete_group_confirm')
      .replace('{name}', group.name)
      .replace('{count}', String(group.sessions.length)))) {
      return;
    }
    setRewindDraft(null); // 整组删除（可能删除当前会话并新建空会话）清空持久化回退草稿
    session.deleteSessions(group.sessions.map((s) => s.value));
    requestDeleteModalClose();
  }, [session.deleteSessions, lang, requestDeleteModalClose]);

  /**
   * 处理权限响应
   *
   * @param requestId - 请求 ID
   * @param allowed - 是否允许
   * @param sessionAllow - 是否允许本会话内（不持久化）
   * @param toolName - 工具名称
   */
  const handlePermissionResponse = (requestId: string, allowed: boolean, sessionAllow: boolean, toolName: string) => {
    session.sendRequest({ type: 'permission_response', request_id: requestId, allowed, session_allow: sessionAllow, tool_name: toolName });
    session.clearModal();
  };

  /**
   * 处理问答响应
   *
   * @param requestId - 请求 ID
   * @param answer - 用户回答
   */
  const handleQuestionResponse = (requestId: string, answer: string) => {
    session.sendRequest({ type: 'question_response', request_id: requestId, answer });
    session.clearModal();
  };

  /** 输入框 + 工具栏合并为单卡片（欢迎态注入标题下方，非欢迎态置于底部） */
  const composer = (
    <div className="glass-surface rounded-3xl focus-within:shadow-glow">
      <PromptInput ref={promptInputRef} lang={lang} busy={session.busy} connected={session.connected}
        hasActiveTasks={session.tasks.some(
          (t) =>
            (t.status === 'in_progress' || t.status === 'pending') &&
            t.metadata?.owner_session_id === session.activeSessionId,
        )}
        commands={session.commands} onSubmit={handleSubmit} onStop={handleStop} stopping={session.stopping}
        inlineOptions={session.inlineOptions} onInlineSelect={handleInlineSelect} onInlineClose={handleInlineClose}
        workspaces={session.workspaces} activeCwd={session.activeWorkspaceCwd}
        welcomeVisible={welcomeVisible}
        onPickWorkspace={(cwd) => handleNewSession(cwd)}
        onAddWorkspace={(path) => session.addWorkspace(path)}
        onManageWorkspaces={() => { setSetupInitialTab('workspaces'); setShowSetupForm(true); }}
        initialDraft={rewindDraft ?? undefined}
        onConsumeInitialDraft={() => setRewindDraft(null)}
        activeMenu={activeMenu} onMenuOpen={setActiveMenu}>
        <Toolbar lang={lang} status={session.status}
          modelOptions={session.modelOptions}
          onSetSetting={(key, value) => {
            if (key === 'model') session.setModelSwitching(true);
            session.sendRequest({ type: 'web_set_setting', setting_key: key, setting_value: value });
          }}
          onRequestModels={() => session.sendRequest({ type: 'web_request_models' })}
          modelSwitching={session.modelSwitching}
          activeMenu={activeMenu} onMenuOpen={setActiveMenu} />
      </PromptInput>
    </div>
  );

  return (
    <div className="flex flex-col h-screen">
      {/* Electron 桌面壳自定义顶部栏（浏览器端返回 null） */}
      <TitleBar lang={lang} />
      <div className="flex flex-1 min-h-0">
      <Sidebar lang={lang} connected={session.connected} sessions={session.sessions}
        workspaces={session.workspaces} activeWorkspaceCwd={session.activeWorkspaceCwd}
        onNewSession={handleNewSession} onSelectSession={handleSelectSession}
        onListSessions={handleListSessions}
        onDeleteSessions={handleDeleteSessions}
        onRenameSession={handleRenameSession}
        onDeleteSession={handleDeleteOneSession}
        collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        width={sidebarWidth} restoringSessionId={session.restoringSessionId}
        onOpenSettings={() => { setSetupInitialTab('settings'); setShowSetupForm(true); }} />
      {!sidebarCollapsed && (
        <div className="w-1 cursor-col-resize hover:bg-primary/20 active:bg-primary/30 transition-colors shrink-0"
          onMouseDown={(e) => handleResizeStart('left', e)} />
      )}
      <div className="flex flex-col flex-1 min-w-0 min-h-0 relative">
        <ChatArea lang={lang} staticItems={session.staticItems} assistantBuffer={session.assistantBuffer}
          streamingReasoning={session.streamingReasoning} pendingToolCalls={session.pendingToolCalls}
          reasoningStreaming={session.reasoningStreaming}
          busy={session.busy} connected={session.connected}
          modal={session.modal} onPermissionResponse={handlePermissionResponse}
          onQuestionResponse={handleQuestionResponse} restoringSessionId={session.restoringSessionId}
          onRewindToTurn={handleRewindToTurn} onRegenerate={handleRegenerate}>
          {/* 欢迎态：输入框 + 工具栏注入到标题/副标题下方（ChatArea 内渲染） */}
          {welcomeVisible && composer}
        </ChatArea>
        {/* 非欢迎态或会话恢复中：输入框 + 工具栏恢复到底部；宽度比主聊天区每边宽 17px（--composer-card-max-width）。
            恢复中 ChatArea 提前返回加载卡不渲染欢迎态 composer，故不会重复渲染。
            GoalBar 停靠在输入框卡片上方 */}
        {(!welcomeVisible || session.restoringSessionId) && (
          <div className="mx-auto max-w-[var(--composer-card-max-width)] w-full min-w-0 px-6 md:px-10 lg:px-16 pt-0 pb-4 shrink-0 flex flex-col gap-1.5">
            <GoalBar lang={lang}
              goal={(session.status?.goal as GoalStatus | null | undefined) ?? null}
              actionError={session.goalActionError}
              onEdit={(objective) => session.sendGoalAction('edit', objective)}
              onPause={() => session.sendGoalAction('pause')}
              onResume={() => session.sendGoalAction('resume')}
              onClear={() => session.sendGoalAction('clear')}
              onDismissError={session.clearGoalActionError} />
            {composer}
          </div>
        )}
        {/* 顶部右侧按钮组（展开右栏/主题/上下文占比）：仅右栏折叠态且非欢迎/非恢复中显示；
            展开后不再显示，控制回归右栏面板头部；right=[20px] 避开内移后的主视图滚动条 */}
        {rightPanelCollapsed && !welcomeVisible && !session.restoringSessionId && (
          <RightPanelControls lang={lang} status={session.status} onToggle={toggleRightPanel} />
        )}
        {/* 顶部左侧按钮组（Sidebar 折叠态承载）：侧栏折叠即显示（欢迎态也可用），
            不与右栏显隐条件绑定 */}
        {sidebarCollapsed && (
          <SidebarControls lang={lang} connected={session.connected}
            onExpand={() => setSidebarCollapsed(false)}
            onNewSession={() => handleNewSession()}
            onDeleteSessions={handleDeleteSessions}
            onOpenSettings={() => { setSetupInitialTab('settings'); setShowSetupForm(true); }} />
        )}
      </div>
      {!rightPanelCollapsed && !welcomeVisible && (
        <div className="w-1 cursor-col-resize hover:bg-primary/20 active:bg-primary/30 transition-colors shrink-0"
          onMouseDown={(e) => handleResizeStart('right', e)} />
      )}
      {!rightPanelCollapsed && !welcomeVisible && (
      <RightPanel lang={lang} status={session.status}
        collapsed={rightPanelCollapsed} onToggle={toggleRightPanel}
        onRefreshResources={() => session.requestResources()}
        todoItems={session.todoItems} skills={session.skills} plugins={session.plugins}
        rules={session.rules} mcpServers={session.mcpServers}
        width={rightPanelWidth} />
      )}
      </div>

      {/* 删除会话弹窗（仅 sidebar 触发；按目录分组查看，支持整组删除） */}
      {deleteModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className={`absolute inset-0 bg-black/35 backdrop-blur-md ${deleteModalClosing ? 'animate-fade-out' : 'animate-fade'}`} onClick={handleCloseDeleteModal} />
          <div
            onAnimationEnd={handleDeleteModalAnimationEnd}
            className={`relative bg-surface-card rounded-2xl border border-border-light shadow-card w-[460px] max-h-[70vh] flex flex-col ${deleteModalClosing ? 'animate-scale-out' : 'animate-scale-in'} modal-origin-center`}
          >
            <div className="px-6 py-4 border-b border-border-light">
              <h3 className="text-lg font-semibold text-content-primary">{t(lang, 'delete_session')}</h3>
            </div>
            <div className="flex-1 overflow-y-auto py-2">
              {regularSessions.length === 0 ? (
                <div className="px-6 py-8 text-center text-sm text-content-disabled">{t(lang, 'no_sessions')}</div>
              ) : deleteGroups.map((group, gi) => (
                <DeleteGroupSection
                  key={group.cwd || `__unknown_${gi}`}
                  group={group}
                  deleteSelected={deleteSelected}
                  onToggleItem={toggleDeleteItem}
                  onDeleteGroup={handleDeleteGroup}
                  lang={lang}
                />
              ))}
            </div>
            <div className="px-6 py-4 border-t border-border-light flex items-center justify-between">
              <div>{hasAllOption && (
                <button onClick={() => {
                  // 删除全部限定在当前活跃工作区目录（多目录空间下互不影响）；
                  // 后端会原子化地新建空会话，避免两阶段竞态
                  session.deleteSessions([], true, session.activeWorkspaceCwd ?? undefined);
                  requestDeleteModalClose();
                }} className="danger-action px-4 py-2 text-sm text-danger rounded-lg cursor-pointer">{t(lang, 'delete_all_workspace')}</button>
              )}</div>
              <div className="flex gap-2">
                <button onClick={handleCloseDeleteModal} className="px-4 py-2 text-sm text-content-secondary glass-option-hover rounded-lg transition-colors cursor-pointer border border-white/40">{t(lang, 'cancel')}</button>
                <button onClick={handleConfirmDelete} disabled={deleteSelected.size === 0}
                  className="px-4 py-2 text-sm text-white bg-danger hover:bg-danger-hover rounded-lg transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
                  {t(lang, 'confirm_delete')} ({deleteSelected.size})
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 回退确认弹窗（选择回退范围） */}
      {rewindConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/35 backdrop-blur-md animate-fade-in" onClick={() => setRewindConfirm(null)} />
          <div className="relative bg-surface-card rounded-2xl border border-border-light shadow-card w-[380px] flex flex-col animate-scale-in modal-origin-center">
            <div className="px-6 py-4 border-b border-border-light">
              <h3 className="text-lg font-semibold text-content-primary">{t(lang, 'rewind_confirm_title')}</h3>
            </div>
            <div className="py-2 px-1">
              {([
                { mode: 'both', label: t(lang, 'rewind_both'), desc: t(lang, 'rewind_both_desc') },
                { mode: 'conversation', label: t(lang, 'rewind_conversation'), desc: t(lang, 'rewind_conversation_desc') },
              ] as const).map((opt) => (
                <button
                  key={opt.mode}
                  onClick={() => handleConfirmRewind(opt.mode)}
                  className="w-full text-left px-6 py-3 cursor-pointer glass-option-hover transition-colors rounded-lg flex items-center justify-between group"
                >
                  <div>
                    <div className="text-sm font-medium text-content-primary">{opt.label}</div>
                    <div className="text-xs text-content-disabled mt-0.5">{opt.desc}</div>
                  </div>
                  <svg className="w-4 h-4 text-content-disabled opacity-0 group-hover:opacity-100 transition-opacity" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M6 3l5 5-5 5" />
                  </svg>
                </button>
              ))}
            </div>
            <div className="px-6 py-4 border-t border-border-light flex justify-end">
              <button onClick={() => setRewindConfirm(null)} className="px-4 py-2 text-sm text-content-secondary glass-option-hover rounded-lg transition-colors cursor-pointer border border-white/40">
                {t(lang, 'cancel')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 单个会话删除确认（侧边栏会话项操作菜单触发）：
          自定义 React 模态替代原生 window.confirm，避免 Electron 桌面壳中
          原生对话框关闭后遗留的焦点异常导致后续输入框无法聚焦 */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/35 backdrop-blur-md animate-fade-in" onClick={handleCancelDeleteOne} />
          <div className="relative bg-surface-card rounded-2xl border border-border-light shadow-card w-[380px] flex flex-col animate-scale-in modal-origin-center">
            <div className="px-6 py-4 border-b border-border-light">
              <h3 className="text-lg font-semibold text-content-primary">{t(lang, 'delete_session')}</h3>
            </div>
            <div className="px-6 py-4">
              <p className="text-sm text-content-secondary leading-relaxed">{t(lang, 'confirm_delete_session')}</p>
            </div>
            <div className="px-6 py-4 border-t border-border-light flex justify-end gap-2">
              <button onClick={handleCancelDeleteOne} className="px-4 py-2 text-sm text-content-secondary glass-option-hover rounded-lg transition-colors cursor-pointer border border-white/40">
                {t(lang, 'cancel')}
              </button>
              <button onClick={handleConfirmDeleteOne} autoFocus
                className="px-4 py-2 text-sm text-white bg-danger hover:bg-danger-hover rounded-lg transition-colors cursor-pointer">
                {t(lang, 'confirm_delete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 自定义输入模态框（/max-tokens custom、/context-window __custom__、/rename 分支） */}
      {customInputModal && (
        <CustomInputModal
          lang={lang}
          prompt={customInputModal.prompt}
          invalidMessage={customInputModal.invalidMessage}
          mode={customInputModal.command === 'rename' ? 'text' : 'numeric'}
          onSubmit={handleCustomSubmit}
          onCancel={handleCustomCancel}
        />
      )}

      {/* Agent 摘要浮动卡片（查看已完成 agent 时展示） */}
      {agentResult != null && (
        <div className="fixed bottom-24 right-6 z-40 w-[420px] max-w-[calc(100vw-3rem)] animate-fade-in-up">
          <div className="glass-surface rounded-2xl overflow-hidden flex flex-col shadow-glow">
            <div className="px-4 py-3 flex items-center justify-between border-b border-white/30">
              <div className="text-sm font-semibold text-content-primary">{t(lang, 'agentResultCardTitle')}</div>
              <button
                onClick={() => setAgentResult(null)}
                className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-content-disabled hover:text-content-primary glass-option-hover transition-colors cursor-pointer"
              >
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M2 2l8 8M10 2l-8 8" />
                </svg>
              </button>
            </div>
            <div className="px-4 py-3 max-h-[60vh] overflow-y-auto">
              <div className="text-sm leading-relaxed whitespace-pre-wrap break-words text-content-primary select-text">
                {agentResult}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Agent 创建向导（/agent create 或 /agent new 触发） */}
      {showAgentWizard && (
        <AgentWizardForm
          lang={lang}
          tools={session.agentWizardTools}
          models={session.agentWizardModels}
          generated={session.agentGenerated}
          generateLoading={session.agentGenerateLoading}
          generateError={session.agentGenerateError}
          result={session.agentWizardResult}
          onInit={session.sendAgentWizardInit}
          onGenerate={session.sendAgentGenerateRequest}
          onSubmit={handleSubmitAgentWizard}
          onClose={handleCloseAgentWizard}
        />
      )}

      {/* 设置配置表单（首次登录自动弹出，或点击左栏 settings 齿轮触发） */}
      {showSetupForm && (
        <SetupForm
          lang={lang}
          firstLogin={session.firstLogin}
          initialTab={setupInitialTab}
          workspaces={session.workspaces}
          onAddWorkspace={session.addWorkspace}
          onRemoveWorkspace={session.removeWorkspace}
          onRequestWorkspaces={session.requestWorkspaces}
          onSetDefaultWorkspace={(path) => {
            // 默认目录 = settings.working_directory（REST PATCH，语义保留）
            settingsApi.updateWorkingDirectory(path)
              .then(() => session.requestWorkspaces())
              .catch(() => undefined);
          }}
          onSetUiLanguage={handleSetUiLanguage}
          onSaved={handleSetupSaved}
          onClose={handleCloseSetupForm}
        />
      )}

      {/* Toast 通知 */}
      {toastMessage && (
        <div
          key={toastKeyRef.current}
          className={`fixed bottom-20 right-6 z-50 ${toastExiting ? 'animate-toast-out' : 'animate-toast-in'}`}
          onMouseEnter={handleToastMouseEnter} onMouseLeave={handleToastMouseLeave}
        >
          <div className="glass-surface border border-black/10 rounded-2xl max-w-sm overflow-hidden">
            <div className="flex items-start gap-3 px-4 py-3">
              <pre className="text-sm text-content-primary whitespace-pre-wrap font-mono leading-relaxed flex-1 max-h-40 overflow-y-auto">{toastMessage.text}</pre>
              <button onClick={closeToast}
                className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-content-disabled hover:text-content-primary glass-option-hover transition-colors cursor-pointer">
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M2 2l8 8M10 2l-8 8" /></svg>
              </button>
            </div>
            <div className="h-0.5 bg-black/10">
              <div
                key={toastKeyRef.current}
                className={`h-full animate-progress-shrink ${
                  toastMessage.type === 'error' ? 'bg-danger/80' : toastMessage.type === 'success' ? 'bg-success/80' : 'bg-primary/80'
                }`}
                style={{ animationDuration: `${TOAST_DURATION}ms` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* 首次启动连接后端的全屏遮罩层（替代原顶部"正在连接..."横条） */}
      {!session.connected && <ConnectingOverlay lang={lang} />}
      {/* 应用内图片预览（Lightbox）：点击 markdown 图片/图片链接时打开 */}
      <ImagePreview lang={lang} />
    </div>
  );
}

/** 删除弹窗中的单个目录分组（可展开/关闭；会话项保持缩进；右侧整组删除） */
function DeleteGroupSection({ group, deleteSelected, onToggleItem, onDeleteGroup, lang }: {
  group: { cwd: string; name: string; sessions: { value: string; label: string }[] };
  deleteSelected: Set<string>;
  onToggleItem: (v: string) => void;
  onDeleteGroup: (g: { cwd: string; name: string; sessions: { value: string; label: string }[] }) => void;
  lang: UiLanguage;
}) {
  const [open, setOpen] = useState(true);

  return (
    <div className="mb-1">
      {/* 组头：点击切换展开/关闭；右侧整组删除按钮独立（不触发展开）。
          卡片尺寸（py-2）与会话项一致；文件图标起点 36px（px-4 + chevron 12px + gap-2 8px） */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((v) => !v); } }}
        className="flex items-center gap-2 px-4 py-2 cursor-pointer glass-option-hover transition-colors rounded-lg"
        title={group.cwd}
      >
        {/* 展开指示（旋转） */}
        <svg className={`w-3 h-3 shrink-0 text-content-secondary transition-transform duration-150 ${open ? 'rotate-90' : ''}`} viewBox="0 0 16 16" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 3l5 5-5 5" />
        </svg>
        {/* 文件夹双图标（展开=打开文件夹、折叠=关闭文件夹，与侧栏组头一致） */}
        {open ? <FolderOpenIcon className="w-3.5 h-3.5 shrink-0 text-content-secondary" /> : <FolderClosedIcon className="w-3.5 h-3.5 shrink-0 text-content-secondary" />}
        <span className="text-sm text-content-secondary truncate flex-1">{group.name}</span>
        <span className="text-[10px] text-content-disabled tabular-nums shrink-0">{group.sessions.length}</span>
        <button
          onClick={(e) => { e.stopPropagation(); onDeleteGroup(group); }}
          className="text-[11px] text-danger hover:bg-danger/10 rounded-md px-2 py-0.5 transition-colors cursor-pointer shrink-0"
        >
          {t(lang, 'delete_group')}
        </button>
      </div>
      {/* 组内会话 checkbox 列表：外层缩进使 checkbox 与组头文件夹图标同列，
          悬浮背景覆盖选中方框与缩进区 */}
      {open && (
        <div className="space-y-0.5 px-2 pl-4">
          {group.sessions.map((s) => (
            <label key={s.value} className="flex items-center gap-3 pl-5 pr-3 py-2 cursor-pointer glass-option-hover transition-colors rounded-lg">
              <input type="checkbox" checked={deleteSelected.has(s.value)} onChange={() => onToggleItem(s.value)} className="w-4 h-4 rounded accent-danger" />
              <span className="text-sm text-content-secondary truncate flex-1">{s.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
