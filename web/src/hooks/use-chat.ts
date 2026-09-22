"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CanvasOperation } from "@/features/canvas/types";
import type { SDLScene } from "@/lib/scene-kit";
import { resolveApiBase } from "@/lib/api-base";
import { getAuthHeaders } from "@/lib/firebase";
import {
  decodeConversationStoryboardCommandV1,
  type ConversationStoryboardCommandV1,
} from "@/lib/live-scene/conversation-storyboard-command";
import { LiveSceneSseDecoder } from "@/lib/live-scene/sse";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

type SSEEvent =
  | { type: "session"; session_id: string }
  | { type: "canvas_update"; operations: CanvasOperation[] }
  | {
      type: "animation_event";
      tool: string;
      sdl?: SDLScene;
      [key: string]: unknown;
    }
  | { type: "storyboard_command"; command: unknown }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

export interface UseChatOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  agentId?: string;
  sessionId?: string | null;
  onSessionReady?: (sessionId: string) => void;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onSDLScene?: (sdl: SDLScene) => void;
  onStoryboardCommand?: (command: ConversationStoryboardCommandV1) => void;
}

export function useChat(options: UseChatOptions = {}) {
  const {
    apiUrl: requestedApiUrl,
    canvasMode = false,
    agentId,
    sessionId: externalSessionId,
    onSessionReady,
    onCanvasUpdate,
    onSDLScene,
    onStoryboardCommand,
  } = options;
  const apiUrl = resolveApiBase(requestedApiUrl);

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const sessionIdRef = useRef<string | null>(externalSessionId ?? null);
  const requestGenerationRef = useRef(0);
  const activeRequestRef = useRef<AbortController | null>(null);

  // Sync external sessionId into the ref without mutating it during render.
  useEffect(() => {
    if (externalSessionId !== undefined && externalSessionId !== null) {
      sessionIdRef.current = externalSessionId;
    }
  }, [externalSessionId]);

  useEffect(() => {
    return () => {
      requestGenerationRef.current += 1;
      activeRequestRef.current?.abort();
      activeRequestRef.current = null;
    };
  }, []);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;

    activeRequestRef.current?.abort();
    const controller = new AbortController();
    activeRequestRef.current = controller;
    const generation = ++requestGenerationRef.current;
    const isCurrent = () =>
      requestGenerationRef.current === generation &&
      !controller.signal.aborted;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    let assistantId: string | null = null;
    let fullContent = "";
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;

    try {
      const response = await fetch(`${apiUrl}/chat`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(await getAuthHeaders()),
        },
        body: JSON.stringify({
          message: text,
          session_id: sessionIdRef.current,
          canvas_mode: canvasMode,
          agent_id: agentId,
        }),
      });

      if (!isCurrent()) {
        await response.body?.cancel();
        return;
      }
      reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new LiveSceneSseDecoder();

      const processEvent = (rawData: string) => {
        if (!isCurrent()) return;
        try {
          const data = JSON.parse(rawData) as SSEEvent;

          if (
            data.type === "session" &&
            typeof data.session_id === "string" &&
            data.session_id.length > 0
          ) {
            sessionIdRef.current = data.session_id;
            onSessionReady?.(data.session_id);
          } else if (data.type === "canvas_update") {
            onCanvasUpdate?.(data.operations);
          } else if (data.type === "animation_event") {
            if (data.tool === "teach_with_visuals" && data.sdl) {
              onSDLScene?.(data.sdl);
            }
          } else if (data.type === "storyboard_command") {
            try {
              const command = decodeConversationStoryboardCommandV1(
                data.command,
              );
              if (isCurrent()) onStoryboardCommand?.(command);
            } catch (error) {
              console.warn(
                "[Chat] Ignoring malformed storyboard command:",
                error,
              );
            }
          } else if (data.type === "chunk") {
            fullContent += data.text;
            if (!assistantId) {
              assistantId = crypto.randomUUID();
              setMessages((prev) => [
                ...prev,
                { id: assistantId!, role: "assistant", content: fullContent },
              ]);
            } else {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId ? { ...msg, content: fullContent } : msg
                )
              );
            }
          } else if (data.type === "error") {
            setMessages((prev) => [
              ...prev,
              {
                id: crypto.randomUUID(),
                role: "assistant",
                content: `Error: ${data.message}`,
              },
            ]);
          }
        } catch (error) {
          console.warn("[Chat] Failed to parse SSE event:", rawData, error);
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!isCurrent()) {
          await reader.cancel();
          return;
        }
        for (const event of decoder.push(value)) {
          processEvent(event.data);
        }
      }
      for (const event of decoder.finish()) {
        processEvent(event.data);
      }
    } catch (e) {
      const error = e as Error;
      if (reader) {
        try {
          await reader.cancel(error);
        } catch {
          // Preserve the decoder/network failure that ended the request.
        }
      }
      if (isCurrent() && error.name !== "AbortError") {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: `Error: ${error.message}`,
          },
        ]);
      }
    } finally {
      reader?.releaseLock();
      if (requestGenerationRef.current === generation) {
        activeRequestRef.current = null;
        setIsLoading(false);
      }
    }
  }, [
    agentId,
    apiUrl,
    canvasMode,
    onSessionReady,
    onCanvasUpdate,
    onSDLScene,
    onStoryboardCommand,
  ]);

  const clearChat = useCallback(async () => {
    requestGenerationRef.current += 1;
    activeRequestRef.current?.abort();
    activeRequestRef.current = null;
    setIsLoading(false);

    const sessionId = sessionIdRef.current;
    sessionIdRef.current = null;
    setMessages([]);
    if (sessionId) {
      try {
        await fetch(`${apiUrl}/chat/${sessionId}`, {
          method: "DELETE",
          headers: await getAuthHeaders(),
        });
      } catch {
        // Ignore
      }
    }
  }, [apiUrl]);

  return {
    messages,
    isLoading,
    sendMessage,
    clearChat,
  };
}
