# 项目指令文件与记忆文件

## AI 指令文件 (CLAUDE.md / ILLUSION.md / AGENTS.md)

`CLAUDE.md`、`ILLUSION.md` 和 `AGENTS.md` 是**等效的** AI 指令文件。IllusionAgent 识别这三个名称，行为完全一致。

### 发现位置

系统在以下位置扫描这些文件（仅在当前工作目录内，**不在** `~/.illusion/` 全局目录中）：

1. **项目根目录**：`{cwd}/CLAUDE.md`、`{cwd}/AGENTS.md`、`{cwd}/ILLUSION.md`
2. **`.claude/` 目录**：`{cwd}/.claude/CLAUDE.md`
3. **`.illusion/` 目录**：`{cwd}/.illusion/CLAUDE.md`、`{cwd}/.illusion/AGENTS.md`、`{cwd}/.illusion/ILLUSION.md`

所有发现的文件合并为系统提示词中的 `# Project Instructions` 部分。每个文件限制 12,000 字符（超出部分截断）。

### 规则文件

除上述指令文件外，系统还扫描：

- `{cwd}/.claude/rules/*.md` — 按文件名排序，每个文件是独立的规则

### 使用方法

在项目根目录创建任一文件，提供项目特定的上下文和指令：

```markdown
# 项目说明

这是一个 Python Web 项目，使用 FastAPI 框架。

## 代码规范

- 使用 Python 3.10+ 特性
- 遵循 PEP 8 代码风格
- 使用 type hints

## 目录结构

- src/api: API 路由
- src/models: 数据模型
- src/services: 业务逻辑

## 注意事项

- 不要修改 tests/ 目录下的文件
- 提交前运行 pytest
```

### 源码参考

文件发现逻辑：`src/illusion/prompts/claudemd.py` — `discover_claude_md_files()` 函数。

---

## 记忆文件 (MEMORY.md)

记忆系统通过 `MEMORY.md` 和关联的记忆文件提供项目知识持久化功能。

### 存储位置

记忆采用**单层 user 级存储**：

1. **默认**：`~/.illusion/memory/{项目名}-{sha1哈希前12位}/`
2. **自定义**：`settings.json` → `memory.directory` 设置后使用该目录（绝对路径或 `~/` 开头）

### 目录结构（按类型分目录）

记忆文件按 `type` 字段存储于 MEMORY.md 同级的类型子目录中，避免根目录杂乱：

```
~/.illusion/memory/{项目}-{hash}/
├── MEMORY.md                  ← 入口索引
├── user/                      ← user 类型记忆
│   └── user_role.md
├── feedback/                  ← feedback 类型记忆
│   └── feedback_testing.md
├── project/                   ← project 类型记忆
│   └── project_plan.md
└── reference/                 ← reference 类型记忆
    └── reference_linear.md
```

根目录下的旧布局文件（迁移前）仍会被扫描兼容；MEMORY.md 索引使用相对路径（含类型子目录前缀，如 `- [Title](user/user_role.md) — hook`）。

### MEMORY.md 入口文件

`MEMORY.md` 是入口索引文件，每条记录是一行指针：

```markdown
- [标题](user/user_role.md) — 一行描述
- [另一个主题](project/roadmap.md) — 另一行描述
```

**限制：**
- 最大 200 行 / 25000 字节（由 `memory.max_entrypoint_lines` / `memory.max_entrypoint_bytes` 控制），超出部分截断并附警告
- 最多 5 个相关记忆文件注入上下文（由 `memory.max_files` 控制）

### 记忆文件格式

每个记忆文件使用 frontmatter 格式，存放在与 `type` 对应的子目录：

```markdown
---
name: short-kebab-case-slug
description: 一行摘要，用于相关性匹配
type: user|feedback|project|reference
---

记忆条目的内容。feedback/project 类型建议结构化为：
- 规则/事实
- **Why:** 原因
- **How to apply:** 何时/何处适用
```

### 记忆类型

| 类型 | 用途 | 子目录 |
|------|------|--------|
| `user` | 用户角色、目标、偏好、知识水平 | `user/` |
| `feedback` | 工作方式指导（纠正和确认） | `feedback/` |
| `project` | 进行中的工作、目标、计划、Bug、事件 | `project/` |
| `reference` | 外部系统指针（Linear、Slack、Grafana 等） | `reference/` |

