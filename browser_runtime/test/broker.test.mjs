/**
 * broker 客户端协议测试：回环 TCP 假宿主上的 list/execute 往返、令牌鉴权、
 * 断线在途请求失败、端点环境变量解析。
 */
import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { BrowserBrokerClient, brokerEndpointFromEnv } from "../dist/lib.js";

/** 启动一个假 broker 宿主：按 handler 回应 JSON-lines 请求。 */
async function startFakeBroker(handler) {
  const server = net.createServer((socket) => {
    let buffer = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let index;
      while ((index = buffer.indexOf(0x0a)) !== -1) {
        const line = buffer.subarray(0, index).toString("utf8");
        buffer = buffer.subarray(index + 1);
        if (!line) continue;
        const request = JSON.parse(line);
        const response = handler(request);
        socket.write(JSON.stringify(response) + "\n");
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    port,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

test("broker round trip: list 与 execute", async () => {
  const fake = await startFakeBroker((request) => {
    if (request.op === "list") {
      assert.equal(request.token, "t".repeat(40));
      return { id: request.id, ok: true, browsers: [{ id: "cdp", generation: 1, type: "cdp", name: "x", capabilities: {} }] };
    }
    if (request.op === "execute") {
      assert.equal(request.browserId, "cdp");
      assert.equal(request.browserGeneration, 1);
      assert.deepEqual(request.command, { method: "getState" });
      return { id: request.id, ok: true, result: { ok: true, elapsedMs: 3 } };
    }
    return { id: request.id, ok: false, error: "unknown op" };
  });
  const client = new BrowserBrokerClient({ host: "127.0.0.1", port: fake.port, token: "t".repeat(40), sessionId: "s" });
  try {
    const browsers = await client.list();
    assert.equal(browsers.length, 1);
    assert.equal(browsers[0].id, "cdp");
    const result = await client.execute("cdp", 1, { method: "getState" });
    assert.equal(result.ok, true);
    assert.equal(result.elapsedMs, 3);
  } finally {
    await client.close();
    await fake.close();
  }
});

test("broker 令牌错误 → 明确错误", async () => {
  const fake = await startFakeBroker((request) => ({ id: request.id, ok: false, error: "unauthorized" }));
  const client = new BrowserBrokerClient({ host: "127.0.0.1", port: fake.port, token: "t".repeat(40), sessionId: "s" });
  try {
    await assert.rejects(() => client.list(), /unauthorized/);
  } finally {
    await client.close();
    await fake.close();
  }
});

test("broker 连接失败 → BrokerUnavailableError", async () => {
  const client = new BrowserBrokerClient({ host: "127.0.0.1", port: 1, token: "t".repeat(40), sessionId: "s" });
  await assert.rejects(() => client.list(), (error) => error.name === "BrokerUnavailableError");
  await client.close();
});

test("brokerEndpointFromEnv：缺失/非法/正常", () => {
  assert.equal(brokerEndpointFromEnv({}), null);
  assert.equal(brokerEndpointFromEnv({ ILLUSION_BROWSER_BROKER_HOST: "127.0.0.1", ILLUSION_BROWSER_BROKER_PORT: "nan", ILLUSION_BROWSER_BROKER_TOKEN: "t" }), null);
  const endpoint = brokerEndpointFromEnv({
    ILLUSION_BROWSER_BROKER_HOST: "127.0.0.1",
    ILLUSION_BROWSER_BROKER_PORT: "12345",
    ILLUSION_BROWSER_BROKER_TOKEN: "t".repeat(40),
    ILLUSION_SESSION_ID: "abc",
  });
  assert.deepEqual(endpoint, { host: "127.0.0.1", port: 12345, token: "t".repeat(40), sessionId: "abc" });
});
