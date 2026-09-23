"use client";

import { X } from "lucide-react";
import { useMemo, type RefObject } from "react";

import { SVGCanvas } from "@/components/svg-canvas";
import type { SVGCanvasHandle } from "@/features/canvas/types";
import { API_BASE } from "@/lib/api";
import type { ConversationStoryboardCommandV1 } from "@/lib/live-scene/conversation-storyboard-command";
import { cn } from "@/lib/utils";

import { LiveSemanticStoryboard } from "./live-semantic-storyboard";
import { createSemanticStoryboardSceneStreamRunner } from "./semantic-storyboard-model-stream";

export interface ActiveConversationStoryboard {
  readonly command: ConversationStoryboardCommandV1;
  readonly sessionId: string;
}

interface ConversationStoryboardWorkspaceProps {
  readonly activeStoryboard: ActiveConversationStoryboard | null;
  readonly canvasRef: RefObject<SVGCanvasHandle | null>;
  readonly onCloseStoryboard: () => void;
}

/** Keep the legacy canvas alive while a session-bound Gate 1.8 lesson is visible. */
export function ConversationStoryboardWorkspace({
  activeStoryboard,
  canvasRef,
  onCloseStoryboard,
}: ConversationStoryboardWorkspaceProps) {
  const activeSessionId = activeStoryboard?.sessionId;
  const runStream = useMemo(() => {
    if (!activeSessionId) return null;
    return createSemanticStoryboardSceneStreamRunner({
      apiUrl: API_BASE,
      endpoint: "product",
      sessionId: activeSessionId,
      getHeaders: async () => {
        const { getAuthHeaders } = await import("@/lib/firebase");
        const headers = await getAuthHeaders();
        if (!headers.Authorization) {
          throw new Error("Sign in again to direct a live visual explanation.");
        }
        return headers;
      },
    });
  }, [activeSessionId]);

  return (
    <div className="relative h-full min-h-0 w-full overflow-hidden">
      <div
        className={cn(
          "h-full w-full",
          activeStoryboard && "pointer-events-none invisible absolute inset-0",
        )}
        aria-hidden={activeStoryboard ? "true" : undefined}
        data-testid="legacy-session-canvas"
      >
        <SVGCanvas
          ref={canvasRef}
          width={800}
          height={600}
          className="h-full w-full"
        />
      </div>

      {activeStoryboard && runStream && (
        <div
          className="absolute inset-0 overflow-y-auto bg-void"
          data-testid="conversation-storyboard-workspace"
        >
          <button
            type="button"
            aria-label="Close visual lesson"
            onClick={onCloseStoryboard}
            className="absolute right-3 top-3 z-30 flex h-10 w-10 items-center justify-center rounded-full border border-chalk-faint/30 bg-void/90 text-chalk-soft shadow-lg transition-colors hover:border-chalk-faint/60 hover:text-chalk focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber"
          >
            <X className="h-4 w-4" />
          </button>
          <LiveSemanticStoryboard
            key={activeStoryboard.command.commandId}
            presentation="embedded"
            autoStart
            initialProblemSpec={activeStoryboard.command.problemSpec}
            initialPrompt={activeStoryboard.command.prompt}
            runStream={runStream}
          />
        </div>
      )}
    </div>
  );
}
