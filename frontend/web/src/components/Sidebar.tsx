/**
 * @fileoverview 侧边栏组件
 *
 * Web 前端的侧边栏组件，支持：
 * - 折叠/展开功能
 * - 新建会话（在当前活跃目录创建；输入框目录按钮可选其他目录）
 * - 会话列表按目录空间分组显示（多目录并发）
 * - 运行中/等待输入会话的视觉区分
 * - 删除会话功能
 * - 会话项操作菜单（重命名/删除单个会话，三个点按钮触发）
 * - 连接状态显示
 * - 底部设置入口（含当前工作区指示）
 *
 * @module Sidebar
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { t, type UiLanguage } from '../i18n';
import type { WebWorkspaceItem } from '../types/protocol';
import { FolderClosedIcon, FolderOpenIcon, NewChatIcon } from './icons';

/**
 * 会话列表项（供侧边栏渲染的最小结构）
 */
export interface SidebarSession {
  /** 会话 ID */
  value: string;
  /** 会话显示标签 */
  label: string;
  /** 会话所属工作区目录 */
  cwd: string;
  /** 是否正在运行任务（彩色流动边框） */
  busy: boolean;
  /** 会话阶段：idle/thinking/tool_executing/awaiting_input */
  phase: string;
  /** 是否为活跃会话（左侧渐变指示条） */
  active: boolean;
  /** 创建时间戳（秒，来自后端 created_at；用于仅显示日期） */
  createdAt: number;
  /** 轮次数量（来自后端 turn_count） */
  turnCount: number;
  /** 会话摘要（自动生成，标题缺失时兜底展示） */
  summary: string;
  /** 自定义会话名称（存在时优先展示） */
  title: string;
}

/** 目录分组渲染结构 */
interface WorkspaceGroup {
  /** 目录绝对路径 */
  cwd: string;
  /** 显示名（目录 basename） */
  name: string;
  /** 是否为默认工作区 */
  isDefault: boolean;
  /** 该目录下的会话（按活跃度排序） */
  sessions: SidebarSession[];
  /** 组内是否有运行中的会话 */
  busy: boolean;
}

/**
 * Sidebar 组件属性接口
 */
interface SidebarProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 是否已连接 */
  connected: boolean;
  /** 会话列表（含运行状态与所属目录） */
  sessions: SidebarSession[];
  /** 注册的工作区列表（默认目录首位） */
  workspaces: WebWorkspaceItem[];
  /** 活跃会话所属工作区目录（null 表示未知，回退默认目录） */
  activeWorkspaceCwd: string | null;
  /** 新建会话回调（在指定目录创建；缺省 = 当前活跃目录） */
  onNewSession: (cwd?: string) => void;
  /** 选择会话回调（携带所属目录供恢复请求路由） */
  onSelectSession: (sessionId: string, cwd?: string) => void;
  /** 列出会话回调 */
  onListSessions: () => void;
  /** 删除会话回调 */
  onDeleteSessions: () => void;
  /** 重命名单个会话回调（会话项操作菜单触发，参数为会话 ID） */
  onRenameSession: (sessionId: string) => void;
  /** 删除单个会话回调（会话项操作菜单触发，参数为会话 ID） */
  onDeleteSession: (sessionId: string) => void;
  /** 是否折叠 */
  collapsed: boolean;
  /** 折叠/展开切换回调 */
  onToggle: () => void;
  /** 侧边栏宽度（可选，默认 280） */
  width?: number;
  /** 正在恢复的会话 ID（可选，用于在对应会话项显示加载 spinner） */
  restoringSessionId?: string | null;
  /** 打开设置配置表单回调（点击底部 settings 齿轮图标触发） */
  onOpenSettings: () => void;
}

