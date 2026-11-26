/**
 * @fileoverview 同步闪烁 Hook
 *
 * 从全局动画时钟派生闪烁状态，所有使用 useBlink 的组件自动同步。
 * 闪烁间隔 600ms，保持终端动画节奏稳定。
 *
 * @module hooks/useBlink
 */

import {useGlobalAnimationClock} from './useAnimationFrame.js';

/** 闪烁间隔毫秒数 */
const BLINK_INTERVAL_MS = 600;

/**
 * 同步闪烁 Hook
 *
 * @param enabled - 是否启用闪烁
 * @returns 当前是否可见（true = 显示，false = 隐藏）
 */
export function useBlink(enabled: boolean): boolean {
	const timestamp = useGlobalAnimationClock();

	if (!enabled) {
		return true; // 未启用时始终可见
	}

	// 从全局时钟派生可见性，所有组件共享同一时钟
	const phase = Math.floor(timestamp / BLINK_INTERVAL_MS) % 2;
	return phase === 0;
}
