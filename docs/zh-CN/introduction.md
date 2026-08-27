# IllusionAgent

<div align="center">

**幻想与实用，于此交融**

*融合多个开源项目精华，构建统一智能代理*

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
- 🧠 **多智能体协作** - 内置通用（general-purpose）、探索（explore）、验证（verification）等专业子代理，支持任务编排
- 🛠️ **丰富的工具集** - 完整的基础 + 渠道工具集 + MCP 动态工具扩展
- 📦 **上下文压缩** - 微压缩（清除旧工具结果）+ 全压缩（LLM 摘要），上下文占满时自动触发
- 🌐 **Web UI 界面** - 通过 `illusion web` 启动浏览器聊天界面，暖色设计、会话管理、实时流式响应（终端界面的补充方案）
- 🌍 **中英双语支持** - 所有 CLI 输出根据 `ui_language` 设置自动切换中英文
- 📝 **全面 Markdown 渲染** - 直角边框表格、圆角卡片代码块、多色富文本、链接等
- 📂 **项目级配置友好** - 自动生成 skills、rules、mcp、plugins 目录，项目同名 skill 优先覆盖全局
- 🔌 **灵活扩展系统** - 插件、钩子、技能、MCP 服务器
- 🔐 **完善权限控制** - 四种模式（default / plan / full_auto / yolo）+ 细粒度规则 + 会话级 / 单次允许
- 💾 **记忆与上下文** - 项目知识持久化与动态检索
- 🎨 **双界面模式** - React + Ink 终端 TUI + 浏览器 Web UI
- 🎯 **推理强度控制** - 支持 low/medium/high/xhigh/max 五种推理强度级别，自动降级处理
- 🪟 **Windows 系统深度优化** - 自动查找 Git、PowerShell 支持、路径兼容性优化
- 🖥️ **终端渲染零闪烁** - 基于 Ink Static 组件的稳定渲染，抑制 resize 事件干扰

### 界面展示

<div align="center">
  <p>欢迎界面 & 富文本渲染</p>
  <img src="../images/image1.png" alt="IllusionAgent 欢迎界面" width="48%" />
  <img src="../images/image2.png" alt="IllusionAgent 富文本渲染" width="48%" />
</div>

<div align="center">
  <p>演示视频</p>
  <a href="https://b23.tv/3mWe9It">
    <img src="../images/illusion-agent-zh.png" alt="点击观看演示视频" width="720" />
  </a>
  <p><a href="https://b23.tv/3mWe9It">📺 B站观看演示视频</a></p>
</div>

### 设计来源与创新

**继承自 Claude Code**：完整注入 Claude Code 的系统提示词、工具定义、权限模型和多智能体协调架构，确保行为一致性。

**灵感源自 OpenHarness**：Python 架构层面的设计参考了 OpenHarness 的理念。

**Cron 架构对齐 OpenClaw**：定时任务系统采用与 OpenClaw 相同的调度器架构，支持独立会话执行、执行历史记录和连续错误追踪。

**cc-switch 代理路由**：通过 cc-switch 反代工具实现本地代理路由，支持将请求转发到不同的 AI 提供商。

**基础设施移植自 kimi-cli**：异步队列（aioqueue，Queue + shutdown 哨兵，Python < 3.13 polyfill）、stderr fd 级重定向（stderr_redirect，StderrRedirector）、跨平台 SIGINT 处理（signals）等核心基础设施模块移植自 kimi-cli 项目，仅调整文档字符串与日志适配。

**渠道实现参考 hermes-agent**：飞书 WS 长连接与消息渲染策略、微信 iLink API 客户端、QQ Bot WS 网关等渠道模块的连接/重连/渲染模式参考自 hermes-agent 项目。

**Windows 深度优化**：自动查找 Git 安装路径，PowerShell 与 Bash 工具统一处理，路径分隔符自动兼容，Windows 用户开箱即用。

**终端零闪烁**：采用 Ink `<Static>` 组件架构，已完成消息静态渲染，流式消息动态渲染，彻底解决终端闪烁问题。

**中英双语界面**：所有 CLI 输出（auth、mcp、plugin、cron、session 等）均通过 i18n 系统根据 `ui_language` 字段自动切换语言，首次运行时可选择语言偏好。

**全面 Markdown 渲染**：终端内完整渲染直角边框表格、圆角卡片式代码块、多色富文本（加粗、斜体、行内代码、链接等），AI 回复可读性大幅提升。

**项目级配置自动化**：自动生成 `<project>/.illusion/rules/` 和 `<project>/.illusion/skills/` 目录，项目级配置优先于全局配置，便于团队协作。

**Web UI 界面**：基于 React + Vite + Tailwind CSS 前端和 FastAPI + WebSocket 后端的浏览器聊天界面。暖色设计风格，支持会话管理、侧边栏导航、实时流式响应、右侧面板显示上下文使用量，以及完整的国际化支持。通过 `illusion web` 启动。注意：终端界面为推荐的首选模式，功能更完整、性能更优；Web UI 仅作为终端不可用时的补充方案。
