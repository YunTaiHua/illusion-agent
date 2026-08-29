/**
 * MCP 服务器进程级测试：以子进程启动 dist/mcp-server.js，经 stdio JSON-RPC
 * 完成 initialize → tools/list → tools/call（含 js 执行与 emitImage），
 * broker 使用进程内假宿主——覆盖除真实浏览器外的完整链路。
 */
import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const distServer = resolve(fileURLToPath(new URL("../dist/mcp-server.js", import.meta.url)));

/** 启动假 broker（回环 TCP）：list 返回一个描述符；execute 回显 ok。 */
async function startFakeBroker() {
  const server = net.createServer((socket) => {
    socket.on("error", () => {}); // 进程终止导致的 ECONNRESET 可容忍
    let buffer = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let index;
      while ((index = buffer.indexOf(0x0a)) !== -1) {
        const line = buffer.subarray(0, index).toString("utf8");
        buffer = buffer.subarray(index + 1);
        if (!line) continue;
        const request = JSON.parse(line);
        let response;
        if (request.op === "list") {
          response = {
            id: request.id,
            ok: true,
            browsers: [{ id: "cdp", generation: 0, type: "cdp", name: "fake", capabilities: {} }],
          };
        } else {
          response = { id: request.id, ok: true, result: { ok: true, value: "browser-ok", elapsedMs: 1 } };
        }
        socket.write(JSON.stringify(response) + "\n");
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  return {
    port: server.address().port,
    close: () =>
      new Promise((r) => {
        server.close(() => r());
      }),
  };
}

class McpClient {
  constructor(proc) {
    this.proc = proc;
    this.buffer = "";
    this.nextId = 0;
    this.pending = new Map();
    proc.stdout.on("data", (chunk) => this.onData(chunk));
  }

  onData(chunk) {
    this.buffer += chunk.toString("utf8");
    let index;
    while ((index = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (!line) continue;
      const message = JSON.parse(line);
      const pending = this.pending.get(message.id);
      if (pending) {
        this.pending.delete(message.id);
        pending(message);
      }
    }
  }

  request(method, params) {
    const id = ++this.nextId;
    return new Promise((resolveRequest, reject) => {
      const timer = setTimeout(() => reject(new Error(`MCP request '${method}' timed out`)), 30_000);
      this.pending.set(id, (message) => {
        clearTimeout(timer);
        resolveRequest(message);
      });
      this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }

  callTool(name, args) {
    return this.request("tools/call", { name, arguments: args });
  }
}

test("mcp-server 进程：initialize → tools/list → js + emitImage 全链路", async () => {
  const fakeBroker = await startFakeBroker();
  const configDir = mkdtempSync(join(tmpdir(), "illusion-mcp-e2e-"));
  const env = {
    ...process.env,
    ILLUSION_BROWSER_BROKER_HOST: "127.0.0.1",
    ILLUSION_BROWSER_BROKER_PORT: String(fakeBroker.port),
    ILLUSION_BROWSER_BROKER_TOKEN: "t".repeat(40),
    ILLUSION_CONFIG_DIR: configDir,
    ILLUSION_PLUGIN_ROOT: resolve(fileURLToPath(new URL("../", import.meta.url))),
  };
  const proc = spawn(process.execPath, [distServer], { env, stdio: ["pipe", "pipe", "pipe"] });
  proc.stderr.on("data", (chunk) => process.stderr.write(`[server] ${chunk}`));
  const client = new McpClient(proc);

  try {
    // initialize（协议握手）
    const init = await client.request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "test", version: "0" },
    });
    assert.equal(init.result.serverInfo.name, "illusion-node-repl");
    client.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");

    // tools/list：三个工具
    const tools = await client.request("tools/list", {});
    const names = tools.result.tools.map((t) => t.name).sort();
    assert.deepEqual(names, ["js", "js_add_node_module_dir", "js_reset"]);

    // js：结果捕获 + broker execute 转发（agent.browsers 桥接）
    const js = await client.callTool("js", {
      code: `
const { setupBrowserRuntime } = await importModule("./browser-client-import-helper.js").catch(() => ({}));
// 直接验证桥接（browser-client 需真实文件路径，这里直接读 bridge symbol）
const bridge = globalThis[Symbol.for("illusion.node-repl.browser-control-bridge")];
const browsers = await bridge.list();
const envelope = await bridge.execute("cdp", 0, { method: "getState" });
({ count: browsers.length, ok: envelope.ok })`,
    });
    const text = js.result.content.find((c) => c.type === "text").text;
    assert.match(text, /"count": 1/);
    assert.match(text, /"ok": true/);

    // js：emitImage → image 内容块 + artifact 落盘
    const shot = await client.callTool("js", {
      code: `await nodeRepl.emitImage("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="); "shot"`,
    });
    const image = shot.result.content.find((c) => c.type === "image");
    assert.ok(image, "image content block present");
    assert.equal(image.mimeType, "image/png");
    const pathsLine = shot.result.content.find((c) => c.type === "text").text;
    assert.match(pathsLine, /browser-\d+-[0-9a-f]+\.png/);
    const artifactPath = pathsLine.match(/([A-Za-z]:\\[^\s]+\.png|[^\s]*browser-[^\s]*\.png)/)?.[0];
    assert.ok(artifactPath && existsSync(artifactPath), "artifact written to disk");

    // js_reset 兼容屏障
    const reset = await client.callTool("js_reset", {});
    assert.match(reset.result.content[0].text, /fresh kernel/);

    // js_add_node_module_dir：去重返回布尔
    const dir = mkdtempSync(join(tmpdir(), "illusion-modules-"));
    const add1 = await client.callTool("js_add_node_module_dir", { path: dir });
    const add2 = await client.callTool("js_add_node_module_dir", { path: dir });
    assert.equal(add1.result.content[0].text, "true");
    assert.equal(add2.result.content[0].text, "false");
    rmSync(dir, { recursive: true, force: true });
  } finally {
    proc.kill();
    await fakeBroker.close();
    rmSync(configDir, { recursive: true, force: true });
  }
});
