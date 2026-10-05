"""
ARIA 快照与 ref 体系
====================

向页面注入
一段自包含脚本，产出带 ``[ref=eN]`` 标记的 ARIA 树（YAML 风格），并给可交互
元素写入 ``data-illusion-ref`` 属性——后续 browser_click/browser_type 用
``[data-illusion-ref="eN"]`` 定位元素。

该脚本同时服务于两个后端：
    - PlaywrightBackend：page.evaluate(SNAPSHOT_SCRIPT)
    - DesktopBridgeBackend：webContents.executeJavaScript(SNAPSHOT_SCRIPT)

ref 的稳定性约定：同一元素在多次快照间保留已有 ref（跨快照稳定 id）；
元素被移除后其 ref 消失，旧 ref 定位会得到 0 个匹配，工具层据此提示
模型重新快照。
"""

from __future__ import annotations

SNAPSHOT_SCRIPT = r"""
(() => {
  // 防御性外壳：真实站点上脚本任何一处抛错（此前 bing/github 均触发
  // "Script failed to execute"）都不能让快照整体失败——捕获并随载荷返回，
  // 工具层据此向模型报告真实异常。
  try {
    return __illusionSnapshotCore();
  } catch (err) {
    return {
      url: location.href,
      title: document.title || "",
      yaml: "",
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      error: String((err && err.stack) || err),
    };
  }
})();

function __illusionSnapshotCore() {
  const MAX_NODES = 1200;
  const INTERACTIVE = ['button','link','textbox','searchbox','checkbox','radio','combobox',
    'slider','spinbutton','switch','tab','option','menuitem','treeitem','listbox'];
  let nodeCount = 0;
  let truncated = false;

  // === ref 分配（防撞号）===
  // data-illusion-ref 持久化在 DOM 元素上（跨快照稳定 id），但本脚本每次
  // 执行都重新运行——若计数器从 0 起步，页面新出现的元素会拿到 e1，
  // 与仍留在 DOM 中的旧 e1 撞号（快照输出中出现同 ref 多元素，触发
  // 工具层的歧义拒绝）。修复：
  //   1. 预扫描全文档：refCounts 记录每个现存 ref 的出现次数（撞号检测），
  //      maxRefNum 取最大编号；
  //   2. 新 ref 从 maxRefNum+1 起步线性探测，绝不与现存/已分配冲突；
  //   3. 元素仅保留"全文档唯一"的现存 ref，撞号 ref 双方都重新分配。
  const usedRefs = new Set();
  const refCounts = Object.create(null);
  let maxRefNum = 0;
  try {
    for (const el of document.querySelectorAll('[data-illusion-ref]')) {
      const ref = el.getAttribute('data-illusion-ref') || '';
      const m = /^e\d+$/.test(ref);
      if (m) {
        usedRefs.add(ref);
        refCounts[ref] = (refCounts[ref] || 0) + 1;
        const n = parseInt(ref.slice(1), 10);
        if (n > maxRefNum) maxRefNum = n;
      }
    }
  } catch (e) {}
  let refCounter = maxRefNum;

  function allocRef() {
    // 线性探测找一个未占用的编号（防御手工构造的 data-illusion-ref）
    let n = refCounter + 1;
    while (usedRefs.has('e' + n)) n += 1;
    refCounter = n;
    const ref = 'e' + n;
    usedRefs.add(ref);
    refCounts[ref] = 1;
    return ref;
  }

  function isVisible(el, style) {
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return !(rect.width <= 0 && rect.height <= 0);
  }

  function roleOf(el) {
    const explicit = (el.getAttribute('role') || '').trim();
    if (explicit) return explicit === 'none' || explicit === 'presentation' ? null : explicit;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      if (type === 'hidden') return null;
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (type === 'search') return 'searchbox';
      if (type === 'button' || type === 'submit' || type === 'reset' || type === 'file') return 'button';
      if (type === 'image') return 'img';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'option') return 'option';
    if (tag === 'img') return 'img';
    if (/^h([1-6])$/.test(tag)) return 'heading';
    if (tag === 'ul' || tag === 'ol' || tag === 'dl') return 'list';
    if (tag === 'li' || tag === 'dt' || tag === 'dd') return 'listitem';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'aside') return 'complementary';
    if (tag === 'header') return 'banner';
    if (tag === 'footer') return 'contentinfo';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'table') return 'table';
    if (tag === 'tr') return 'row';
    if (tag === 'th' || tag === 'td') return 'cell';
    if (tag === 'hr') return 'separator';
    if (tag === 'progress') return 'progressbar';
    if (tag === 'output') return 'status';
    if (tag === 'fieldset') return 'group';
    if (el.isContentEditable) return 'textbox';
    return null;
  }

  function accessibleName(el, doc) {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim().slice(0, 120);
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = [];
      for (const id of labelledby.split(/\s+/)) {
        const node = doc.getElementById(id);
        if (node) parts.push((node.textContent || '').replace(/\s+/g, ' ').trim());
      }
      const joined = parts.filter(Boolean).join(' ');
      if (joined) return joined.slice(0, 120);
    }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'img') {
      const alt = el.getAttribute('alt');
      if (alt && alt.trim()) return alt.trim().slice(0, 120);
    }
    if (tag === 'input' || tag === 'textarea') {
      const ph = el.getAttribute('placeholder');
      if (ph && ph.trim()) return ph.trim().slice(0, 120);
      if (tag === 'input' && (type === 'button' || type === 'submit' || type === 'reset')) {
        const v = el.getAttribute('value');
        if (v && v.trim()) return v.trim().slice(0, 120);
      }
    }
    if (el.id) {
      try {
        const esc = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
        const lbl = doc.querySelector('label[for="' + esc + '"]');
        if (lbl) {
          const t = (lbl.textContent || '').replace(/\s+/g, ' ').trim();
          if (t) return t.slice(0, 120);
        }
      } catch (e) {}
    }
    const wrap = el.closest('label');
    if (wrap && wrap !== el) {
      const t = (wrap.textContent || '').replace(/\s+/g, ' ').trim();
      if (t) return t.slice(0, 120);
    }
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim().slice(0, 120);
    let text = '';
    if (tag === 'select') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      if (opt) text = opt.textContent || '';
    } else if (tag === 'input' || tag === 'textarea') {
      text = String(el.value || '');
    } else {
      text = el.textContent || '';
    }
    text = text.replace(/\s+/g, ' ').trim();
    return text ? text.slice(0, 120) : '';
  }

  function quote(name) { return JSON.stringify(name); }

  function ensureRef(el) {
    const existing = el.getAttribute('data-illusion-ref');
    // 仅当元素持有"全文档唯一"的现存 ref 时保留（跨快照稳定）；
    // 无 ref 或撞号 ref（refCounts > 1）→ 重新分配
    if (existing && refCounts[existing] === 1) {
      return existing;
    }
    const ref = allocRef();
    try { el.setAttribute('data-illusion-ref', ref); } catch (e) {}
    return ref;
  }

  function elementLine(el, role, name, depth) {
    const attrs = [];
    const tag = el.tagName.toLowerCase();
    if (role === 'heading') {
      const level = /^h([1-6])$/.test(tag) ? tag[1] : (el.getAttribute('aria-level') || '1');
      attrs.push('[level=' + level + ']');
    }
    if (['checkbox','radio','switch','option','menuitemcheckbox','menuitemradio'].includes(role)) {
      const checked = el.checked !== undefined ? !!el.checked : el.getAttribute('aria-checked') === 'true';
      if (checked) attrs.push('[checked]');
    }
    const expanded = el.getAttribute('aria-expanded');
    if (expanded === 'true') attrs.push('[expanded]');
    if (expanded === 'false') attrs.push('[collapsed]');
    if (role === 'option' && el.selected) attrs.push('[selected]');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') attrs.push('[disabled]');
    if (el.getAttribute('aria-invalid') === 'true') attrs.push('[invalid]');
    attrs.push('[ref=' + ensureRef(el) + ']');
    const namePart = name ? ' ' + quote(name) : '';
    return '  '.repeat(depth) + '- ' + role + namePart + ' ' + attrs.join(' ');
  }

  function emitText(text, depth, out) {
    const t = text.replace(/\s+/g, ' ').trim();
    if (t) out.push('  '.repeat(depth) + '- text: ' + quote(t.slice(0, 200)));
  }

  function walk(el, depth, out, doc) {
    if (nodeCount >= MAX_NODES) { truncated = true; return; }
    if (el.nodeType !== 1) return;
    const tag = el.tagName.toLowerCase();
    if (['script','style','noscript','template','head','meta','link','title','base'].includes(tag)) return;
    if (el.getAttribute('aria-hidden') === 'true') return;
    if (tag === 'svg' || tag === 'canvas') {
      // 画布/矢量图产出占位行，让模型感知其存在（快照看不到其内部内容）
      nodeCount += 1;
      const name = el.getAttribute('aria-label') || el.getAttribute('title') || '';
      out.push('  '.repeat(depth) + '- ' + (tag === 'svg' ? 'image' : 'canvas') +
        (name ? ' ' + quote(name) : '') + ' [ref=' + ensureRef(el) + ']');
      return;
    }
    let style;
    try { style = getComputedStyle(el); } catch (e) { return; }
    if (!isVisible(el, style)) return;

    if (tag === 'iframe') {
      nodeCount += 1;
      let child = null;
      try { child = el.contentDocument; } catch (e) { child = null; }
      if (child && child.body) {
        out.push('  '.repeat(depth) + '- iframe:');
        for (const c of child.body.children) walk(c, depth + 1, out, child);
      } else {
        out.push('  '.repeat(depth) + '- iframe [cross-origin]');
      }
      return;
    }

    const role = roleOf(el);
    if (role) {
      nodeCount += 1;
      out.push(elementLine(el, role, accessibleName(el, doc), depth));
      // 原生 select 额外展开 option 列表（可逐项点选）
      if (role === 'combobox' && tag === 'select') {
        for (const opt of el.options || []) {
          if (nodeCount >= MAX_NODES) { truncated = true; break; }
          nodeCount += 1;
          const oname = (opt.textContent || '').replace(/\s+/g, ' ').trim();
          out.push('  '.repeat(depth + 1) + '- option ' + quote(oname) +
            (opt.selected ? ' [selected]' : '') + ' [ref=' + ensureRef(opt) + ']');
        }
      }
      // 可交互叶子元素不再下钻（其内容已由可访问名概括）
      if (INTERACTIVE.includes(role) && role !== 'listbox' && role !== 'option') return;
      for (const c of el.children) walk(c, depth + 1, out, doc);
      return;
    }
    // 通用容器：不产出节点行，直接下钻；仅当其携带直接文本时输出 text 行
    let directText = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) directText += n.textContent || '';
    }
    if (el.children.length === 0) {
      nodeCount += 1;
      emitText(directText, depth, out);
      return;
    }
    if (directText.trim()) emitText(directText, depth, out);
    for (const c of el.children) walk(c, depth + 1, out, doc);
  }

  const out = [];
  for (const c of (document.body ? document.body.children : [])) walk(c, 0, out, document);
  let yaml = out.join('\n');
  if (truncated) yaml += '\n- text: "[snapshot truncated: page too large - narrow scope or scroll]"';
  return {
    url: location.href,
    title: document.title || '',
    yaml,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
  };
}
"""


