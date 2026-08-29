/**
 * broker 客户端 —— node_repl 内核 ↔ Python 宿主的 Browser Control 通道。
 *
 * 协议：127.0.0.1 回环 TCP + 令牌鉴权，JSON-lines 分帧；请求/响应形状与
 * browser-use 插件的 nodeReplBroker 一致（见 Python 侧 illusion/browser_use/broker.py）。
 *
 * 连接策略：惰性连接 + 断线自动重连（指数退避上限 5s）；在途请求在连接断开时
 * 立即以错误兑现，不悬挂等待。内核每次 js 调用新建，broker 客户端由 MCP 服务器
 * 进程持有并跨内核复用，保证连接可以被复用与统一回收。
 */

const CONNECT_TIMEOUT_MS = 5_000;
const REQUEST_TIMEOUT_MS = 180_000; // 导航/截图等长操作的兜底上限
const MAX_RECONNECT_DELAY_MS = 5_000;
const MAX_FRAME_BYTES = 32 * 1024 * 1024;

export interface BrokerEndpoint {
  host: string;
  port: number;
  token: string;
  sessionId: string;
}

interface PendingRequest {
  resolve: (value: BrokerResponse) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

type BrokerResponse =
  | { id: string; ok: true; browsers?: unknown[]; result?: unknown }
  | { id: string; ok: false; error: string };

function assertOk(response: BrokerResponse): void {
  if (!response.ok) {
    throw new BrokerCommandError(response.error);
  }
}

export class BrokerUnavailableError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "BrokerUnavailableError";
  }
}

/** 宿主返回 ok=false（鉴权失败 / 未知 op / 处理异常）。 */
export class BrokerCommandError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BrokerCommandError";
  }
}

export class BrowserBrokerClient {
  private readonly endpoint: BrokerEndpoint;
  private socket: import("node:net").Socket | null = null;
  private connecting: Promise<void> | null = null;
  private buffer: Buffer<ArrayBufferLike> = Buffer.alloc(0);
  private readonly pending = new Map<string, PendingRequest>();
  private nextRequestId = 0;
  private reconnectDelay = 250;
  private closed = false;

  constructor(endpoint: BrokerEndpoint) {
    this.endpoint = endpoint;
  }

  /** list：返回宿主注册表中的浏览器描述符。 */
  async list(): Promise<unknown[]> {
    const response = await this.request({ op: "list" });
    assertOk(response);
    return (response as { browsers?: unknown[] }).browsers ?? [];
  }

  /** execute：向宿主转发一条浏览器命令，返回结果信封。 */
  async execute(browserId: string, browserGeneration: number, command: unknown): Promise<unknown> {
    const response = await this.request({ op: "execute", browserId, browserGeneration, command });
    assertOk(response);
    return (response as { result?: unknown }).result;
  }