### 记忆强化（后台自动提取 + Auto Dream）

系统自动维护记忆质量，无需手动干预：

- **后台提取**：每 `memory.extract_interval` 轮对话结束后，后台运行受限子代理分析新消息，主动保存值得记住的内容（用户偏好、纠正、项目上下文）。子代理只能读取和写入记忆目录（含类型子目录）。可通过 `memory.extract_model` 指定提取使用的模型（env_N.model_M 格式，不指定继承当前）。
- **Auto Dream 整合**：距上次整合超过 `memory.dream_min_hours`（默认 24h）且会话数达到 `memory.dream_min_sessions`（默认 5）时，后台运行整合子代理：合并重复条目、更新过时内容、解决冲突、修剪无价值条目。可通过 `memory.dream_model` 指定整合使用的模型（env_N.model_M 格式，不指定继承当前）。

### 手动模式（默认，关闭后台 LLM 调用）

`memory.auto_extract` **默认关闭**（false）：记忆功能默认启用，但后台 LLM 总结调用（后台提取 + Auto Dream）默认不运行。设置为 `true` 可开启自动提取/整合。此时记忆完全手动记录：用户明确要求记住某事时，主对话 LLM 直接使用 Write/Edit 工具写入记忆文件（按类型放入对应子目录并更新 MEMORY.md 索引），零额外 LLM 消耗。

### 记忆管理

记忆条目可通过以下方式管理：
- 交互式会话中的 `/memory` 斜杠命令
- `remember` 技能（审查并提议重组）
- 直接编辑记忆目录中的文件

### 启用/禁用与自定义目录

- `settings.json` → `memory.enabled: false` 可完全禁用记忆功能（不注入提示词、不搜索、不后台提取）
- 项目级 `permissions.json` → `denied_memory: true` 可在单个项目禁用记忆
- `settings.json` → `memory.directory: "~/my-memory"` 可自定义记忆目录（web 端设置弹窗中亦可配置）
- `memory.extract_model` / `memory.dream_model`：分别为提取/整合子代理指定模型（`env_N.model_N` 格式，不指定继承当前模型）

### 初始化

`/init` 命令在记忆目录创建初始 `MEMORY.md` 模板。

### 源码参考

- 路径解析：`src/illusion/memory/paths.py`
- 提示词构建：`src/illusion/memory/memdir.py`
- 管理功能：`src/illusion/memory/manager.py`
- 后台提取：`src/illusion/memory/extract.py`
- 记忆整合：`src/illusion/memory/auto_dream.py`

---

## 项目级权限配置 (permissions.json)

项目级 `permissions.json` 文件位于 `<project>/.illusion/permissions.json`，用于控制该项目的权限和禁用开关。

### 配置示例

```json
{
  "denied_tools": ["bash"],
  "denied_skills": ["dangerous-skill"],
  "denied_hooks": ["PreToolUse"],
  "denied_plugins": ["unwanted-plugin"],
  "denied_mcp_servers": ["external-server"],
  "denied_memory": false
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `denied_tools` | list | `[]` | 始终拒绝的工具列表 |
| `denied_skills` | list | `[]` | 禁用的 skill 名称列表，`["*"]` 表示全部禁用 |
| `denied_hooks` | list | `[]` | 禁用的 hook 事件列表，`["*"]` 表示全部禁用 |
| `enabled_plugins` | object | `{}` | 项目安装的插件开关，按清单名称映射到 `true` 或 `false`，未设置时采用插件默认值 |
| `denied_plugins` | list | `[]` | 本项目禁止加载的插件名称，适用于所有来源，优先于启用开关；`["*"]` 表示全部禁用 |
| `denied_mcp_servers` | list | `[]` | 禁用的 MCP 服务器名称列表，`["*"]` 表示全部禁用 |
| `denied_memory` | bool | `false` | 是否禁用 memory 功能 |
| `denied_rules` | list | `[]` | 禁用的规则名称列表，`["*"]` 表示全部禁用 |

### 优先级规则

1. 项目级 `permissions.json` — 最高优先级
2. 全局 `settings.json` — 次优先级
3. 默认值 — 最低优先级

### 源码参考

- 权限加载器：`src/illusion/permissions/loader.py`
- 权限数据类：`src/illusion/permissions/schemas.py`
