# Web UI Security Model

This document describes the security defenses of `illusion web`: what is enforced, what the rules are, and how to configure them.

***

## 1. Overview

Every `/api/*` and `/ws` request passes through two layers:

1. **Web access authentication** (implementation: `src/illusion/ui/web/auth.py`) verifies *who* is accessing: signed cookie / `Authorization: Bearer` / `?token=` — any valid credential lets the request through, otherwise 401.
2. **Browser-trust fence** (implementation: `src/illusion/ui/web/security.py`) verifies *where* the request comes from: Host check + Sec-Fetch-Site check + Origin same-origin check, rejecting on the first failure (403).

Both layers are always on (fail-closed) with no bypass switch; normal local use is fully transparent — open the full URL printed at startup once and you are signed in.

***

## 2. The Three Fences

| Fence                        | Rule                                                                                                         | Defends Against           |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------- |
| **Host check**               | Host header must be a loopback address or an explicitly declared trusted host                                | DNS rebinding             |
| **Sec-Fetch-Site check**     | `Sec-Fetch-Site: cross-site` → reject                                                                        | Modern cross-site fetches |
| **Origin same-origin check** | If Origin is present it must match Host exactly; literal `null` → reject; absent → covered by the Host fence | CSWSH / CSRF              |

Decision flow:

```
request → ① Host is loopback or trusted? ──no──→ 403
            │yes
            ↓
          ② Sec-Fetch-Site == cross-site? ──yes──→ 403
            │no
            ↓
          ③ no Origin header → allow (fence ① already bound the request)
             Origin present → same origin as Host? ──no──→ 403
            │yes
            ↓
           allow
```

Rule details:

* **The Host fence binds every request.** Over plain HTTP, browser image loads and navigations carry neither Origin nor Sec-Fetch headers — indistinguishable from curl — so there is no "missing marker" shortcut anywhere.

* **Ports are irrelevant to the defense.** The fence inspects only the hostname part of the Host header — `127.0.0.1:3000`, `127.0.0.1:8080`, and `127.0.0.1:58896` are all allowed, while `evil.example` is rejected on every port. If the service moves to another port, protection is unchanged; "default 3000" is just a default argument value.

* **WebSocket handshakes are checked before accept**; a rejected handshake gets an HTTP 403 upgrade refusal and never enters session state.

* Rejections are uniform: `403 Forbidden` with plain-text body `forbidden`.

***

## 3. Plane Separation

The two entry points carry different privilege levels:

| Endpoint      | Payload                                                                 | Trusted hosts allowed? |
| ------------- | ----------------------------------------------------------------------- | ---------------------- |
| `REST /api/*` | Privileged plane: environment config & credentials, cron jobs, channels | **No — loopback only** |
| `WS /ws`      | Session plane: conversation, agent driving                              | Yes                    |

In other words, a LAN device declared as a trusted host can use chat, but every configuration / credential / task-management operation stays local-only. The corresponding frontend panels degrade gracefully with errors when accessed from another machine.

Additionally, FastAPI's auto-generated API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled entirely — they sit outside the fenced paths yet expose the full API schema.

Static assets (pages and scripts) are unfenced: they contain nothing sensitive, so any attack chain terminates at the API fence.

***

## 4. Trusted Host Configuration

### 4.1 Declaring Them

```bash
# Explicit declaration (repeatable)
illusion web --trusted-host nas.example
illusion web --trusted-host nas.example:8080   # exact port
illusion web --trusted-host [fd00::5]          # IPv6 literal

# Binding all interfaces auto-derives this machine's LAN addresses
illusion web --host 0.0.0.0
```

When binding to `0.0.0.0` or `::`, startup samples this machine's LAN-reachable unicast addresses (excluding loopback / link-local / multicast) and merges them into the trusted list. Virtual adapter addresses may be included as well; pass `--trusted-host` explicitly when you need finer control.

### 4.2 Canonical Form Requirement

Entries must be bare canonical `host[:port]`. Malformed variants abort startup with a loud error instead of being silently ignored:

