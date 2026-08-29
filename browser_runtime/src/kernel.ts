/**
 * vm 内核 —— 每次 js 调用一个全新上下文。
 *
 * 语义（与 browser-use 插件 skill 文档一致）：
 *   - 变量、import、模块缓存、browser/tab 绑定不跨调用持久化
 *   - `js_reset` 仅作为兼容屏障（下一次调用本来就是全新内核）
 *   - `js_add_node_module_dir` 的模块搜索根属于 MCP 服务器进程状态，
 *     对后续的新内核生效
 *
 * 结果捕获：用 meriyah 解析源码，把最后一个顶层表达式语句改写为
 * `return`，再以 `(async () => { ... })()` 包裹执行——既支持顶层 await，
 * 又让 `{ a, b }` 这类"单元格末尾表达式"成为该次调用的返回值。
 */

import { createContext, runInContext } from "node:vm";
import { createRequire } from "node:module";
import { homedir, tmpdir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { inspect } from "node:util";
import { parse } from "meriyah";
import { injectBrowserBridge, defaultDocumentationRoot } from "./bridge.ts";
import { type BrowserBrokerClient } from "./broker.ts";
import { saveImageArtifact } from "./artifacts.ts";

/** 默认单次调用整体超时（含异步浏览器命令等待）。 */
export const DEFAULT_JS_TIMEOUT_MS = 120_000;

export interface JsRunOutput {
  /** 单元格末尾表达式的值（无则缺省）。 */
  result?: unknown;
  /** console 输出（tee 捕获）。 */
  logs: string;
  /** 未捕获异常。 */
  error?: { name: string; message: string; stack?: string };
  /** nodeRepl.emitImage 收集的图片（tab.screenshot 结果等）。 */
  images: { base64: string; mimeType: string }[];
  /** 图片落盘的 artifact 绝对路径（与 images 一一对应）。 */
  browserScreenshotPaths: string[];
  /** nodeRepl.setResponseMeta 合并的响应元数据。 */
  responseMeta?: Record<string, unknown>;
}

export interface KernelOptions {
  /** 插件运行时根（含 browser-client.mjs 与 docs/）。 */
  pluginRoot: string;
  /** artifact 落盘根（ILLUSION_CONFIG_DIR）。 */
  configDir: string;
  /** js_add_node_module_dir 累积的模块搜索根（进程级状态）。 */
  moduleDirs: string[];
  /** broker 客户端（browserUseEnabled 时注入 bridge）。 */
  broker: BrowserBrokerClient | null;
  /** 内核工作目录（工作区目录；require/import 解析基准）。 */
  cwd: string;
  /** 单次调用超时毫秒。 */
  timeoutMs?: number;
}

/** 内核可访问的环境变量白名单（不透传 API key 等敏感项）。 */
const ENV_ALLOWLIST = [
  "ILLUSION_PLUGIN_ROOT",
  "ZCODE_PLUGIN_ROOT",
  "CLAUDE_PLUGIN_ROOT",
  "ILLUSION_CONFIG_DIR",
  "ILLUSION_SESSION_ID",
  "HOME",
  "USERPROFILE",
  "TEMP",
  "TMP",
  "OS",
  "PATH",
  "APPDATA",
  "LOCALAPPDATA",
  "PROGRAMFILES",
  "SYSTEMROOT",
  "WINDIR",
];

interface TeeConsole {
  log: (...args: unknown[]) => void;
  info: (...args: unknown[]) => void;
  warn: (...args: unknown[]) => void;
  error: (...args: unknown[]) => void;
  debug: (...args: unknown[]) => void;
}

interface NodeReplApi {
  cwd: string;
  homeDir: string;
  tmpDir: string;
  write: (text: string) => void;
  emitImage: (image: unknown) => Promise<void>;
  setResponseMeta: (meta: Record<string, unknown>) => void;
}

export class JsKernelRunner {
  private readonly options: KernelOptions;
  private responseMeta: Record<string, unknown> = {};

  constructor(options: KernelOptions) {
    this.options = options;
  }

  /** 执行一段 JS 并返回结构化输出（fresh context；不跨调用保留状态）。 */
  async run(code: string): Promise<JsRunOutput> {
    const logs: string[] = [];
    const writes: string[] = [];
    const images: JsRunOutput["images"] = [];
    const screenshotPaths: string[] = [];
    this.responseMeta = {};

    const { context, cleanup } = this.buildContext({
      pushLog: (line) => {
        if (logs.length < 2_000) {
          logs.push(line);
        }
      },
      pushWrite: (text) => {
        if (writes.length < 2_000) {
          writes.push(text);
        }
      },
      pushImage: async (image) => {
        const saved = await saveImageArtifact(image, this.options.configDir);
        images.push({ base64: saved.base64, mimeType: saved.mimeType });
        screenshotPaths.push(saved.path);
      },
    });

    const prepared = prepareUserCode(code);
    const wrapped = `(async () => {\n${prepared}\n})()`;
    const timeoutMs = this.options.timeoutMs ?? DEFAULT_JS_TIMEOUT_MS;
    try {
      const promise = runInContext(wrapped, context, {
        timeout: timeoutMs,
        displayErrors: true,
        breakOnSigint: true,
      });
      const result = await Promise.race([
        Promise.resolve(promise),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error(`js call timed out after ${timeoutMs}ms`)), timeoutMs),
        ),
      ]);
      return this.finalize(result, logs, writes, images, screenshotPaths);
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      const output = this.finalize(undefined, logs, writes, images, screenshotPaths);
      output.error = {
        name: err.name,
        message: err.message,
        stack: typeof err.stack === "string" ? err.stack.split("\n").slice(0, 20).join("\n") : undefined,
      };
      return output;
    } finally {
      // 内核生命周期结束：清空该 cell 注册的 timers/intervals，避免后台句柄泄漏
      cleanup();
    }
  }

  private finalize(
    result: unknown,
    logs: string[],
    writes: string[],
    images: JsRunOutput["images"],
    screenshotPaths: string[],
  ): JsRunOutput {
    const output: JsRunOutput = {
      logs: [...writes, ...logs].join("\n"),
      images,
      browserScreenshotPaths: screenshotPaths,
    };
    if (result !== undefined) {
      output.result = result;
    }
    if (Object.keys(this.responseMeta).length > 0) {
      output.responseMeta = this.responseMeta;
    }
    return output;
  }

  private buildContext(hooks: {
    pushLog: (line: string) => void;
    pushWrite: (text: string) => void;
    pushImage: (image: unknown) => Promise<void>;
  }): { context: ReturnType<typeof createContext>; cleanup: () => void } {
    const timers: NodeJS.Timeout[] = [];
    const intervals: NodeJS.Timeout[] = [];
    const sandbox = this.buildSandbox(hooks, timers, intervals);
    const context = createContext(sandbox);
    // 常规 globals（createContext 只做 sandbox 合并，标准内建需要显式补齐）
    injectStandardGlobals(context);
    if (this.options.broker) {
      injectBrowserBridge(context as unknown as Record<symbol | string, unknown>, {
        broker: this.options.broker,
        documentationRoot: defaultDocumentationRoot(this.options.pluginRoot),
      });
    }
    const cleanup = (): void => {
      for (const timer of timers) {
        clearTimeout(timer);
      }
      for (const timer of intervals) {
        clearInterval(timer);
      }
    };
    return { context, cleanup };
  }

  private buildSandbox(
    hooks: { pushLog: (line: string) => void; pushWrite: (text: string) => void; pushImage: (image: unknown) => Promise<void> },
    timers: NodeJS.Timeout[],
    intervals: NodeJS.Timeout[],
  ): Record<string, unknown> {
    const format = (args: unknown[]): string =>
      args
        .map((arg) => (typeof arg === "string" ? arg : safeInspect(arg)))
        .join(" ");
    const teeConsole: TeeConsole = {
      log: (...args) => hooks.pushLog(format(args)),
      info: (...args) => hooks.pushLog(format(args)),
      warn: (...args) => hooks.pushLog(`[warn] ${format(args)}`),
      error: (...args) => hooks.pushLog(`[error] ${format(args)}`),
      debug: (...args) => hooks.pushLog(`[debug] ${format(args)}`),
    };
    const restrictedEnv: Record<string, string> = {};
    for (const key of ENV_ALLOWLIST) {
      const value = process.env[key];
      if (value !== undefined) {
        restrictedEnv[key] = value;
      }
    }
    const cwd = resolve(this.options.cwd);
    const restrictedProcess: Record<string, unknown> = {
      cwd: () => cwd,
      platform: process.platform,
      version: process.version,
      pid: process.pid,
      env: restrictedEnv,
      hrtime: (start?: [number, number]) => process.hrtime(start),
      memoryUsage: () => process.memoryUsage(),
      nextTick: (fn: (...args: unknown[]) => void, ...args: unknown[]) =>
        queueMicrotask(() => fn(...args)),
    };
    const hostRequire = createRequire(`${cwd}/`);
    const nodeReplApi: NodeReplApi = {
      cwd,
      homeDir: homedir(),
      tmpDir: tmpdir(),
      write: (text) => hooks.pushWrite(String(text)),
      emitImage: (image) => hooks.pushImage(image),
      setResponseMeta: (meta) => {
        this.responseMeta = { ...this.responseMeta, ...meta };
      },
    };
    return {
      console: teeConsole,
      process: restrictedProcess,
      Buffer,
      URL,
      URLSearchParams,
      TextEncoder,
      TextDecoder,
      setTimeout: (fn: (...args: unknown[]) => void, ms: number, ...args: unknown[]) => {
        const timer = setTimeout(() => fn(...args), ms);
        timers.push(timer);
        return timer;
      },
      clearTimeout: (timer: NodeJS.Timeout) => clearTimeout(timer),
      setInterval: (fn: (...args: unknown[]) => void, ms: number, ...args: unknown[]) => {
        const timer = setInterval(() => fn(...args), ms);
        intervals.push(timer);
        return timer;
      },
      clearInterval: (timer: NodeJS.Timeout) => clearInterval(timer),
      queueMicrotask,
      structuredClone,
      fetch: (...args: Parameters<typeof fetch>) => fetch(...args),
      require: hostRequire,
      importModule: async (specifier: string) => {
        const resolved = resolveSpecifier(specifier, cwd, this.options.moduleDirs, hostRequire);
        return import(resolved);
      },
      nodeRepl: nodeReplApi,
    };
  }
}

