/**
 * 运行时检测与选择模块
 * ====================
 *
 * 桌面版内置一份 Python 与 Node.js 运行时（位于应用 resources 目录），
 * 同时支持优先使用用户 PATH 中符合版本要求的环境。
 *
 * 选择策略（对应 docs/zh-CN/desktop.md "检测逻辑" 一节）：
 *   1. 优先在 PATH 中查找符合版本要求的 python / node
 *   2. 找不到或版本不达标时，回退到内置运行时
 *
 * 内置运行时路径（打包后）：
 *   <process.resourcesPath>/python/<plat-arch>/...
 *   <process.resourcesPath>/node/<plat-arch>/...
 *
 * 开发模式下（未打包），从 desktop/resources/<plat-arch>/ 读取，
 * 若不存在则只依赖 PATH。
 */
import * as child_process from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

/** 后端要求的最低 Python 版本（与 pyproject.toml requires-python 对齐） */
const MIN_PYTHON: number[] = [3, 10];
/** 内置 Node 最低版本（用于运行用户脚本 / MCP server） */
const MIN_NODE: number[] = [18, 0];

/** 平台-架构标识，用于定位内置运行时子目录 */
function platArch(): string {
  const plat = process.platform; // win32 / darwin / linux
  const arch = process.arch; // x64 / arm64
  if (plat === 'win32') return `win-${arch}`;
  if (plat === 'darwin') return `mac-${arch}`;
  return `linux-${arch}`;
}

/** 可执行文件名（Windows 加 .exe） */
function exeName(name: string): string {
  return process.platform === 'win32' ? `${name}.exe` : name;
}

// 是否开发模式：ELECTRON_IS_DEV 环境变量显式控制，或 process.resourcesPath
// 未注入（未打包）时判定为开发。避免在运行时模块顶层依赖 electron。
function isDev(): boolean {
  return !!(process.env.ELECTRON_IS_DEV || !process.resourcesPath);
}

function bundledResourcesRoot(): string {
  if (!isDev() && process.resourcesPath) {
    return process.resourcesPath;
  }
  return path.resolve(__dirname, '..', 'resources');
}

/** 内置 Python 可执行路径（若存在），兼容多种解压布局 */
export function bundledPythonPath(): string | null {
  const root = bundledResourcesRoot();
  const base = path.join(root, 'python', platArch());
  // python-build-standalone install_only 布局：
  //   win:   python/python.exe
  //   unix:  python/install/bin/python3
  // 穷举所有可能层级，并兼容 python / python3 两种命名
  const py = exeName('python');
  const py3 = exeName('python3');
  const candidates = [
    path.join(base, 'python', py),                  // win: python/python.exe
    path.join(base, 'python', py3),                 // unix: python/python3
    path.join(base, 'python', 'install', 'bin', py), // unix: python/install/bin/python
    path.join(base, 'python', 'install', 'bin', py3),// unix: python/install/bin/python3
    path.join(base, 'install', 'bin', py),
    path.join(base, 'install', 'bin', py3),
    path.join(base, 'install', py),
    path.join(base, 'bin', py),
    path.join(base, 'bin', py3),
  ];
  return candidates.find((c) => fs.existsSync(c)) ?? null;
}

/** 内置 Node 可执行路径（若存在） */
export function bundledNodePath(): string | null {
  const root = bundledResourcesRoot();
  const base = path.join(root, 'node', platArch());
  const candidates = [
    path.join(base, 'bin', exeName('node')),
    path.join(base, exeName('node')),
  ];
  return candidates.find((c) => fs.existsSync(c)) ?? null;
}