  async close(): Promise<void> {
    this.closed = true;
    const socket = this.socket;
    this.socket = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new BrokerUnavailableError("broker client closed"));
    }
    this.pending.clear();
    if (socket) {
      socket.destroy();
    }
  }

  // --- 内部 ---

  private async request(body: Record<string, unknown>): Promise<BrokerResponse> {
    if (this.closed) {
      throw new BrokerUnavailableError("broker client closed");
    }
    const socket = await this.ensureConnected();
    const id = `req-${++this.nextRequestId}-${Date.now()}`;
    return new Promise<BrokerResponse>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new BrokerUnavailableError(`broker request '${String(body.op)}' timed out`));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      const payload = JSON.stringify({
        id,
        token: this.endpoint.token,
        // browser use 仅主代理可用（与插件 runtime_scope 语义一致；broker 侧同样校验）
        runtimeScope: "main",
        sessionId: this.endpoint.sessionId,
        ...body,
      });
      socket.write(payload + "\n", (error) => {
        if (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new BrokerUnavailableError(`broker write failed: ${error.message}`, { cause: error }));
        }
      });
    });
  }

  private async ensureConnected(): Promise<import("node:net").Socket> {
    if (this.socket && !this.socket.destroyed) {
      return this.socket;
    }
    if (!this.connecting) {
      this.connecting = this.connect().finally(() => {
        this.connecting = null;
      });
    }
    await this.connecting;
    if (!this.socket) {
      throw new BrokerUnavailableError("broker connection failed");
    }
    return this.socket;
  }

  private async connect(): Promise<void> {
    const { createConnection } = await import("node:net");
    const attempt = async (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        const socket = createConnection(
          { host: this.endpoint.host, port: this.endpoint.port },
          () => {
            socket.setTimeout(0);
            this.socket = socket;
            resolve();
          },
        );
        socket.setTimeout(CONNECT_TIMEOUT_MS, () => {
          socket.destroy();
          reject(new BrokerUnavailableError("broker connect timeout"));
        });
        socket.once("error", (error) => {
          socket.destroy();
          reject(new BrokerUnavailableError(`broker connect failed: ${error.message}`, { cause: error }));
        });
        socket.setNoDelay(true);
        socket.on("data", (chunk) => this.onData(chunk));
        socket.on("close", () => this.onClose());
      });
    // 惰性重连：宿主可能在会话中途才启动（MCP 服务器早于 service.start 就绪）
    for (let attempts = 0; ; attempts++) {
      try {
        await attempt();
        this.reconnectDelay = 250;
        return;
      } catch (error) {
        this.socket = null;
        if (this.closed) {
          throw error;
        }
        if (attempts >= 6) {
          throw error;
        }
        const delay = Math.min(this.reconnectDelay * 2 ** attempts, MAX_RECONNECT_DELAY_MS);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  private onData(chunk: Buffer): void {
    this.buffer = this.buffer.length === 0 ? chunk : Buffer.concat([this.buffer, chunk]);
    for (;;) {
      const newlineIndex = this.buffer.indexOf(0x0a);
      if (newlineIndex === -1) {
        if (this.buffer.length > MAX_FRAME_BYTES) {
          this.buffer = Buffer.alloc(0);
        }
        return;
      }
      const line = this.buffer.subarray(0, newlineIndex);
      this.buffer = this.buffer.subarray(newlineIndex + 1);
      if (line.length === 0) {
        continue;
      }
      this.onFrame(line);
    }
  }

  private onFrame(line: Buffer): void {
    let response: BrokerResponse;
    try {
      response = JSON.parse(line.toString("utf8")) as BrokerResponse;
    } catch {
      return; // 非法帧静默丢弃（与宿主侧防探测策略一致）
    }
    const pending = this.pending.get(response.id);
    if (!pending) {
      return;
    }
    this.pending.delete(response.id);
    clearTimeout(pending.timer);
    pending.resolve(response);
  }

  private onClose(): void {
    const hadSocket = this.socket !== null;
    this.socket = null;
    this.buffer = Buffer.alloc(0);
    if (!hadSocket) {
      return;
    }
    // 在途请求立即失败，避免悬挂
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new BrokerUnavailableError("broker connection closed"));
    }
    this.pending.clear();
  }
}

/** 从环境变量读取 broker 端点；未配置时返回 null（内核不注入 bridge）。 */
export function brokerEndpointFromEnv(env: NodeJS.ProcessEnv): BrokerEndpoint | null {
  const host = env.ILLUSION_BROWSER_BROKER_HOST;
  const portRaw = env.ILLUSION_BROWSER_BROKER_PORT;
  const token = env.ILLUSION_BROWSER_BROKER_TOKEN;
  const sessionId = env.ILLUSION_SESSION_ID ?? "browser-session";
  if (!host || !portRaw || !token) {
    return null;
  }
  const port = Number.parseInt(portRaw, 10);
  if (!Number.isInteger(port) || port <= 0) {
    return null;
  }
  return { host, port, token, sessionId };
}
