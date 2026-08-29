# 命令系统

## 📚 命令系统

### 主命令行选项

`illusion` 主命令支持以下选项，按功能分组：

#### Session（会话）

| 选项 | 简写 | 说明 |
|------|------|------|
| `--continue` | `-c` | 继续当前目录的最近一次会话 |
| `--resume <SESSION_ID>` | `-r` | 通过会话 ID 恢复会话（必须指定 ID） |
| `--name <NAME>` | `-n` | 为本次会话设置显示名称（存入 `tool_metadata.session_name`） |

#### Model & Effort（模型与推理强度）

| 选项 | 简写 | 说明 |
|------|------|------|
| `--model <MODEL>` | `-m` | 模型 ID，格式为 `env_N.model_N`（如 `env_1.model_2`），设置后持久化到 settings.json |
| `--effort <LEVEL>` | `-e` | 推理强度级别：`low` / `medium` / `high` / `xhigh` / `max`，设置后持久化到 settings.json |
| `--max-turns <N>` | `-t` | 最大代理轮次数，设置后持久化到 settings.json |

#### Output（输出）

| 选项 | 简写 | 说明 |
|------|------|------|
| `--print <PROMPT>` | `-p` | 非交互式打印模式：执行单次提示词后退出 |
| `--output-format <FORMAT>` | - | `--print` 模式的输出格式：`text`（默认）/ `json` / `stream-json` |

#### Permissions（权限）

| 选项 | 说明 |
|------|------|
| `--permission-mode <MODE>` | 权限模式：`default` / `plan` / `full_auto` / `yolo`，设置后持久化到 settings.json |
| `--dangerously-skip-permissions` | 跳过所有权限检查（等价于 `--permission-mode full_auto`，仅适用于沙箱环境） |

#### 全局

| 选项 | 简写 | 说明 |
|------|------|------|
| `--version` | `-v` | 显示版本号并退出 |
| `--help` | `-h` | 显示帮助信息并退出 |

### 运行模式

`illusion` 支持三种主要运行模式：

#### 1. 交互式会话模式（默认）

```bash
illusion                            # 启动交互式会话
illusion -m env_1.model_2           # 指定模型启动
illusion --permission-mode full_auto  # 以自动权限模式启动
illusion -e high                    # 高推理强度启动（持久化到 settings）
```

#### 2. 非交互式打印模式

```bash
illusion -p "帮我分析这个项目的结构"
illusion -p "say hi" --output-format json
illusion -p "refactor this" -t 10
illusion -e high -p "分析代码"      # 持久化 effort 并执行
```

#### 3. 会话恢复模式

```bash
illusion -c -p "继续分析"           # 继续最近会话（必须配合 -p）
illusion -r <session-id> -p "继续"  # 恢复指定会话（必须配合 -p）
illusion -c -p "继续" --name "feature-work"  # 继续会话并命名
```

注意：`-c`/`-r` 现在必须配合 `-p` 使用，否则报错。`--resume` 不带值的 picker 模式已移除（非 backend_only 路径）。

### 参数透传

核心命令选项（model/effort/max_turns/permission_mode/name/continue/resume）会完整透传到 React 终端前端（`launch_react_tui` → `build_backend_command`）和结构化后端主机（`run_backend_host` → `build_runtime`），确保在交互式模式、`--backend-only` 子进程模式、`-c`/`-r` 会话恢复模式下均生效。

### 常见组合示例

```bash
# 指定模型 + 权限模式
illusion -m env_1.model_2 --permission-mode plan

# 高推理强度 + 打印模式（持久化 effort）
illusion -e high -p "分析这段代码的性能瓶颈"

# 限制轮次 + 打印模式（持久化 max_turns）
illusion -t 5 -p "快速检查语法错误"

# 继续会话 + 打印模式
illusion -c -p "继续上次的任务"

# 为会话命名
illusion --name "debug-auth-issue"
```

### 子命令

