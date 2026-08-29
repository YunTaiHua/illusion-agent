/**
 * node_repl MCP 服务器（stdio）。
 *
 * 工具集与官方 browser-use 插件一致（模型可见名 mcp__node_repl__js 等）：
 *   - js                    在全新 Node 内核执行 JavaScript（顶层 await 支持）
 *   - js_reset              兼容屏障：丢弃当前绑定（下一调用本就全新内核）
 *   - js_add_node_module_dir  向模块解析根追加目录（对后续新内核生效）
 *
 * 环境变量：
 *   ILLUSION_PLUGIN_ROOT       运行时根（含 browser-client.mjs 与 docs/）
 *   ILLUSION_BROWSER_BROKER_HOST/PORT/TOKEN  broker 端点（缺失 = Browser Use 关闭）
 *   ILLUSION_CONFIG_DIR        artifact 落盘根
 *   ILLUSION_SESSION_ID        会话标识（透传给 broker）
 */

import { homedir } from "node:os";
import { resolve } from "node:path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { BrowserBrokerClient, brokerEndpointFromEnv } from "./broker.ts";
import { JsKernelRunner, type JsRunOutput } from "./kernel.ts";

const JsInputSchema = z.object({
  code: z.string().min(1),
  title: z.string().optional(),
});

const JsAddNodeModuleDirInputSchema = z.object({
  path: z.string().min(1),
});

const TOOL_DEFINITIONS = [
  {
    name: "js",
    description:
      "Browser Use only. Run JavaScript in a fresh Node-backed kernel with top-level await only as instructed by the Browser Use skill to control a browser. Do not use it as a general-purpose JavaScript runtime or for filesystem, shell, package inspection, data processing, or other non-browser work. Always provide the required `title` as a short user-facing description in the user's language without implementation terms. If `timeout_ms` is omitted, execution times out after 120000 ms. If the code may take more than 30000 ms including all awaited operations, set `timeout_ms` to at least the estimated total runtime plus 15000 ms; split the work into multiple calls if that exceeds the 120000 ms maximum. Global bindings and module cache do not persist across calls — bootstrap Browser Use in every call. Use dynamic imports such as `await import(\"pkg\")`.",
    inputSchema: {
      type: "object" as const,
      properties: {
        code: { type: "string", description: "JavaScript code to execute in the Node REPL kernel" },
        title: {
          type: "string",
          description:
            "Short user-facing description in the user's language that describes the intended action without implementation terms",
        },
      },
      required: ["code"],
      additionalProperties: false,
    },
  },
  {
    name: "js_reset",
    description:
      "Browser Use only. Compatibility barrier for callers that still request a JavaScript kernel reset. Every `js` call already starts in a fresh kernel, so this does not clear per-session module search roots added by js_add_node_module_dir.",
    inputSchema: { type: "object" as const, properties: {}, additionalProperties: false },
  },
  {
    name: "js_add_node_module_dir",
    description:
      "Browser Use only. Add an absolute `node_modules` directory to the current session's Node module search roots for future fresh calls. The directory stays available across `js` calls. Returns true when newly added and false when already present.",
    inputSchema: {
      type: "object" as const,
      properties: {
        path: { type: "string", description: "Absolute path to a node_modules directory." },
      },
      required: ["path"],
      additionalProperties: false,
    },
  },
];

async function main(): Promise<void> {
  const pluginRoot = resolve(
    process.env.ILLUSION_PLUGIN_ROOT ?? process.env.ZCODE_PLUGIN_ROOT ?? process.cwd(),
  );
  const configDir = process.env.ILLUSION_CONFIG_DIR || resolve(homedir(), ".illusion");
  const moduleDirs: string[] = [];

  const endpoint = brokerEndpointFromEnv(process.env);
  const broker = endpoint ? new BrowserBrokerClient(endpoint) : null;
  const kernel = new JsKernelRunner({
    pluginRoot,
    configDir,
    moduleDirs,
    broker,
    cwd: process.env.ILLUSION_WORKSPACE_CWD || process.cwd(),
    timeoutMs: readTimeoutMs(process.env.ILLUSION_JS_TIMEOUT_MS),
  });

  const server = new Server(
    { name: "illusion-node-repl", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOL_DEFINITIONS,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const name = request.params.name;
    const args = (request.params.arguments ?? {}) as Record<string, unknown>;
    try {
      if (name === "js") {
        const input = JsInputSchema.parse(args);
        const output = await kernel.run(input.code);
        return toToolResult(output);
      }
      if (name === "js_reset") {
        // 兼容屏障：fresh kernel per call，无需销毁状态（module roots 属于进程级）
        return {
          content: [
            {
              type: "text",
              text: "Kernel reset. The next `js` call already runs in a fresh kernel; module search roots persist.",
            },
          ],
        };
      }
      if (name === "js_add_node_module_dir") {
        const input = JsAddNodeModuleDirInputSchema.parse(args);
        const dir = resolve(input.path);
        if (!moduleDirs.includes(dir)) {
          moduleDirs.push(dir);
          return { content: [{ type: "text", text: "true" }] };
        }
        return { content: [{ type: "text", text: "false" }] };
      }
      return {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      };
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      return {
        content: [{ type: "text", text: `${err.name}: ${err.message}` }],
        isError: true,
      };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
  // 保持进程存活直到 stdio 关闭
  process.on("disconnect", () => {
    void broker?.close().then(() => process.exit(0));
  });
}

function readTimeoutMs(raw: string | undefined): number | undefined {
  if (!raw) {
    return undefined;
  }
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

interface JsOutput {
  result?: unknown;
  logs?: string;
  error?: { name: string; message: string; stack?: string };
  images?: { base64: string; mimeType: string }[];
  browserScreenshotPaths?: string[];
  responseMeta?: Record<string, unknown>;
}

/** JsRunOutput → MCP 工具结果（文本 + image 内容块；与插件 formatModelContent 对齐）。 */
function toToolResult(output: JsRunOutput) {
  const content: Array<
    { type: "text"; text: string } | { type: "image"; data: string; mimeType: string }
  > = [];
  const sections: string[] = [];
  if (output.result !== undefined) {
    sections.push(formatResult(output.result));
  }
  if (output.logs) {
    sections.push(`[logs]\n${output.logs}`);
  }
  if (output.browserScreenshotPaths && output.browserScreenshotPaths.length > 0) {
    sections.push(`[screenshots saved]\n${output.browserScreenshotPaths.join("\n")}`);
  }
  if (output.responseMeta) {
    sections.push(`[meta]\n${JSON.stringify(output.responseMeta, null, 2)}`);
  }
  if (output.error) {
    const stack = output.error.stack ? `\n${output.error.stack}` : "";
    sections.unshift(`${output.error.name}: ${output.error.message}${stack}`);
  }
  content.push({ type: "text", text: sections.join("\n\n") || "(no output)" });
  for (const image of output.images ?? []) {
    content.push({ type: "image", data: image.base64, mimeType: image.mimeType });
  }
  return { content, isError: output.error !== undefined };
}

function formatResult(result: unknown): string {
  if (typeof result === "string") {
    return result;
  }
  try {
    return JSON.stringify(result, null, 2) ?? String(result);
  } catch {
    return String(result);
  }
}

main().catch((error) => {
  console.error("node_repl MCP server fatal:", error);
  process.exit(1);
});
