/**
 * 图片 artifact 落盘 —— nodeRepl.emitImage 的存储侧。
 *
 * 接受三种输入（与官方插件 emitImage 语义一致）：
 *   - Uint8Array / ArrayBuffer：原始字节（PNG/JPEG 由调用方保证）
 *   - string：base64 裸串或 data URL（data:image/png;base64,...）
 *
 * 全部图片写盘到 <ILLUSION_CONFIG_DIR>/browser/artifacts/<yyyymmdd>/ 下，
 * 返回 { base64, mimeType, path }：base64 经 MCP 结果回传给模型（image 内容块），
 * path 供会话 artifact 引用（browserScreenshotPaths）。
 */

import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

export interface SavedImage {
  base64: string;
  mimeType: string;
  path: string;
}

const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

export async function saveImageArtifact(image: unknown, configDir: string): Promise<SavedImage> {
  const { bytes, mimeType } = normalizeImage(image);
  if (bytes.length === 0) {
    throw new TypeError("nodeRepl.emitImage requires non-empty image bytes");
  }
  if (bytes.length > MAX_IMAGE_BYTES) {
    throw new TypeError("nodeRepl.emitImage image exceeds 20MB limit");
  }
  const base64 = bytes.toString("base64");
  const extension = mimeType.includes("jpeg") ? "jpg" : mimeType.includes("webp") ? "webp" : "png";
  const artifactsDir = join(configDir || ".", "browser", "artifacts", stamp());
  await mkdir(artifactsDir, { recursive: true });
  const digest = createHash("sha256").update(bytes).digest("hex").slice(0, 12);
  const path = join(artifactsDir, `browser-${Date.now()}-${digest}.${extension}`);
  await writeFile(path, bytes);
  return { base64, mimeType, path };
}

function normalizeImage(image: unknown): { bytes: Buffer; mimeType: string } {
  if (image instanceof Uint8Array) {
    return { bytes: Buffer.from(image), mimeType: "image/png" };
  }
  if (image instanceof ArrayBuffer) {
    return { bytes: Buffer.from(new Uint8Array(image)), mimeType: "image/png" };
  }
  if (typeof image === "string") {
    return normalizeDataUrl(image);
  }
  if (image && typeof image === "object") {
    // 允许 { data: Uint8Array | base64, mimeType?: string } 形式
    const record = image as { data?: unknown; base64?: unknown; mimeType?: unknown };
    const mimeType = typeof record.mimeType === "string" ? record.mimeType : "image/png";
    if (record.data instanceof Uint8Array) {
      return { bytes: Buffer.from(record.data), mimeType };
    }
    if (typeof record.base64 === "string") {
      return { bytes: Buffer.from(record.base64, "base64"), mimeType };
    }
  }
  throw new TypeError(
    "nodeRepl.emitImage accepts Uint8Array, ArrayBuffer, base64 string, or data URL",
  );
}

function normalizeDataUrl(value: string): { bytes: Buffer; mimeType: string } {
  const match = /^data:(image\/[a-z+]+);base64,(.+)$/is.exec(value.trim());
  if (match) {
    return { bytes: Buffer.from(match[2] as string, "base64"), mimeType: match[1] as string };
  }
  // 去除可能的空白（base64 允许换行）
  return { bytes: Buffer.from(value.replace(/\s+/g, ""), "base64"), mimeType: "image/png" };
}

function stamp(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}${month}${day}`;
}
