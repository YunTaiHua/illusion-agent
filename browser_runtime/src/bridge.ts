/**
 * browser-control 桥接 —— 向内核注入 agent.browsers 依赖的 runtime bridge。
 *
 * 桥接对象形状与 browser-use 插件一致（browser-client.mjs 的
 * setupBrowserRuntime 按此消费）：
 *   { assertAvailable(), documentationRoot, list(), execute(browserId, generation, command) }
 *
 * 同时注册 illusion 与 zcode 两个 symbol 名：主名 `illusion.node-repl.browser-control-bridge`，
 * 兼容名沿用官方插件的 `zcode.node-repl.browser-control-bridge`，保证移植的
 * browser-client.mjs / skill 引导代码无需改动即可工作。
 *
 * 可用性语义：broker 未配置（会话未启用 Browser Use）时不注入桥接对象，
 * browser-client 的 setupBrowserRuntime 会抛出明确的"运行时不可用"错误；
 * broker 连接故障由命令级错误表达（BrokerUnavailableError）。
 */

import { join } from "node:path";
import type { BrowserBrokerClient } from "./broker.ts";

/** 桥接对象接口（browser-client.mjs 消费方）。 */
export interface BrowserControlBridge {
  assertAvailable: () => void;
  documentationRoot: string;
  list: () => Promise<unknown[]>;
  execute: (browserId: string, browserGeneration: number, command: unknown) => Promise<unknown>;
}

export const ILLUSION_BRIDGE_SYMBOL = Symbol.for("illusion.node-repl.browser-control-bridge");
/** 兼容名：与官方 browser-use 插件保持一致。 */
export const ZCODE_BRIDGE_SYMBOL = Symbol.for("zcode.node-repl.browser-control-bridge");

/** 注入桥接对象；broker 为 null 时跳过注入（保持内核纯净）。 */
export function injectBrowserBridge(
  globals: Record<symbol | string, unknown>,
  deps: { broker: BrowserBrokerClient; documentationRoot: string },
): void {
  const broker = deps.broker;
  const bridge: BrowserControlBridge = {
    assertAvailable: () => undefined,
    documentationRoot: deps.documentationRoot,
    list: () => broker.list(),
    execute: (browserId, browserGeneration, command) =>
      broker.execute(browserId, browserGeneration, command),
  };
  globals[ILLUSION_BRIDGE_SYMBOL] = bridge;
  globals[ZCODE_BRIDGE_SYMBOL] = bridge;
}

/** documentationRoot 解析（供 server 组装 kernel options 复用）。 */
export function defaultDocumentationRoot(pluginRoot: string): string {
  return join(pluginRoot, "docs");
}
