/**
 * @fileoverview 右侧面板组件
 *
 * Web 前端的右侧面板组件，显示（所有区块常驻，空资源显示占位）：
 * - 待办事项列表
 * - 智能体与任务（复用 /agent 双数据源，随会话切换）
 * - 文件目录树（懒加载）
 * - Git 文件变更
 * - 技能列表
 * - MCP 服务器列表
 * - 插件列表
 * - 规则列表
 * - 上下文窗口使用量
 *
 * @module RightPanel
 */

import { useState } from 'react';
import { t, type UiLanguage } from '../i18n';
import { useTheme, type Theme } from '../hooks/useTheme';
import TodoPanel from './TodoPanel';
import FileTreeSection from './FileTreeSection';
import GitSection from './GitSection';
import SessionFilesSection from './SessionFilesSection';
import {
  ChartBarIcon, ChevronRightIcon, CpuIcon, LayersIcon, McpIcon, MonitorIcon,
  MoonIcon, PanelRightIcon, PluginsIcon, RefreshIcon, RulesIcon, SparkleIcon, SunIcon,
} from './icons';
import type {
  AgentTaskItem,
  FileTreeNode,
  GitStatusSnapshot,
  McpServerSnapshot,
  PluginSnapshot,
  RuleSnapshot,
  SessionFileItem,
  SkillSnapshot,
  TodoItemSnapshot,
} from '../types/protocol';

/**
 * RightPanel 组件属性接口
 */
interface RightPanelProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 后端状态 */
  status: Record<string, unknown>;
  /** 是否折叠 */
  collapsed: boolean;
  /** 折叠/展开切换回调 */
  onToggle: () => void;
  /** 待办事项列表 */
  todoItems: TodoItemSnapshot[];
  /** 智能体与后台任务列表（随会话） */
  agentTasks: AgentTaskItem[];
  /** 拉取智能体与任务 */
  onRequestAgentTasks: () => void;
  /** 查看智能体/任务摘要 */
  onViewAgentTask: (id: string) => void;
  /** 文件树缓存（目录相对路径 → 子条目，'' 为根） */
  fileTree: Record<string, FileTreeNode[]>;
  /** 文件树正在加载的目录路径列表 */
  fileTreeLoadingPaths: string[];
  /** Git 状态快照（null = 未拉取） */
  gitStatus: GitStatusSnapshot | null;
  /** Git 状态加载中 */
  gitLoading: boolean;
  /** 会话内修改文件列表（随会话隔离） */
  sessionFiles: SessionFileItem[];
  /** 会话文件拉取中 */
  sessionFilesLoading: boolean;
  /** 拉取会话内修改文件 */
  onRequestSessionFiles: () => void;
  /** 打开会话内修改文件预览 */
  onOpenSessionFile: (path: string) => void;
  /** 拉取文件树单层条目 */
  onRequestFileTree: (path: string, force?: boolean) => void;
  /** 拉取 Git 状态 */
  onRequestGitStatus: () => void;
  /** 打开文件内容预览（文件树点击） */
  onOpenFile: (path: string) => void;
  /** 打开文件 diff 预览（Git 变更点击） */
  onOpenFileDiff: (path: string) => void;
  /** 技能列表 */
  skills: SkillSnapshot[];
  /** 插件列表 */
  plugins: PluginSnapshot[];
  /** 规则列表 */
  rules: RuleSnapshot[];
  /** MCP 服务器列表 */
  mcpServers: McpServerSnapshot[];
  /** 面板宽度（可选，默认 260） */
  width?: number;
  /** 展开时刷新资源回调（区块展开或面板展开时触发） */
  onRefreshResources?: () => void;
}

/**
 * 右侧面板组件
 *
 * Web 前端的右侧面板组件。
 *
 * @param props - 组件属性
 * @returns 返回右侧面板的 JSX 元素
 */
