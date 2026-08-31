/**
 * 后端进程管理模块
 * ==================
 *
 * 负责以子进程方式启动 Illusion Agent 后端（`python -m illusion web`），
 * 动态分配空闲端口，等待后端就绪后通知主进程加载 URL。
 *
 * 关键设计：
 *   - 端口动态分配：监听 0 端口获取系统分配的空闲端口，立即关闭并交给后端
 *   - 就绪检测：轮询 http://127.0.0.1:<port>/ 直至 2xx 或超时
 *   - 进程树清理：退出时 kill 整个进程组（Windows 用 taskkill /T）
 *
 * 注意：后端 illusion web 默认会 webbrowser.open 打开系统浏览器，
 * 桌面版通过环境变量 ILLUSION_NO_BROWSER_OPEN=1 关闭该行为
 * （需后端 web.py 配合，见 docs/zh-CN/desktop.md 已知 TODO）。
 */
import * as child_process from 'node:child_process';
import * as http from 'node:http';
import * as net from 'node:net';
import * as readline from 'node:readline';
import { EventEmitter } from 'node:events';

/** 后端启动选项 */
export interface BackendOptions {
  /** Python 可执行路径 */
  pythonPath: string;
  /** 监听地址，默认 127.0.0.1 */
  host?: string;
  /** 后端工作目录，默认继承当前进程 */
  cwd?: string;
  /** 子进程环境变量，默认继承 */
  env?: NodeJS.ProcessEnv;
}

/** 分配一个空闲端口（监听 0 后立即关闭，交给后端使用） */
export function allocatePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      if (addr && typeof addr === 'object') {
        const port = addr.port;
        srv.close(() => resolve(port));
      } else {
        reject(new Error('无法分配端口'));
      }
    });
  });
}

/** 轮询 URL 直至返回 2xx 或超时 */
function waitUntilReady(host: string, port: number, timeoutMs = 30000): Promise<void> {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const check = () => {
      if (Date.now() - start > timeoutMs) {
        reject(new Error(`后端在 ${timeoutMs}ms 内未就绪`));
        return;
      }
      const req = http.get(`http://${host}:${port}/`, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) {
          resolve();
        } else {
          setTimeout(check, 300);
        }
      });
      req.on('error', () => setTimeout(check, 300));
      req.setTimeout(2000, () => {
        req.destroy();
        setTimeout(check, 300);
      });
    };
    check();
  });
}

/**
 * 后端进程控制器。
 *
 * 用法：
 *   const backend = new Backend({ pythonPath });
 *   backend.on('ready', (url) => mainWindow.loadURL(url));
 *   await backend.start();
 *   // ... 退出时
 *   backend.kill();
 */
export class Backend extends EventEmitter {
  private proc: child_process.ChildProcess | null = null;
  private port: number | null = null;
  private readonly opts: Required<BackendOptions>;

  constructor(opts: BackendOptions) {
    super();
    this.opts = {
      host: '127.0.0.1',
      cwd: process.cwd(),
      env: process.env,
      ...opts,
    };
  }

  /** 启动后端，resolve 的值即为应加载的 URL（含访问 token） */
  async start(): Promise<string> {
    const port = await allocatePort();
    this.port = port;

    // spawn python -m illusion web --port <port> --host <host>
    const args = [
      '-m',
      'illusion',
      'web',
      '--port',
      String(port),
      '--host',
      this.opts.host,
    ];
    // 关闭后端自动打开系统浏览器（桌面版自行 loadURL）
    const env = { ...this.opts.env, ILLUSION_NO_BROWSER_OPEN: '1' };

    this.proc = child_process.spawn(this.opts.pythonPath, args, {
      cwd: this.opts.cwd,
      env,
      // stdout 走 pipe：Web 启动后 launch token 随机生成并打印在访问 URL 中，
      // 桌面壳须从 stdout 解析该 URL 才能加载认证后的页面
      // （stderr 仍忽略，避免 uvicorn 日志混入解析流）
      stdio: ['ignore', 'pipe', 'ignore'],
      windowsHide: true,
    });

    this.proc.on('exit', (code) => {
      this.emit('exit', code);
    });

    // 等待后端打印访问 URL（web.py 输出 "Illusion Agent Web UI: http://...?token=..."）。
    // 该行为后端就绪的前置信号：URL 行出现后再轮询 HTTP 直至可服务。
    const urlPromise = new Promise<string>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error('后端未在 30s 内输出访问 URL')),
        30000,
      );
      if (!this.proc?.stdout) {
        clearTimeout(timer);
        reject(new Error('后端 stdout 不可用'));
        return;
      }
      const rl = readline.createInterface({ input: this.proc.stdout });
      rl.on('line', (line) => {
        const match = /(https?:\/\/\S+)/.exec(line);
        if (match) {
          clearTimeout(timer);
          rl.close();
          resolve(match[1]);
        }
      });
    });

    // 等待就绪
    try {
      const url = await urlPromise;
      await waitUntilReady(this.opts.host, port);
      this.emit('ready', url);
      return url;
    } catch (e) {
      this.emit('error', (e as Error).message);
      throw e;
    }
  }

  /** 获取已分配端口 */
  getPort(): number | null {
    return this.port;
  }

  /** 杀死后端进程树 */
  kill(): void {
    if (!this.proc || this.proc.pid == null) return;
    try {
      if (process.platform === 'win32') {
        // Windows: taskkill /T 杀整个进程树（含守护进程）
        child_process.execSync(`taskkill /pid ${this.proc.pid} /T /F`, {
          stdio: 'ignore',
        });
      } else {
        // Unix: SIGTERM 主进程；守护进程通过 IPC 检测连接归零自行退出
        process.kill(this.proc.pid, 'SIGTERM');
      }
    } catch {
      // 进程可能已退出，忽略
    }
    this.proc = null;
  }
}
