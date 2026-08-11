"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CanvasOperation } from "@/features/canvas/types";
import type { SDLScene } from "@/lib/scene-kit";
import { getAuthHeaders } from "@/lib/firebase";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

type SSEEvent =
  | { type: "session"; session_id: string }
  | { type: "canvas_update"; operations: CanvasOperation[] }
  | { type: "animation_event"; tool: string; sdl?: SDLScene; [key: string]: unknown }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

interface UseChatOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  agentId?: string;
  sessionId?: string | null;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onSDLScene?: (sdl: SDLScene) => void;
}

export function useChat(options: UseChatOptions = {}) {
  const { apiUrl = "http://localhost:8000", canvasMode = false, agentId, sessionId: externalSessionId, onCanvasUpdate, onSDLScene } = options;

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const sessionIdRef = useRef<string | null>(externalSessionId ?? null);

  // Sync external sessionId into the ref without mutating it during render.
  useEffect(() => {
    if (externalSessionId !== undefined && externalSessionId !== null) {
      sessionIdRef.current = externalSessionId;
    }
  }, [externalSessionId]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    let assistantId: string | null = null;
    let fullContent = "";

    try {
      const response = await fetch(`${apiUrl}/chat`, {
        method: "POST",
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

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6)) as SSEEvent;

              if (data.type === "session") {
                sessionIdRef.current = data.session_id;
              } else if (data.type === "canvas_update") {
                onCanvasUpdate?.(data.operations);
              } else if (data.type === "animation_event") {
                if (data.tool === "teach_with_visuals" && data.sdl) {
                  onSDLScene?.(data.sdl);
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
            } catch {
              console.warn("[Chat] Failed to parse SSE event:", line);
            }
          }
        }
      }
    } catch (e) {
      const error = e as Error;
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: `Error: ${error.message}`,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [agentId, apiUrl, canvasMode, onCanvasUpdate, onSDLScene]);

  const clearChat = useCallback(async () => {
    if (sessionIdRef.current) {
      try {
        await fetch(`${apiUrl}/chat/${sessionIdRef.current}`, {
          method: "DELETE",
          headers: await getAuthHeaders(),
        });
      } catch {
        // Ignore
      }
    }
    sessionIdRef.current = null;
    setMessages([]);
  }, [apiUrl]);

  return {
    messages,
    isLoading,
    sendMessage,
    clearChat,
  };
}
