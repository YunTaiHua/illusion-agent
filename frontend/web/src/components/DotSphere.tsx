/**
 * @fileoverview 粒子球体动画（连接遮罩层视觉，替代莫比乌斯环）
 *
 * 斐波那契球面均匀采样 + 绕竖直轴 Rodrigues 旋转 + 透视投影。
 * 立体感完全由粒子的大小/透明度梯度表达：受光面粒子更大更实，
 * 背光边缘粒子缩小变淡。零外部依赖，Canvas 2D 实现。
 *
 * 视觉参数与 HTML 预览（.illusion/temp/mask_dot_sphere_preview.html）定稿一致；
 * Desktop 加载页（desktop/src/main.ts 内联版）与本组件保持对称。
 *
 * @module DotSphere
 */

import { useEffect, useRef } from 'react';

/** 球体直径（px） */
const DIAMETER = 170;
/** 画布内边距（px）：避免边缘粒子被画布裁剪 */
const PAD = 12;
/** 球面粒子数量 */
const PARTICLE_COUNT = 120;
/** 旋转角速度（rad/s），绕竖直轴 */
const ROTATION_SPEED = 1.15;
/** 大小对比（0-1）：受光/背光粒子的半径差强度 */
const SIZE_CONTRAST = 1;
/** 深浅对比（0-1）：受光/背光粒子的透明度差强度 */
const ALPHA_CONTRAST = 0.7;
/** 粒子整体大小倍率 */
const PARTICLE_SCALE = 1.3;
/** 光面强度（0-1）：光照项在明暗混合中的权重 */
const LIGHT_INTENSITY = 0.7;
/** 粒子颜色：磷光青（与浅色主题标题「群青海岸」磷光带同族） */
const COLOR = '#2a9d99';

/** 三维向量（元组类型：索引访问不会产生 undefined） */
type Vec3 = [number, number, number];

/** 光照方向：左上前方（归一化） */
const LIGHT: Vec3 = (() => {
  const v: Vec3 = [-0.35, -0.45, 0.83];
  const m = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / m, v[1] / m, v[2] / m];
})();

/** 斐波那契球：单位球面均匀采样（点距均匀，无经纬网格感） */
function fibonacci(n: number): Vec3[] {
  const pts: Vec3[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = golden * i;
    pts.push([Math.cos(th) * r, y, Math.sin(th) * r]);
  }
  return pts;
}

/** Rodrigues 旋转：向量 v 绕单位轴 k 旋转（ca/sa 为夹角余弦/正弦） */
function rotate(v: Vec3, k: Vec3, ca: number, sa: number): Vec3 {
  const dot = k[0] * v[0] + k[1] * v[1] + k[2] * v[2];
  const cx = k[1] * v[2] - k[2] * v[1];
  const cy = k[2] * v[0] - k[0] * v[2];
  const cz = k[0] * v[1] - k[1] * v[0];
  return [
    v[0] * ca + cx * sa + k[0] * dot * (1 - ca),
    v[1] * ca + cy * sa + k[1] * dot * (1 - ca),
    v[2] * ca + cz * sa + k[2] * dot * (1 - ca),
  ];
}

/** 单个粒子的投影结果 */
interface Projected {
  x: number;
  y: number;
  scale: number;
  z: number;
  lit: number;
  depth: number;
}

/**
 * 粒子球体组件
 *
 * @returns 粒子球 Canvas 元素
 */
export default function DotSphere() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const size = DIAMETER + PAD * 2;
    canvas.width = size * dpr;
    canvas.height = size * dpr;

    const R = (DIAMETER / 2) * 0.92;
    const c = size / 2;
    const persp = 620;
    const pts = fibonacci(PARTICLE_COUNT);

    const draw = (time: number): void => {
      const angle = time * ROTATION_SPEED;
      const ca = Math.cos(angle);
      const sa = Math.sin(angle);

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);
      ctx.fillStyle = COLOR;

      const proj: Projected[] = [];
      for (const p of pts) {
        const rp = rotate(p, [0, 1, 0], ca, sa);
        const scale = persp / (persp + rp[2] * R);
        const lit = Math.max(0, rp[0] * LIGHT[0] + rp[1] * LIGHT[1] + rp[2] * LIGHT[2]);
        const depth = (rp[2] + 1) / 2;
        proj.push({ x: c + rp[0] * R * scale, y: c + rp[1] * R * scale, scale, z: rp[2], lit, depth });
      }
      proj.sort((a, b) => a.z - b.z); // 远 → 近，保证前面粒子覆盖后面

      for (const q of proj) {
        // 明暗混合：深度与光照加权（光照权重随光面强度增强）
        const litW = 0.35 + 0.45 * LIGHT_INTENSITY;
        const mix = (1 - litW) * q.depth + litW * Math.pow(q.lit, 1.15);

        // 大小：SIZE_CONTRAST 控制半径在 [rMin, rMax] 间的分布
        const rMin = 0.9 * PARTICLE_SCALE;
        const rMax = 7.2 * PARTICLE_SCALE;
        const r = (rMin + (rMax - rMin) * (SIZE_CONTRAST * mix + (1 - SIZE_CONTRAST) * 0.45)) * q.scale;

        // 深浅：ALPHA_CONTRAST 控制 alpha 在 [aMin, 1] 间的分布
        const aMin = 0.05;
        const alpha = aMin + (1 - aMin) * (ALPHA_CONTRAST * mix + (1 - ALPHA_CONTRAST) * 0.5);

        ctx.globalAlpha = Math.min(1, alpha);
        ctx.beginPath();
        ctx.arc(q.x, q.y, r * 0.5, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    // prefers-reduced-motion：静态渲染一帧，不做旋转动画
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      draw(0);
      return;
    }

    let raf = 0;
    const start = performance.now();
    const loop = (now: number): void => {
      draw((now - start) / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={canvasRef} style={{ width: DIAMETER + PAD * 2, height: DIAMETER + PAD * 2 }} aria-hidden="true" />;
}