# 元素拾取脚本（网页元素选择器；独立于快照，按坐标拾取并分配 ref）。
# 模板：{args} 由调用方内联 JSON（evaluate 的字符串表达式不透传 arg）。
PICK_SCRIPT_TEMPLATE = r"""
(({x, y}) => {
  const el = document.elementFromPoint(x, y);
  if (!el || el.nodeType !== 1) return null;
  const U = '__illusion_pick_9d2c__';
  // 分配不冲突的 ref（复用快照的防撞号策略：跳过现存编号）
  let maxNum = 0;
  const existing = new Set();
  for (const n of document.querySelectorAll('[data-illusion-ref]')) {
    const r = n.getAttribute('data-illusion-ref') || '';
    if (/^e\d+$/.test(r)) { existing.add(r); const i = parseInt(r.slice(1), 10); if (i > maxNum) maxNum = i; }
  }
  let ref = el.getAttribute('data-illusion-ref');
  if (!ref || !/^e\d+$/.test(ref)) {
    let n = maxNum + 1;
    while (existing.has('e' + n)) n += 1;
    ref = 'e' + n;
    try { el.setAttribute('data-illusion-ref', ref); } catch (e) {}
  }
  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute('role') || (tag === 'input'
    ? ((el.getAttribute('type') || 'text').toLowerCase())
    : '');
  const name = (el.getAttribute('aria-label')
    || (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement ? (el.placeholder || '') : '')
    || (el.textContent || '').replace(/\s+/g, ' ').trim()).slice(0, 60);
  // 选择器：id 优先，否则 3 层以内的结构路径
  let selector = el.id ? '#' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) : '';
  if (!selector) {
    const parts = [];
    let cur = el;
    for (let depth = 0; cur && cur.nodeType === 1 && depth < 3; depth += 1) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { part += '#' + cur.id; parts.unshift(part); break; }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((c) => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = cur.parentElement;
    }
    selector = parts.join(' > ');
  }
  const isEditable = el.isContentEditable || tag === 'textarea' || tag === 'select'
    || (tag === 'input' && (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden');
  return {
    tag,
    role: role || undefined,
    name: name || undefined,
    ref,
    selector,
    editable: isEditable,
    url: location.href,
  };
})__PICK_XY_ARGS__
"""


# 取消页面内进行中的拾取（与 PICK_ELEMENT 注入脚本配套；页面端无脚本时静默）
PICK_CANCEL_PAGE_SCRIPT = """
(() => {
  const picker = window.__illusionWebElementPicker;
  if (picker && typeof picker.cancel === "function") picker.cancel();
})()
"""
