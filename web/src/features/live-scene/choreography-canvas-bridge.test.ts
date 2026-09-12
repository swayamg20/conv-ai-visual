import { describe, expect, it, vi } from "vitest";

import type { SVGCanvasHandle } from "@/features/canvas/types";
import { createSceneState } from "@/lib/live-scene";

import { ChoreographyCanvasBridge } from "./choreography-canvas-bridge";

const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });

function canvasHandle(
  overrides: Partial<SVGCanvasHandle> = {},
): SVGCanvasHandle {
  return {
    cancelMotion: vi.fn(),
    clear: vi.fn(),
    materializeScene: vi.fn(),
    ...overrides,
  } as unknown as SVGCanvasHandle;
}

describe("ChoreographyCanvasBridge", () => {
  it("forwards the retained Replay lifecycle to an attached canvas", () => {
    const cancelMotion = vi.fn();
    const clear = vi.fn();
    const materializeScene = vi.fn();
    const prepareReplayScene = vi.fn();
    const finishReplayScene = vi.fn();
    const bridge = new ChoreographyCanvasBridge();
    bridge.attach(
      canvasHandle({
        cancelMotion,
        clear,
        materializeScene,
        prepareReplayScene,
        finishReplayScene,
      }),
    );

    bridge.prepareReplayScene(EMPTY_SCENE);
    bridge.finishReplayScene();

    expect(prepareReplayScene).toHaveBeenCalledOnce();
    expect(prepareReplayScene).toHaveBeenCalledWith(EMPTY_SCENE);
    expect(finishReplayScene).toHaveBeenCalledOnce();
    expect(cancelMotion).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(materializeScene).not.toHaveBeenCalled();
  });

  it("uses the destructive legacy fallback when Replay hooks are unavailable", () => {
    const calls: string[] = [];
    const bridge = new ChoreographyCanvasBridge();
    bridge.attach(
      canvasHandle({
        cancelMotion: vi.fn(() => calls.push("cancelMotion")),
        clear: vi.fn(() => calls.push("clear")),
        materializeScene: vi.fn((scene) => {
          expect(scene).toBe(EMPTY_SCENE);
          calls.push("materializeScene");
        }),
      }),
    );

    bridge.prepareReplayScene(EMPTY_SCENE);
    expect(calls).toEqual(["cancelMotion", "clear", "materializeScene"]);

    expect(() => bridge.finishReplayScene()).not.toThrow();
    expect(calls).toEqual(["cancelMotion", "clear", "materializeScene"]);
  });
});
