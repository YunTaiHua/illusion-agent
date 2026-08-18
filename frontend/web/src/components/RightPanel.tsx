/**
 * @fileoverview 右侧面板组件
 *
 * Web 前端的右侧面板组件，显示：
 * - 待办事项列表
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
import type { McpServerSnapshot, PluginSnapshot, RuleSnapshot, SkillSnapshot, TodoItemSnapshot } from '../types/protocol';

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
  skills, plugins, rules, mcpServers, width = 260, onRefreshResources,
}: RightPanelProps) {
  // 主题（浅色/深色/跟随系统）— 展开态顶部 header 的主题切换按钮
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

  // 分组统计
  const projectSkills = skills.filter((s) => s.source === 'project');
  const enabledPlugins = plugins.filter((p) => p.enabled);
  const projectRules = rules.filter((r) => r.source === 'project');

  return (
    <aside className="glass-panel border-l border-white/30 flex flex-col h-full shrink-0 overflow-y-auto scrollbar-hidden select-none" style={{ width: `${width}px` }}>
      {/* 标题行：主题切换按钮 + 居中标题 + 折叠按钮（3 列 grid 严格居中） */}
      <div className="grid grid-cols-3 items-center px-5 pt-3 pb-2">
        <button onClick={toggleTheme} title={themeTitle}
          aria-label={themeTitle}
          className="justify-self-start w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer">
          {theme === 'light' && (
            /* 太阳图标（当前浅色，点击切换到深色） */
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8" cy="8" r="3" />
              <path d="M8 1.5v1.5M8 13v1.5M1.5 8h1.5M13 8h1.5M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
            </svg>
          )}
          {theme === 'dark' && (
            /* 月亮图标（当前深色，点击切换到跟随系统） */
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M13 8.5a5 5 0 0 1-5.5-5.5 5 5 0 1 0 5.5 5.5z" />
            </svg>
          )}
          {theme === 'system' && (
            /* 显示器图标（当前跟随系统，点击切换到浅色） */
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="3" width="12" height="8" rx="1" />
              <path d="M6 13h4M8 11v2" />
            </svg>
          )}
        </button>
        <span className="justify-self-center font-body font-bold text-content-primary text-sm tracking-wider">{t(lang, 'management_title')}</span>
        <button onClick={onToggle} title={t(lang, 'collapse_panel')}
          className="justify-self-end w-7 h-7 flex items-center justify-center rounded-lg text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 3l5 5-5 5" />
          </svg>
        </button>
      </div>

      {/* Todo 列表（始终显示，空列表显示占位） */}
      <div className="px-3 pb-3">
        <TodoPanel items={todoItems} lang={lang} />
      </div>

      {/* Skills */}
      {skills.length > 0 && (
        <CollapsibleSection
          title="Skills"
          count={skills.length}
          subtitle={projectSkills.length > 0 ? `${projectSkills.length} ${t(lang, 'project_label')}` : undefined}
          defaultCollapsed={true}
          onExpand={onRefreshResources}
          icon={
            <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 1.5l1.8 4.2 4.2 1.8-4.2 1.8L8 13.5 6.2 9.3 2 7.5l4.2-1.8L8 1.5z" />
            </svg>
          }
        >
          {skills.map((s) => (
            <ItemRow key={s.name} name={s.name} description={s.description} tag={s.source === 'project' ? 'P' : undefined} />
          ))}
        </CollapsibleSection>
      )}

      {/* MCP Servers */}
      {mcpServers.length > 0 && (
        <CollapsibleSection
          title="MCP"
          count={mcpServers.length}
          subtitle={mcpServers.some((s) => s.state === 'connected') ? `${mcpServers.filter((s) => s.state === 'connected').length} ${t(lang, 'connected_label')}` : undefined}
          onExpand={onRefreshResources}
          icon={
            <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M9.94133 6.50173C11.3218 7.99603 11.3218 10.3011 9.94128 11.7954C9.88691 11.8542 9.82125 11.9196 9.72099 12.0198L7.75707 13.9838C7.65709 14.0838 7.592 14.1491 7.53334 14.2034C6.03906 15.5843 3.7327 15.5854 2.23827 14.2048C2.17933 14.1503 2.11374 14.0844 2.01315 13.9838C1.91318 13.8839 1.84922 13.8188 1.79495 13.7601C0.413857 12.2657 0.413909 9.95948 1.795 8.46503C1.84923 8.4064 1.91335 8.34115 2.01321 8.24129L3.79275 6.46313C3.71814 7.08101 3.75236 7.71445 3.90115 8.33518L3.00344 9.23151C2.89398 9.34097 2.8535 9.38307 2.82251 9.41658C1.93771 10.3744 1.93704 11.8514 2.82179 12.8092C2.85279 12.8427 2.89383 12.884 3.0034 12.9936C3.11272 13.1029 3.15429 13.1442 3.18777 13.1752C4.14561 14.0603 5.62381 14.0608 6.58178 13.1758C6.61532 13.1448 6.65722 13.1032 6.76685 12.9935L8.73077 11.0296C8.83999 10.9204 8.88142 10.8787 8.91238 10.8452C9.79744 9.88728 9.7969 8.40911 8.91173 7.45124C8.88074 7.41775 8.83944 7.3762 8.73011 7.26687C8.62082 7.15757 8.58061 7.11623 8.54712 7.08526C8.37347 6.92477 8.18243 6.79361 7.98088 6.69165L9.00289 5.66964C9.17506 5.78373 9.34035 5.91265 9.49663 6.05703C9.55538 6.11135 9.62026 6.17652 9.72036 6.27662C9.82094 6.3772 9.88686 6.4428 9.94133 6.50173Z" fill="currentColor" />
              <path d="M6.06816 9.49196C4.68626 7.99724 4.68667 5.68942 6.06885 4.19487C6.12268 4.13671 6.18789 4.07306 6.28706 3.9739L8.24541 2.01416C8.34478 1.91479 8.41018 1.85055 8.46845 1.79665C9.96301 0.414902 12.2689 0.414922 13.7635 1.79665C13.8217 1.85051 13.8866 1.91559 13.9858 2.01486C14.0849 2.11394 14.1502 2.17769 14.204 2.23583C15.5861 3.7304 15.5866 6.03823 14.2047 7.53291C14.1508 7.59125 14.0854 7.65638 13.9858 7.75595L12.1994 9.54098C12.2614 8.92982 12.2185 8.30587 12.0634 7.69657L12.9956 6.76573C13.1044 6.65692 13.1458 6.61529 13.1765 6.58205C14.0621 5.62404 14.0621 4.1454 13.1765 3.18738C13.1458 3.15419 13.104 3.1135 12.9956 3.00508C12.8877 2.89716 12.8471 2.85551 12.814 2.82485C11.8559 1.9389 10.376 1.93886 9.41794 2.82485C9.38479 2.85554 9.34381 2.89622 9.23564 3.00439L7.27728 4.96413C7.16875 5.07265 7.12708 5.11322 7.09636 5.14643C6.21074 6.10441 6.21153 7.58236 7.09705 8.5404C7.12775 8.57357 7.16826 8.61575 7.27659 8.72408C7.38456 8.83205 7.42647 8.87227 7.45958 8.90293C7.62849 9.0591 7.81309 9.1881 8.00856 9.28894L6.98795 10.3095C6.82111 10.1978 6.66052 10.0715 6.50872 9.93114C6.45057 9.87733 6.38547 9.81341 6.28637 9.71431C6.1871 9.61504 6.12202 9.55018 6.06816 9.49196Z" fill="currentColor" />
            </svg>
          }
        >
          {mcpServers.map((s) => (
            <ItemRow key={s.name} name={s.name} description={s.state} tag={s.tool_count != null ? `${s.tool_count}t` : undefined} />
          ))}
        </CollapsibleSection>
      )}

      {/* Plugins */}
      {plugins.length > 0 && (
        <CollapsibleSection
          title="Plugins"
          count={plugins.length}
          subtitle={enabledPlugins.length > 0 ? `${enabledPlugins.length} ${t(lang, 'enabled_label')}` : undefined}
          onExpand={onRefreshResources}
          icon={
            <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              {/* 2×2 应用网格：左上方形、右上圆形、左下三角形、右下加号 */}
              <rect x="1.5" y="1.5" width="5" height="5" rx="0.8" />
              <circle cx="12" cy="4" r="2.5" />
              <path d="M4 9.5L6.4 14.2H1.6Z" />
              <path d="M12 9.5v5M9.5 12h5" />
            </svg>
          }
        >
          {plugins.map((p) => (
            <ItemRow key={p.name} name={p.name} description={p.description} tag={p.enabled ? undefined : t(lang, 'off_label')} />
          ))}
        </CollapsibleSection>
      )}

      {/* Rules */}
      {rules.length > 0 && (
        <CollapsibleSection
          title="Rules"
          count={rules.length}
          subtitle={projectRules.length > 0 ? `${projectRules.length} ${t(lang, 'project_label')}` : undefined}
          onExpand={onRefreshResources}
          icon={
            <svg className="w-3.5 h-3.5" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M13.3277 9.69629V10.976H7.28086V9.69629H13.3277Z" fill="currentColor" />
              <path d="M13.3277 2.97256V4.25225H7.28086V2.97256H13.3277Z" fill="currentColor" />
              <path d="M4.64512 10.336C4.64505 9.62755 4.07081 9.05322 3.3623 9.05322C2.65386 9.05329 2.07956 9.62759 2.07949 10.336C2.07949 11.0445 2.65382 11.6188 3.3623 11.6188C4.07085 11.6188 4.64512 11.0446 4.64512 10.336ZM5.92559 10.336C5.92559 11.7515 4.77777 12.8993 3.3623 12.8993C1.94689 12.8993 0.799805 11.7515 0.799805 10.336C0.799871 8.92066 1.94693 7.7736 3.3623 7.77354C4.77773 7.77354 5.92552 8.92062 5.92559 10.336Z" fill="currentColor" />
              <path d="M4.64531 3.6123C4.6453 2.90382 4.07098 2.32949 3.3625 2.32949C2.65403 2.32951 2.0797 2.90383 2.07969 3.6123C2.07969 4.32079 2.65402 4.8951 3.3625 4.89512C4.07099 4.89512 4.64531 4.3208 4.64531 3.6123ZM5.925 3.6123C5.925 5.02772 4.77792 6.1748 3.3625 6.1748C1.9471 6.17479 0.8 5.02771 0.8 3.6123C0.800013 2.19691 1.9471 1.04982 3.3625 1.0498C4.77791 1.0498 5.92499 2.1969 5.925 3.6123Z" fill="currentColor" />
            </svg>
          }
        >
          {rules.map((r) => (
            <ItemRow key={`${r.source}-${r.name}`} name={r.name} description="" tag={r.source === 'project' ? 'P' : undefined} />
          ))}
        </CollapsibleSection>
      )}

      {/* Context 使用量 */}
      {contextWindow > 0 && (
        <div className="px-5 py-3 border-t border-border-light">
          <div className="text-xs text-content-secondary font-medium mb-2">{t(lang, 'context_window')}</div>
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
      )}

      {/* 累积 API 用量区块 */}
      {(inputTokens > 0 || outputTokens > 0 || cacheReadTokens > 0 || cacheCreationTokens > 0) && (
        <div className="px-5 py-3 border-t border-border-light">
          <div className="text-xs text-content-secondary font-medium mb-2">{t(lang, 'cumulativeApiUsage')}</div>
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
      )}

      <div className="flex-1" />
    </aside>
  );
}