```bash
# Web UI
illusion web                     # 启动 Web UI 浏览器界面（默认端口 3000）
illusion web --port 8080         # 自定义端口启动
illusion web --trusted-host nas.example  # 声明受信主机（非回环部署时供局域网设备接入 /ws）

# 认证管理
illusion auth login              # 交互式配置提供商（首次登录后会引导设置工作目录）
illusion auth status             # 查看所有环境的认证状态
illusion auth logout [env_N]     # 清除环境凭据
illusion auth switch [env_N]     # 切换活动环境
illusion add model [env_N]       # 向已有环境添加模型（支持循环输入多个）

# 工作目录管理
illusion set                      # 查看当前工作目录
illusion set "E:\Projects\myapp"  # 设置工作目录（不存在则新建）

# MCP 管理
illusion mcp list                # 列出 MCP 服务器
illusion mcp add <name> <config> # 添加服务器
illusion mcp remove <name>       # 移除服务器

# 插件管理
illusion plugin list             # 列出插件
illusion plugin install <source> # 安装插件
illusion plugin uninstall <name> # 卸载插件

# 渠道管理（飞书/微信/QQ 消息渠道）
illusion channel login           # 交互式配置渠道（选择渠道 → 配置凭据）
illusion channel serve           # 前台运行渠道守护进程（监听消息）
illusion channel status          # 查看渠道状态（启用/连接/PID）
illusion channel enable feishu   # 启用飞书渠道
illusion channel disable feishu  # 禁用飞书渠道
illusion channel logout feishu   # 清除飞书渠道凭据

# 定时任务
illusion cron start              # 启动调度器
illusion cron stop               # 停止调度器
illusion cron status             # 查看状态
illusion cron serve              # cron 守护进程主入口（前台运行，守护进程入口）
illusion cron list               # 列出任务
illusion cron toggle <name> <true|false>  # 启用/禁用任务
illusion cron run <name>         # 手动触发执行任务
illusion cron history            # 查看执行历史
illusion cron logs               # 查看调度器日志

# 自更新
illusion update                  # 检查并从 PyPI 安装更新
illusion update --deps           # 同时更新项目依赖
```

### 交互式斜杠命令

在交互式会话中，可使用以下命令：

| 类别 | 命令示例 | 说明 |
|------|----------|------|
| 会话管理 | `/help`, `/clear`, `/exit`, `/rewind`, `/delete` | 管理会话状态 |
| 记忆快照 | `/memory`, `/resume`, `/export`, `/rules` | 记忆与会话管理 |
| 配置设置 | `/config`, `/model`, `/permissions`, `/thinking` | 调整运行配置 |
| 推理控制 | `/effort`, `/max-tokens`, `/turns` | 推理强度、令牌数、轮次控制 |
| 插件扩展 | `/skills`, `/hooks`, `/mcp`, `/plugin` | 管理扩展功能 |
| 项目初始化 | `/init` | 初始化项目 IllusionAgent 文件 |
| 多智能体 | `/continue`, `/agent` | Agent 协作与管理 |

### 非交互模式（打印模式）可用参数

使用 `-p` / `--print <PROMPT>` 进入非交互模式：执行单次提示词后退出，适合脚本和自动化场景。以下参数可与 `-p` 配合使用：

| 参数 | 简写 | 说明 | 持久化 |
|------|------|------|--------|
| `--print <PROMPT>` | `-p` | 进入打印模式，PROMPT 为提示词 | 否 |
| `--output-format <FORMAT>` | - | 输出格式：`text`（默认）/ `json` / `stream-json` | 否 |
| `--model <MODEL>` | `-m` | 指定模型别名或完整模型 ID | 是（写入 `settings.model`） |
| `--effort <LEVEL>` | `-e` | 推理强度：`low` / `medium` / `high` / `xhigh` / `max` | 是（写入 `settings.effort`） |
| `--max-turns <N>` | `-t` | 最大代理轮次数 | 是（写入 `settings.max_turns`） |
| `--permission-mode <MODE>` | - | 权限模式：`default` / `plan` / `full_auto` / `yolo` | 是（写入 `settings.permission.mode`） |
| `--continue` | `-c` | 继续当前目录的最近会话（必须配合 `-p`） | 否 |
| `--resume <SESSION_ID>` | `-r` | 恢复指定会话 ID（必须配合 `-p`） | 否 |
| `--name <NAME>` | `-n` | 为本次会话设置显示名称 | 否 |
| `--dangerously-skip-permissions` | - | 跳过所有权限检查（等价于 `--permission-mode full_auto`） | 否 |

**交互行为**：

- **权限确认**：print 模式采用跨轮次 Y/N 回调——`default` 模式下，需要权限的工具不会直接执行，而是持久化权限请求并以退出码 2 退出，stderr 提示 `权限请求: {tool}，请使用 illusion -c -p "Y" 允许一次 / "N" 拒绝`：
  1. **第 1 轮**：`illusion -p "写文件"` → 工具需要权限 → 持久化到 `pending-permission-<session_id>.json` → 退出码 **2**
  2. **第 2 轮**：`illusion -c -p "Y"` → 检测 pending permission → 注入审批结果 → 继续执行

  **审批输入格式**（不区分大小写）：
  - **Y** / **yes** / **批准**：允许一次（不持久化，仅当前工具调用有效）
  - **N** / 其他任何输入：拒绝（LLM 收到拒绝消息，可选择其他方案）

  如需完全跳过权限确认，请使用 `--permission-mode full_auto`；`plan` 模式阻止所有变更工具。
