/**
 * @fileoverview 欢迎屏幕组件
 *
 * Web 前端的欢迎屏幕组件，在会话开始时显示。
 * 显示应用 Logo 和副标题，输入框与工具栏由上层注入到标题下方。
 *
 * - 背景：纯净（无装饰，以玻璃拟态界面自身为视觉主体）
 * - 标题：暖色渐变文字（Playfair Display 衬线），鼠标扫过时迸发火花
 * - 副标题：等宽字体精致化，与主标题地位等价（扫过触发相同动画）
 *
 * @module WelcomeScreen
 */

import { useEffect, useRef, useState } from 'react';
import type { MouseEvent, ReactNode } from 'react';

/** 每簇火花数量（10 向放射） */
const SPARK_COUNT = 10;
/** 火花线初始长度（px） */
const SPARK_SIZE = 10;
/** 单簇扩散半径范围（px）：各簇大小略有差异，更自然 */
const SPARK_RADIUS_MIN = 28;
const SPARK_RADIUS_MAX = 52;
/** 火花动画时长（ms） */
const SPARK_DURATION = 500;

/** 火花粒子：从簇中心沿 angle 方向外扩并缩短 */
interface Spark {
  x: number;
  y: number;
  angle: number;
  startTime: number;
  /** 所属簇的扩散半径（px） */
  radius: number;
}

/**
 * 读取当前主题的火花颜色（深色用亮白、浅色用杏橙）
 *
 * @returns 十六进制颜色字符串
 */
function readSparkColor(): string {
  const root = document.documentElement;
  const css = getComputedStyle(root);
  if (root.classList.contains('dark')) {
    return css.getPropertyValue('--text-primary').trim() || '#eaeaea';
  }
  return '#f6a866';
}

/**
 * 欢迎屏幕组件
 *
 * 在会话开始时显示应用 Logo 和副标题，输入框由上层注入到标题下方。
 * 鼠标扫过主标题或副标题：火花迸发 + 主/副标题同频闪光
 * （动画结束后自动回到静态；动画进行中扫过被忽略）。
 *
 * @param props - 组件属性
 * @returns 返回欢迎屏幕的 JSX 元素
 */