// ---- 可折叠区域 ----

function CollapsibleSection({
  title, count, subtitle, icon, children, defaultCollapsed = true, onExpand,
}: {
  title: string;
  count: number;
  subtitle?: string;
  /** 分组类型图标（16px，hover 时与 chevron 交叉淡入淡出） */
  icon?: React.ReactNode;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
  /** 折叠→展开时触发（用于刷新数据） */
  onExpand?: () => void;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  const handleToggle = () => {
    const willExpand = collapsed;
    setCollapsed(!collapsed);
    if (willExpand && onExpand) onExpand();
  };

  return (
    <div className="border-t border-border-light">
      <button
        onClick={handleToggle}
        className="group w-full px-5 py-2.5 flex items-center gap-2 glass-option-hover transition-colors cursor-pointer"
      >
        {/* 16px 图标槽位：常显类型图标；hover 时图标淡出、三角指示器淡入 */}
        <span className="relative w-4 h-4 shrink-0 flex items-center justify-center">
          {icon && (
            <span className="absolute inset-0 flex items-center justify-center text-content-secondary transition-opacity duration-100 group-hover:opacity-0">
              {icon}
            </span>
          )}
          <svg
            className={`absolute inset-0 m-auto w-3 h-3 text-content-secondary opacity-0 group-hover:opacity-100 transition-[opacity,transform] duration-100 group-hover:duration-150 ${collapsed ? '' : 'rotate-90'}`}
            viewBox="0 0 14 14" fill="none"
          >
            <path d="M4.25 2.82782L4.25 11.1722C4.25 11.6622 4.84243 11.9076 5.18891 11.5611L9.36109 7.38891C9.57588 7.17412 9.57588 6.82588 9.36109 6.61109L5.18891 2.43891C4.84243 2.09243 4.25 2.33782 4.25 2.82782Z" fill="currentColor" />
          </svg>
        </span>
        <span className="text-xs font-semibold text-content-primary tracking-wide">{title}</span>
        <span className="text-[10px] text-content-secondary bg-[var(--badge-bg)] px-1.5 py-0.5 rounded-full tabular-nums">{count}</span>
        {subtitle && <span className="text-xs text-content-disabled ml-auto">{subtitle}</span>}
      </button>
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
        className={`w-full flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${hasDesc ? 'glass-option-hover cursor-pointer' : 'cursor-default'}`}
        title={hasDesc ? description : name}
      >
        <span className="text-content-primary font-medium truncate flex-1 text-left">{name}</span>
        {tag && (
          <span className="text-[10px] text-primary/80 bg-[var(--badge-bg)] px-1.5 py-0.5 rounded-full font-medium shrink-0">{tag}</span>
        )}
      </button>
      {expanded && hasDesc && (
        <div className="px-2 pb-1.5 text-xs text-content-secondary leading-relaxed whitespace-pre-wrap">{description}</div>
      )}
    </div>
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
        {/* 上级仅在折叠态渲染本按钮组，始终表示"展开右栏"（箭头朝左） */}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10 3l-5 5 5 5" />
        </svg>
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
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8" cy="8" r="3" />
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8h1.5M13 8h1.5M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
          </svg>
        )}
        {theme === 'dark' && (
          /* 月亮图标（当前深色，点击切换到跟随系统） */
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 8.5a5 5 0 0 1-5.5-5.5 5 5 0 1 0 5.5 5.5z" />
          </svg>
        )}
        {theme === 'system' && (
          /* 显示器图标（当前跟随系统，点击切换到浅色） */
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="3" width="12" height="8" rx="1" />
            <path d="M6 13h4M8 11v2" />
          </svg>
        )}
      </button>

      {/* 上下文用量占比（以百分数展示，点击展开右栏查看明细） */}
      <button
        onClick={onToggle}
        title={usageTitle}
        aria-label={usageTitle}
        className="w-9 h-9 flex items-center justify-center rounded-full glass-surface text-content-secondary glass-option-hover hover:text-content-primary transition-colors cursor-pointer"
      >
        <span className="text-[10px] font-semibold tabular-nums leading-none" style={{ color: usageColor }}>
          {contextPercent.toFixed(0)}%
        </span>
      </button>
    </div>
  );
}
