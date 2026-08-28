<div align="center">

# IllusionAgent

[![PyPI](https://img.shields.io/badge/pypi-illusion--agent-blue)](https://pypi.org/project/illusion-agent/) ![Python](https://img.shields.io/badge/python-%3E%3D3.10-green) ![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey) [![GitHub](https://img.shields.io/badge/github-YunTaiHua%2Fillusion--agent-black)](https://github.com/YunTaiHua/illusion-agent)

*幻想与实用，于此交融。融合多个开源项目精华，构建统一智能代理。*

中文 | [English](README.md)

</div>

---

## 📖 项目简介

IllusionAgent 是一款开源的 AI 智能体平台。它将多模型语言模型网关、
中英双语命令行、浏览器端 Web 界面与可扩展的插件生态融为一体，
在 Windows、macOS、Linux 之上皆能从容运行。

无论你习惯终端的克制，还是偏爱浏览器的舒展，IllusionAgent 都能与你的工作流共振：
丰富的内置工具集、专业子代理、2 种压缩方法、MCP 服务器支持、
钩子、插件，以及面向无人值守场景的 Cron 调度器，贯通飞书、微信、QQ 三大渠道。

> 站在巨人之肩 —— Claude Code 提示词体系、OpenHarness 架构理念、
> OpenClaw 调度设计、kimi-cli 基础设施、hermes-agent 渠道模式、cc-switch 路由方案。

### 核心特性

- 🤖 **多 AI 提供商支持** - Anthropic Claude、OpenAI、GitHub Copilot、OpenAI Codex 及任意 OpenAI 兼容端点
- 🧠 **多智能体协作** - 内置通用、探索、验证等专业子代理，支持任务编排
- 🛠️ **丰富的工具集** - 完整的基础 + 渠道工具集 + MCP 动态工具扩展
- 📦 **上下文压缩** - 微压缩（清除旧工具结果）+ 全压缩（LLM 摘要），上下文占满时自动触发
- 🌐 **Web UI 界面** - 通过 `illusion web` 启动浏览器聊天界面，与终端界面相互独立、同等可用
- 🌍 **中英双语支持** - 所有 CLI 输出根据 `ui_language` 设置自动切换中英文
- 📝 **全面 Markdown 渲染** - 直角边框表格、圆角卡片代码块、多色富文本
- 🔌 **灵活扩展系统** - 插件、钩子、技能、MCP 服务器
- 🔐 **完善权限控制** - 三种模式 + 细粒度规则 + Always Allow 一键放行
- 🎯 **推理强度控制** - 支持 low/medium/high/xhigh/max 五种推理强度级别
- 🪟 **Windows 系统深度优化** - 自动查找 Git、PowerShell 支持
- 🖥️ **终端渲染零闪烁** - 基于 Ink Static 组件的稳定渲染
- 📦 **桌面版** - Electron 壳内置 Python/Node.js 运行时，三端便携版分发，解压即用零环境配置

### 界面展示

<div align="center">
  <p>欢迎界面 & 富文本渲染</p>
  <img src="docs/images/image1.png" alt="IllusionAgent 欢迎界面" width="48%" />
  <img src="docs/images/image2.png" alt="IllusionAgent 富文本渲染" width="48%" />
</div>

<div align="center">
  <p>演示视频</p>
  <a href="https://b23.tv/3mWe9It">
    <img src="docs/images/illusion-agent-zh.png" alt="点击观看演示视频" width="720" />
  </a>
  <p><a href="https://b23.tv/3mWe9It">📺 B站观看演示视频</a></p>
</div>

---

## 🚀 快速开始

### 环境要求

- Python >= 3.10
- 支持 Windows、macOS、Linux
- Node.js 18+（仅源码安装需要，`pip install illusion-agent` 无需 Node.js）

### 安装

```bash
# 推荐方式：从 PyPI 安装（无需 Node.js）
pip install illusion-agent

# 备选方式：从源码安装（需要 Node.js 18+）
git clone https://github.com/YunTaiHua/illusion-agent.git
cd illusion-agent
pip install .
```

### 桌面版

不想安装 Python 或 Node.js？直接下载对应平台的安装包，内置运行时，零环境配置：

| 平台 | 下载文件 |
|------|----------|
| Windows | `IllusionAgent-Setup-<版本>.exe`（NSIS 安装器） |
| macOS | `IllusionAgent-<版本>-arm64.dmg` |
| Linux | `IllusionAgent-<版本>-amd64.deb` 或 `IllusionAgent-<版本>-x86_64.AppImage` |

👉 [从 GitHub Release 下载](https://github.com/YunTaiHua/illusion-agent/releases/latest)

桌面版内置 Python 3.12 和 Node.js 24 运行时，详见[桌面版文档](docs/zh-CN/desktop.md)。

### 基本使用

```bash
# 首次使用：配置认证（登录后会引导设置工作目录）
illusion auth login

# 启动交互式会话（推荐）
illusion

# 启动 Web UI 浏览器界面
illusion web

# 非交互式打印模式
illusion -p "帮我分析这个项目的结构"

# 设置或更新工作目录
illusion set "E:\Projects\my-project"
```

### Print 模式说明

`-p` / `--print` 以非交互方式执行单次请求并立即退出：

```bash
# 只读分析（安全，默认权限模式）
illusion -p "帮我分析这个项目的结构"

# 允许写入文件 / 执行命令，无需交互式审批
illusion --permission-mode full_auto -p "修复失败的测试"

# 进程以退出码 2 结束后，继续回答待处理的问题 / 权限 / 计划
illusion -c -p "Y"

# 指定模型和 effort 等级用于 print 模式
illusion -m env_1.model_2 -e high -p "重构此模块"
```

重要细节：

- 提示词值必须放在 **最后一个参数**，因为 typer 会贪婪解析 `-p`。
- 默认权限模式下，变更类工具会以退出码 **2** 退出并保留待审批项；使用 `illusion -c -p "Y"`、`"F"` 或 `"N"` 继续回答。
- 退出码：`0` 成功，`1` 错误，`2` 等待跨轮次输入。

### 界面说明

终端（`illusion`）与 Web UI（`illusion web`）是两个相互独立、同等重要的界面。它们共享同一个后端运行时、设置和会话存储，按你的工作流选择即可。

---

## 📚 详细文档

| 主题 | English | 中文 |
|------|---------|------|
| 项目简介 | [docs/en/introduction.md](docs/en/introduction.md) | [docs/zh-CN/introduction.md](docs/zh-CN/introduction.md) |
| 快速开始 | [docs/en/getting-started.md](docs/en/getting-started.md) | [docs/zh-CN/getting-started.md](docs/zh-CN/getting-started.md) |
| 命令系统 | [docs/en/commands.md](docs/en/commands.md) | [docs/zh-CN/commands.md](docs/zh-CN/commands.md) |
| Goal 自动续跑目标 | [docs/en/goal.md](docs/en/goal.md) | [docs/zh-CN/goal.md](docs/zh-CN/goal.md) |
| 设置与凭据 | [docs/en/settings.md](docs/en/settings.md) | [docs/zh-CN/settings.md](docs/zh-CN/settings.md) |
| 项目文件与记忆 | [docs/en/project-files.md](docs/en/project-files.md) | [docs/zh-CN/project-files.md](docs/zh-CN/project-files.md) |
| 扩展系统 (MCP, 插件, 技能, 钩子) | [docs/en/extensions.md](docs/en/extensions.md) | [docs/zh-CN/extensions.md](docs/zh-CN/extensions.md) |
| 项目架构 | [docs/en/architecture.md](docs/en/architecture.md) | [docs/zh-CN/architecture.md](docs/zh-CN/architecture.md) |
| Web UI 安全 | [docs/en/security.md](docs/en/security.md) | [docs/zh-CN/security.md](docs/zh-CN/security.md) |
| Token 计量与压缩 | [docs/en/token-metering.md](docs/en/token-metering.md) | [docs/zh-CN/token-metering.md](docs/zh-CN/token-metering.md) |
| 消息渠道 | [docs/en/channels.md](docs/en/channels.md) | [docs/zh-CN/channels.md](docs/zh-CN/channels.md) |
| 桌面版 | [docs/en/desktop.md](docs/en/desktop.md) | [docs/zh-CN/desktop.md](docs/zh-CN/desktop.md) |

---

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证开源。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

</div>
