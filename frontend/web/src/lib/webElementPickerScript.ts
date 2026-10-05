/**
 * 网页元素拾取注入脚本
 * ====================
 *
 * 运行时行为：
 * - 调用一次即启动选择态，返回 Promise，用户点击/Esc 时 resolve 并自清理；
 * - hover 高亮（蓝色 overlay + 周边压暗）+ 信息 popover（tag/尺寸/颜色/字体）；
 * - 采集富上下文：tag/role/accessibleName/selector/xpath/text/nearbyText/
 *   htmlExcerpt/attributes/rect/style；
 * - 给选中元素写入 data-illusion-ref（复用快照的防撞
 * 号策略），agent 拿到拾取结果后可直接 browser_click/browser_type 操作。
 *
 * 两端共用：
 * - Web/托管模式：PlaywrightBackend 通过 page.evaluate 执行（自动 await
 *   脚本返回的 Promise，用户操作完成后 resolve）；
 * - 桌面模式：BrowserHostLayer 通过 webview.executeJavaScript 执行。
 *
 * 注意：脚本内的花括号不经模板替换，参数以字符串常量内联，无占位符。
 */

export const PICK_ELEMENT_SCRIPT = `(() => {
  const stateKey = "__illusionWebElementPicker";
  const existing = window[stateKey];
  if (existing && typeof existing.cancel === "function") existing.cancel();

  const OPTIONS = { maxTextChars: 4000, maxHtmlChars: 6000, maxAttributeChars: 500 };

  const truncate = (value, maxLength) => {
    const normalized = (value ?? "").replace(/\\s+/g, " ").trim();
    return normalized.length > maxLength ? normalized.slice(0, maxLength) + "..." : normalized;
  };
  const cssEscape = (value) => {
    const esc = window.CSS && window.CSS.escape ? window.CSS.escape : undefined;
    return esc ? esc(value) : value.replace(/[^a-zA-Z0-9_-]/g, "\\\\$&");
  };

  // ref 分配：与快照同一防撞号策略（现存收集 + max+1 线性探测）
  let refMax = 0;
  const usedRefs = new Set();
  for (const node of document.querySelectorAll("[data-illusion-ref]")) {
    const r = node.getAttribute("data-illusion-ref") || "";
    if (/^e\\d+$/.test(r)) { usedRefs.add(r); const n = parseInt(r.slice(1), 10); if (n > refMax) refMax = n; }
  }
  const allocRef = (el) => {
    let ref = el.getAttribute("data-illusion-ref");
    if (ref && usedRefs.has(ref)) return ref;
    let n = refMax + 1;
    while (usedRefs.has("e" + n)) n += 1;
    ref = "e" + n;
    try { el.setAttribute("data-illusion-ref", ref); } catch (e) {}
    return ref;
  };

  const getImplicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "img") return "img";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "button" || type === "submit" || type === "reset") return "button";
      return "textbox";
    }
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "nav") return "navigation";
    if (tag === "main") return "main";
    if (tag === "form") return "form";
    if (/^h[1-6]$/.test(tag)) return "heading";
    return "";
  };

  const getAccessibleName = (el) => {
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const label = labelledBy.split(/\\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? node.textContent || "" : "";
      }).join(" ");
      const normalized = truncate(label, OPTIONS.maxTextChars);
      if (normalized) return normalized;
    }
    return truncate(
      el.getAttribute("aria-label") || el.getAttribute("alt") || el.getAttribute("title")
        || el.getAttribute("placeholder") || (el.innerText || el.textContent),
      OPTIONS.maxTextChars,
    );
  };

  const getSelector = (el) => {
    if (el.id) return "#" + cssEscape(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 8) {
      const tag = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift(tag + "#" + cssEscape(cur.id)); break; }
      const classNames = Array.from(cur.classList).filter(Boolean).slice(0, 2)
        .map((c) => "." + cssEscape(c)).join("");
      let part = tag + classNames;
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((s) => s.tagName === cur.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(cur) + 1) + ")";
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(" > ");
  };

  const getXPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 12) {
      const tag = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (!parent) { parts.unshift("/" + tag); break; }
      const same = Array.from(parent.children).filter((s) => s.tagName === cur.tagName);
      parts.unshift(tag + "[" + (same.indexOf(cur) + 1) + "]");
      cur = parent;
    }
    return "/" + parts.join("/");
  };

  const readElementText = (el) => {
    if (el instanceof HTMLInputElement) {
      if ((el.type || "").toLowerCase() === "password") return "[masked password input]";
      return truncate(el.getAttribute("aria-label") || el.getAttribute("placeholder")
        || el.name || el.type, OPTIONS.maxTextChars);
    }
    if (el instanceof HTMLTextAreaElement) {
      return truncate(el.getAttribute("aria-label") || el.getAttribute("placeholder")
        || el.name || "textarea", OPTIONS.maxTextChars);
    }
    return truncate(el.innerText || el.textContent, OPTIONS.maxTextChars);
  };

  const getNearbyText = (el) => {
    const container = el.closest("article, section, main, form, li, tr, dialog") || el.parentElement || el;
    return truncate(container.innerText || container.textContent, OPTIONS.maxTextChars);
  };

  const getHtmlExcerpt = (el) => {
    const clone = el.cloneNode(true);
    if (!(clone instanceof Element)) return "";
    clone.querySelectorAll("script, style, noscript, template").forEach((n) => n.remove());
    clone.querySelectorAll("input, textarea").forEach((n) => {
      if (n instanceof HTMLInputElement) {
        n.removeAttribute("value");
        if ((n.type || "").toLowerCase() === "password") n.setAttribute("type", "password");
      }
      if (n instanceof HTMLTextAreaElement) n.textContent = "";
    });
    return truncate(clone.outerHTML, OPTIONS.maxHtmlChars);
  };

  const getAttributes = (el) => {
    const attrs = {};
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const allowed = ["id","class","href","src","alt","title","name","type","placeholder"]
        .includes(name) || name.startsWith("aria-");
      if (!allowed || name === "value") continue;
      attrs[name] = truncate(attr.value, OPTIONS.maxAttributeChars);
    }
    return attrs;
  };

  const overlay = document.createElement("div");
  overlay.setAttribute("data-illusion-web-element-picker", "overlay");
  Object.assign(overlay.style, {
    background: "rgba(37, 99, 235, 0.12)",
    border: "2px solid #2563eb",
    borderRadius: "4px",
    boxShadow: "0 0 0 9999px rgba(15, 23, 42, 0.10)",
    boxSizing: "border-box", display: "none", left: "0", pointerEvents: "none",
    position: "fixed", top: "0", zIndex: "2147483647",
  });

  const label = document.createElement("div");
  Object.assign(label.style, {
    backdropFilter: "blur(10px)",
    background: "rgba(17, 24, 39, 0.92)",
    border: "1px solid rgba(255, 255, 255, 0.14)",
    borderRadius: "18px",
    boxShadow: "0 18px 38px rgba(15, 23, 42, 0.28)",
    boxSizing: "border-box", color: "#f9fafb", display: "none",
    font: "12px/1.4 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    left: "0", maxWidth: "calc(100vw - 16px)", minWidth: "214px",
    padding: "12px 18px 14px", pointerEvents: "none", position: "fixed",
    top: "0", width: "min(320px, calc(100vw - 16px))", zIndex: "2147483647",
  });

  document.documentElement.append(overlay, label);

  let hovered = null;
  let settled = false;
  let finishPicker = null;

  const cleanup = () => {
    document.removeEventListener("mousemove", handleMouseMove, true);
    document.removeEventListener("click", handleClick, true);
    document.removeEventListener("keydown", handleKeyDown, true);
    overlay.remove();
    label.remove();
    delete window[stateKey];
    document.documentElement.style.cursor = "";
  };

  const appendPopoverRow = (name, value) => {
    if (!value) return;
    const row = document.createElement("div");
    Object.assign(row.style, {
      alignItems: "baseline", columnGap: "16px", display: "grid",
      gridTemplateColumns: "auto minmax(0, 1fr)", minWidth: "0",
    });
    const nameNode = document.createElement("span");
    nameNode.textContent = name;
    Object.assign(nameNode.style, {
      color: "rgba(255, 255, 255, 0.62)", fontSize: "15px",
      fontWeight: "600", minWidth: "0", whiteSpace: "nowrap",
    });
    const valueNode = document.createElement("span");
    valueNode.textContent = value;
    Object.assign(valueNode.style, {
      color: "#ffffff",
      fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
      fontSize: "15px", fontWeight: "700", minWidth: "0",
      overflow: "hidden", textAlign: "right", textOverflow: "ellipsis", whiteSpace: "nowrap",
    });
    row.append(nameNode, valueNode);
    label.append(row);
  };

  const formatSize = (rect) => Math.round(rect.width) + "x" + Math.round(rect.height);

  const renderPopover = (target, rect) => {
    const style = window.getComputedStyle(target);
    label.replaceChildren();
    const header = document.createElement("div");
    Object.assign(header.style, {
      alignItems: "baseline", columnGap: "16px", display: "grid",
      gridTemplateColumns: "minmax(0, 1fr) auto", minWidth: "0",
    });
    const tagNode = document.createElement("span");
    tagNode.textContent = target.tagName.toLowerCase();
    Object.assign(tagNode.style, {
      color: "#ffffff", fontSize: "16px", fontWeight: "800",
      minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    });
    const sizeNode = document.createElement("span");
    sizeNode.textContent = formatSize(rect);
    Object.assign(sizeNode.style, {
      color: "#ffffff",
      fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
      fontSize: "15px", fontWeight: "800", whiteSpace: "nowrap",
    });
    header.append(tagNode, sizeNode);
    label.append(header);
    appendPopoverRow("Color", style.color);
    if (style.backgroundColor && style.backgroundColor !== "transparent"
      && style.backgroundColor !== "rgba(0, 0, 0, 0)") {
      appendPopoverRow("Background", style.backgroundColor);
    }
    appendPopoverRow("Font", truncate([style.fontSize, style.fontFamily].filter(Boolean).join(" "), 96));
  };

  const updateOverlay = (target) => {
    if (!target || target === overlay || target === label || label.contains(target)) {
      overlay.style.display = "none";
      label.style.display = "none";
      return;
    }
    const rect = target.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      overlay.style.display = "none";
      label.style.display = "none";
      return;
    }
    overlay.style.display = "block";
    overlay.style.left = Math.max(0, rect.left) + "px";
    overlay.style.top = Math.max(0, rect.top) + "px";
    overlay.style.width = rect.width + "px";
    overlay.style.height = rect.height + "px";
    label.style.display = "block";
    renderPopover(target, rect);
    const labelWidth = label.offsetWidth || 240;
    const labelHeight = label.offsetHeight || 90;
    const padding = 8, gap = 12;
    let left = rect.left + rect.width / 2 - labelWidth / 2;
    let top = rect.bottom + gap;
    if (top + labelHeight > window.innerHeight - padding) top = rect.top - labelHeight - gap;
    left = Math.max(padding, Math.min(left, window.innerWidth - labelWidth - padding));
    top = Math.max(padding, Math.min(top, window.innerHeight - padding));
    label.style.left = left + "px";
    label.style.top = top + "px";
  };

  function handleMouseMove(event) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    hovered = target;
    updateOverlay(target);
  }

  function collectElement(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
      pageUrl: location.href,
      pageTitle: document.title,
      tagName: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || getImplicitRole(el) || undefined,
      accessibleName: getAccessibleName(el) || undefined,
      selector: getSelector(el),
      xpath: getXPath(el),
      text: readElementText(el) || undefined,
      nearbyText: getNearbyText(el) || undefined,
      htmlExcerpt: getHtmlExcerpt(el) || undefined,
      attributes: getAttributes(el),
      ref: allocRef(el),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      style: {
        color: style.color,
        backgroundColor: style.backgroundColor,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        display: style.display,
      },
      capturedAt: Date.now(),
    };
  }

  function handleClick(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    if (!hovered) { finishPicker({ status: "cancelled" }); return; }
    finishPicker({ status: "selected", element: collectElement(hovered) });
  }

  function handleKeyDown(event) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    finishPicker({ status: "cancelled" });
  }

  return new Promise((resolve) => {
    const finish = (result) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(result);
    };
    finishPicker = finish;
    window[stateKey] = { cancel: () => finish({ status: "cancelled" }) };
    document.documentElement.style.cursor = "crosshair";
    document.addEventListener("mousemove", handleMouseMove, true);
    document.addEventListener("click", handleClick, true);
    document.addEventListener("keydown", handleKeyDown, true);
  });
})()`;

export const PICK_CANCEL_SCRIPT = `(() => {
  const picker = window.__illusionWebElementPicker;
  if (picker && typeof picker.cancel === "function") picker.cancel();
})()`;
