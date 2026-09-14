/**
 * @fileoverview 流式平滑揭示 hook
 *
 * 将分块到达的模型输出按自适应节奏逐帧揭示，避免大段文本瞬间砸落，
 * 也避免高速流下逐字重排卡顿。
 * - 到达率 EMA + chunk 大小 EMA
 * - 积压队列压力积分（v = base + backlog^1.25 × pressure）
 * - 实时滞后上限与空闲 settle 排水
 *
 * @module useSmoothReveal
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** 到达率 EMA 平滑系数 */
const EMA_ALPHA = 0.35;
/** 无输入视为活跃的窗口（ms）：窗口内到达仍算 live */
const ACTIVE_INPUT_WINDOW_MS = 220;
/** 空闲多久后进入 settle 加速排水（ms） */
const SETTLE_AFTER_MS = 280;
/** 最小揭示速度（chars/s），防止慢速卡住观感 */
const MIN_CPS = 24;
/** 空闲 flush 基础速度（chars/s） */
const FLUSH_CPS = 180;
/** settle/追赶排水上限（chars/s）：大首包/高速流需能在数百 ms 内清空积压 */
const MAX_FLUSH_CPS = 1200;
/** 实时滞后字符上限：超出部分加速追赶 */
const LIVE_LAG_CHAR_CEILING = 32;
/** 追赶时间常数（s） */
const CATCHUP_SECONDS = 0.15;
/** settle 排水时长下限/上限（ms） */
const SETTLE_DRAIN_MIN_MS = 120;
const SETTLE_DRAIN_MAX_MS = 420;
/** 队列压力：base + backlog^exp × pressure */
const QUEUE_BASE_SPEED_CPS = 90;
const QUEUE_ACCEL_EXPONENT = 1.25;
const QUEUE_PRESSURE_FACTOR = 0.85;

/** 数值钳制 */
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * 数字累积速度：按积压深度加速揭示。
 *
 * @param backlog - 未揭示字符数
 * @param dtMs - 帧间隔（ms）
 * @param debt - 上一帧残留的分数积分
 * @returns 本帧揭示字符数与新 debt
 */
function computeQueueStep(
  backlog: number,
  dtMs: number,
  debt: number,
): { revealChars: number; debt: number; speedCps: number } {
  if (backlog <= 0 || dtMs <= 0)
    return { revealChars: 0, debt: 0, speedCps: 0 };
  const speedCps = Math.min(
    MAX_FLUSH_CPS,
    QUEUE_BASE_SPEED_CPS +
      Math.pow(backlog, QUEUE_ACCEL_EXPONENT) * QUEUE_PRESSURE_FACTOR,
  );
  const accumulated = Math.max(0, debt) + speedCps * (dtMs / 1000);
  const revealChars = Math.min(backlog, Math.floor(accumulated));
  return {
    revealChars,
    debt: revealChars >= backlog ? 0 : accumulated - revealChars,
    speedCps,
  };
}

/**
 * settle 排水速率：输入结束后在有界时间内清空积压。
 *
 * @param backlog - 未揭示字符数
 * @returns 目标 chars/s
 */
function computeSettleDrain(backlog: number): number {
  if (backlog <= 0) return 0;
  const overflow = Math.max(0, backlog - 300);
  const overflowCps = (overflow * 1000) / 2;
  const drainTargetMs = clamp(
    backlog * 8,
    SETTLE_DRAIN_MIN_MS,
    SETTLE_DRAIN_MAX_MS,
  );
  const settleCps = (backlog * 1000) / drainTargetMs;
  return clamp(Math.max(settleCps, overflowCps), FLUSH_CPS, MAX_FLUSH_CPS);
}

export interface UseSmoothRevealOptions {
  /** 关闭时直接透传 content（历史消息、无障碍 reduced-motion 等） */
  enabled?: boolean;
  /** 生产者已结束：以有界速度排空剩余积压 */
  inputComplete?: boolean;
}

/**
 * 平滑揭示分块流文本。
 *
 * @param content - 已累积的完整输入（只会追加）
 * @param options - enabled / inputComplete
 * @returns 当前帧应展示的文本前缀
 */
