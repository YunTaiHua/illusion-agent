/**
 * @fileoverview 左侧轮次导航组件
 *
 * 长会话的轮次刻度轨道：已载入轮次与未载入轮次共用一条垂直刻度梯。
 * 未载入轮次来自后端全量轮次大纲（web_restore_completed 的
 * turn_outline 前缀），以更浅的实色区分；hover 显示该轮预览（用户提问
 * + 助手回复摘要 + 载入状态），点击已载入轮滚动到该轮，点击未载入轮先
 * 分页加载（web_request_history）再跳转（由 ChatArea 编排）。
 *
 * 视觉与交互规范：
 * - 刻度统一基准尺寸（12px × 2px），静态时一律不伸长；仅当前阅读位置
 *   所在轮以主色高亮（不伸长）。
 * - 悬停时被悬停刻度伸长至 20px 并高亮；上下相邻的共 4 个刻度按距离
 *   递减伸长（16px / 13px），形成平滑阶梯；此时原高亮刻度暂时取消高亮。
 * - 鼠标离开轨道恢复静态。
 * - 轮次增减（加载更多/新轮次）时轨道平滑滚动让当前刻度回中。
 *
 * 全部几何使用 px 任意值——rem 受根字号（可为 15px 等非默认值）影响
 * 产生小数像素，叠加 Windows 125%/150% 缩放后各刻度设备像素取整不一，
 * 会导致刻度高度参差；2/4/10/12/16px 在 DPR 1 / 1.25 / 1.5 / 2 下
 * 组合为整数设备像素行距（12px 行距 → 12/15/18/24），刻度高度严格一致。
 *
 * 纯展示组件：不持有跳转/加载逻辑，经 onNavigate 上抛。
 *
 * @module TurnNavigator
 */

import { memo, useEffect, useRef, useState } from 'react';
import { t, type UiLanguage } from '../i18n';

/** 刻度基准宽度（静态，px） */
const BASE_WIDTH = 12;
/** 悬停刻度宽度（最大伸长） */
const HOVER_WIDTH = 32;
/** 距悬停刻度 1 格的邻刻度宽度（阶梯中段） */
const NEAR_WIDTH = 24;
/** 距悬停刻度 2 格的邻刻度宽度（阶梯远端） */
const FAR_WIDTH = 17;

/**
 * 导航条目：一个刻度对应一轮
 */
export interface TurnNavItem {
  /** 轮号（1-based，会话内全序，与大纲/已载入轮共用同一序号空间） */
  turn: number;
  /** 该轮首条用户消息预览 */
  prompt: string;
  /** 该轮最后一条助手回复预览 */
  response: string;
  /** 是否已载入到前端（false = 仅大纲预览，点击需先分页加载） */
  loaded: boolean;
}

/**
 * TurnNavigator 组件属性接口
 */
interface TurnNavigatorProps {
  /** 当前 UI 语言 */
  lang: UiLanguage;
  /** 导航条目（未载入前缀 + 已载入后缀，按轮号升序） */
  items: TurnNavItem[];
  /** 当前阅读位置所在轮（null = 未确定） */
  activeTurn: number | null;
  /** 正在分页加载的目标轮（该刻度脉冲提示） */
  busyTurn: number | null;
  /** 分页加载中（顶部提示） */
  loading: boolean;
  /** 点击刻度：已载入 → 滚动跳转；未载入 → 加载后跳转（ChatArea 编排） */
  onNavigate: (item: TurnNavItem) => void;
}

/**
 * 左侧轮次导航组件
 *
 * @param props - 组件属性
 * @returns 返回导航轨道 JSX（条目少于 2 时返回 null）
 */
