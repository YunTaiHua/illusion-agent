# 设置与凭据配置

## 目录

- [配置概览](#配置概览)
- [凭据文件 (credentials.json)](#凭据文件-credentialsjson)
- [全局配置 (settings.json)](#全局配置-settingsjson)
  - [working_directory](#working_directory)
  - [环境配置 (EnvConfig)](#环境配置-envconfig)
  - [各 API 格式配置示例](#各-api-格式配置示例)
  - [权限配置](#权限配置)
  - [环境变量](#环境变量)
  - [记忆系统配置](#记忆系统配置)
  - [会话自动标题配置](#会话自动标题配置)
  - [通知开关（Toast 与音效）](#通知开关toast-与音效)
  - [沙箱配置](#沙箱配置)

---

## 配置概览

| 文件 | 位置 | 作用域 | 用途 |
|------|------|--------|------|
| `settings.json` | `~/.illusion/settings.json` | 全局 | 主设置：API 配置、权限、钩子等 |
| `credentials.json` | `~/.illusion/credentials.json` | 全局 | 安全凭据存储（API 密钥） |

环境变量覆盖：`ILLUSION_CONFIG_DIR` 替换 `~/.illusion/`，`ILLUSION_DATA_DIR` 替换 `~/.illusion/data/`，`ILLUSION_LOGS_DIR` 替换 `~/.illusion/logs/`。

### 配置优先级

1. **CLI 参数** — 最高优先级
2. **配置文件** — `~/.illusion/settings.json`
3. **默认值** — 内置默认配置

---

## 凭据文件 (credentials.json)

位于 `~/.illusion/credentials.json`，由 `illusion auth login` 管理。凭据按 `env_N` 分组存储。

```json
{
  "env_1": {
    "api_key": "sk-ant-xxxxx"
  },
  "env_2": {
    "api_key": "sk-xxxxx"
  }
}
```

**API 密钥存储方式：**

| 方式 | 位置 | 优势 |
|------|------|------|
| **安全模式** | `credentials.json`（由 `illusion auth login` 管理） | 密钥与配置分离，文件权限受保护 |
| **便捷模式** | `settings.json` 的 `env_N.api_key` | 配置集中在一个文件 |

运行时优先级：`env_N.api_key` > `credentials.json`。

> **文件权限 600**：在 Unix/Linux 上，文件设置为 `rw-------`（仅所有者可读写）。Windows 上静默跳过。

---

## 全局配置 (settings.json)

### 格式

使用 `env_N` 分组格式。每个 `env_N` 是独立的环境配置（EnvConfig）。`model` 字段引用 `env_N.model_N`。

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
  },
  "env_2": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "model": "env_1.model_1",
  "context_window": 200000
}
```

### 完整配置结构

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
  },
  "model": "env_1.model_1",
  "context_window": 200000,
  "max_tokens": 16384,
  "max_turns": 200,
  "permission": {
    "mode": "default",
    "allowed_tools": [],
    "denied_tools": [],
    "allowed_shell_commands": [],
    "path_rules": [],
    "denied_commands": []
  },
  "hooks": {},
  "memory": {
    "enabled": true,
    "max_files": 5,
    "max_entrypoint_lines": 200
  },
  "title": {
    "enabled": false,
    "model": "env_1.model_1"
  },
  "notifications": {
    "enabled": true,
    "sound": true
  },
  "sandbox": {
    "enabled_platforms": [],
    "excluded_commands": [],
    "network": {
      "allowed_domains": [],
      "denied_domains": [],
      "allow_unix_sockets": [],
      "allow_all_unix_sockets": false,
      "allow_local_binding": false,
      "http_proxy_port": null,
      "socks_proxy_port": null
    },
    "filesystem": {
      "allow_read": [],
      "deny_read": [],
      "allow_write": ["."],
      "deny_write": []
    },
    "ignore_violations": {},
    "enable_weaker_nested_sandbox": false,
    "enable_weaker_network_isolation": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false,
    "ripgrep": null
  },
  "enabled_plugins": {},
  "mcp_servers": {},
  "working_directory": null,
  "ui_language": "zh-CN",
  "effort": "medium"
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `env_N` | object | - | 环境配置组（EnvConfig） |
| `model` | string | "env_1.model_1" | 当前活跃模型引用：`env_N.model_N` |
| `context_window` | int | 200000 | 上下文窗口大小（tokens） |
| `max_tokens` | int | 16384 | 最大输出 token 数 |
| `max_turns` | int | 200 | 最大对话轮数 |
| `ui_language` | string | "" | 界面语言（空时首次登录引导选择，兜底中文） |
| `effort` | string | "medium" | 推理强度：low/medium/high/xhigh/max |
| `notifications.enabled` | bool | true | Toast 通知总开关（任务完成/终止、询问、权限提醒；关闭后后端不再下发 toast 事件） |
| `notifications.sound` | bool | true | Toast 提示音效开关（仅在 `notifications.enabled` 开启时生效） |
| `working_directory` | string | - | 固定工作目录（可选） |

---

## working_directory

固定工作目录。如果设置此字段，illusion-agent启动时会自动切换到该目录。

**设置方式：**
- 通过 `illusion set [path]` 命令设置（推荐）
- 首次运行 `illusion auth login` 时会引导设置
- 直接编辑 `settings.json`

**类型：** 字符串（可选）

**默认值：** 不设置或为空

**示例：**

```json
{
  "working_directory": "E:\\Projects\\my-project"
}
```

**行为：**
- 如果字段存在且不为空，启动时自动切换到指定目录
- 如果字段不存在或为空，使用启动时的当前目录
- 如果指定的目录不存在，`illusion set` 会自动新建目录
- 如果启动时目录校验失败（如权限不足），记录警告日志，使用当前目录

### 目录空间（Web 多工作区）

`working_directory` 在 Web 端作为**默认工作区**。Web 端支持在多个目录空间并发运行，每个目录拥有独立的会话历史与项目级配置（`<目录>/.illusion/` 的权限、skills、rules、MCP、插件、hooks），模型与 API 环境全局共享。

- 注册表存储于 `~/.illusion/workspaces.json`（默认工作区不重复入库，动态注入）
- Web 设置 →「目录空间」页可添加/移除/设默认目录；**移除目录会连带删除该目录的全部会话**（目录存在运行中的会话时禁止移除）
- 会话列表按目录分组；输入框目录按钮（欢迎界面常显）可选择目录新建会话
- 每个目录的运行时 bundle 懒构建、空闲自动驱逐，避免资源翻倍

---

### 环境配置 (EnvConfig)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_format` | string | 是 | API 格式：`anthropic` / `openai` / `response` / `copilot` / `codex` |
| `base_url` | string\|null | 否 | 自定义 API 端点，null 使用默认端点 |
| `api_key` | string | 否 | API 密钥（标准 x-api-key 认证） |
| `auth_token` | string | 否 | Bearer Token 认证（用于 LongCat 等使用 `Authorization: Bearer` 的提供商） |
| `model_N` | string | 否 | 模型名称：`model_1`、`model_2`、... |

### 多模型配置

```json
{
  "env_1": {
    "api_format": "openai",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model_1": "stepfun-ai/step-3.5-flash",
    "model_2": "minimaxai/minimax-m2.7",
    "model_3": "meta/llama-3.1-405b-instruct"
  },
  "model": "env_1.model_1"
}
```

**切换模型：**
```bash
/model                          # 交互式切换
illusion -m env_1.model_2       # CLI 参数指定
```

---

### 各 API 格式配置示例

#### 1. Anthropic Claude API

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6",
    "model_2": "claude-opus-4-6"
  },
  "model": "env_1.model_1"
}
```

#### 2. OpenAI API

```json
{
  "env_1": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "model": "env_1.model_1"
}
```

#### 3. 自定义格式

在 `illusion auth login` 中选择"自定义格式"，输入 API 格式、端点、密钥和模型名。

#### 4. GitHub Copilot

```bash
illusion auth login  # 选择 GitHub Copilot
```

在浏览器中完成 GitHub 授权后自动配置。认证数据存储在 `~/.illusion/copilot_auth.json`。

```json
{
  "env_1": {
    "api_format": "copilot",
    "base_url": "https://api.githubcopilot.com",
    "model_1": "gpt-5.5"
  }
}
```

#### 5. OpenAI Codex（ChatGPT 订阅）

```bash
illusion auth login   # 选择 OpenAI Codex
```

使用 Device Code 流程完成 ChatGPT 订阅认证。认证数据存储在 `~/.illusion/codex_oauth_auth.json`。

```json
{
  "env_1": {
    "api_format": "codex",
    "base_url": "https://chatgpt.com/backend-api",
    "model_1": "codex-mini"
  }
}
```

#### 6. LongCat（Bearer Token 认证）

LongCat 使用 `Authorization: Bearer` 认证方式，需要通过 `auth_token` 字段配置（而非 `api_key`）。

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": "https://api.longcat.chat/anthropic",
    "auth_token": "ak_your_longcat_api_key",
    "model_1": "LongCat-2.0"
  }
}
```

#### 7. 多格式混合配置

```json
{
  "env_1": {
    "api_format": "anthropic",
    "base_url": null,
    "model_1": "claude-sonnet-4-6"
  },
  "env_2": {
    "api_format": "openai",
    "base_url": "https://api.openai.com/v1",
    "model_1": "gpt-5.4"
  },
  "env_3": {
    "api_format": "copilot",
    "base_url": "https://api.githubcopilot.com",
    "model_1": "gpt-5.5"
  },
  "model": "env_1.model_1"
}
```

---

### 权限配置

#### 权限模式

| 模式 | 值 | 说明 |
|------|-----|------|
| 默认模式 | `default` | 修改类工具需要用户确认 |
| 计划模式 | `plan` | 阻止所有修改类工具 |
| 全自动模式 | `full_auto` | 受沙箱限制并拦高危（HIGH 需确认） |
| YOLO 模式 | `yolo` | **绕过沙箱完全运行**，不施加任何沙箱限制 |

```json
{
  "permission": {
    "mode": "default",
    "allowed_tools": ["read_file", "grep", "glob"],
    "denied_tools": ["bash"],
    "path_rules": [
      {"pattern": "src/**", "allow": true},
      {"pattern": "secrets/**", "allow": false}
    ],
    "denied_commands": ["/init", "/memory"]
  }
}
```

#### auto 模式下：LLM 自动审核

`full_auto`（auto）模式下需确认的拦截分两类：**高危操作**（HIGH，如 `rm` / `git reset --hard`，以及删除类、git 破坏性操作、格式化/块设备写入、PowerShell 删除/格式化系列、复合命令分段等内置高危命令集）与**沙箱拦截的常规操作**（如工作区外读写）。默认走人工确认；可开启 **LLM 自动审核**，由审核模型代替人工裁决：

```json
{
  "permission": {
    "mode": "full_auto",
    "auto_review": true,
    "review_model": "env_2.model_1"
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `permission.auto_review` | `false` | 仅 `full_auto` 模式生效：开启后高危操作与沙箱拦截（工作区外读写）均由 LLM 审核放行，不再弹人工确认框；关闭时恢复现有人工确认流程。`yolo` / `plan` / `default` 模式不受影响 |
| `permission.review_model` | 未设置 | 审核模型（`env_N.model_M` 格式），未设置时继承当前会话模型 |

- 审核为固定 `effort = high`、最大输出 `8192` token 的单轮子代理；API 失败/输出不可解析时重试 3 次，最终失败 fail-closed 拒绝（绝不静默放行）
- 审核活动记录到 `~/.illusion/logs/permission_review.log`（可用 `ILLUSION_LOGS_DIR` 覆盖）
- **Web 端**：设置 → 基础配置 →「权限 LLM 自动审核」区块，切换开关并选择审核模型
- **终端**：`/permissions auto on|off|toggle|status`、`/permissions model show|set REF|set inherit`、`/permissions` 查看当前状态

#### 权限确认超时（Web 与终端，统一作用于所有会话）

**所有会话**（主对话与子代理）在 Web 与终端端发起权限确认时，带约 **285s** 等待超时（取值刻意小于子代理无活动超时 300s，确保超时原因优先于笼统的 "Agent timed out" 出现）：超时后以带原因的失败结束（如「权限确认超时」），错误作为工具结果回流（任务不终止），并自动清理遗留的确认弹窗。**ask_user_question 普通问答**（非沙箱权限）区别对待：15 分钟超时后不报错，返回 "(no response)" 占位答案并提示 agent 自行选择最合适的选项继续。

#### 渠道权限模式

渠道（飞书 / QQ / 微信）运行的 agent 固定使用 `yolo` 权限模式：不向渠道会话弹出权限确认（包括工作区外/沙箱类「常规操作」与高危操作），但 `denied_tools` / 路径 deny 规则 / `denied_commands` 等显式拒绝规则仍然生效；LLM 主动调用提问（`ask_user_question`）时仍会向渠道发送消息询问。

---

### 环境变量

| 变量 | 说明 |
|------|------|
| `ILLUSION_CONFIG_DIR` | 覆盖配置目录路径（默认：`~/.illusion/`） |
| `ILLUSION_DATA_DIR` | 覆盖数据目录路径（默认：`~/.illusion/data/`） |
| `ILLUSION_LOGS_DIR` | 覆盖日志目录路径（默认：`~/.illusion/logs/`） |

> **注意：** API 密钥、模型名称等运行时设置仅通过 `settings.json` 和 `credentials.json` 管理。使用 `illusion auth login` 配置凭据。

---

### 记忆系统配置

```json
{
  "memory": {
    "enabled": true,
    "max_files": 5,
    "max_entrypoint_lines": 200
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | true | 启用记忆功能 |
| `max_files` | 5 | 最大记忆文件数 |
| `max_entrypoint_lines` | 200 | MEMORY.md 入口文件最大行数 |

---

### 会话自动标题配置

首回合结束后在后台运行一个轻量子代理，根据用户首条真实消息生成简洁会话标题，写入会话 `meta.json` 的 `title` 字段（`/resume`、`/delete` 列表与 web 侧边栏据此显示）。后台执行不阻塞对话进行。

```json
{
  "title": {
    "enabled": false,
    "model": "env_1.model_1"
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | false | 是否启用自动标题（默认关闭） |
| `model` | 空（继承当前） | 标题生成子代理使用的模型（`env_N.model_M` 格式），留空继承当前会话模型 |

- 仅首回合触发，且只取用户首条真实消息；若首条为 `/goal` 命令，回退使用当前 goal 的 objective。
- 若会话已手动重命名（meta 已有 title），自动标题不会覆盖用户命名。
- 标题生成为后台任务，偶发为空时自动重试；活动记录于 `~/.illusion/logs/title.log`。

---

### 通知开关（Toast 与音效）

控制 Web 端 / 桌面端的 toast 通知行为。后端在**任务完成、任务终止、询问等待回答、权限请求确认**四类事件时下发 toast；前端按用户是否在场决定呈现方式：

| 用户状态 | 行为 |
|----------|------|
| 正在应用界面监管（页面可见且聚焦） | 全部静默：不弹 toast、不响音效、不发系统通知（界面内的运行状态与待确认弹窗直接可见） |
| 页面可见但失焦 | 系统级通知 + 提示音效（应用内不再重复弹出对应卡片） |
| 页面不可见（切走标签页 / 最小化到托盘） | 系统级通知 + 提示音效；回到应用后不补显应用内卡片 |

系统级通知在桌面壳内为 Electron 系统通知（点击可回到应用），纯浏览器为 Web Notification。两类通知均为**极简两段式**——固定短标题（按事件类型本地化）+ 一行纯文本摘要（正文 Markdown 自动降为单行，不与横幅原生排版冲突）。**浏览器需授权一次**：首次点击或按键时自动弹出授权请求（后台标签页中申请会被浏览器拦截，故提前到用户手势时机）；拒绝后仅剩提示音效（可在浏览器站点设置中重新允许通知）。

```json
{
  "notifications": {
    "enabled": true,
    "sound": true
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | true | Toast 总开关。关闭后后端不再下发任何 toast 事件（含系统级透传） |
| `sound` | true | 提示音效开关 |

除直接编辑 settings.json 外，也可在 **Web 端设置弹窗 → 设置 → 通知开关** 中切换，保存后即时生效。

> **联动规则**：两个开关独立保存，但**音效只在 toast 总开关开启时才处理**——`enabled=false` 时无论 `sound` 取值如何都静默。

---

### 沙箱配置

沙箱系统为 shell 命令提供操作系统级隔离。支持两平台：

| 平台 | 机制 | 依赖 |
|------|------|------|
| Linux / WSL | bubblewrap (bwrap) + 可选 seccomp | `bwrap`、`socat` |
| macOS | Apple Seatbelt (sandbox-exec) | 内置 |

**Windows 原生不支持 OS 级沙箱**（bwrap/sandbox-exec 均为 POSIX 专属，Windows 上命令一律无沙箱运行）。Windows 的隔离依赖**权限层**：命令风险分级（高危拦截/确认）+ 文件系统白名单（`filesystem.allow_write` 等），这两层对所有平台一致生效。

#### 基础配置

```json
{
  "sandbox": {
    "enabled_platforms": [],
    "excluded_commands": []
  }
}
```

#### 网络配置

```json
{
  "sandbox": {
    "network": {
      "allowed_domains": ["api.anthropic.com", "*.github.com"],
      "denied_domains": ["malicious.example.com"],
      "allow_unix_sockets": [],
      "allow_all_unix_sockets": false,
      "allow_local_binding": false,
      "http_proxy_port": null,
      "socks_proxy_port": null
    }
  }
}
```

#### 文件系统配置

```json
{
  "sandbox": {
    "filesystem": {
      "allow_write": [".", "./output"],
      "deny_write": [".git/hooks", ".env"],
      "deny_read": ["./secrets"],
      "allow_read": ["./secrets/public"]
    }
  }
}
```

> **文件系统限制语义**：写入为**默认拒绝**——仅 `allow_write` 白名单内的路径可写，`deny_write` 覆盖白名单；读取为**默认允许**——仅 `deny_read` 限制。此限制同样作用于文件工具（Write/Edit/Read 等），对齐 OS 级沙箱行为：工作目录（`"."`）之外的文件写入会被沙箱拦截/请求确认。

#### 高级选项

```json
{
  "sandbox": {
    "enable_weaker_nested_sandbox": false,
    "enable_weaker_network_isolation": false,
    "mandatory_deny_search_depth": 3,
    "allow_git_config": false,
    "ripgrep": {
      "command": "rg",
      "args": []
    }
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enable_weaker_network_isolation` | false | macOS 允许访问 trustd（Go 工具 TLS 校验所需，**降低网络隔离**，存在数据外泄风险） |
| `enable_weaker_nested_sandbox` | false | Docker 环境跳过 `--proc /proc` |
| `allow_git_config` | false | 允许写入 `.git/config` |
| `ripgrep` | null | 沙箱内置 ripgrep 命令与参数 |

#### 排除命令

```json
{
  "sandbox": {
    "excluded_commands": [
      "npm test",
      "make:*",
      "git status"
    ]
  }
}
```

#### 高危操作分级

操作按风险等级分级，**高危操作权限等级高于读取**：

- **高危（HIGH）**：删除 / 还原 / 强制重置类命令（`rm`、`Remove-Item`、`git restore`、`git clean`、`git reset --hard` 等）。即使某路径已被**会话级允许**，涉及高危操作时仍会重新请求用户确认，防止"已放开访问"被用作删除通行证。
- **中危（MEDIUM）**：一般变更操作（写文件、编辑等）。
- **低危（LOW）**：只读操作（读文件、查询命令等）。

**风险分级规则已内置**（LOW/MEDIUM/HIGH）：

- `dangerous_bash_patterns`（HIGH）：高危 bash 命令正则，匹配即请求确认
- `dangerous_powershell_patterns`（HIGH）：高危 powershell 命令正则
- `read_only_commands`（LOW）：只读命令前缀，直接放行
- `medium_risk_tools`（MEDIUM）：变更类工具，默认需要确认

若希望放行某个会被高危拦截的指令，可在 `settings.json` 的 `permission.allowed_shell_commands` 中配置**命令级白名单**（bash / powershell 命令通用）：

- **非高危命令**：命中前缀即放行（如配置 `git push` 放行 `git push origin main`）。
- **高危命令**：仅当白名单项**完整列出**该高危命令头时才放行——即该项本身也是高危模式。仅配置普通前缀**不会**豁免其高危子命令（如配置 `git push` 不放行 `git push --force`；配置 `rm` 不放行 `rm -rf`）。需豁免高危时，请配置完整命令头，如 `git push --force`、`rm -rf`、`Remove-Item`。

```json
{
  "permission": {
    "mode": "default",
    "allowed_shell_commands": ["git push --force", "rm -rf", "Remove-Item"]
  }
}
```

#### 沙箱配置与风险分级的关系

`sandbox` 配置与内置风险分级（LOW/MEDIUM/HIGH）是**两个相互独立的维度**，在权限检查时串联执行：

| 维度 | 本质 | 决定什么 | 位于 |
|------|------|----------|------|
| 沙箱配置（`sandbox.*`） | 运行时隔离 | "命令能碰哪些路径/域名" | `settings.json` 的 `sandbox` 段 |
| 风险分级 | 决策分类 | "这个操作多危险，是否弹窗确认" | 内置（`risk.py`），只读 |

- **沙箱配置**（`filesystem.*`、`network.*`、`excluded_commands`）约束 OS 沙箱实际行为，不直接决定是否弹窗。
- **风险分级**（`dangerous_bash_patterns` / `read_only_commands` / `medium_risk_tools`）决定是否弹窗确认。
- **串联顺序**：先查沙箱路径限制（`filesystem`），再算风险分级。命中沙箱 deny 或 HIGH 都会触发确认。
- **关键交集**：高危操作（HIGH）即使路径已被会话级允许，仍会重新请求确认，防止"已放开访问"被当作删除通行证。

**各权限模式对两个维度的消费方式：**

| 模式 | 消费沙箱配置 | 消费风险分级 |
|------|--------------|--------------|
| `default` | 全量生效（filesystem/network/excluded） | 完整消费：LOW 放行 / MEDIUM 确认 / HIGH 必问 |
| `full_auto` | 受沙箱文件系统限制 | 只拦 HIGH，其余放行 |
| `plan` | 计划文件豁免，其余变更被挡 | 不按分级，按"是否变更工具"拦截 |
| `yolo` | 全部绕过 | 忽略，仅保留显式工具/路径 deny |
