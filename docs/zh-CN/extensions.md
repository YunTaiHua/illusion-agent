# 扩展系统：MCP、规则、插件、技能与钩子

## 目录

- [概述](#概述)
- [MCP 服务器配置](#mcp-服务器配置)
- [规则配置](#规则配置)
- [插件系统](#插件系统)
- [技能系统](#技能系统)
- [钩子系统](#钩子系统)

---

## 概述

IllusionAgent 会从用户配置根目录、当前项目、内置内容和已启用插件解析资源。

- `<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认是 `~/.illusion`。
- 项目级资源位于 `<project>/.illusion`。
- 资源发现只读取已有文件和目录。缺失的资源目录和文件会被跳过，不会被创建。明确的写入、安装或初始化命令会在需要时创建目标。

不同资源类型使用不同的加载顺序。项目技能会覆盖同名用户技能，命令使用首次发现优先，因此用户命令优先于项目命令和插件命令。插件资源仅从已启用插件加载。

---

## MCP 服务器配置

### 配置类型

| 类型 | 字段 | 说明 |
|------|------|------|
| `stdio` | command, args, env, cwd, log_file, enabled | 标准输入输出通信 |
| `http` | url, headers, enabled | HTTP 协议（Streamable HTTP，兼容 `streamable-http`/`streamableHttp`/`streamable_http`/`streamablehttp` 别名） |
| `sse` | url, headers, enabled | Server-Sent Events 协议 |

所有类型支持 `enabled` 字段，默认值为 `true`。设置为 `false` 可禁用服务器而不删除配置。

`type` 字段可省略。省略时按 `stdio` 处理：

```json
{
  "command": "python",
  "args": ["server.py"]
}
```

### 标准文件位置

| 来源 | 标准位置 | 说明 |
|------|----------|------|
| 用户级 MCP | `<user-config>/mcp.json` | `<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认 `~/.illusion` |
| 项目级 MCP | `<project>/.illusion/mcp.json`（优先）或 `<project>/.illusion/.mcp.json` | 仅在首选文件不存在时读取点文件 |
| 设置中的 MCP | `<user-config>/settings.json` → `mcp_servers` | 结构化 settings 来源 |
| 插件 MCP | 插件根目录 `.mcp.json`、清单 `mcp_file` 值、插件根目录 `mcp.json` | 插件服务器注册为 `{plugin_name}:{server_name}` |

两个作用域的独立配置文件都叫 `mcp.json`。旧的 `<user-config>/mcp/` 和 `<project>/.illusion/mcp/` 目录不再扫描。手动迁移 `mcp/filesystem.json` 时，将原服务器对象放入同一作用域 `mcp.json` 的 `mcpServers.filesystem` 下。如果旧文件本身已包含命名映射，将各项按原名合并到 `mcpServers` 即可。应用会保留磁盘上的旧文件，发现资源时会跳过缺失路径。

插件清单可以通过 `mcpServers` / `mcp_servers` 增加 MCP 文件路径或内联定义。清单路径必须相对于插件根目录，不能越出插件根目录。

### 文件格式

标准 MCP 文件可以直接包含一个服务器对象：

```json
{
  "command": "python",
  "args": ["server.py"],
  "enabled": true
}
```

也可以在 `mcpServers` 或 `mcp_servers` 下包含命名服务器映射：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
    },
    "remote-api": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {"Authorization": "Bearer token"}
    }
  }
}
```

也支持 `{ "filesystem": { ... } }` 这样的无包装映射，以及嵌套的 `mcp.servers`、`mcp.mcpServers` 和 `mcp.mcp_servers` 形式。

### 合并顺序

实际优先级为项目文件、用户文件、`settings.json`、插件服务器。项目条目覆盖同名用户条目，用户条目覆盖同名 settings 条目。插件服务器使用 `{plugin_name}:{server_name}` 命名空间，不替换已有的非插件条目。

CLI 管理：

```bash
illusion mcp list
illusion mcp add <name> <config>
illusion mcp remove <name>
```

### 源码参考

- 配置加载：`src/illusion/mcp/config.py`
- 路径解析：`src/illusion/config/paths.py`
- 类型定义：`src/illusion/mcp/types.py`

---

## 规则配置

规则是 `.md` 文件，为 AI 提供项目特定指令。

### 发现位置

规则从以下位置发现：
1. `{cwd}/.claude/rules/*.md` — 按文件名排序
2. `{cwd}/.illusion/rules/*.md` — 按文件名排序
3. AI 指令文件（`CLAUDE.md`、`ILLUSION.md`、`AGENTS.md`）在项目根目录、`.claude/` 和 `.illusion/` 目录中

### 规则文件格式

每个 `.md` 文件是独立规则，文件名决定排序顺序：

```
.claude/rules/
├── 01-python-style.md
├── 02-testing.md
└── 03-git-workflow.md
```

### 初始化

`/init` 命令在 `.illusion/rules/` 生成默认规则：
- `python-style.md` — Python 代码风格规则
- `testing.md` — 测试框架和规范
- `project-structure.md` — 项目结构指南

### 源码参考

- 发现逻辑：`src/illusion/prompts/claudemd.py` — `discover_claude_md_files()`
- 规则生成：`src/illusion/commands/init/generation/rules.py`

---

## 插件系统

### 插件目录

1. **用户级**：`<user-config>/plugins/`
2. **项目级**：`<project>/.illusion/plugins/`

`<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认 `~/.illusion`。只有目录已经存在时才会扫描，插件目录缺失时不会创建。

### 插件发现

插件根目录下的可见直接子目录，只有包含以下清单候选之一时才会被考虑，候选顺序如下：

1. `.illusion-plugin/plugin.json`
2. `.claude-plugin/plugin.json`
3. `.codex-plugin/plugin.json`
4. `.zcode-plugin/plugin.json`
5. `plugin.json`

第一个已存在的候选决定插件清单格式。如果第一个候选存在但格式无效，该插件会被跳过，不会回退到后续候选。根目录扫描不会发现隐藏插件目录。

### 插件清单

清单为 JSON 文件。清单声明的资源路径必须相对于插件根目录，越出插件根目录的路径会被拒绝。

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "我的自定义插件",
  "enabled_by_default": true,
  "skills_dir": "skills",
  "hooks_file": "hooks.json",
  "mcp_file": "mcp.json",
  "commands": "commands",
  "agents": "agents",
  "skills": "skills",
  "hooks": "hooks/hooks.json",
  "mcpServers": ".mcp.json"
}
```

完整清单字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | 必填 | 插件名称 |
| `version` | string | `"0.0.0"` | 插件版本 |
| `description` | string | `""` | 插件描述 |
| `enabled_by_default` | bool | true | 初始启用状态 |
| `skills_dir` | string | `"skills"` | 默认技能入口 |
| `hooks_file` | string | `"hooks.json"` | 默认钩子文件候选 |
| `mcp_file` | string | `"mcp.json"` | 默认 MCP 文件候选 |
| `commands` | string \| list \| dict | null | 命令目录、路径或内联映射 |
| `agents` | string \| list \| dict | null | 代理目录、路径或内联映射 |
| `skills` | string \| list | null | 技能目录或路径，省略时使用 `skills_dir` |
| `hooks` | string \| dict \| list | null | 钩子路径或内联定义 |
| `mcpServers` / `mcp_servers` | string \| list \| dict | null | MCP 路径或内联定义 |
| `settings` | dict | null | 插件默认设置 |
| `user_config` / `userConfig` | dict | null | 插件用户配置元数据 |

### 插件资源默认位置

清单没有用显式入口替换时，加载器会检查：

- `skills/` 作为技能入口
- `commands/` 作为命令入口
- `agents/` 作为代理入口
- `hooks/hooks.json`、`hooks_file` 值和 `hooks.json` 作为钩子入口
- `.mcp.json`、`mcp_file` 值和 `mcp.json` 作为 MCP 入口

所有缺失资源路径都会被跳过，不会创建目录。

### 插件目录结构

```text
my-plugin/
├── .illusion-plugin/plugin.json  # 或上面列出的其他清单候选
├── skills/
│   ├── my-skill/
│   │   └── SKILL.md
│   └── another-skill.md
├── commands/                     # 斜杠命令（.md 文件）
├── agents/                       # 代理定义（.md 文件）
├── hooks/
│   └── hooks.json                # 钩子定义
├── .mcp.json                     # MCP 服务器配置
└── settings.json                 # 插件默认设置
```

### 插件启用/禁用

Web 插件开关和终端的 `/plugin enable <名称>`、`/plugin disable <名称>` 都以清单的 `name` 为键保存开关。写入位置由插件的安装来源决定。

| 插件安装位置 | 保存 `enabled_plugins` 的文件 |
|---|---|
| `<user-config>/plugins/` 下的用户级或内置插件 | `<user-config>/settings.json` |
| `<project>/.illusion/plugins/` 下的项目插件 | `<project>/.illusion/permissions.json` |

例如，要在当前项目启用项目内安装的 `academic-research-skills`，可以把以下配置合并到该项目的 `permissions.json`：

```json
{
  "enabled_plugins": { "academic-research-skills": true }
}
```

未配置开关时使用清单的 `enabled_by_default`。项目安装只读取所属项目的开关，即使全局已有同名键。项目 `denied_plugins` 命中名称或 `"*"` 时，对所有来源的插件都优先禁用。普通启停操作会保留这条权限规则。

插件提供的命令、技能和钩子可随开关刷新。插件提供的 MCP 服务在下次构建工作区运行时按新配置连接。重启对应的 Web 服务或终端进程即可应用。

### 插件技能命名

所有插件技能注册格式为 `{插件名}:{技能名}`，避免冲突。

### 插件钩子变量

钩子命令支持 `${CLAUDE_PLUGIN_ROOT}` 和 `${CLAUDE_PLUGIN_DATA}` 变量替换。

### CLI 管理

```bash
illusion plugin list
illusion plugin install <source>
illusion plugin uninstall <name>
```

在终端会话内使用 `/plugin enable <名称>` 和 `/plugin disable <名称>` 启停插件。

### 源码参考

- 插件加载器：`src/illusion/plugins/loader.py`
- 清单 schema：`src/illusion/plugins/schemas.py`
- 插件类型：`src/illusion/plugins/types.py`

---

## 技能系统

### 技能来源（优先级顺序）

1. **内置技能**：`src/illusion/skills/bundled/content/*.md`
2. **用户技能**：`<user-config>/skills/`，支持 `<name>/SKILL.md`、`<name>.md` 和 YAML 技能文件
3. **项目技能**：`<project>/.illusion/skills/`，支持 `<name>/SKILL.md`、`<name>.md` 和 YAML 技能文件
4. **插件技能**：从已启用插件的技能入口加载

`<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认 `~/.illusion`。缺失技能目录会被跳过，不会创建。技能按此顺序注册，因此项目技能和插件技能可以替换前面来源中的同名技能。

### 命令来源

斜杠命令与技能分开管理，来源如下：

1. 用户命令：`<user-config>/commands/**/*.md`
2. 项目命令：`<project>/.illusion/commands/**/*.md`
3. 已启用插件的命令入口，包括默认 `commands/` 目录和清单映射

相对路径中的子目录会组成冒号分隔的命令名。命令按首次发现注册，因此同名时用户命令优先于项目命令和插件命令。缺失命令目录会被跳过，不会创建。

### 代理来源

代理定义来源如下：

1. 内置代理定义
2. 用户 Markdown 文件：`<user-config>/agents/*.md`
3. 项目 Markdown 文件：`<project>/.illusion/agents/*.md`
4. 已启用插件的代理入口，包括默认 `agents/` 目录和清单映射

后面的来源会替换前面来源中的同名代理定义。缺失代理目录会被跳过，不会创建。

### SKILL.md 格式

支持 YAML frontmatter：

```markdown
---
description: 技能描述
allowed-tools: Bash, Read, Write
model: claude-sonnet-4-6
context: fork
effort: high
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: echo check
---

技能内容（markdown）...
```

#### Frontmatter 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 技能名（省略时从文件名派生） |
| `description` | string | 技能描述（显示在技能列表中） |
| `allowed-tools` | string\|list | 逗号分隔或列表的允许工具名 |
| `model` | string | 该技能的模型覆盖 |
| `context` | string | `inline`（展开到对话）或 `fork`（子代理） |
| `effort` | string | 推理强度覆盖 |
| `hooks` | object | 技能调用时注册的钩子 |
| `agent` | string | 使用的代理类型 |
| `disable_model_invocation` | bool | 禁用模型调用 |
| `skill_root` | string | 技能资源根目录 |

### 内置技能

| 技能 | 描述 |
|------|------|
| `debug` | 系统化调试工作流 |
| `verify` | 代码变更验证 |
| `loop` | 循环任务执行 |
| `batch` | 批量操作 |
| `remember` | 记忆管理 |
| `simplify` | 代码质量审查 |
| `skillify` | 将模式转化为技能 |
| `stuck` | 突破阻塞 |
| `update-config` | 配置 settings.json |

### 源码参考

- 技能加载器：`src/illusion/skills/loader.py`
- 技能类型：`src/illusion/skills/types.py`
- 技能注册表：`src/illusion/skills/registry.py`

---

## 钩子系统

### 支持的事件（27 个）

| 事件 | 匹配字段 | 说明 |
|------|----------|------|
| `PreToolUse` | tool_name | 工具执行前 |
| `PostToolUse` | tool_name | 工具执行后 |
| `PostToolUseFailure` | tool_name | 工具执行失败后 |
| `PermissionDenied` | tool_name | 自动模式分类器拒绝后 |
| `Notification` | notification_type | 发送通知时 |
| `UserPromptSubmit` | — | 用户提交提示词时 |
| `SessionStart` | source | 新会话开始 |
| `SessionEnd` | reason | 会话结束 |
| `Stop` | — | Claude 结束响应前 |
| `StopFailure` | error | 因 API 错误结束回合 |
| `SubagentStart` | agent_type | 子代理启动 |
| `SubagentStop` | agent_type | 子代理结束 |
| `PreCompact` | trigger | 压缩前 |
| `PostCompact` | trigger | 压缩后 |
| `PermissionRequest` | tool_name | 权限对话框显示时 |
| `Setup` | trigger | 仓库设置 |
| `ConfigChange` | source | 配置文件变更 |
| `InstructionsLoaded` | load_reason | 指令文件加载时 |
| `WorktreeCreate` | — | 创建工作树 |
| `WorktreeRemove` | — | 移除工作树 |
| `CwdChanged` | — | 工作目录变更 |
| `FileChanged` | — | 监视文件变更 |
| `TaskCreated` | — | 任务创建时 |
| `TaskCompleted` | — | 任务完成时 |
| `TeammateIdle` | — | 队友即将空闲 |
| `Elicitation` | mcp_server_name | MCP 引出请求 |
| `ElicitationResult` | mcp_server_name | 用户响应引出后 |

### 钩子类型

| 类型 | 必填 | 可选 | 说明 |
|------|------|------|------|
| `command` | `command` | `if`, `shell`, `timeout`, `statusMessage`, `once`, `async` | 执行 shell 命令 |
| `prompt` | `prompt` | `if`, `model`, `timeout`, `statusMessage`, `once` | 使用 LLM 验证 |
| `http` | `url` | `if`, `timeout`, `headers`, `allowedEnvVars`, `statusMessage`, `once` | 发送 HTTP POST |
| `agent` | `prompt` | `if`, `model`, `timeout`, `statusMessage`, `once` | 使用 Agent 验证 |

### 钩子配置格式

基于 matcher 的结构：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Tool: $ARGUMENTS' >> /tmp/tool.log",
            "timeout": 30
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "http",
            "url": "https://hooks.example.com/tool-complete",
            "headers": {"Authorization": "Bearer token"}
          }
        ]
      }
    ]
  }
}
```

### 匹配器模式

| 模式 | 示例 | 行为 |
|------|------|------|
| 空 / `*` | `""` | 匹配所有 |
| 精确匹配 | `"Bash"` | 匹配精确工具名 |
| 管道分隔 | `"Write\|Edit"` | 匹配列表中的任意一个 |
| 正则表达式 | `"^git .*"` | 正则匹配工具名 |

### 通用钩子选项

| 选项 | 类型 | 说明 |
|------|------|------|
| `if` | string | 权限规则语法过滤（如 `"Bash(git *)"`) |
| `timeout` | int | 超时秒数 |
| `once` | bool | 为 true 时钩子执行一次后自动移除 |
| `statusMessage` | string | 钩子运行时的自定义加载消息 |

### 命令钩子环境变量

| 变量 | 说明 |
|------|------|
| `CLAUDE_PROJECT_DIR` | 当前工作目录 |
| `CLAUDE_SESSION_ID` | 当前会话 ID |
| `CLAUDE_PLUGIN_ROOT` | 插件安装目录（来自插件时） |
| `CLAUDE_PLUGIN_DATA` | 插件数据目录 |
| `CLAUDE_ENV_FILE` | 写入 bash exports 以应用到后续命令 |

在命令字符串中使用 `$ARGUMENTS` 注入钩子输入 JSON。

### 钩子注册来源

钩子从以下位置加载：

1. `settings.json` → `hooks`
2. 用户级本地 JSON 文件：`<user-config>/hooks.json`、`<user-config>/hooks/hooks.json` 以及其他 `<user-config>/hooks/*.json`
3. 项目级本地 JSON 文件：`<project>/.illusion/hooks.json`、`<project>/.illusion/hooks/hooks.json` 以及其他 `<project>/.illusion/hooks/*.json`
4. 已启用插件的钩子入口，包括默认 `hooks/hooks.json`、清单 `hooks_file` 值、`hooks.json`、显式路径和内联定义

`<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认 `~/.illusion`。钩子发现只读取已有文件，不会创建配置根目录或 `hooks` 目录。

### 钩子结果聚合

同一事件上的多个钩子按优先级聚合：`deny` > `ask` > `allow`。

### 源码参考

- 钩子加载器：`src/illusion/hooks/loader.py`
- 钩子事件：`src/illusion/hooks/events.py`
- 钩子 schema：`src/illusion/hooks/schemas.py`
- 钩子类型：`src/illusion/hooks/types.py`

---

## 插件配置

项目插件的普通开关与项目禁用策略都存放在 `<project>/.illusion/permissions.json`。`enabled_plugins` 保存本项目安装的插件的开关，`denied_plugins` 可按名称或 `"*"` 拒绝任意来源的插件。用户级和内置插件的开关位于用户的 `settings.json`。详见[插件启用/禁用](#插件启用禁用)。

### 源码参考

- 插件加载器：`src/illusion/plugins/loader.py`

---

## MCP 服务器配置

### 全局启用/禁用

在 MCP 服务器配置中添加 `enabled` 字段：

```json
{
  "mcp_servers": {
    "server-name": {
      "command": "python",
      "args": ["server.py"],
      "enabled": false  // 禁用此服务器
    }
  }
}
```

省略 `type` 字段时默认按 `stdio` 处理。

### 项目级禁用

在 `<project>/.illusion/permissions.json` 的 `denied_mcp_servers` 字段中控制：

```json
{
  "denied_mcp_servers": ["server-name"]  // 禁用特定服务器
  "denied_mcp_servers": ["*"]            // 禁用所有服务器
}
```

### 优先级

项目级 `denied_mcp_servers` > 全局 `enabled` 字段 > 默认值

### 源码参考

- MCP 配置加载器：`src/illusion/mcp/config.py`

---

## 记忆系统配置

### 全局启用/禁用

在 `<user-config>/settings.json` 的 `memory.enabled` 字段中控制：

```json
{
  "memory": {
    "enabled": false  // 禁用记忆功能
  }
}
```

### 项目级禁用

在 `<project>/.illusion/permissions.json` 的 `denied_memory` 字段中控制：

```json
{
  "denied_memory": true  // 禁用记忆功能
}
```

### 优先级

项目级 `denied_memory` > 全局 `memory.enabled` > 默认值

### 源码参考

- 记忆管理器：`src/illusion/memory/manager.py`

---

## Skills 配置

### 加载来源

Skills 可以从以下位置加载：
- 内置 skills（`src/illusion/skills/bundled/`）
- 用户 skills（`<user-config>/skills/`，`<user-config>` 由 `ILLUSION_CONFIG_DIR` 决定，默认 `~/.illusion`）
- 项目级 skills（`<project>/.illusion/skills/`）
- 插件 skills（插件的 `skills/` 目录）

### 项目级禁用

在 `<project>/.illusion/permissions.json` 的 `denied_skills` 字段中控制：

```json
{
  "denied_skills": ["skill-name"]  // 禁用特定 skill
  "denied_skills": ["*"]           // 禁用所有 skills
}
```

### 源码参考

- Skills 加载器：`src/illusion/skills/loader.py`

---

## Hooks 配置

### 加载来源

Hooks 可以从以下位置加载：
- 全局 `settings.json` 的 `hooks` 字段
- 用户级 `<user-config>/hooks.json`、`<user-config>/hooks/hooks.json` 和 `<user-config>/hooks/*.json`
- 项目级 `<project>/.illusion/hooks.json`、`<project>/.illusion/hooks/hooks.json` 和 `<project>/.illusion/hooks/*.json`
- 已启用插件的默认入口、清单路径和内联定义

资源发现只读取已有文件，不会创建配置根目录或 hooks 目录。

### 项目级禁用

在 `<project>/.illusion/permissions.json` 的 `denied_hooks` 字段中控制：

```json
{
  "denied_hooks": ["PreToolUse"]  // 禁用特定事件的 hooks
  "denied_hooks": ["*"]           // 禁用所有 hooks
}
```

### 源码参考

- Hooks 加载器：`src/illusion/hooks/loader.py`

---

## Commands 配置

### 命令类型

IllusionAgent 的斜杠命令分为两类：

1. **内置命令**：由运行时命令注册表提供
2. **动态命令**：来自用户 `<user-config>/commands/**/*.md`、项目 `<project>/.illusion/commands/**/*.md`、以及已启用插件的 `commands/` 或清单映射

用户命令先注册，同名时优先于项目命令和插件命令。技能可以通过斜杠输入激活，但技能与命令在注册表中分开保存。缺失目录会被跳过，不会创建。

### 插件命令的启用/禁用

- 项目安装的插件在项目 `permissions.json` 的 `enabled_plugins` 中切换，其他安装在用户 `settings.json` 中切换。
- 项目 `denied_plugins` 对所有安装来源优先生效。
- 插件禁用后，其提供的所有命令都会被禁用

### 源码参考

- 命令注册表：`src/illusion/commands/registry.py`

---

## Rules 配置

### 加载来源

Rules 从以下位置加载：
- `.claude/rules/*.md`
- `.illusion/rules/*.md`

### 项目级禁用

在 `<project>/.illusion/permissions.json` 的 `denied_rules` 字段中控制：

```json
{
  "denied_rules": ["rule-name"]  // 禁用特定 rule
  "denied_rules": ["*"]          // 禁用所有 rules
}
```

### 源码参考

- Rules 发现逻辑：`src/illusion/prompts/claudemd.py`
