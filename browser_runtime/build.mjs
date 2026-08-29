/**
 * IllusionAgent Browser Use 运行时构建脚本。
 *
 * 产物布局（自包含，hatch force-include 整目录注入 wheel）：
 *   dist/mcp-server.js      esbuild 产物（stdio MCP 服务器 + vm 内核 + broker 客户端）
 *   dist/browser-client.mjs 内核内加载的 agent.browsers 客户端（静态资产，直接复制）
 *   dist/docs/              documentationRoot（api.json / documents.json / markdown 文档）
 *
 * esbuild banner 注入真实 createRequire 的原因与官方插件一致：
 * format: "esm" 打包时 CJS 依赖里的 require() 会被替换为 __require shim，
 * ESM 模块作用域没有 require，shim 永远走抛错分支，产物会在模块求值阶段炸掉。
 */
import { chmod, copyFile, cp, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const packageRoot = import.meta.dirname;
const executableFileMode = 0o755;

const nodeRequireBanner = `import { createRequire as __illusionCreateRequire } from "node:module";
const require = __illusionCreateRequire(import.meta.url);`;

const createBundleOptions = ({ entryPoint, outfile }) => ({
  banner: { js: nodeRequireBanner },
  bundle: true,
  entryPoints: [entryPoint],
  format: "esm",
  legalComments: "none",
  outfile,
  platform: "node",
  target: "node20",
});

export const buildBrowserRuntime = async ({ packageRoot: root = packageRoot } = {}) => {
  const distDir = resolve(root, "dist");
  await Promise.all([
    mkdir(distDir, { recursive: true }),
    mkdir(resolve(distDir, "docs"), { recursive: true }),
  ]);

  await Promise.all([
    build(
      createBundleOptions({
        entryPoint: resolve(root, "src", "mcp-server.ts"),
        outfile: resolve(distDir, "mcp-server.js"),
      }),
    ),
    build(
      createBundleOptions({
        entryPoint: resolve(root, "src", "lib.ts"),
        outfile: resolve(distDir, "lib.js"),
      }),
    ),
    copyFile(
      resolve(root, "vendor", "browser-client.mjs"),
      resolve(distDir, "browser-client.mjs"),
    ),
    cp(resolve(root, "docs"), resolve(distDir, "docs"), { recursive: true }),
  ]);

  await chmod(resolve(distDir, "mcp-server.js"), executableFileMode);
  return { distDir };
};

const entryPath = process.argv[1];
if (entryPath && import.meta.url === pathToFileURL(entryPath).href) {
  await buildBrowserRuntime();
  console.log("browser_runtime dist built");
}
