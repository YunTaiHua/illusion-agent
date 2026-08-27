# Introduction

<div align="center">

**Where fantasy meets functionality.**

*The best of many worlds, refined into one intelligent agent*

</div>

---

IllusionAgent is an open-source AI agent platform. It unifies a multi-provider
LLM gateway, a bilingual (Chinese/English) CLI, a browser-based Web UI, and
a flexible extension ecosystem into a single intelligent agent — at home
on Windows, macOS, and Linux.

Whether you prefer the discipline of the terminal or the ease of the browser,
IllusionAgent resonates with your workflow: a rich built-in toolset, specialized
sub-agents, two compaction methods, MCP server support, hooks, plugins, and
a cron scheduler for unattended automation — spanning Feishu, WeChat, and QQ.

> Standing on the shoulders of giants — Claude Code prompts, OpenHarness
> architecture, OpenClaw scheduling, kimi-cli infrastructure, hermes-agent
> channels, cc-switch routing.

## Core Features

- 🤖 **Multi AI Provider Support** - Anthropic Claude, OpenAI, GitHub Copilot, OpenAI Codex, and any OpenAI-compatible endpoint
- 🧠 **Multi-Agent Collaboration** - Built-in specialized agents (general-purpose, explore, verification), supporting task orchestration
- 🛠️ **Rich Toolset** - Full base + channel toolset + MCP dynamic tool extension
- 📦 **Context Compaction** - Microcompact (clear old tool results) + full compaction (LLM summary), auto-triggered as context fills
- 🌐 **Web UI Interface** - Browser-based chat interface with `illusion web`, featuring warm color design, session management, and real-time streaming (supplementary to the recommended terminal interface)
- 🌍 **Bilingual Interface** - All CLI output automatically switches between Chinese and English based on `ui_language` setting
- 📝 **Comprehensive Markdown Rendering** - Box-drawing tables, rounded card-style code blocks, multi-color rich text, links and more
- 📂 **Project-Level Config Friendly** - Auto-generate skills, rules, mcp, plugins directories, project-level skills override global ones
- 🔌 **Flexible Extension System** - Plugins, hooks, skills, MCP servers
- 🔐 **Comprehensive Permission Control** - Four modes (default / plan / full_auto / yolo) + fine-grained rules + session-level / one-time approval
- 💾 **Memory & Context** - Project knowledge persistence and dynamic retrieval
- 🎨 **Dual Interface** - Modern React + Ink terminal TUI + browser-based Web UI
- 🎯 **Reasoning Effort Control** - Supports low/medium/high/xhigh/max five reasoning effort levels with automatic fallback
- 🪟 **Deep Windows Optimization** - Auto-detect Git, PowerShell support, path compatibility optimization
- 🖥️ **Zero Terminal Flicker** - Stable rendering based on Ink Static component, suppressing resize event interference

## Interface Preview

<div align="center">
  <p>Welcome screen & rich text rendering</p>
  <img src="../images/image1.png" alt="IllusionAgent welcome screen" width="48%" />
  <img src="../images/image2.png" alt="IllusionAgent rich text rendering" width="48%" />
</div>

<div align="center">
  <p>Demo video</p>
  <a href="https://www.youtube.com/watch?v=ExrzKVjWPls">
    <img src="../images/illusion-agent-en.png" alt="Click to watch demo video" width="720" />
  </a>
  <p><a href="https://www.youtube.com/watch?v=ExrzKVjWPls">📺 Watch demo on YouTube</a></p>
</div>

## Design Origins & Innovations

**Inherited from Claude Code**: Complete injection of Claude Code's system prompts, tool definitions, permission model, and multi-agent coordination architecture, ensuring behavioral consistency.

**Inspired by OpenHarness**: Python architecture design references OpenHarness's ideas.

**Cron Architecture Aligned with OpenClaw**: The scheduled task system uses the same scheduler architecture as OpenClaw, supporting independent session execution, execution history tracking, and consecutive error monitoring.

**cc-switch Proxy Routing**: Local proxy routing through the cc-switch reverse proxy tool, supporting request forwarding to different AI providers.

**Infrastructure Ported from kimi-cli**: Core infrastructure modules including async queue (aioqueue, Queue + shutdown sentinel, Python < 3.13 polyfill), stderr fd-level redirect (stderr_redirect, StderrRedirector), and cross-platform SIGINT handler (signals) are ported from the kimi-cli project, with only docstring and logging adaptations.

**Channel Implementation Inspired by hermes-agent**: The connection/reconnection/rendering patterns of channel modules — Feishu WS long connection and message rendering strategy, WeChat iLink API client, and QQ Bot WS gateway — are referenced from the hermes-agent project.

**Deep Windows Optimization**: Auto-detect Git installation path, unified PowerShell and Bash tool processing, automatic path separator compatibility, out-of-the-box experience for Windows users.

**Zero Terminal Flicker**: Uses Ink `<Static>` component architecture, static rendering for completed messages, dynamic rendering for streaming messages, completely solving terminal flicker issues.

**Bilingual Interface**: All CLI output (auth, mcp, plugin, cron, session, etc.) automatically switches language via the i18n system based on the `ui_language` field. Language preference can be selected on first run.

**Comprehensive Markdown Rendering**: Full rendering of box-drawing tables, rounded card-style code blocks, multi-color rich text (bold, italic, inline code, links), significantly improving AI response readability.

**Project-Level Config Automation**: Auto-generate `<project>/.illusion/rules/` and `<project>/.illusion/skills/` directories, project-level configuration takes precedence over global configuration, facilitating team collaboration.

**Web UI Interface**: Browser-based chat interface powered by React + Vite + Tailwind CSS frontend and FastAPI + WebSocket backend. Features warm color design, session management, sidebar navigation, real-time streaming responses, right panel with context usage display, and full i18n support. Launch with `illusion web`. Note: The terminal interface is recommended as the primary mode for full feature support and better performance; the Web UI is intended as a supplementary option for scenarios where a terminal is unavailable.
