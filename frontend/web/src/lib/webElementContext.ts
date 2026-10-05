/**
 * @fileoverview 网页元素拾取上下文
 *
 * 浏览器元素拾取的统一数据链路：picker 脚本产出的富 payload 经窗口事件
 * 流入 composer，以 pill 附件形态驻留输入框上方（悬停展开详情、可单条/
 * 整组移除），提交时序列化为 `# Web page elements:` Markdown 块拼进最终 prompt。
 *
 * 事件为自定义窗口事件（传输无关）：桌面 webview 与 Web 截图流两条拾取
 * 路径派发同一事件，composer 单点消费。
 *
 * @module webElementContext
 */

/** 拾取结果加入聊天的事件名（picker 完成时派发） */
export const WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT = "illusion:web-element-context-add-to-chat";
/** 从聊天移除拾取结果的事件名 */
export const WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT =
  "illusion:web-element-context-remove-from-chat";

/** Markdown 上下文块标题（与一致，后端解析锚点） */
const WEB_ELEMENT_CONTEXT_BLOCK_TITLE = "# Web page elements:";
const MAX_MARKDOWN_FIELD_LENGTH = 8_000;

/** 元素矩形（视口坐标） */
export interface WebElementRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 元素样式摘要 */
export interface WebElementStyleSummary {
  color?: string;
  backgroundColor?: string;
  fontFamily?: string;
  fontSize?: string;
  fontWeight?: string;
  display?: string;
}

/** 拾取到的网页元素上下文（picker payload + 工作区归属） */
export interface WebElementContextPayload {
  id?: string;
  workspacePath: string;
  workspaceIdentity?: string;
  pageUrl: string;
  pageTitle: string;
  tagName: string;
  role?: string;
  accessibleName?: string;
  selector?: string;
  xpath?: string;
  text?: string;
  nearbyText?: string;
  htmlExcerpt?: string;
  attributes?: Record<string, string>;
  /** 页面内 data-illusion-ref 引用，agent 可直接操作 */
  ref?: string;
  rect?: WebElementRect;
  style?: WebElementStyleSummary;
  capturedAt: number;
}

/** composer 附件形态（payload + 稳定 id） */
export interface WebElementContextComposerAttachment extends WebElementContextPayload {
  id: string;
}

/** 解析后的 prompt（可见正文 + 元素上下文附件） */
export interface ParsedWebElementContextPrompt {
  visibleContent: string;
  webElementContexts: WebElementContextComposerAttachment[];
}