| Variant                                    | Rejected Because                                                                                     |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `0x7f.0.0.1` / `127.1` / `127.000.000.001` | Browsers normalize variant IPs before filling the Host header; such entries never match real traffic |
| `example.1` / `0x7f000001`                 | WHATWG parses the whole host as IPv4 when the last label is numeric/hex                              |
| `user@nas.example`                         | userinfo injection                                                                                   |
| `nas.example/path`                         | embedded path                                                                                        |
| `nas.example:081`                          | zero-padded port                                                                                     |
| `" nas.example"`                           | surrounding whitespace                                                                               |

Ordinary hostnames containing digits (`nas1.example.com`, `pi4.lan`) are unaffected.

### 4.3 Matching Rules

* An entry without a port matches the hostname on **any port**;

* An entry with a port requires host and port to match exactly.

***

## 5. Web Access Authentication

The authentication layer gates *who may use this web UI* :

* **Launch token**: generated randomly on every startup (valid for the process lifetime). The CLI prints the full access URL with the token (`http://host:port/?token=...`). Non-browser clients (scripts, tests) can authenticate with an `Authorization: Bearer <token>` header or a `?token=` query parameter.

* **Signed cookie**: on the first visit carrying the token, the server mints an HMAC-SHA256 signed cookie (HttpOnly + SameSite=Strict + authority binding + 30-day lifetime) and 303-redirects to a clean URL without the token — the address bar keeps no token, subsequent requests authenticate via the cookie.

* **Persistent signing secret**: the cookie signing key is persisted in the config directory (`~/.illusion/web_auth_secret.json`, a 32-byte random). Cookies survive backend restarts, so you don't have to reopen the printed URL.

The three credential paths are equivalent and optional: signed cookie (browser), `Authorization: Bearer <token>` (REST/WS), `?token=` query parameter (WS/static probes). Token comparison is constant-time; tampered, expired, or cross-authority cookies are rejected.

***

## 6. Design Boundary

* The fence filters request origins (DNS rebinding / CSRF); the authentication layer verifies access credentials — distinct duties, neither is optional.

* Authentication is access control for local / trusted-host deployments, **not a multi-user identity system**: all credentials share the same launch token with no per-user isolation. For multi-user isolation, put TLS + auth in a reverse proxy in front of the service — do not loosen the fence.

***

## 7. Behavior Cheat Sheet

| Scenario                                                                           | Behavior                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local browser opens the full URL printed by the CLI (with token)                   | First visit exchanges the token for a signed cookie and redirects to a clean URL; normal use                                                                                                                                                                                  |
| Page refresh / backend restart                                                     | Signed cookie remains valid (persistent secret), no need to reopen the URL                                                                                                                                                                                                    |
| Local browser opens a bare address (no token)                                      | REST / WS / index all return 401 (prompt to use the full URL)                                                                                                                                                                                                                 |
| Desktop shell (Electron)                                                           | Parses the token-bearing URL from the backend stdout and loads it; same-origin, transparent                                                                                                                                                                                   |
| Development mode (Vite dev server)                                                 | Open the Vite address with `?token=...` appended (the frontend injects Bearer / WS token automatically), or visit the logged full URL once to obtain the cookie; keep the hostname consistent between exchange and access (localhost and 127.0.0.1 are distinct domains)      |
| CLI scripts / test clients                                                         | Carry `Authorization: Bearer <token>` or its `?token=` query parameter                                                                                                                                                                                                        |
| LAN device via trusted host                                                        | Replace the host part of the printed full URL with this machine's LAN IP (`http://<LAN-IP>:<port>/?token=...`) and send it to the device; opening it once mints a signed cookie for that address. Configuration panels remain unavailable (privileged plane is loopback-only) |
| Signed cookie expired/cleared (30-day lifetime, new machine, cleared browser data) | Reopen the full access URL printed at startup once — no other configuration needed                                                                                                                                                                                            |
| Cross-site requests / DNS rebinding / non-HTTP clients carrying a foreign hostname | 401 without credentials, 403 with credentials                                                                                                                                                                                                                                 |

## 8. Tests

* Full regression coverage of the authentication layer lives in `tests/web/test_web_auth.py` (token validation, signed-cookie minting/tampering/expiry/authority binding, the three credential paths, WS handshake, 401 before 403).

* Regression coverage of the fence lives in `tests/web/test_browser_trust.py`.