export default function WelcomeScreen({ children }: WelcomeScreenProps) {
  /**
   * 闪光状态：鼠标扫过置 true 触发 .flash-once 动画，动画结束（animationend）自动复位。
   * 动画进行中鼠标再次扫过被忽略（guard 返回），制造"难以掌控"的交互感。
   */
  const [flashing, setFlashing] = useState(false);

  /** 欢迎屏根容器 ref（火花坐标基准） */
  const containerRef = useRef<HTMLDivElement>(null);
  /** 副标题 ref（火花散布区域下边界） */
  const subtitleRef = useRef<HTMLParagraphElement>(null);
  /** 火花 Canvas 与粒子状态（ref 持有，不触发重渲染） */
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sparksRef = useRef<Spark[]>([]);
  const rafIdRef = useRef(0);
  /** 火花颜色缓存（触发时从主题读取） */
  const sparkColorRef = useRef('#2a9d99');

  /** 火花散布区域的外扩边距（px） */
  const SPARK_AREA_PAD = 36;

  /**
   * 绘制一帧火花：粒子外扩 + 线长收缩，超时移除；全部消失后停止 rAF
   */
  const drawSparks = (now: number) => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const alive = sparksRef.current.filter(s => {
      const elapsed = now - s.startTime;
      if (elapsed >= SPARK_DURATION) return false;
      const progress = elapsed / SPARK_DURATION;
      const eased = progress * (2 - progress); // ease-out
      const distance = eased * s.radius;
      const lineLength = SPARK_SIZE * (1 - eased);
      const x1 = s.x + distance * Math.cos(s.angle);
      const y1 = s.y + distance * Math.sin(s.angle);
      const x2 = s.x + (distance + lineLength) * Math.cos(s.angle);
      const y2 = s.y + (distance + lineLength) * Math.sin(s.angle);
      ctx.strokeStyle = sparkColorRef.current;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      return true;
    });
    sparksRef.current = alive;
    if (alive.length > 0) {
      rafIdRef.current = requestAnimationFrame(drawSparks);
    } else {
      rafIdRef.current = 0;
    }
  };

  /**
   * 从 (x, y, w, h) 区域内在随机位置迸发一簇径向火花
   */
  const emitSparks = (areaX: number, areaY: number, areaW: number, areaH: number) => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;
    const rect = parent.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * devicePixelRatio));
    canvas.height = Math.max(1, Math.floor(rect.height * devicePixelRatio));

    sparkColorRef.current = readSparkColor();
    const now = performance.now();
    const cx = areaX + Math.random() * areaW;
    const cy = areaY + Math.random() * areaH;
    const radius = SPARK_RADIUS_MIN + Math.random() * (SPARK_RADIUS_MAX - SPARK_RADIUS_MIN);
    const startAngle = Math.random() * Math.PI * 2;
    for (let i = 0; i < SPARK_COUNT; i++) {
      sparksRef.current.push({
        x: cx,
        y: cy,
        angle: startAngle + (2 * Math.PI * i) / SPARK_COUNT,
        startTime: now,
        radius,
      });
    }
    if (rafIdRef.current === 0) {
      rafIdRef.current = requestAnimationFrame(drawSparks);
    }
  };

  // 卸载时停止火花动画循环
  useEffect(() => () => cancelAnimationFrame(rafIdRef.current), []);

  /**
   * 鼠标扫过主标题/副标题：火花迸发 + 主副标题同频闪光；
   * 动画期间扫过无效（guard），动画结束后才可再次触发
   */
  const handleTitleMouseEnter = (e: MouseEvent<HTMLElement>) => {
    if (flashing) return;
    const rect = containerRef.current?.getBoundingClientRect();
    const titleRect = e.currentTarget.getBoundingClientRect();
    const subRect = subtitleRef.current?.getBoundingClientRect();
    if (!rect) return;
    emitSparks(
      Math.min(titleRect.left, subRect?.left ?? titleRect.left) - rect.left - SPARK_AREA_PAD,
      Math.min(titleRect.top, subRect?.top ?? titleRect.top) - rect.top - SPARK_AREA_PAD,
      Math.max(titleRect.right, subRect?.right ?? titleRect.right) -
        Math.min(titleRect.left, subRect?.left ?? titleRect.left) +
        SPARK_AREA_PAD * 2,
      Math.max(titleRect.bottom, subRect?.bottom ?? titleRect.bottom) -
        Math.min(titleRect.top, subRect?.top ?? titleRect.top) +
        SPARK_AREA_PAD * 2
    );
    setFlashing(true);
  };

  return (
    <div ref={containerRef} className="h-full flex flex-col items-center overflow-y-auto select-text relative scrollbar-hidden">
      {/* 内容块：m-auto 垂直居中；内容超高时自动滚动且顶部可达（避免 justify-center 裁切） */}
      <div className="m-auto flex flex-col items-center w-full max-w-[var(--composer-card-max-width)] px-6 md:px-10 lg:px-16 pt-6 pb-14 relative z-10">
        {/* Logo — 暖色渐变文字（静态；鼠标扫过时迸发火花 + 与副标题同频闪光扫过一轮） */}
        {/* leading-tight 而非 text-6xl 默认的 line-height:1：背景渐变（background-clip:text）
          只绘制在元素盒内，line-height:1 时 "g" 的 descender 溢出元素盒导致下半部分无背景
          （文字透明不可见），视觉上像被下方副标题截断 */}
        <h1
          className={`gradient-text text-6xl leading-tight font-bold tracking-tight select-none ${flashing ? 'flash-once' : ''}`}
          style={{ fontFamily: "'Playfair Display', Georgia, serif" }}
          onMouseEnter={handleTitleMouseEnter}
          onAnimationEnd={() => setFlashing(false)}
        >
          Illusion Agent
        </h1>

        {/* 副标题 — 等宽字体精致化；与主标题地位等价：鼠标扫过同样触发
            火花 + 同频闪光（不可点击、不可选中，仅扫过触发） */}
        <p
          ref={subtitleRef}
          className={`mt-6 font-mono text-[13px] tracking-[0.4em] uppercase shiny-text select-none ${flashing ? 'flash-once' : ''}`}
          onMouseEnter={handleTitleMouseEnter}
          onAnimationEnd={() => setFlashing(false)}
        >
          Where fantasy meets functionality
        </p>

        {/* 输入框 + 工具栏（欢迎态由上层注入到标题/副标题下方） */}
        {children && (
          <div className="w-full mt-12 shrink-0">
            {children}
          </div>
        )}
      </div>

      {/* 火花画布：铺满整个欢迎屏，扩散范围覆盖标题周边，不拦截鼠标 */}
      <canvas
        ref={canvasRef}
        aria-hidden
        className="absolute inset-0 pointer-events-none z-20"
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  );
}

/**
 * WelcomeScreen 组件属性接口
 */
interface WelcomeScreenProps {
  /** 注入到标题下方的内容（输入框 + 工具栏卡片） */
  children?: ReactNode;
}