/** 生成附件 id */
export function createWebElementContextId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `web-element-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** 工作区归属键（与一致：identity 优先，回退路径） */
export function getWebElementContextWorkspaceKey(
  workspacePath: string,
  workspaceIdentity?: string,
): string {
  return workspaceIdentity?.trim() || workspacePath;
}

/** 事件守卫：加入聊天 */
export function isWebElementContextAddToChatEvent(
  event: Event,
): event is CustomEvent<WebElementContextPayload> {
  return (
    event.type === WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT &&
    "detail" in event &&
    typeof (event as CustomEvent<unknown>).detail === "object" &&
    (event as CustomEvent<unknown>).detail !== null
  );
}

/** 事件守卫：从聊天移除 */
export function isWebElementContextRemoveFromChatEvent(
  event: Event,
): event is CustomEvent<{ id: string; workspacePath: string; workspaceIdentity?: string }> {
  return (
    event.type === WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT &&
    "detail" in event &&
    typeof (event as CustomEvent<unknown>).detail === "object" &&
    (event as CustomEvent<unknown>).detail !== null
  );
}

/** payload 合法性校验（） */
export function isWebElementContextPayload(payload: unknown): payload is WebElementContextPayload {
  const candidate = payload as WebElementContextPayload;
  return (
    typeof payload === "object" &&
    payload !== null &&
    typeof candidate.workspacePath === "string" &&
    candidate.workspacePath.length > 0 &&
    (candidate.workspaceIdentity === undefined ||
      typeof candidate.workspaceIdentity === "string") &&
    typeof candidate.pageUrl === "string" &&
    candidate.pageUrl.length > 0 &&
    typeof candidate.pageTitle === "string" &&
    typeof candidate.tagName === "string" &&
    candidate.tagName.length > 0 &&
    typeof candidate.capturedAt === "number"
  );
}

function truncateMarkdownValue(value: string | undefined): string {
  if (!value) return "";
  const normalized = value.trim();
  return normalized.length > MAX_MARKDOWN_FIELD_LENGTH
    ? `${normalized.slice(0, MAX_MARKDOWN_FIELD_LENGTH)}\n\n[truncated]`
    : normalized;
}

function appendOptionalLine(lines: string[], label: string, value: string | undefined): void {
  const normalized = truncateMarkdownValue(value);
  if (normalized) lines.push(`${label}: ${normalized}`);
}

function formatAttributes(attributes: Record<string, string> | undefined): string {
  if (!attributes || Object.keys(attributes).length === 0) return "";
  return Object.entries(attributes)
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(" ");
}

function formatFont(style: WebElementStyleSummary | undefined): string {
  if (!style?.fontSize && !style?.fontFamily) return "";
  return [style.fontSize, style.fontFamily].filter(Boolean).join(" ");
}

/** 单个元素的 Markdown 段（与buildWebElementContextMarkdown 一致） */
function buildWebElementContextMarkdown(payload: WebElementContextPayload): string {
  const lines = [
    "## Element",
    `URL: ${payload.pageUrl}`,
    `Title: ${payload.pageTitle || "(untitled)"}`,
    `Tag: ${payload.tagName.toLowerCase()}`,
  ];

  appendOptionalLine(lines, "Role", payload.role);
  appendOptionalLine(lines, "Accessible name", payload.accessibleName);
  appendOptionalLine(lines, "Selector", payload.selector);
  appendOptionalLine(lines, "XPath", payload.xpath);
  appendOptionalLine(lines, "Ref", payload.ref);
  appendOptionalLine(lines, "Attributes", formatAttributes(payload.attributes));
  appendOptionalLine(lines, "Color", payload.style?.color);
  appendOptionalLine(lines, "Background", payload.style?.backgroundColor);
  appendOptionalLine(lines, "Font", formatFont(payload.style));
  appendOptionalLine(lines, "Font weight", payload.style?.fontWeight);
  appendOptionalLine(lines, "Display", payload.style?.display);

  if (payload.rect) {
    lines.push(
      `Rect: x=${Math.round(payload.rect.x)}, y=${Math.round(payload.rect.y)}, width=${Math.round(payload.rect.width)}, height=${Math.round(payload.rect.height)}`,
    );
  }

  const text = truncateMarkdownValue(payload.text);
  if (text) lines.push("", "Text:", "```", text, "```");

  const nearbyText = truncateMarkdownValue(payload.nearbyText);
  if (nearbyText) lines.push("", "Nearby context:", "```", nearbyText, "```");

  const htmlExcerpt = truncateMarkdownValue(payload.htmlExcerpt);
  if (htmlExcerpt) lines.push("", "HTML excerpt:", "```html", htmlExcerpt, "```");

  return lines.join("\n");
}

/**
 * 把元素上下文序列化为 prompt 尾块
 *
 * @param text - 用户输入的正文
 * @param contexts - 驻留的拾取附件
 * @returns 拼装 `# Web page elements:` 块后的完整 prompt
 */
export function buildPromptWithWebElementContexts(
  text: string,
  contexts: readonly WebElementContextComposerAttachment[],
): string {
  const content = text.trimEnd();
  if (contexts.length === 0) return content.trim();

  const contextBlock = `${WEB_ELEMENT_CONTEXT_BLOCK_TITLE}\n\n${contexts
    .map((context, index) =>
      buildWebElementContextMarkdown(context).replace("## Element", `## Element ${index + 1}`),
    )
    .join("\n\n")}`;

  return `${content}${content ? "\n\n" : ""}${contextBlock}`.trim();
}

/** 从整块文本中读 `Label: value` 行 */
function readField(rawItem: string, label: string): string {
  const match = new RegExp(`^${label}:\\s*(.*)$`, "m").exec(rawItem);
  return match?.[1]?.trim() ?? "";
}

/** 读取围栏代码段（Text / Nearby context / HTML excerpt） */
function readFencedSection(rawItem: string, label: string): string {
  const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escapedLabel}:\\s*\\n\`\`\`(?:html)?\\n([\\s\\S]*?)\\n\`\`\``, "m").exec(
    rawItem,
  );
  return match?.[1]?.trim() ?? "";
}

