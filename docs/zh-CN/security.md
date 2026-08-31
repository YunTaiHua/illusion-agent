# Web UI 安全模型

本文档说明 `illusion web` 的安全防御体系：做了哪些防护、规则是什么、如何配置。

***

## 1. 概述

所有 `/api/*` 与 `/ws` 请求都强制经过**web 访问认证**（实现见 `src/illusion/ui/web/auth.py`）与**浏览器信任栅栏**（实现见 `src/illusion/ui/web/security.py`）两层防护：

1. 认证层验证「是谁在访问」：签名 cookie / `Authorization: Bearer` / `?token=` 三路凭据任意有效才放行，否则 401；
2. 信任栅栏验证「请求从哪里来」：Host 校验 + Sec-Fetch-Site 校验 + Origin 同源校验，任一失败即拒绝（403）。

两层始终启用（fail-closed），无绕过开关；正常本机使用完全透明（首次用启动时打印的完整 URL 打开一次即可）。

***

## 2. 三道栅栏

| 栅栏                    | 规则                                                       | 防御目标          |
| --------------------- | -------------------------------------------------------- | ------------- |
| **Host 校验**           | Host 头必须是回环地址或显式声明的受信主机                                  | DNS rebinding |
| **Sec-Fetch-Site 校验** | `Sec-Fetch-Site: cross-site` 直接拒绝                        | 现代浏览器跨站 fetch |
| **Origin 同源校验**       | 携带 Origin 时必须与 Host 严格同源；`null` origin 拒绝；缺失时由 Host 栅栏兜底 | CSWSH / CSRF  |

判定流程：

```
请求 → ① Host 是回环或受信主机？ ──否──→ 403
         │是
         ↓
       ② Sec-Fetch-Site == cross-site？ ──是──→ 403
         │否
         ↓
       ③ 无 Origin → 放行（①已兜底）
         有 Origin → Origin 与 Host 同源？ ──否──→ 403
         │是
         ↓
        放行
```

规则要点：

* **Host 栅栏绑定一切请求。** 明文 HTTP 下浏览器的 `<img>` 加载与页面导航既不带 Origin 也不带 Sec-Fetch 头，与 curl 无法区分，因此不存在「缺标记就走捷径」的分支。

* **端口与防护无关。** 栅栏判定只取 Host 头的主机名部分——`127.0.0.1:3000`、`127.0.0.1:8080`、`127.0.0.1:58896` 同样放行，`evil.example` 打任何端口同样被拒。服务因端口占用换到其他端口后，防护不变；「默认 3000」只是默认参数值。

* **WebSocket 在握手前校验**，拒绝时以 HTTP 403 拒绝升级，连接不会进入会话状态。

* 拒绝响应统一为 `403 Forbidden` 纯文本 `forbidden`。

***

## 3. 入口分级

两类入口的安全等级不同：

| 入口            | 承载内容                        | 受信主机可访问？     |
| ------------- | --------------------------- | ------------ |
| `REST /api/*` | 特权平面：环境配置与凭据读写、cron 任务与渠道管理 | **否，仅限本机回环** |
| `WS /ws`      | 会话平面：对话交互、Agent 驱动          | 是            |

即：局域网设备通过受信主机声明可以使用聊天功能，但所有配置/凭据/任务管理操作仅限本机执行。前端对应面板在非本机访问时会优雅降级报错。

另外，FastAPI 自动生成的接口文档（`/docs`、`/redoc`、`/openapi.json`）已禁用——它们不受栅栏保护且包含完整接口结构。

静态资源（页面与脚本）不设防：无敏感内容，攻击链断在 API 栅栏。

***

## 4. 受信主机配置

### 4.1 声明方式

```bash
# 显式声明（可多次传入）
illusion web --trusted-host nas.example
illusion web --trusted-host nas.example:8080   # 精确到端口
illusion web --trusted-host [fd00::5]          # IPv6 字面量

# 绑定所有接口时自动派生本机 LAN 地址
illusion web --host 0.0.0.0
```

绑定 `0.0.0.0` 或 `::` 时，启动过程自动采样本机可被局域网直达的单播地址（排除回环/链路本地/多播）并入受信列表；虚拟网卡地址可能一并纳入，需要更细粒度时用 `--trusted-host` 显式指定。

### 4.2 规范形要求

条目必须是裸 `host[:port]` 规范形，以下变形在启动时报错退出（而非静默忽略）：

