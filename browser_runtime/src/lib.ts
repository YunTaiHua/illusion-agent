/**
 * 供 node --test 使用的导出桶（与 mcp-server.js 同一套 esbuild 选项打包，
 * 测试校验的产物与发布产物一致；不包含会立即启动 stdio 监听的 server 入口）。
 */
export { BrowserBrokerClient, brokerEndpointFromEnv, BrokerUnavailableError } from "./broker.ts";
export { JsKernelRunner, prepareUserCode, DEFAULT_JS_TIMEOUT_MS } from "./kernel.ts";
export { injectBrowserBridge, defaultDocumentationRoot, ILLUSION_BRIDGE_SYMBOL, ZCODE_BRIDGE_SYMBOL } from "./bridge.ts";
export { saveImageArtifact } from "./artifacts.ts";
