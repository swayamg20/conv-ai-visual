"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence, PanInfo } from "framer-motion";
import { X } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { TranscriptList } from "./transcript-list";
import type { PipelineState } from "@/hooks/use-webrtc";

interface TechnicalDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  transcripts: string[];
  logs: string[];
  pipelineState: PipelineState;
}

export function TechnicalDrawer({
  isOpen,
  onClose,
  transcripts,
  logs,
  pipelineState,
}: TechnicalDrawerProps) {
  const [activeTab, setActiveTab] = useState("transcripts");
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll logs
  useEffect(() => {
    if (isOpen) {
      logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, isOpen]);

  const handleDragEnd = (_: any, info: PanInfo) => {
    // Close drawer if dragged down more than 100px
    if (info.offset.y > 100) {
      onClose();
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Drawer */}
          <motion.div
            initial={{ y: "100%", opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: "100%", opacity: 0 }}
            transition={{
              type: "spring",
              damping: 30,
              stiffness: 300,
            }}
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.5 }}
            onDragEnd={handleDragEnd}
            className="fixed bottom-0 left-0 right-0 z-50 flex flex-col glass-card rounded-t-3xl border-t border-white/10 shadow-glass-lg"
            style={{ height: "60vh", maxHeight: "60vh" }}
          >
            {/* Drag Handle */}
            <div className="flex justify-center pt-3 pb-2">
              <div className="w-12 h-1.5 bg-white/20 rounded-full" />
            </div>

            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
              <h2 className="text-lg font-semibold text-foreground">
                Technical Details
              </h2>
              <button
                onClick={onClose}
                className="p-2 rounded-lg hover:bg-white/10 transition-colors"
              >
                <X className="h-5 w-5 text-muted-foreground" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-hidden">
              <Tabs value={activeTab} onValueChange={setActiveTab} className="h-full flex flex-col">
                <TabsList className="mx-6 mt-4">
                  <TabsTrigger value="transcripts">Transcripts</TabsTrigger>
                  <TabsTrigger value="metrics">Metrics</TabsTrigger>
                  <TabsTrigger value="logs">System Logs</TabsTrigger>
                </TabsList>

                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1, staggerChildren: 0.05 }}
                  className="flex-1 overflow-auto px-6 py-4"
                >
                  <TabsContent value="transcripts" className="mt-0">
                    {transcripts.length > 0 ? (
                      <TranscriptList transcripts={transcripts} />
                    ) : (
                      <div className="flex items-center justify-center h-40 text-muted-foreground">
                        No transcripts yet...
                      </div>
                    )}
                  </TabsContent>

                  <TabsContent value="metrics" className="mt-0">
                    <div className="flex flex-col items-center justify-center h-40 gap-4">
                      <div className="text-sm text-muted-foreground">Pipeline State</div>
                      <div className="text-2xl font-semibold">
                        {pipelineState === "idle" && "⚪ Idle"}
                        {pipelineState === "listening" && "🎤 Listening"}
                        {pipelineState === "processing" && "🧠 Processing"}
                        {pipelineState === "speaking" && "🔊 Speaking"}
                      </div>
                    </div>
                  </TabsContent>

                  <TabsContent value="logs" className="mt-0">
                    <div className="space-y-1 font-mono text-xs text-muted-foreground">
                      {logs.length > 0 ? (
                        logs.map((line, i) => (
                          <div key={i} className="whitespace-pre-wrap leading-relaxed">
                            {line}
                          </div>
                        ))
                      ) : (
                        <div className="flex items-center justify-center h-40 text-center">
                          No logs yet...
                        </div>
                      )}
                      <div ref={logsEndRef} />
                    </div>
                  </TabsContent>
                </motion.div>
              </Tabs>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