export function useSmoothReveal(
  content: string,
  { enabled = true, inputComplete = false }: UseSmoothRevealOptions = {},
): string {
  // 关闭时立即展示；开启时从空开始逐步揭示（首帧大包也进入队列）
  const initial = enabled ? "" : content;
  const [displayed, setDisplayed] = useState(initial);

  const displayedRef = useRef(initial);
  const displayedCountRef = useRef(countChars(initial));
  const targetRef = useRef(initial);
  const targetCharsRef = useRef([...initial]);
  const targetCountRef = useRef(countChars(initial));

  const emaCpsRef = useRef(80);
  const lastInputTsRef = useRef(0);
  const lastInputCountRef = useRef(0);
  const arrivalEmaRef = useRef(80);
  const debtRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastFrameTsRef = useRef<number | null>(null);
  const completeRef = useRef(inputComplete);
  completeRef.current = inputComplete;

  const stopLoop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    lastFrameTsRef.current = null;
  }, []);

  const syncImmediate = useCallback(
    (next: string) => {
      stopLoop();
      const chars = [...next];
      targetRef.current = next;
      targetCharsRef.current = chars;
      targetCountRef.current = chars.length;
      displayedRef.current = next;
      displayedCountRef.current = chars.length;
      debtRef.current = 0;
      emaCpsRef.current = 80;
      arrivalEmaRef.current = 80;
      lastInputTsRef.current = performance.now();
      lastInputCountRef.current = chars.length;
      setDisplayed(next);
    },
    [stopLoop],
  );

  const startLoop = useCallback(() => {
    if (rafRef.current !== null) return;

    const tick = (now: number) => {
      const targetCount = targetCountRef.current;
      const displayedCount = displayedCountRef.current;
      const backlog = targetCount - displayedCount;

      if (backlog <= 0) {
        debtRef.current = 0;
        stopLoop();
        return;
      }

      if (lastFrameTsRef.current === null) {
        lastFrameTsRef.current = now;
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      const frameMs = Math.max(0, now - lastFrameTsRef.current);
      const dtSeconds = Math.max(0.001, Math.min(frameMs / 1000, 0.12));
      lastFrameTsRef.current = now;

      const idleMs = now - lastInputTsRef.current;
      const producerDone = completeRef.current;
      const inputActive = !producerDone && idleMs <= ACTIVE_INPUT_WINDOW_MS;
      const settling =
        producerDone || (!inputActive && idleMs >= SETTLE_AFTER_MS);

      // 到达率追踪：live 时对齐 EMA，settle/completion 时有界排水
      const trackedCps = Math.max(emaCpsRef.current, arrivalEmaRef.current);
      const baseCps = clamp(trackedCps, MIN_CPS, MAX_FLUSH_CPS);

      let revealChars: number;
      if (producerDone || settling) {
        const drainCps = computeSettleDrain(backlog);
        const accumulated = Math.max(0, debtRef.current) + drainCps * dtSeconds;
        revealChars = Math.min(backlog, Math.floor(accumulated));
        debtRef.current =
          revealChars >= backlog ? 0 : accumulated - revealChars;
      } else if (inputActive) {
        const overflow = Math.max(0, backlog - LIVE_LAG_CHAR_CEILING);
        const catchup = overflow > 0 ? overflow / CATCHUP_SECONDS : 0;
        const currentCps = clamp(
          baseCps * 1.08 + catchup,
          MIN_CPS,
          MAX_FLUSH_CPS,
        );
        // 用统一的队列积分释放，保证 debt 连续
        const speedScaled = Math.min(MAX_FLUSH_CPS, currentCps);
        const accumulated =
          Math.max(0, debtRef.current) + speedScaled * dtSeconds;
        revealChars = Math.min(backlog, Math.floor(accumulated));
        debtRef.current =
          revealChars >= backlog ? 0 : accumulated - revealChars;
      } else {
        // 短暂空闲但仍算 live 窗口外：用压力队列平滑过渡
        const step = computeQueueStep(backlog, frameMs, debtRef.current);
        revealChars = step.revealChars;
        debtRef.current = step.debt;
      }

      // 至少揭示 1 字符（active 时），避免慢速完全停滞
      if (revealChars <= 0 && inputActive) {
        revealChars = Math.min(1, backlog);
        debtRef.current = 0;
      }

      if (revealChars > 0) {
        const nextCount = Math.min(displayedCount + revealChars, targetCount);
        const segment = targetCharsRef.current
          .slice(displayedCount, nextCount)
          .join("");
        if (segment) {
          const nextDisplayed = displayedRef.current + segment;
          displayedRef.current = nextDisplayed;
          displayedCountRef.current = nextCount;
          setDisplayed(nextDisplayed);
        }
        // 空 segment（索引异常）不再整段 snap，避免一帧倾泻全文
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
  }, [stopLoop]);

  useEffect(() => {
    if (!enabled) {
      syncImmediate(content);
      return;
    }

    const prevTarget = targetRef.current;
    if (content === prevTarget) return;

    const now = performance.now();
    // 非追加（回退/清空/新会话）：立即对齐，避免错位
    if (!content.startsWith(prevTarget)) {
      syncImmediate(content);
      return;
    }

    const appended = content.slice(prevTarget.length);
    const appendedChars = [...appended];
    targetRef.current = content;
    // concat 而非 push(...arr)：超大 delta 会触发引擎参数上限 RangeError
    targetCharsRef.current = targetCharsRef.current.concat(appendedChars);
    targetCountRef.current += appendedChars.length;

    const hadSample = lastInputTsRef.current > 0;
    const deltaChars = targetCountRef.current - lastInputCountRef.current;
    const deltaMs = Math.max(1, now - lastInputTsRef.current);
    if (hadSample && deltaChars > 0) {
      const instantCps = clamp(
        (deltaChars * 1000) / deltaMs,
        MIN_CPS,
        MAX_FLUSH_CPS * 3,
      );
      arrivalEmaRef.current =
        arrivalEmaRef.current * (1 - EMA_ALPHA) + instantCps * EMA_ALPHA;
      emaCpsRef.current =
        emaCpsRef.current * (1 - EMA_ALPHA) + instantCps * EMA_ALPHA;
    }

    lastInputTsRef.current = now;
    lastInputCountRef.current = targetCountRef.current;
    startLoop();
  }, [content, enabled, startLoop, syncImmediate]);

  useEffect(() => {
    return () => stopLoop();
  }, [stopLoop]);

  return displayed;
}

/** 按 Unicode 码点计数（避免代理对拆半） */
function countChars(text: string): number {
  let n = 0;
  for (const _ of text) {
    void _;
    n += 1;
  }
  return n;
}
