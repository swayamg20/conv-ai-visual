"use client";

import { useState, useRef, useCallback } from "react";
import type { CanvasOperation } from "./use-webrtc";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface UseChatOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onSDLScene?: (sdl: any) => void;
}

export function useChat(options: UseChatOptions = {}) {
  const { apiUrl = "http://localhost:8000", canvasMode = false, onCanvasUpdate, onSDLScene } = options;

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const sessionIdRef = useRef<string | null>(null);

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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: sessionIdRef.current,
          canvas_mode: canvasMode,
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
              const data = JSON.parse(line.slice(6));

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
              // Ignore parse errors
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
  }, [apiUrl, canvasMode, onCanvasUpdate, onSDLScene]);

  const clearChat = useCallback(async () => {
    if (sessionIdRef.current) {
      try {
        await fetch(`${apiUrl}/chat/${sessionIdRef.current}`, { method: "DELETE" });
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