/** 调用候选可执行文件取版本号，失败返回 null */
function getVersion(bin: string): number[] | null {
  try {
    const out = child_process.execSync(`"${bin}" --version`, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    // "Python 3.12.1" / "v18.20.0" / "v22.13.1"
    const m = out.match(/(\d+)\.(\d+)\.(\d+)/);
    if (!m) return null;
    return [Number(m[1]), Number(m[2]), Number(m[3])];
  } catch {
    return null;
  }
}

/** 版本比较：a >= min */
function gte(a: number[], min: number[]): boolean {
  for (let i = 0; i < min.length; i++) {
    if ((a[i] ?? 0) < min[i]) return false;
    if ((a[i] ?? 0) > min[i]) return true;
  }
  return true;
}

/** 在 PATH 中查找名为 name 的所有可执行，返回绝对路径数组（可能为空） */
function whichAll(name: string): string[] {
  try {
    // where (win) / which -a (unix，-a 列出所有匹配)
    const cmd = process.platform === 'win32' ? `where ${name}` : `which -a ${name}`;
    const out = child_process.execSync(cmd, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return out.split(/\r?\n/).filter(Boolean);
  } catch {
    return [];
  }
}

export interface RuntimeChoice {
  /** 最终用于启动后端的 Python 可执行路径；空字符串表示未找到 */
  python: string;
  /** 是否使用用户自有 Python（true）还是内置（false） */
  pythonFromUser: boolean;
  /** Node 可执行路径（null 表示无可用 Node） */
  node: string | null;
  /** Node 是否来自用户 */
  nodeFromUser: boolean;
}

/**
 * 检测并选择 Python / Node 运行时。
 *
 * 顺序：
 *   - Python: 用户 PATH python / python3（版本达标）→ 内置 python
 *   - Node:   用户 PATH node（版本达标）→ 内置 node
 *
 * 是否"来自用户"通过路径是否位于内置 resourcesRoot 下判断。
 */
export function resolveRuntime(): RuntimeChoice {
  const root = path.resolve(bundledResourcesRoot());

  // --- Python 选择：内置运行时优先，用户 PATH 的 python 仅作内置缺失时的兜底 ---
  // 该 python 用于启动后端进程，必须含可直接执行的 `python -m illusion`（内置
  // python 打包了完整 illusion 包与全部依赖）；用户系统 python 可能是残缺安装
  // （如仅有 illusion 包目录而无 __main__.py），若用它启动后端会导致后端一直
  // 未就绪。开发模式（无内置运行时）自动回退到用户 python。
  // pythonFromUser 表示"用户是否另有可用的 python 环境"，与后端用哪个 python
  // 无关：main.ts 依据它决定是否把内置 python 注入 PATH——用户有环境时不注入，
  // 使 LLM 的 bash 工具执行脚本用用户环境。
  let python = '';
  let pythonFromUser = false;
  // 先判定用户是否有可用的 python 环境（决定脚本执行环境是否用用户侧）
  const userPythons = [...whichAll('python'), ...whichAll('python3')];
  let userPythonHit: string | null = null;
  for (const candidate of userPythons) {
    const v = getVersion(candidate);
    if (v && gte(v, MIN_PYTHON)) {
      userPythonHit = candidate;
      pythonFromUser = true;
      break;
    }
  }
  // 后端进程用 python：内置优先（完整 illusion），缺失时回退用户 python
  const bundled = bundledPythonPath();
  if (bundled) {
    const v = getVersion(bundled);
    if (v && gte(v, MIN_PYTHON)) {
      python = bundled;
    }
  }
  if (!python) {
    for (const candidate of userPythons) {
      const v = getVersion(candidate);
      if (v && gte(v, MIN_PYTHON)) {
        python = candidate;
        break;
      }
    }
  }

  // --- Node: 同样优先用户环境（遍历所有 node）---
  let node: string | null = null;
  let nodeFromUser = false;
  for (const candidate of whichAll('node')) {
    const v = getVersion(candidate);
    if (v && gte(v, MIN_NODE)) {
      node = candidate;
      nodeFromUser = true;
      break;
    }
  }
  if (!node) {
    const bundled = bundledNodePath();
    if (bundled) {
      const v = getVersion(bundled);
      if (v && gte(v, MIN_NODE)) {
        node = bundled;
        nodeFromUser = false;
      }
    }
  }

  // 修正 pythonFromUser：仅当"用户检测命中的路径"实际位于内置目录内
  // （如用户 PATH 指向了内置目录）才视为无独立用户环境。
  // 注意不能检查后端选中的 python——内置优先策略下它恒为内置路径，
  // 若据此覆盖会把正确的用户环境检测结果无条件抹掉（导致 bash 工具
  // 的 PATH 被注入内置 python，用户脚本跑错解释器）。
  if (pythonFromUser && userPythonHit && path.resolve(userPythonHit).startsWith(root)) {
    pythonFromUser = false;
  }

  return { python, pythonFromUser, node, nodeFromUser };
}