/** 规范化模块说明符为可 import 的 URL / 路径（支持相对、绝对与裸说明符）。 */
function resolveSpecifier(
  specifier: string,
  cwd: string,
  moduleDirs: string[],
  hostRequire: NodeJS.Require,
): string {
  if (specifier.startsWith("node:")) {
    return specifier;
  }
  if (specifier.startsWith("file://")) {
    return specifier;
  }
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    return pathToFileURL(join(cwd, specifier)).href;
  }
  if (isAbsolute(specifier)) {
    return pathToFileURL(specifier).href;
  }
  // 裸说明符：cwd + 追加的模块根（js_add_node_module_dir）依次解析
  const searchPaths = [cwd, ...moduleDirs];
  try {
    const resolvedPath = hostRequire.resolve(specifier, { paths: searchPaths });
    // CJS 模块同样经 ESM import 加载（Node 的 CJS-ESM 互操作）
    return pathToFileURL(resolvedPath).href;
  } catch {
    return specifier; // 交给宿主 import() 抛出原生错误
  }
}

/** 把标准内建与 Symbol.toStringTag 补进 vm context（createContext 不复制内建）。 */
function injectStandardGlobals(context: ReturnType<typeof createContext>): void {
  const globals: Record<string, unknown> = {
    Object,
    Array,
    String,
    Number,
    Boolean,
    Symbol,
    BigInt,
    Math,
    JSON,
    Date,
    RegExp,
    Error,
    EvalError,
    RangeError,
    ReferenceError,
    SyntaxError,
    TypeError,
    URIError,
    AggregateError,
    Map,
    Set,
    WeakMap,
    WeakSet,
    WeakRef,
    Promise,
    ArrayBuffer,
    SharedArrayBuffer,
    DataView,
    Int8Array,
    Uint8Array,
    Uint8ClampedArray,
    Int16Array,
    Uint16Array,
    Int32Array,
    Uint32Array,
    Float32Array,
    Float64Array,
    BigInt64Array,
    BigUint64Array,
    Atomics,
    Reflect,
    Proxy,
    Intl,
    globalThis: context,
  };
  for (const [key, value] of Object.entries(globals)) {
    if (!(key in context)) {
      Object.defineProperty(context, key, {
        value,
        writable: true,
        enumerable: true,
        configurable: true,
      });
    }
  }
}

/** 解析源码，将最后一个顶层表达式语句改写为 return（解析失败则原样返回）。 */
export function prepareUserCode(code: string): string {
  let ast: unknown;
  try {
    ast = parse(code, { module: true, next: true, webcompat: true, ranges: true });
  } catch {
    return code;
  }
  const body = (ast as { body?: { type: string; range?: [number, number] }[] } | null)?.body;
  if (!Array.isArray(body) || body.length === 0) {
    return code;
  }
  const last = body[body.length - 1];
  const statementRange = last?.type === "ExpressionStatement" ? last.range : undefined;
  if (!statementRange) {
    return code;
  }
  // 语句 range 含 ASI 补的分号：截取语句文本后剥掉尾部分号，
  // 得到完整（含括号）的末尾表达式，改写为 return 语句
  let statementText = code.slice(statementRange[0], statementRange[1]).trimEnd();
  while (statementText.endsWith(";")) {
    statementText = statementText.slice(0, -1).trimEnd();
  }
  const head = code.slice(0, statementRange[0]);
  return `${head}return ${statementText};`;
}

function safeInspect(value: unknown): string {
  try {
    return inspect(value, { depth: 4, maxArrayLength: 100, breakLength: 120 });
  } catch {
    return String(value);
  }
}