| 变形                                         | 拒绝原因                                  |
| ------------------------------------------ | ------------------------------------- |
| `0x7f.0.0.1` / `127.1` / `127.000.000.001` | 浏览器会把变形 IP 规范化后填入 Host，此类条目永远匹配不上真实流量 |
| `example.1` / `0x7f000001`                 | 末段为数字/十六进制时 WHATWG 将整个主机解析为 IPv4      |
| `user@nas.example`                         | userinfo 注入                           |
| `nas.example/path`                         | 附带路径                                  |
| `nas.example:081`                          | 零填充端口                                 |
| `" nas.example"`                           | 首尾空白                                  |

含数字的正常主机名（如 `nas1.example.com`、`pi4.lan`）不受影响。

### 4.3 匹配规则

* 无端口的条目匹配该主机的**任意端口**；

* 带端口的条目要求 host 与 port 都精确一致。

***

## 5. Web 访问认证

认证层为「谁能用这个 Web 界面」把关：

* **launch token**：每次启动随机生成（进程生命周期有效），CLI 打印带 token 的完整访问 URL（`http://host:port/?token=...`）。非浏览器客户端（脚本、测试）可用 `Authorization: Bearer <token>` 头或 `?token=` 查询参数直接访问。

* **签名 cookie**：浏览器首次携带 token 访问首页时，服务端校验通过后签发 HMAC-SHA256 签名 cookie（HttpOnly + SameSite=Strict + authority 绑定 + 30 天有效期）并 303 跳转到不带 token 的干净 URL——地址栏不残留 token，之后的请求凭 cookie 自动通过。

* **持久化签名 secret**：cookie 签名密钥持久化于配置目录（`~/.illusion/web_auth_secret.json`，32 字节随机），后端重启后已签发的 cookie 依旧有效，无需重新打开打印的 URL。

校验路径三选一等效：签名 cookie（浏览器）、`Authorization: Bearer <token>`（REST/WS）、`?token=` 查询参数（WS/静态探测）。token 比较为常量时间比较；cookie 篡改、过期、跨 authority 挪用一律拒绝。

***

## 6. 设计边界

* 栅栏过滤请求来源（防 DNS rebinding / CSRF），认证层验证访问凭据——两层职责不同，都不可省略。

* 认证面向「本机 / 受信主机部署」的访问控制，**不是多用户身份体系**：所有凭据共享同一个 launch token，不做用户级隔离。多用户场景应在反向代理层（TLS + 认证）实现，而不是放宽栅栏。

***

## 7. 行为速查

| 场景                                       | 行为                                                                                                                            |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 本机浏览器打开 CLI 打印的完整 URL（含 token）           | 首次访问自动换取签名 cookie 并跳转干净地址，正常使用                                                                                                |
| 浏览器刷新 / 后端重启后                            | 已签发 cookie 仍有效（签名 secret 持久化），无需重新打开 URL                                                                                      |
| 本机浏览器直接打开裸地址（无 token）                    | REST / WS / 首页一律 401（提示使用完整访问 URL）                                                                                            |
| 桌面壳（Electron）                            | 从后端 stdout 解析带 token 的 URL 并加载，同源加载透明                                                                                         |
| 开发模式（Vite dev server）                    | 直接在 Vite 地址后附加 `?token=...` 打开（前端自动注入 Bearer / WS token），或先访问日志中完整 URL 换取 cookie；注意兑换与访问需保持同一主机名（localhost 与 127.0.0.1 视为不同域） |
| CLI 脚本 / 测试客户端                           | 携带 `Authorization: Bearer <token>` 或其 `?token=` 查询参数                                                                          |
| 局域网设备经受信主机访问                             | 把打印的完整 URL 中的地址段替换为本机局域网 IP（`http://<LAN-IP>:<port>/?token=...`）发给设备，打开一次即换取该地址的签名 cookie；配置面板仍不可用（特权平面限本机） |
| 签名 cookie 失效（30 天过期 / 换机器 / 清除浏览器数据）      | 重新用启动时打印的完整访问 URL 打开一次即可，无需其他配置                                                                                 |
| 跨站请求 / DNS rebinding / 非 HTTP 客户端携带外部主机名 | 无凭据 401，有凭据 403                                                                                                               |

## 8. 测试

* 认证层完整回归覆盖见 `tests/web/test_web_auth.py`（token 校验、签名 cookie 签发/篡改/过期/authority 绑定、三路凭据、WS 握手、401 先于 403）。

* 信任栅栏行为回归覆盖见 `tests/web/test_browser_trust.py`。