- **沙箱权限确认（两选项）**：print 模式命中的**沙箱限制**使用独立的**两选项**跨轮次确认（允许/拒绝），与通用权限的 Y/N 两选项区分，且不提供"始终允许"：
  1. **第 1 轮**：`illusion -p "..."` → 工具命中沙箱限制 → 持久化到 `pending-sandbox-<session_id>.json` → 退出码 **2**，stderr 提示 `沙箱权限请求: {tool}，请使用 illusion -c -p "Y" 允许 / "N" 拒绝`
  2. **第 2 轮**：`illusion -c -p "Y"` → 允许该次沙箱受限操作并继续；`illusion -c -p "N"` → 拒绝。

  **高危操作**：破坏性命令（`rm`、`git restore`、`Remove-Item` 等）等级高于读取，即使某路径已被会话级允许，相关删除/还原操作仍会触发沙箱确认。
- **ask_user_question 交互**：当 LLM 调用 ask_user_question 工具时，print 模式采用**跨轮次非交互**模式：
  1. **第 1 轮**：`illusion -p "做某事"` → agent 执行中调用 ask_user_question → 工具持久化问题到 `pending-question-<session_id>.json`，返回特殊标记作为 tool_result → agent 结束当前轮次 → 程序以**退出码 2** 退出（表示等待用户回答）
  2. **第 2 轮**：`illusion -c -p "<答案>"` → 检测到 pending question → 把答案注入为 tool_result（替换标记）→ 调用 `continue_pending` 继续执行 agent

  这样设计的目的是让 illusion agent 可被其他 agent 操控：每次 `-p` 调用是原子的请求-响应，不在同一轮内等待交互输入。退出码语义：
  - `0`：正常完成
  - `1`：错误
  - `2`：等待用户回答（下次用 `-c -p` 回答）

  **多问题回答格式**（agent 友好）：
  - **单问题**：直接输入答案文本。`multiSelect` 时用逗号分隔，如 `选项A,选项B`
  - **多问题**：使用 JSON 格式，key 为问题展示时方括号内的 header：
    ```bash
    illusion -c -p "{\"水果\": \"草莓\", \"操作系统\": \"Windows\", \"Emoji\": \"少用点\"}"
    ```
    `multiSelect` 的值用数组：`{"水果": ["草莓", "芒果"]}`。非 JSON 输入会原样传递给 LLM（向后兼容）
- **计划审批交互**：当 LLM 在 print 模式下调用 `exit_plan_mode` 时，采用与 ask_user_question 相同的跨轮次模式：
  1. **第 1 轮**：`illusion -p "实现功能X"` → 代理进入计划模式、编写计划文件、调用 `exit_plan_mode` → 计划持久化到 `pending-plan-approval-<session_id>.json` → 退出码 **2**
  2. **第 2 轮**：`illusion -c -p "批准"` → 检测到 pending plan approval → 注入审批结果 → 继续执行

  退出码语义：0=正常完成，1=错误，2=等待用户输入（ask_user_question、计划审批或权限确认）。

  **审批输入格式**：输入"批准"/"approve"/"yes"/"y"（不区分大小写）表示批准；其他任何输入视为拒绝并作为反馈意见。例如 `illusion -c -p "需要增加测试用例"` 会被解析为拒绝+反馈。
- **持久化时机**：标记"持久化"的参数会在执行提示词之前写入 `settings.json`，即使后续执行失败，持久化仍生效。

**示例**：

```bash
# 基本用法
illusion -p "分析这个项目的结构"

# 指定模型 + 输出 JSON
illusion -m env_1.model_2 -p "列出 TODO 注释" --output-format json

# 高推理强度 + 限制轮次（均持久化）
illusion -e high -t 10 -p "重构这个函数"

# 完全自动权限 + 持久化
illusion --permission-mode full_auto -p "运行测试"

# 继续上次会话
illusion -c -p "继续上次的任务"

# 恢复指定会话
illusion -r <session-id> -p "继续"

# 组合：模型 + 权限 + effort + 轮次 + 会话恢复
illusion -m env_1.model_2 -e max -t 20 --permission-mode full_auto -c -p "完成这个功能"
```
