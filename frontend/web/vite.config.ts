import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// 从 pyproject.toml 读取版本号
function getVersion(): string {
  try {
    const pyprojectPath = resolve(__dirname, '../../pyproject.toml');
    const content = readFileSync(pyprojectPath, 'utf-8');
    const match = content.match(/version\s*=\s*"([^"]+)"/);
    return match ? match[1] : '0.0.0';
  } catch {
    return '0.0.0';
  }
}

// manualChunks：把体积大的第三方库拆成独立 chunk，避免主 bundle 超限。
// 拆分必须满足"无循环依赖"：react-vendor 只装纯净的 react 运行时，
// highlight 零依赖可独立，其余第三方一律归 vendor。
// 注意不要在这里细分 markdown 生态（remark/rehype 等）——它们与
// react-is、property-information 等通用小包彼此交叉引用，强制分块会
// 触发 "Circular chunk" 警告。让它们统一留在 vendor，由 Rollup 内部归并。
function manualChunks(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined;
  // 仅精确匹配 react 运行时目录（react/react-dom/scheduler）。
  // 用分隔符约束避免误匹配 react-markdown、react-is 等，防止与
  // markdown/vendor 形成循环依赖。
  if (/[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react-vendor';
  // highlight.js 语言高亮库体积大且几乎零依赖，可安全独立拆包
  if (id.includes('highlight.js') || id.includes('lowlight')) return 'highlight';
  // 其余第三方依赖统一归入 vendor chunk
  return 'vendor';
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(getVersion()),
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:3000',
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
    // 拆分后的 vendor chunk（markdown 生态 + 全部第三方）体积较大，
    // 统一调高阈值避免误报警——真正的拆包已通过 manualChunks 完成
    chunkSizeWarningLimit: 700,
  },
});
