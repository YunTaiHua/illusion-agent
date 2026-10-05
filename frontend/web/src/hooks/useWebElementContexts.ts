/**
 * @fileoverview 网页元素拾取上下文 hook
 *
 * 监听窗口事件维护 composer 的拾取附件列表：picker 完成时加入（同工作区
 * 才接纳，跨工作区丢弃），单条/整组移除，切换工作区或会话作用域时清空。
 *
 * @module useWebElementContexts
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT,
  WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT,
  createWebElementContextId,
  getWebElementContextWorkspaceKey,
  isWebElementContextAddToChatEvent,
  isWebElementContextPayload,
  isWebElementContextRemoveFromChatEvent,
  type WebElementContextComposerAttachment,
  type WebElementContextPayload,
} from "../lib/webElementContext";

interface WebElementContextRemovePayload {
  id: string;
  workspacePath: string;
  workspaceIdentity?: string;
}

interface UseWebElementContextsOptions {
  /** 当前工作区路径（归属过滤） */
  workspacePath: string;
  workspaceIdentity?: string;
  /** 是否监听加入事件（默认 true；多处挂载时只应由 composer 监听） */
  listenAddToChatEvents?: boolean;
  /** 会话/作用域 id：切换时清空（跨会话不串扰） */
  scopeId?: string | null;
}

interface UseWebElementContextsResult {
  contexts: readonly WebElementContextComposerAttachment[];
  hasContexts: boolean;
  removeContext: (id: string) => void;
  clearContexts: () => void;
}

function isRemovePayload(payload: unknown): payload is WebElementContextRemovePayload {
  const candidate = payload as WebElementContextRemovePayload;
  return (
    typeof payload === "object" &&
    payload !== null &&
    typeof candidate.id === "string" &&
    candidate.id.length > 0 &&
    typeof candidate.workspacePath === "string" &&
    candidate.workspacePath.length > 0 &&
    (candidate.workspaceIdentity === undefined ||
      typeof candidate.workspaceIdentity === "string")
  );
}

function toComposerAttachment(
  payload: unknown,
): WebElementContextComposerAttachment | null {
  if (!isWebElementContextPayload(payload)) return null;
  return {
    ...payload,
    id: (payload as WebElementContextPayload).id ?? createWebElementContextId(),
  };
}

/**
 * 维护 composer 的网页元素拾取附件列表
 *
 * @param options - 工作区归属 / 监听开关 / 作用域
 * @returns 附件列表与移除/清空操作
 */
export function useWebElementContexts({
  workspacePath,
  workspaceIdentity,
  listenAddToChatEvents = true,
  scopeId = null,
}: UseWebElementContextsOptions): UseWebElementContextsResult {
  const [contexts, setContexts] = useState<readonly WebElementContextComposerAttachment[]>([]);
  const workspaceKey = getWebElementContextWorkspaceKey(workspacePath, workspaceIdentity);

  // 切换工作区/作用域：清空（上一工作区的元素不带到新上下文）
  useEffect(() => {
    setContexts([]);
  }, [scopeId, workspaceKey]);

  const removeContext = useCallback((id: string) => {
    setContexts((items) => items.filter((item) => item.id !== id));
  }, []);

  const clearContexts = useCallback(() => {
    setContexts([]);
  }, []);

  useEffect(() => {
    if (!listenAddToChatEvents || typeof window === "undefined") return;

    const handleAdd = (event: Event) => {
      if (!isWebElementContextAddToChatEvent(event)) return;
      const attachment = toComposerAttachment(event.detail);
      if (!attachment) return;
      const eventWorkspaceKey = getWebElementContextWorkspaceKey(
        attachment.workspacePath,
        attachment.workspaceIdentity,
      );
      if (eventWorkspaceKey !== workspaceKey) return;
      event.preventDefault();
      setContexts((items) => {
        const existingIndex = items.findIndex((item) => item.id === attachment.id);
        if (existingIndex < 0) return [...items, attachment];
        return items.map((item) => (item.id === attachment.id ? attachment : item));
      });
    };

    const handleRemove = (event: Event) => {
      if (!isWebElementContextRemoveFromChatEvent(event)) return;
      if (!isRemovePayload(event.detail)) return;
      const eventWorkspaceKey = getWebElementContextWorkspaceKey(
        event.detail.workspacePath,
        event.detail.workspaceIdentity,
      );
      if (eventWorkspaceKey !== workspaceKey) return;
      event.preventDefault();
      removeContext(event.detail.id);
    };

    window.addEventListener(WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT, handleAdd);
    window.addEventListener(WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT, handleRemove);
    return () => {
      window.removeEventListener(WEB_ELEMENT_CONTEXT_ADD_TO_CHAT_EVENT, handleAdd);
      window.removeEventListener(WEB_ELEMENT_CONTEXT_REMOVE_FROM_CHAT_EVENT, handleRemove);
    };
  }, [listenAddToChatEvents, removeContext, workspaceKey]);

  return useMemo(
    () => ({
      contexts,
      hasContexts: contexts.length > 0,
      removeContext,
      clearContexts,
    }),
    [clearContexts, contexts, removeContext],
  );
}