/** 目录 basename 提取（路径分隔符兼容 Windows / POSIX） */
function basenameOf(path: string): string {
  if (!path) return '';
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/**
 * 会话创建时间短格式：始终保持 YYYY/M/D（空间充裕，直接带上年份）。
 * createdAt 为秒级时间戳。
 */
function formatShortDate(createdAtSeconds: number): string {
  if (!createdAtSeconds) return '';
  const d = new Date(createdAtSeconds * 1000);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * 会话创建时间的具体时刻（HH:MM），紧随日期之后展示；createdAt 为秒级时间戳。
 */
function formatClock(createdAtSeconds: number): string {
  if (!createdAtSeconds) return '';
  const d = new Date(createdAtSeconds * 1000);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

/**
 * 会话列表项（带活跃指示条、运行状态视觉与操作菜单）
 *
 * 每个会话项右侧带"三个点"操作按钮：点击弹出重命名/删除菜单。
 * 浮层通过 Portal 渲染到 body 并固定定位，避免被滚动容器裁剪。
 */
function SessionItem({ session, isRestoring, isActive, lang, onSelect, onRename, onDelete }: {
  session: SidebarSession;
  isRestoring: boolean;
  isActive: boolean;
  lang: UiLanguage;
  onSelect: (id: string, cwd?: string) => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  // 操作菜单开关与浮层锚点位置（基于三个点按钮的视口坐标）
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const dotsRef = useRef<HTMLButtonElement>(null);
  // 会话项根节点引用：用于判定 scroll 事件是否来自会话列表自身滚动，
  // 避免聊天区流式跟随滚动等外部滚动误关闭操作菜单
  const itemRef = useRef<HTMLDivElement>(null);

  /** 操作菜单预估高度（重命名 + 删除两项，含 padding），用于视口底部翻转判断 */
  const MENU_EST_HEIGHT = 90;

  /** 打开操作菜单：以三个点按钮为锚点计算浮层位置；靠近视口底部时向上翻转 */
  const openMenu = () => {
    const el = dotsRef.current;
    if (el) {
      const rect = el.getBoundingClientRect();
      const below = rect.bottom + 4;
      const top = below + MENU_EST_HEIGHT > window.innerHeight
        ? Math.max(8, rect.top - MENU_EST_HEIGHT - 4)
        : below;
      setMenuPos({ top, left: rect.right });
    }
    setMenuOpen(true);
  };

  // 菜单打开期间：滚动会话列表关闭（固定定位浮层不跟随滚动）、
  // Escape 关闭、窗口 resize 关闭（固定定位位置失效）
  // 注意：仅会话列表自身的滚动会关闭菜单；聊天区等其他区域的滚动
  // （如流式输出自动跟随到底部）不影响菜单位置，不应关闭——
  // 否则流式阶段点击三点后菜单会被聊天区滚动事件立即关闭，表现为不可点击
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    // 仅当滚动发生在会话列表容器（即包含本会话项的滚动祖先）时才关闭菜单；
    // 其他区域滚动事件的 target 不会包含本会话项节点，予以忽略
    const onScroll = (e: Event) => {
      const target = e.target as Element | null;
      if (target && itemRef.current && target.contains(itemRef.current)) {
        close();
      }
    };
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('scroll', onScroll, true);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', close);
    };
  }, [menuOpen]);

  // 状态样式：
  // - 所有会话项预留 1px 透明边框（状态切换无布局跳动）
  // - 活跃（active）：主色淡背景 + 主色细边框（不再加重字重）
  // - 运行中（busy）：细边框 + 一小段主色系光束沿边框流动（BorderBeam 风格）
  // - 无图标：缩进表达所属目录关系；pr-8 预留右侧操作按钮位，悬停显隐无跳动
  const className = [
    'session-item relative w-full text-left pl-9 pr-8 py-2 rounded-lg text-sm transition-colors cursor-pointer flex items-center gap-2 animate-fade',
    isActive
      ? 'session-active text-content-primary'
      : 'text-content-secondary glass-option-hover hover:text-content-primary',
    session.busy ? 'session-running' : '',
  ].filter(Boolean).join(' ');

  // 显示标题：优先自定义名称，其次会话摘要，兜底原标签（含具体时间，通常不触发）
  const displayTitle = session.title || session.summary || session.label;
  // 元信息：日期 + 具体时间 + 轮数；dateTimeStr 合并日期与时刻（同一时间源，同空同现）
  const dateStr = formatShortDate(session.createdAt);
  const timeStr = formatClock(session.createdAt);
  const dateTimeStr = [dateStr, timeStr].filter(Boolean).join(' ');
  const roundsStr = t(lang, 'session_rounds').replace('{count}', String(session.turnCount));

  return (
    <div className="relative group" ref={itemRef}>
      <button
        onClick={() => onSelect(session.value, session.cwd || undefined)}
        className={className}
        // 悬浮提示保留原有完整信息（含具体时间），与可见的"仅日期"互为补充
        title={session.label}
      >
        {/* 运行中/恢复中 spinner：绝对定位占用缩进空白，文字位置保持不动。
            运行中（busy）复用恢复中的动画样式 */}
        {(isRestoring || session.busy) && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 w-4 flex items-center justify-center">
            <svg className="animate-spin w-3.5 h-3.5 text-primary" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </span>
        )}
        {/* 两行布局：首行标题截断占据剩余宽度；次行元信息（日期 时间 轮数）不截断固定靠右，
            不与标题争抢宽度，右侧 pr-8 为操作按钮预留位 */}
        <span className="flex-1 min-w-0 flex flex-col leading-tight">
          <span className="truncate mt-px">{displayTitle}</span>
          <span className="flex items-center gap-2 text-[11px] text-content-disabled mt-0.5">
            {dateTimeStr && <span className="shrink-0 tabular-nums">{dateTimeStr}</span>}
            <span className="shrink-0 tabular-nums">{roundsStr}</span>
          </span>
        </span>
      </button>
      {/* 会话操作按钮（三个点）：默认隐藏，悬停会话项时显现（与目录项图标一致）；
          点击弹出重命名/删除菜单；独立于会话选择，阻止冒泡 */}
      <button
        ref={dotsRef}
        onClick={(e) => {
          e.stopPropagation();
          if (menuOpen) setMenuOpen(false);
          else openMenu();
        }}
        aria-label={t(lang, 'session_actions')}
        title={t(lang, 'session_actions')}
        className={`absolute right-1.5 top-1/2 -translate-y-1/2 w-6 h-6 flex items-center justify-center rounded-md text-content-disabled hover:text-content-primary glass-option-hover transition cursor-pointer ${menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <circle cx="8" cy="3.5" r="1.5" />
          <circle cx="8" cy="8" r="1.5" />
          <circle cx="8" cy="12.5" r="1.5" />
        </svg>
      </button>
      {menuOpen && menuPos && createPortal(
        <>
          {/* 点击遮罩关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
          <div
            className="fixed z-50 bg-surface-card-alt border border-border-medium rounded-xl overflow-hidden min-w-[150px] p-1 animate-fade shadow-card dropdown-panel"
            style={{ top: menuPos.top, left: menuPos.left - 150 }}
          >
            <button
              onClick={() => { setMenuOpen(false); onRename(session.value); }}
              className="w-full text-left px-4 py-2 border border-transparent hover:border-border-light text-sm text-content-primary glass-option-hover hover:text-content-primary transition-colors cursor-pointer flex items-center gap-2.5"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
              </svg>
              {t(lang, 'rename_session')}
            </button>
            <button
              onClick={() => { setMenuOpen(false); onDelete(session.value); }}
              className="w-full text-left px-4 py-2 border border-transparent hover:border-border-light text-sm text-danger glass-option-hover transition-colors cursor-pointer flex items-center gap-2.5"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                <line x1="10" y1="11" x2="10" y2="17" />
                <line x1="14" y1="11" x2="14" y2="17" />
              </svg>
              {t(lang, 'delete_session')}
            </button>
          </div>
        </>,
        document.body,
      )}
    </div>
  );
}

/** 单个目录分组（组头 + 组内会话列表，支持折叠与前 5 条展开） */
function WorkspaceGroupSection({ group, lang, collapsedByDefault, restoringSessionId, onSelectSession, onNewSession, onRenameSession, onDeleteSession }: {
  group: WorkspaceGroup;
  lang: UiLanguage;
  collapsedByDefault: boolean;
  restoringSessionId?: string | null;
  onSelectSession: (id: string, cwd?: string) => void;
  onNewSession: (cwd?: string) => void;
  onRenameSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(!collapsedByDefault);
  const [listExpanded, setListExpanded] = useState(false);

  // 折叠默认值变化时（如活跃目录切换导致展开）同步本地状态
  useEffect(() => {
    if (!collapsedByDefault) setExpanded(true);
  }, [collapsedByDefault]);

  const visible = listExpanded ? group.sessions : group.sessions.slice(0, 5);

  return (
    <div>
      {/* 组头：目录名（无选中高光、无边框）；文件夹图标随展开/折叠切换，
          悬停时淡出并淡入三角指示器（指向右，展开时旋转指向下）；
          右侧新建会话指示器（点击即在该目录新建） */}
      <div
        className="w-full flex items-center rounded-lg transition-colors cursor-pointer glass-option-hover hover:text-content-primary"
      >
        <button
          onClick={() => setExpanded((v) => !v)}
          title={group.cwd}
          className="group flex-1 min-w-0 flex items-center gap-2 text-left px-3 py-2.5 text-sm text-content-secondary"
        >
          {/* 16px 图标槽位：常显文件夹图标（展开=打开、折叠=关闭）；悬停时淡出、三角指示器淡入 */}
          <span className="relative w-4 h-4 shrink-0 flex items-center justify-center text-content-secondary">
            <span className="absolute inset-0 flex items-center justify-center transition-opacity duration-100 group-hover:opacity-0">
              {expanded ? <FolderOpenIcon className="w-3.5 h-3.5" /> : <FolderClosedIcon className="w-3.5 h-3.5" />}
            </span>
            <svg className={`absolute inset-0 m-auto w-3 h-3 opacity-0 transition-[opacity,transform] duration-100 group-hover:opacity-100 group-hover:duration-150 ${expanded ? 'rotate-90' : ''}`} viewBox="0 0 14 14" fill="none">
              <path d="M4.25 2.82782L4.25 11.1722C4.25 11.6622 4.84243 11.9076 5.18891 11.5611L9.36109 7.38891C9.57588 7.17412 9.57588 6.82588 9.36109 6.61109L5.18891 2.43891C4.84243 2.09243 4.25 2.33782 4.25 2.82782Z" fill="currentColor" />
            </svg>
          </span>
          <span className="truncate flex-1 text-left">{group.name}</span>
        </button>
        {/* 新建会话指示器：点击在该目录新建会话（目录自动切换为该目录，输入框可再改） */}
        <button
          onClick={() => onNewSession(group.cwd)}
          title={`${t(lang, 'workspace_new_in')} · ${group.name}`}
          className="shrink-0 mr-1.5 w-6 h-6 flex items-center justify-center rounded-md text-content-secondary hover:text-primary transition-colors cursor-pointer"
        >
          <NewChatIcon className="w-3.5 h-3.5" />
        </button>
      </div>
      {expanded && (
        <div className="space-y-0.5">
          {visible.map((s) => (
            <SessionItem
              key={s.value}
              session={s}
              isRestoring={restoringSessionId === s.value}
              isActive={s.active}
              lang={lang}
              onSelect={onSelectSession}
              onRename={onRenameSession}
              onDelete={onDeleteSession}
            />
          ))}
          {group.sessions.length > 5 && (
            <button
              onClick={() => setListExpanded(!listExpanded)}
              className="w-full flex items-center justify-center py-1.5 text-content-disabled hover:text-content-secondary glass-option-hover rounded-lg transition-colors cursor-pointer mt-1"
              title={listExpanded ? t(lang, 'collapse_messages') : t(lang, 'show_earlier').replace('{count}', String(group.sessions.length - 5))}
            >
              <svg className={`w-3.5 h-3.5 transition-transform duration-150 ${listExpanded ? 'rotate-180' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 4.5L6 7.5L9 4.5" />
              </svg>
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  lang, connected, sessions, workspaces, activeWorkspaceCwd, onNewSession, onSelectSession, onListSessions, onDeleteSessions, onRenameSession, onDeleteSession, collapsed, onToggle, width = 280, restoringSessionId, onOpenSettings,
}: SidebarProps) {

  // 按目录分组：分组顺序 = 注册表顺序（默认目录首位）。
  // 仅展示已知工作区的会话：被移除目录的会话后端不再推送，
  // 前端也不渲染未知目录组（避免残留会话以"未知目录"形式出现）
  const groups = useMemo<WorkspaceGroup[]>(() => {
    const byCwd = new Map<string, SidebarSession[]>();
    for (const s of sessions) {
      const key = s.cwd || '';
      const bucket = byCwd.get(key);
      if (bucket) bucket.push(s);
      else byCwd.set(key, [s]);
    }
    const ordered: WorkspaceGroup[] = [];
    for (const ws of workspaces) {
      const bucket = byCwd.get(ws.path);
      ordered.push({
        cwd: ws.path,
        name: ws.name || basenameOf(ws.path),
        isDefault: ws.is_default,
        sessions: bucket ?? [],
        busy: (bucket ?? []).some((s) => s.busy),
      });
    }
    return ordered;
  }, [sessions, workspaces, lang]);

  const activeCwd = activeWorkspaceCwd ?? workspaces.find((w) => w.is_default)?.path ?? '';
  const activeGroupName = basenameOf(activeCwd);

  if (collapsed) {
    // 折叠态与右栏一致：侧栏整体隐藏（不保留竖条），控制项由顶部左侧的
    // SidebarControls 浮出按钮组承载，见文件底部导出
    return null;
  }

  return (
    <aside className="glass-panel panel-below-titlebar flex flex-col h-full shrink-0 select-none transition-[width] duration-300 ease-in-out" style={{ width: `${width}px` }}>
      <div className="flex items-center justify-between px-5 py-4">
        <button
          onClick={onToggle}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-content-secondary hover:text-content-primary glass-option-hover transition-colors cursor-pointer"
          title={t(lang, 'collapse_panel')}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M10 3l-5 5 5 5" />
          </svg>
        </button>
        <span className="font-body font-bold text-content-primary text-sm tracking-wider">{t(lang, 'sidebar_title')}</span>
        <button
          onClick={onDeleteSessions}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-content-secondary hover:text-danger transition-colors cursor-pointer"
          title={t(lang, 'delete_session')}
          aria-label={t(lang, 'delete_session')}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            <line x1="10" y1="11" x2="10" y2="17" />
            <line x1="14" y1="11" x2="14" y2="17" />
          </svg>
        </button>
      </div>
      <div className="px-4 py-3">
        <button
          onClick={() => onNewSession()}
          disabled={!connected}
          title={activeCwd ? `${t(lang, 'new_session')} · ${activeGroupName}` : t(lang, 'new_session')}
          className="pill-badge w-full px-3 py-2.5 rounded-lg text-sm leading-4 text-content-primary transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 text-center"
          style={{ borderColor: 'var(--border-medium)' }}
        >
          <NewChatIcon className="w-4 h-4 shrink-0" />
          {t(lang, 'new_session')}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-3">
        <div className="py-2 text-[11px] text-content-disabled font-semibold px-1 uppercase tracking-widest">
          {t(lang, 'resume_session')}
        </div>
        {groups.some((g) => g.sessions.length > 0) ? (
          <div className="space-y-1">
            {groups.filter((g) => g.sessions.length > 0).map((g) => (
              <WorkspaceGroupSection
                key={g.cwd || '__unknown'}
                group={g}
                lang={lang}
                collapsedByDefault={g.cwd !== activeCwd}
                restoringSessionId={restoringSessionId}
                onSelectSession={onSelectSession}
                onNewSession={onNewSession}
                onRenameSession={onRenameSession}
                onDeleteSession={onDeleteSession}
              />
            ))}
          </div>
        ) : (
          <button
            onClick={onListSessions}
            disabled={!connected}
            className="w-full text-left px-3 py-2 rounded-lg text-sm text-content-disabled glass-option-hover hover:text-content-secondary transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {t(lang, 'load_more')}
          </button>
        )}
      </div>
      {/* 底部设置区 */}
      <div className="px-4 py-3">
        <button
          onClick={onOpenSettings}
          title={t(lang, 'sidebarSettingsTooltip')}
          className="pill-badge w-full flex items-center justify-center gap-2 text-xs text-content-secondary hover:text-content-primary rounded-lg px-2 py-2 transition-colors cursor-pointer"
          style={{ borderColor: 'var(--border-medium)' }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          <span className="text-[11px] font-medium">{t(lang, 'settings')}</span>
        </button>
      </div>
    </aside>
  );
}

/**
 * 顶部左侧控制按钮组（Sidebar 折叠态的承载，与右栏按钮组风格一致）
 *
 * 折叠时侧栏整体隐藏，由本组件在聊天区顶部左侧浮出纵向按钮组：
 * - 展开按钮：点击展开侧栏
 * - 新建会话 / 删除会话快捷键
 * - 设置入口
 *
 * @param props.lang - 当前 UI 语言
 * @param props.connected - 是否已连接（新建按钮据此禁用）
 * @param props.onExpand - 展开侧栏回调
 * @param props.onNewSession - 新建会话回调
 * @param props.onDeleteSessions - 删除会话回调
 * @param props.onOpenSettings - 打开设置回调
 */
export function SidebarControls({
  lang, connected, onExpand, onNewSession, onDeleteSessions, onOpenSettings,
}: {
  lang: UiLanguage;
  connected: boolean;
  onExpand: () => void;
  onNewSession: () => void;
  onDeleteSessions: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <div className="absolute left-[16px] top-3 z-20 flex flex-col items-center gap-2 select-none">
      {/* 展开侧栏 */}
      <button
        onClick={onExpand}
        title={t(lang, 'expand_panel')}
        aria-label={t(lang, 'expand_panel')}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-card-alt border border-border-light text-content-secondary glass-option-hover hover:text-primary transition-colors cursor-pointer"
      >
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M6 3l5 5-5 5" />
        </svg>
      </button>
      {/* 新建会话 */}
      <button
        onClick={onNewSession}
        disabled={!connected}
        title={t(lang, 'new_session')}
        aria-label={t(lang, 'new_session')}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-card-alt border border-border-light text-content-secondary glass-option-hover hover:text-primary transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <NewChatIcon className="w-[15px] h-[15px]" />
      </button>
      {/* 删除会话 */}
      <button
        onClick={onDeleteSessions}
        title={t(lang, 'delete_session')}
        aria-label={t(lang, 'delete_session')}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-card-alt border border-border-light text-danger glass-option-hover transition-colors cursor-pointer"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <line x1="10" y1="11" x2="10" y2="17" />
          <line x1="14" y1="11" x2="14" y2="17" />
        </svg>
      </button>
      {/* 设置 */}
      <button
        onClick={onOpenSettings}
        title={t(lang, 'sidebarSettingsTooltip')}
        aria-label={t(lang, 'sidebarSettingsTooltip')}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-card-alt border border-border-light text-content-secondary glass-option-hover hover:text-primary transition-colors cursor-pointer"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
    </div>
  );
}