export default function RightPanel({
  lang, status, collapsed, onToggle, todoItems,
  agentTasks, onRequestAgentTasks, onViewAgentTask,
  fileTree, fileTreeLoadingPaths, gitStatus, gitLoading,
  sessionFiles, sessionFilesLoading, onRequestSessionFiles, onOpenSessionFile,
  onRequestFileTree, onRequestGitStatus, onOpenFile, onOpenFileDiff,
  skills, plugins, rules, mcpServers, width = 260, onRefreshResources,
}: RightPanelProps) {
  // 主题（浅色/深色/跟随系统）— 移动到底部按钮，与左栏设置按钮风格一致
  const { theme, toggleTheme } = useTheme();
  const themeLabels: Record<Theme, string> = {
    light: t(lang, 'theme_light'),
    dark: t(lang, 'theme_dark'),
    system: t(lang, 'theme_system'),
  };
  const themeTitle = themeLabels[theme];
  // tab 视图：'usage' = 待办 + 上下文窗口 + 累积 API 用量（默认，待办与用量同表单）；
  // 'sections' = 智能体与任务…规则各区块。切换 tab 时顶部标题栏与底部主题按钮保持不变
  const [tab, setTab] = useState<'sections' | 'usage'>('usage');
  // 上下文使用量
  const contextWindow = Number(status?.context_window ?? 0);
  const contextTokens = Number(status?.context_tokens ?? 0);
  const contextPercent = contextWindow > 0 ? Math.min(100, Math.round(contextTokens * 1000 / contextWindow) / 10) : 0;
  // token 计量分项数据（累积）
  const inputTokens = Number(status?.input_tokens ?? 0);
  const outputTokens = Number(status?.output_tokens ?? 0);
  const cacheReadTokens = Number(status?.cache_read_input_tokens ?? 0);
  const cacheCreationTokens = Number(status?.cache_creation_input_tokens ?? 0);
  // 最后一次 API 调用的真实分项（Context Window 区块）
  const contextCacheRead = Number(status?.context_cache_read ?? 0);
  const contextCacheCreation = Number(status?.context_cache_creation ?? 0);
  const contextInput = Number(status?.context_input ?? 0);
  const contextOutput = Number(status?.context_output ?? 0);
  const contextCached = contextCacheRead + contextCacheCreation;
  const hasLastApiBreakdown = contextCached > 0 || contextInput > 0 || contextOutput > 0;
  // 缓存命中率 = cache_read / (cache_read + cache_creation + input_tokens)，保留一位小数
  // 右栏不计算输入/输出/缓存占窗口的百分比，只计算缓存命中率
  const totalInputWithCache = contextCached + contextInput;
  const cacheHitRate = totalInputWithCache > 0 ? Math.round(contextCacheRead * 1000 / totalInputWithCache) / 10 : 0;

  // 折叠态：整个右栏（含侧边窄条）一并隐藏，展开/主题/上下文圆环由顶部右侧
  // 按钮组（RightPanelControls，见文件底部导出）承载
  if (collapsed) return null;

  return (
    <aside className="glass-panel panel-below-titlebar flex flex-col h-full shrink-0 select-none" style={{ width: `${width}px` }}>
      {/* 标题行：tab 切换按钮 + 居中标题 + 折叠按钮（3 列 grid 严格居中） */}
      <div className="grid grid-cols-3 items-center px-5 pt-4 pb-3 shrink-0">
        <button
          onClick={() => setTab((t) => (t === 'sections' ? 'usage' : 'sections'))}
          title={tab === 'sections' ? t(lang, 'panel_tab_usage') : t(lang, 'panel_tab_sections')}
          aria-label={tab === 'sections' ? t(lang, 'panel_tab_usage') : t(lang, 'panel_tab_sections')}
          className="justify-self-start w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary hover:text-primary transition-colors cursor-pointer"
        >
          {tab === 'sections' ? (
            /* 当前在区块，点击切到用量：条形图图标 */
            <ChartBarIcon className="w-4 h-4" />
          ) : (
            /* 当前在用量，点击切到区块：层叠菱形图标 */
            <LayersIcon className="w-4 h-4" />
          )}
        </button>
        <span className="justify-self-center font-body font-bold text-content-primary text-sm tracking-wider">{t(lang, 'management_title')}</span>
        <button onClick={onToggle} title={t(lang, 'collapse_panel')}
          className="justify-self-end w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary hover:text-primary transition-colors cursor-pointer">
          <PanelRightIcon className="w-4 h-4" />
        </button>
      </div>

      {/* tab 内容区（顶部标题栏与底部主题按钮固定保留，仅此区域随 tab 切换） */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-hidden">
      {tab === 'sections' ? (
      <>
      {/* 智能体与任务（复用 /agent 双数据源：前台 agent + 后台任务通知；随会话切换） */}
      <CollapsibleSection
        title={t(lang, 'agents_title')}
        count={agentTasks.length}
        defaultCollapsed={true}
        onExpand={onRequestAgentTasks}
        onRefresh={onRequestAgentTasks}
        refreshLabel={t(lang, 'refresh')}
        topBorder={false}
        icon={
          <CpuIcon className="w-3.5 h-3.5" />
        }
      >
        {agentTasks.length === 0 ? (
          <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_agent_tasks')}</div>
        ) : (
          agentTasks.map((task) => (
            <AgentTaskRow key={`${task.type}-${task.id}`} task={task} lang={lang} onView={onViewAgentTask} />
          ))
        )}
      </CollapsibleSection>

      {/* Files（工作区文件目录树，懒加载） */}
      <FileTreeSection
        lang={lang}
        fileTree={fileTree}
        loadingPaths={fileTreeLoadingPaths}
        onRequestDir={onRequestFileTree}
        onOpenFile={onOpenFile}
        topBorder={false}
      />

      {/* Git（分支 + 变更文件；非 Git 仓库自动隐藏） */}
      <GitSection
        lang={lang}
        status={gitStatus}
        loading={gitLoading}
        onRefresh={onRequestGitStatus}
        onOpenDiff={onOpenFileDiff}
        topBorder={false}
      />

      {/* 会话文件（本会话内变更工具修改的文件；独立于 Git 与工作区边界，可直接预览） */}
      <SessionFilesSection
        lang={lang}
        files={sessionFiles}
        loading={sessionFilesLoading}
        onRefresh={onRequestSessionFiles}
        onOpenFile={onOpenSessionFile}
        topBorder={false}
      />

      {/* 技能 */}
      <CollapsibleSection
        title={t(lang, 'skills_title')}
        count={skills.length}
        defaultCollapsed={true}
        onExpand={onRefreshResources}
        onRefresh={onRefreshResources}
        refreshLabel={t(lang, 'refresh')}
        topBorder={false}
        icon={
          <SparkleIcon className="w-3.5 h-3.5" />
        }
      >
        {skills.length === 0 ? (
          <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_skills')}</div>
        ) : skills.map((s) => (
          <ItemRow key={s.name} name={s.name} description={s.description} tag={s.source === 'project' ? 'P' : undefined} />
        ))}
      </CollapsibleSection>

      {/* MCP 服务器 */}
      <CollapsibleSection
        title={t(lang, 'mcp_title')}
        count={mcpServers.length}
        onExpand={onRefreshResources}
        onRefresh={onRefreshResources}
        refreshLabel={t(lang, 'refresh')}
        topBorder={false}
        icon={
          <McpIcon className="w-3.5 h-3.5" />
        }
      >
        {mcpServers.length === 0 ? (
          <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_mcp')}</div>
        ) : mcpServers.map((s) => (
          <ItemRow key={s.name} name={s.name} description={s.state} tag={s.tool_count != null ? `${s.tool_count}t` : undefined} />
        ))}
      </CollapsibleSection>

      {/* 插件 */}
      <CollapsibleSection
        title={t(lang, 'plugins_title')}
        count={plugins.length}
        onExpand={onRefreshResources}
        onRefresh={onRefreshResources}
        refreshLabel={t(lang, 'refresh')}
        topBorder={false}
        icon={
          <PluginsIcon className="w-3.5 h-3.5" />
        }
      >
        {plugins.length === 0 ? (
          <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_plugins')}</div>
        ) : plugins.map((p) => (
          <ItemRow key={p.name} name={p.name} description={p.description} tag={p.enabled ? undefined : t(lang, 'off_label')} />
        ))}
      </CollapsibleSection>

      {/* 规则 */}
      <CollapsibleSection
        title={t(lang, 'rules_title')}
        count={rules.length}
        onExpand={onRefreshResources}
        onRefresh={onRefreshResources}
        refreshLabel={t(lang, 'refresh')}
        topBorder={false}
        icon={
          <RulesIcon className="w-3.5 h-3.5" />
        }
      >
        {rules.length === 0 ? (
          <div className="px-2 py-1 text-xs text-content-disabled">{t(lang, 'no_rules')}</div>
        ) : rules.map((r) => (
          <ItemRow key={`${r.source}-${r.name}`} name={r.name} description="" tag={r.source === 'project' ? 'P' : undefined} />
        ))}
      </CollapsibleSection>
      </>
      ) : (
      <>
        {/* Todo 列表（与用量同表单；始终显示，空列表显示占位） */}
        <div className="px-4 py-3">
          <TodoPanel items={todoItems} lang={lang} />
        </div>

        {/* 上下文窗口（标题风格对齐左栏会话列表标题；标题与正文间保留 gap） */}
        {contextWindow > 0 && (
          <div className="px-4 pb-3">
            <div className="py-2 pb-2 text-[11px] text-content-disabled font-semibold px-1 uppercase tracking-widest">{t(lang, 'context_window')}</div>
            <div className="px-1 pt-1">
              {/* 最后一次 API 调用的真实分项（无数据时显示估算汇总） */}
              {hasLastApiBreakdown ? (
                <>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-content-secondary">{t(lang, 'inputCachedLabel')}</span>
                    <span className="text-content-primary tabular-nums">{formatTokens(contextCached)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-content-secondary">{t(lang, 'inputUncachedLabel')}</span>
                    <span className="text-content-primary tabular-nums">{formatTokens(contextInput)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-content-secondary">{t(lang, 'outputLabel')}</span>
                    <span className="text-content-primary tabular-nums">{formatTokens(contextOutput)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs mb-2">
                    <span className="text-content-secondary">{t(lang, 'cacheHitRate')}</span>
                    <span className="text-content-primary tabular-nums">{cacheHitRate.toFixed(1)}%</span>
                  </div>
                </>
              ) : null}
              {/* 进度条 */}
              <div className="flex items-center gap-3 mb-1">
                <div className="flex-1 h-1.5 bg-black/10 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${contextPercent >= 95 ? 'bg-danger' : contextPercent >= 80 ? 'bg-warning' : 'bg-primary'}`}
                    style={{ width: `${contextPercent}%` }}
                  />
                </div>
                <span className={`text-xs font-medium tabular-nums ${contextPercent >= 95 ? 'text-danger' : 'text-content-secondary'}`}>
                  {contextPercent.toFixed(1)}%
                </span>
              </div>
              <div className="text-xs text-content-secondary tabular-nums">
                {formatTokens(contextTokens)} / {formatTokens(contextWindow)}
              </div>
              <div className="text-xs text-content-secondary tabular-nums mt-1">
                {t(lang, 'remaining')} {formatTokens(Math.max(0, contextWindow - contextTokens))}
              </div>
            </div>
          </div>
        )}

        {/* 累积 API 用量（标题风格与上下文窗口一致，标题与正文间保留 gap） */}
        {(inputTokens > 0 || outputTokens > 0 || cacheReadTokens > 0 || cacheCreationTokens > 0) && (
          <div className="px-4 pb-3">
            <div className="py-2 pb-2 text-[11px] text-content-disabled font-semibold px-1 uppercase tracking-widest">{t(lang, 'cumulativeApiUsage')}</div>
            <div className="px-1 pt-1">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-content-secondary">{t(lang, 'inputCachedLabel')}</span>
                <span className="text-content-primary tabular-nums">{formatTokens(cacheReadTokens + cacheCreationTokens)} ↓</span>
              </div>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-content-secondary">{t(lang, 'inputUncachedLabel')}</span>
                <span className="text-content-primary tabular-nums">{formatTokens(inputTokens)} ↓</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-content-secondary">{t(lang, 'outputLabel')}</span>
                <span className="text-content-primary tabular-nums">{formatTokens(outputTokens)} ↑</span>
              </div>
            </div>
          </div>
        )}
      </>
      )}
      </div>

      {/* 底部：主题切换按钮（svg + 文字，风格对齐左栏设置按钮；切换 tab 时保持保留） */}
      <div className="px-4 py-3 shrink-0">
        <button
          onClick={toggleTheme}
          title={`${t(lang, 'theme')}: ${themeTitle}`}
          aria-label={themeTitle}
          className="pill-badge w-full flex items-center justify-center gap-2 text-xs text-content-secondary hover:text-content-primary rounded-lg px-2 py-2 transition-colors cursor-pointer"
          style={{ borderColor: 'var(--border-medium)' }}
        >
          {theme === 'light' && (
            /* 太阳图标（当前浅色，点击切换到深色）——尺寸与左栏设置按钮图标（14×14）一致 */
            <SunIcon className="w-3.5 h-3.5" />
          )}
          {theme === 'dark' && (
            /* 月亮图标（当前深色，点击切换到跟随系统） */
            <MoonIcon className="w-3.5 h-3.5" />
          )}
          {theme === 'system' && (
            /* 显示器图标（当前跟随系统，点击切换到浅色） */
            <MonitorIcon className="w-3.5 h-3.5" />
          )}
          <span className="text-[11px] font-medium">{themeTitle}</span>
        </button>
      </div>
    </aside>
  );
}

// ---- 可折叠区域 ----

/**
 * 可折叠区块组件（右栏各列表区块共用）
 *
 * 头部：图标/指示器 + 标题 + 右侧计数徽标槽位。
 * 悬浮头部时：图标淡出为展开箭头指示器，右侧计数徽标淡出并原位替换为刷新按钮。
 */
export function CollapsibleSection({
  title, count, icon, children, defaultCollapsed = true, onExpand, onRefresh, refreshLabel, topBorder = true,
}: {
  title: string;
  count: number;
  /** 分组类型图标（16px，hover 时与 chevron 交叉淡入淡出） */
  icon?: React.ReactNode;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
  /** 折叠→展开时触发（用于刷新数据） */
  onExpand?: () => void;
  /** 提供即渲染刷新按钮（悬浮头部浮现，点击不触发折叠） */
  onRefresh?: () => void;
  /** 刷新按钮的悬浮提示文案（调用方传入本地化文本） */
  refreshLabel?: string;
  /** 是否显示区块顶部分隔线（默认显示；右栏各区块间隐藏，仅首个/末个边界保留） */
  topBorder?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  const handleToggle = () => {
    const willExpand = collapsed;
    setCollapsed(!collapsed);
    if (willExpand && onExpand) onExpand();
  };

  return (
    <div className={topBorder ? 'border-t border-border-light' : undefined}>
      {/* 整个头部行（含右侧计数槽位）均可点击折叠/展开——悬浮高亮区=点击热区，
          避免悬浮区域远大于可点文字造成误导；键盘 Enter/Space 同样触发 */}
      <div
        role="button"
        tabIndex={0}
        onClick={handleToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleToggle(); } }}
        className="group/head w-full px-5 py-2 flex items-center gap-2 glass-option-hover transition-colors rounded-md cursor-pointer select-none"
      >
        <div className="flex-1 min-w-0 flex items-center gap-2 py-0.5">
          {/* 16px 图标槽位：常显类型图标；hover 时图标淡出、三角指示器淡入 */}
          <span className="relative w-4 h-4 shrink-0 flex items-center justify-center">
            {icon && (
              <span className="absolute inset-0 flex items-center justify-center text-content-secondary transition-opacity duration-100 group-hover/head:opacity-0">
                {icon}
              </span>
            )}
            <ChevronRightIcon
              className={`absolute inset-0 m-auto w-3 h-3 text-content-secondary opacity-0 group-hover/head:opacity-100 transition-[opacity,transform] duration-100 group-hover/head:duration-150 ${collapsed ? '' : 'rotate-90'}`}
            />
          </span>
          <span className="text-xs font-semibold text-content-primary tracking-wide">{title}</span>
        </div>
        {/* 右侧槽位：默认显示计数徽标；悬浮头部时计数淡出、刷新按钮淡入（同槽交叉替换，不位移） */}
        <span className="relative min-w-6 h-6 shrink-0 flex items-center justify-center">
          {onRefresh ? (
            <>
              <span className="absolute inset-0 flex items-center justify-center transition-opacity duration-100 group-hover/head:opacity-0">
                <span className="text-[10px] text-content-secondary bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full tabular-nums">{count}</span>
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onRefresh(); }}
                title={refreshLabel}
                aria-label={refreshLabel}
                className="absolute inset-0 flex items-center justify-center w-6 h-6 rounded-md text-content-secondary opacity-0 group-hover/head:opacity-100 focus-visible:opacity-100 transition-[opacity,colors] duration-100 group-hover/head:duration-150 hover:text-content-primary hover:bg-[var(--glass-option-active-bg)] cursor-pointer"
              >
                <RefreshIcon className="w-3 h-3" />
              </button>
            </>
          ) : (
            <span className="text-[10px] text-content-secondary bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full tabular-nums">{count}</span>
          )}
        </span>
      </div>
      {/* 展开/折叠微动画（简洁 fade：纯透明度 150ms） */}
      {!collapsed && (
        <div className="animate-fade">
          <div className="px-5 pb-2.5 flex flex-col gap-0.5 max-h-[50vh] overflow-y-auto scrollbar-hidden">
            {children}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- 单行项目 ----

function ItemRow({ name, description, tag }: { name: string; description: string; tag?: string }) {
  const [expanded, setExpanded] = useState(false);
  const hasDesc = !!description?.trim();

  return (
    <div>
      <button
        onClick={() => hasDesc && setExpanded((e) => !e)}
        className={`w-[calc(100%_+_2.5rem)] flex items-center gap-2 -mx-5 pl-7 pr-5 py-1 rounded-md text-xs transition-colors ${hasDesc ? 'glass-option-hover cursor-pointer' : 'cursor-default'}`}
        title={hasDesc ? description : name}
      >
        <span className="text-content-primary font-medium truncate flex-1 text-left">{name}</span>
        {tag && (
          <span className="text-[10px] text-primary/80 bg-[var(--badge-bg-subtle)] px-1.5 py-0.5 rounded-full font-medium shrink-0">{tag}</span>
        )}
      </button>
      {expanded && hasDesc && (
        <div className="px-7 pb-1.5 text-xs text-content-secondary leading-relaxed whitespace-pre-wrap">{description}</div>
      )}
    </div>
  );
}

// ---- 智能体与任务行 ----

/** 状态 → 展示文案与配色 */
function taskStatusInfo(lang: UiLanguage, status: string): { label: string; cls: string } {
  if (status === 'completed') return { label: t(lang, 'task_done'), cls: 'text-success' };
  if (status === 'failed') return { label: t(lang, 'task_failed'), cls: 'text-danger' };
  if (status === 'running') return { label: t(lang, 'task_running'), cls: 'text-warning' };
  return { label: status, cls: 'text-content-disabled' };
}

/** 智能体/任务行：类型徽标 + 标题 + 状态，点击查看摘要（复用 /agent） */
function AgentTaskRow({ task, lang, onView }: { task: AgentTaskItem; lang: UiLanguage; onView: (id: string) => void }) {
  const status = taskStatusInfo(lang, task.status);
  const title = [task.id, task.title !== task.id ? task.title : '', `/${task.type}`].filter(Boolean).join(' · ');

  return (
    <button
      onClick={() => onView(task.id)}
      className="w-[calc(100%_+_2.5rem)] flex items-center gap-2 -mx-5 pl-7 pr-5 py-1 rounded-md text-xs transition-colors glass-option-hover cursor-pointer"
      title={`${title}${task.summary ? `\n${task.summary}` : ''}`}
    >
      {/* 类型徽标：智能体/任务统一主色背景块，固定宽度保证各行标题缩进一致 */}
      <span className="w-10 text-center text-[10px] py-0.5 rounded-full font-medium shrink-0 text-primary bg-primary/10">
        {task.type === 'agent' ? t(lang, 'agent_type_agent') : t(lang, 'agent_type_task')}
      </span>
      <span className="text-content-primary font-medium truncate flex-1 text-left">{task.title}</span>
      <span className={`text-[10px] shrink-0 ${status.cls}`}>{status.label}</span>
    </button>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/**
 * 顶部右侧控制按钮组（右栏折叠态的替代承载）
 *
 * 原折叠态在右边缘显示一个 12px 窄条；现优化为在聊天区顶部右侧纵向排布三个按钮：
 * - 展开按钮：点击展开/收起右栏
 * - 主题按钮：循环切换浅色/深色/跟随系统
 * - 上下文占比：以百分数展示上下文窗口使用量（点击展开右栏查看明细）
 *
 * 定位：absolute top，right 对齐主视图滚动条左侧并留出间距
 * （主视图滚动条经 margin 内移 6px，按钮组右移相应避开，避免被遮挡）。
 *
 * @param props.lang - 当前 UI 语言
 * @param props.status - 后端状态（读 context_window / context_tokens）
 * @param props.onToggle - 折叠/展开切换回调
 */
export function RightPanelControls({
  lang, status, onToggle,
}: {
  lang: UiLanguage;
  status: Record<string, unknown>;
  onToggle: () => void;
}) {
  const { theme, toggleTheme } = useTheme();
  const themeLabels: Record<Theme, string> = {
    light: t(lang, 'theme_light'),
    dark: t(lang, 'theme_dark'),
    system: t(lang, 'theme_system'),
  };
  const themeTitle = themeLabels[theme];
  // 上下文使用量
  const contextWindow = Number(status?.context_window ?? 0);
  const contextTokens = Number(status?.context_tokens ?? 0);
  const contextPercent = contextWindow > 0 ? Math.min(100, Math.round(contextTokens * 1000 / contextWindow) / 10) : 0;
  // 占比文字颜色随用量变化（与右栏进度条的 bg-primary/bg-warning/bg-danger 色值一致）
  const usageColor = contextPercent >= 95 ? '#d45b5b' : contextPercent >= 80 ? '#e8a84c' : '#2a9d99';
  const usageTitle = contextWindow > 0
    ? `${t(lang, 'context_window')}: ${contextPercent.toFixed(1)}% (${formatTokens(contextTokens)}/${formatTokens(contextWindow)})`
    : t(lang, 'context_window');

  return (
    <div className="absolute top-3 right-[20px] z-20 flex flex-col items-center gap-2 select-none">
      {/* 展开/收起右栏 */}
      <button
        onClick={onToggle}
        title={t(lang, 'expand_panel')}
        aria-label={t(lang, 'expand_panel')}
        className="w-8 h-8 flex items-center justify-center rounded-full glass-surface text-content-secondary glass-option-hover hover:text-primary transition-colors cursor-pointer"
      >
        {/* 上级仅在折叠态渲染本按钮组，始终表示"展开右栏"（面板图标竖条在右） */}
        <PanelRightIcon className="w-[15px] h-[15px]" />
      </button>

      {/* 主题切换（三态循环：浅色→深色→跟随系统→浅色） */}
      <button
        onClick={toggleTheme}
        title={`${t(lang, 'theme')}: ${themeTitle}`}
        aria-label={themeTitle}
        className="w-8 h-8 flex items-center justify-center rounded-full glass-surface text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        {theme === 'light' && (
          /* 太阳图标（当前浅色，点击切换到深色） */
          <SunIcon className="w-[15px] h-[15px]" />
        )}
        {theme === 'dark' && (
          /* 月亮图标（当前深色，点击切换到跟随系统） */
          <MoonIcon className="w-[15px] h-[15px]" />
        )}
        {theme === 'system' && (
          /* 显示器图标（当前跟随系统，点击切换到浅色） */
          <MonitorIcon className="w-[15px] h-[15px]" />
        )}
      </button>

      {/* 上下文用量占比（环形饼状图，点击展开右栏查看明细） */}
      <button
        onClick={onToggle}
        title={usageTitle}
        aria-label={usageTitle}
        className="w-8 h-8 flex items-center justify-center rounded-full glass-surface text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        <svg width="15" height="15" viewBox="0 0 30 30" aria-hidden="true">
          {/* 轨道圆 */}
          <circle cx="15" cy="15" r="11.5" fill="none" stroke="currentColor" strokeOpacity="0.15" strokeWidth="4" />
          {/* 用量弧（从 12 点方向顺时针；0% 不渲染避免 round cap 残点，
              上限 99.5% 周长避免 100% 时两端 cap 在接缝处重叠加粗） */}
          {contextPercent > 0 && (
            <circle
              cx="15" cy="15" r="11.5" fill="none" stroke={usageColor} strokeWidth="4" strokeLinecap="round"
              transform="rotate(-90 15 15)"
              strokeDasharray={`${Math.min(contextPercent, 99.5) / 100 * 2 * Math.PI * 11.5} ${2 * Math.PI * 11.5}`}
            />
          )}
        </svg>
      </button>
    </div>
  );
}