const TurnNavigator = memo(function TurnNavigator({ lang, items, activeTurn, busyTurn, loading, onNavigate }: TurnNavigatorProps) {
  const railRef = useRef<HTMLDivElement>(null);
  // 外层定位容器（tooltip 的 offsetParent）：rail 居中于其内，tooltip
  // 的纵向位置必须叠加 rail 在容器内的偏移才贴近悬停刻度
  const wrapperRef = useRef<HTMLDivElement>(null);
  // hover 的轮号 + 刻度在轨道内容中的偏移（渲染时换算视口位置；
  // 轨道滚动容器自带 relative，offsetTop 即相对滚动内容的距离）
  const [hover, setHover] = useState<{ turn: number; offsetTop: number } | null>(null);
  // 指针是否在轨道内（暂停 active 自动跟随，避免与阅读冲突）
  const pointerInsideRef = useRef(false);

  // active 轮或轮次集合变化时刻度平滑滑动回中（指针在轨道内时不打扰；
  // 加载更多前插新刻度后，当前刻度以滑动动画回到视野中央而非瞬间跳位）
  useEffect(() => {
    const rail = railRef.current;
    if (!rail || pointerInsideRef.current || activeTurn == null) return;
    const el = rail.querySelector<HTMLElement>(`[data-nav-turn="${activeTurn}"]`);
    if (!el) return;
    const target = el.offsetTop - rail.clientHeight / 2 + el.offsetHeight / 2;
    rail.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }, [activeTurn, items]);

  if (items.length < 2) return null;

  const hoveredTurn = hover?.turn ?? null;
  const hoveredIdx = hoveredTurn == null
    ? -1
    : items.findIndex((i) => i.turn === hoveredTurn);
  const hoveredItem = hover ? items.find((i) => i.turn === hover.turn) : undefined;

  // tooltip 纵向位置（相对外层定位容器）：
  // 刻度内容偏移 - 轨道滚动量 + 轨道在容器内的居中偏移 - 少量上移，
  // 使卡片纵向大体对准悬停刻度；并夹在容器边界内（预览卡最高约 150px）
  let tooltipTop = 0;
  if (hover && railRef.current && wrapperRef.current) {
    const top = railRef.current.offsetTop + hover.offsetTop - railRef.current.scrollTop - 14;
    const maxTop = Math.max(4, wrapperRef.current.clientHeight - 150);
    tooltipTop = Math.max(4, Math.min(top, maxTop));
  }

  return (
    <div ref={wrapperRef} className="relative shrink-0 self-stretch hidden sm:flex">
      {/* 固定高度刻度带：my-auto 在整列内垂直居中，h-[420px] 保证导航条
          高度恒定（不随轮数/列高变化），max-h-full 兜底矮视口；
          内部滚动被限制在该带内，而非整个聊天列高度 */}
      <div
        ref={railRef}
        className="relative my-auto h-[420px] max-h-full w-[40px] overflow-y-auto scrollbar-hidden flex flex-col py-[12px] pr-[4px] pl-[4px]"
        onMouseEnter={() => { pointerInsideRef.current = true; }}
        onMouseLeave={() => {
          pointerInsideRef.current = false;
          setHover(null);
        }}
      >
        {/* my-auto 垂直居中：内容不足一屏时居中显示，超出时可正常滚动
            （justify-center 在滚动容器里会裁掉顶部且无法滚达） */}
        <div className="my-auto flex flex-col gap-[2px]">
          {items.map((item, idx) => {
            const isActive = item.turn === activeTurn;
            const isBusy = item.turn === busyTurn;
            const isHovered = item.turn === hoveredTurn;
            // 悬停阶梯：被悬停刻度最大伸长，上下各两个按距离递减
            let width = BASE_WIDTH;
            if (isHovered) {
              width = HOVER_WIDTH;
            } else if (hoveredIdx >= 0) {
              const dist = Math.abs(idx - hoveredIdx);
              if (dist === 1) width = NEAR_WIDTH;
              else if (dist === 2) width = FAR_WIDTH;
            }
            // 高亮归属：悬停谁谁高亮；未悬停时当前阅读轮高亮
            const highlight = isHovered || (hoveredTurn == null && isActive);
            const label = item.loaded
              ? t(lang, 'turn_nav_jump').replace('{turn}', String(item.turn))
              : t(lang, 'turn_nav_jump_load').replace('{turn}', String(item.turn));
            return (
              <button
                key={item.turn}
                data-nav-turn={item.turn}
                aria-label={label}
                title={label}
                onMouseEnter={(e) => setHover({ turn: item.turn, offsetTop: e.currentTarget.offsetTop })}
                onFocus={(e) => setHover({ turn: item.turn, offsetTop: e.currentTarget.offsetTop })}
                onBlur={() => setHover(null)}
                onClick={() => onNavigate(item)}
                className="group/nav flex h-[10px] w-full items-center cursor-pointer"
              >
                {/* 统一 2px 细刻度，宽度内联样式（px 定值）随悬停阶梯过渡 */}
                {/* 刻度用主题感知的浅色实色（nav-tick-* 变量，浅/深主题
                    各有合适亮度），与主色高亮拉开对比——若刻度本身是
                    深灰，主色绿在深灰中不够醒目 */}
                <span
                  style={{ width: `${width}px` }}
                  className={`h-[2px] rounded-full transition-[width,background-color] duration-200 ${
                    isBusy
                      ? 'bg-primary animate-pulse'
                      : highlight
                        ? 'bg-primary'
                        : item.loaded
                          ? 'bg-[var(--nav-tick-loaded)] group-hover/nav:bg-[var(--text-disabled)]'
                          : 'bg-[var(--nav-tick-unloaded)] group-hover/nav:bg-[var(--nav-tick-loaded)]'
                  }`}
                />
              </button>
            );
          })}
        </div>
        {/* 分页加载指示：绝对定位悬浮，不挤占刻度行（避免刻度跳动） */}
        {loading && (
          <span className="absolute top-[4px] left-1/2 -translate-x-1/2 h-[12px] w-[12px] rounded-full border-2 border-border-medium border-t-primary animate-spin" />
        )}
      </div>
      {/* hover 预览气泡：轮号 + 提问/回复摘要 + 载入状态（指向主聊天区一侧） */}
      {hoveredItem && (
        <div
          className="pointer-events-none absolute left-full z-40"
          style={{ top: tooltipTop }}
        >
          <div className="ml-2 w-72 max-w-[min(20rem,40vw)] rounded-lg glass-surface border border-border-light shadow-lg p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-content-primary tabular-nums">
                {t(lang, 'turn_nav_turn_title').replace('{turn}', String(hoveredItem.turn))}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${hoveredItem.loaded ? 'text-content-secondary bg-[var(--badge-bg-subtle)]' : 'text-content-disabled bg-[var(--badge-bg-subtle)]'}`}>
                {hoveredItem.loaded ? t(lang, 'turn_nav_loaded') : t(lang, 'turn_nav_unloaded')}
              </span>
            </div>
            <div className="mt-1.5 text-xs text-content-secondary max-h-[2.6em] overflow-hidden break-all">
              {hoveredItem.prompt || t(lang, 'turn_nav_prompt_empty')}
            </div>
            {hoveredItem.response && (
              <div className="mt-1 text-xs text-content-disabled max-h-[3.9em] overflow-hidden break-all">
                {hoveredItem.response}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
});

export default TurnNavigator;
