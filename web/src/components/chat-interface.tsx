"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Trash2, User, Bot, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Message } from "@/hooks/use-chat";
import { WaveformToSketch } from "@/components/murmur-doodles";

interface ChatInterfaceProps {
  messages: Message[];
  isLoading: boolean;
  onSendMessage: (message: string) => void;
  onClearChat: () => void;
}

export function ChatInterface({
  messages,
  isLoading,
  onSendMessage,
  onClearChat,
}: ChatInterfaceProps) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (input.trim() && !isLoading) {
        onSendMessage(input.trim());
        setInput("");
      }
    },
    [input, isLoading, onSendMessage]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e);
      }
    },
    [handleSubmit]
  );

  return (
    <div className="flex flex-col h-full w-full">
      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
        {messages.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center h-full text-center px-4"
          >
            <div className="mb-6">
              <WaveformToSketch />
            </div>
            <h2 className="text-2xl font-semibold text-foreground/90 mb-3">
              Start a conversation
            </h2>
            <p className="text-sm text-muted-foreground max-w-sm leading-relaxed">
              Type a message below to chat with Murmur. Your conversation will
              be remembered during this session.
            </p>
          </motion.div>
        ) : (
          <AnimatePresence mode="popLayout">
            {messages.map((message) => (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.2 }}
                className={cn(
                  "flex gap-3",
                  message.role === "user" ? "justify-end" : "justify-start"
                )}
              >
                {message.role === "assistant" && (
                  <div className="flex-shrink-0 w-8 h-8 rounded-full bg-lavender/15 flex items-center justify-center">
                    <Bot className="h-4 w-4 text-lavender" />
                  </div>
                )}
                <div
                  className={cn(
                    "max-w-[80%] px-4 py-3 text-sm",
                    message.role === "user"
                      ? "glass-card rounded-2xl rounded-br-[6px]"
                      : "bg-transparent border-l-2 border-amber rounded-none pl-4"
                  )}
                >
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
                {message.role === "user" && (
                  <div className="flex-shrink-0 w-8 h-8 rounded-full glass-card flex items-center justify-center">
                    <User className="h-4 w-4 text-chalk" />
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        )}

        {/* Loading indicator */}
        {isLoading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex gap-3 justify-start"
          >
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-lavender/15 flex items-center justify-center">
              <Bot className="h-4 w-4 text-lavender" />
            </div>
            <div className="bg-transparent border-l-2 border-amber pl-4 py-3">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          </motion.div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-chalk-faint/30 p-4 bg-white/[0.02]">
        <form onSubmit={handleSubmit} className="flex gap-3 max-w-2xl mx-auto">
          <div className="flex-1 relative">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your message..."
              disabled={isLoading}
              className="w-full h-12 border-b border-chalk-faint bg-transparent px-2 pr-12 text-sm text-foreground placeholder:text-chalk-soft focus:outline-none focus:border-amber transition-colors disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg bg-amber text-void hover:bg-amber/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={onClearChat}
              className="p-3 rounded-xl border border-chalk-faint/30 bg-transparent text-muted-foreground hover:text-ember hover:border-ember/50 transition-colors"
              title="Clear chat"
            >
              <Trash2 className="h-5 w-5" />
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