function parseFontSummary(font: string): Pick<WebElementStyleSummary, "fontFamily" | "fontSize"> {
  const match = /^([0-9.]+(?:px|rem|em|pt|%))\s+(.+)$/iu.exec(font);
  if (!match) return { fontFamily: font };
  const fontSize = match[1];
  const fontFamily = match[2];
  if (!fontSize || !fontFamily) return { fontFamily: font };
  return { fontSize, fontFamily };
}

function readStyleSummary(rawItem: string): WebElementStyleSummary | undefined {
  const color = readField(rawItem, "Color");
  const backgroundColor = readField(rawItem, "Background");
  const font = readField(rawItem, "Font");
  const fontWeight = readField(rawItem, "Font weight");
  const display = readField(rawItem, "Display");
  const style: WebElementStyleSummary = {
    ...(color ? { color } : {}),
    ...(backgroundColor ? { backgroundColor } : {}),
    ...(font ? parseFontSummary(font) : {}),
    ...(fontWeight ? { fontWeight } : {}),
    ...(display ? { display } : {}),
  };
  return Object.keys(style).length > 0 ? style : undefined;
}

function parseElementItem(
  rawItem: string,
  index: number,
  workspacePath: string,
  workspaceIdentity?: string,
): WebElementContextComposerAttachment | null {
  const pageUrl = readField(rawItem, "URL");
  const pageTitle = readField(rawItem, "Title");
  const tagName = readField(rawItem, "Tag");
  if (!pageUrl || !tagName) return null;
  return {
    id: `parsed-web-element-${index + 1}-${tagName}`,
    workspacePath,
    ...(workspaceIdentity ? { workspaceIdentity } : {}),
    pageUrl,
    pageTitle: pageTitle === "(untitled)" ? "" : pageTitle,
    tagName,
    role: readField(rawItem, "Role") || undefined,
    accessibleName: readField(rawItem, "Accessible name") || undefined,
    selector: readField(rawItem, "Selector") || undefined,
    xpath: readField(rawItem, "XPath") || undefined,
    ref: readField(rawItem, "Ref") || undefined,
    text: readFencedSection(rawItem, "Text") || undefined,
    nearbyText: readFencedSection(rawItem, "Nearby context") || undefined,
    htmlExcerpt: readFencedSection(rawItem, "HTML excerpt") || undefined,
    style: readStyleSummary(rawItem),
    capturedAt: 0,
  };
}

/**
 * 从 prompt 文本解析元素上下文块（会话恢复/草稿回填用）
 *
 * @param content - 含上下文块的完整文本
 * @param options - 工作区归属
 * @returns 可见正文 + 解析出的附件列表
 */
export function parsePromptWebElementContexts(
  content: string,
  options: { workspacePath: string; workspaceIdentity?: string },
): ParsedWebElementContextPrompt {
  const blockMatch = /(?:^|\n\n)# Web page elements:\s*\n\n([\s\S]*?)\s*$/.exec(content);
  if (!blockMatch || blockMatch.index < 0) {
    return { visibleContent: content, webElementContexts: [] };
  }
  const rawBlock = blockMatch[1];
  if (rawBlock === undefined) {
    return { visibleContent: content, webElementContexts: [] };
  }
  const rawItems = rawBlock
    .split(/\n(?=## Element(?:\s+\d+)?\n)/)
    .map((item) => item.trim())
    .filter(Boolean);
  const webElementContexts = rawItems
    .map((item, index) =>
      parseElementItem(item, index, options.workspacePath, options.workspaceIdentity),
    )
    .filter((item): item is WebElementContextComposerAttachment => item !== null);
  if (webElementContexts.length === 0) {
    return { visibleContent: content, webElementContexts: [] };
  }
  return {
    visibleContent: content.slice(0, blockMatch.index).trimEnd(),
    webElementContexts,
  };
}

/** 派发「拾取结果加入聊天」事件（picker 完成时调用） */
export function dispatchWebElementContextAddToChat(payload: WebElementContextPayload): void {
  window.dispatchEvent(
    new CustomEvent(WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT, { detail: payload }),
  );
}

/** 派发「从聊天移除」事件 */
export function dispatchWebElementContextRemoveFromChat(payload: {
  id: string;
  workspacePath: string;
  workspaceIdentity?: string;
}): void {
  window.dispatchEvent(
    new CustomEvent(WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT, { detail: payload }),
  );
}
