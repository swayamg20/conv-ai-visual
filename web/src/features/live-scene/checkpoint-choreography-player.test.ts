import { describe, expect, it, vi } from "vitest";

import { createSceneState } from "@/lib/live-scene";

import {
  CheckpointChoreographyPlayer,
  type CheckpointChoreographyRenderer,
} from "./checkpoint-choreography-player";

const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });
const RESTORED_SCENE = createSceneState({ revision: 1, nodes: [] });
const RESTORED_VIEWPORT = Object.freeze({
  v: 1 as const,
  x: 20,
  y: 30,
  width: 640,
  height: 480,
});

function harness(
  replayHooks: Pick<
    CheckpointChoreographyRenderer,
    "prepareReplayScene" | "finishReplayScene"
  > = {},
) {
  const renderer = {
    playCheckpointChoreography:
      vi.fn() as unknown as CheckpointChoreographyRenderer["playCheckpointChoreography"],
    materializeScene: vi.fn(),
    materializeViewport: vi.fn(),
    cancelMotion: vi.fn(),
    clear: vi.fn(),
    ...replayHooks,
  } satisfies CheckpointChoreographyRenderer;
  return { player: new CheckpointChoreographyPlayer(renderer), renderer };
}

describe("CheckpointChoreographyPlayer replay materialization", () => {
  it("uses the retained Replay hooks and finishes the retained scene once", () => {
    const prepareReplayScene = vi.fn();
    const finishReplayScene = vi.fn();
    const { player, renderer } = harness({
      prepareReplayScene,
      finishReplayScene,
    });

    player.materializeEmpty(EMPTY_SCENE);

    expect(prepareReplayScene).toHaveBeenCalledOnce();
    expect(prepareReplayScene).toHaveBeenCalledWith(EMPTY_SCENE);
    expect(renderer.cancelMotion).not.toHaveBeenCalled();
    expect(renderer.clear).not.toHaveBeenCalled();
    expect(renderer.materializeScene).not.toHaveBeenCalled();

    player.finishReplay();
    player.finishReplay();
    expect(finishReplayScene).toHaveBeenCalledOnce();
  });

  it("preserves the exact legacy clear and materialize fallback", () => {
    const calls: string[] = [];
    const { player, renderer } = harness();
    renderer.cancelMotion.mockImplementation(() => calls.push("cancelMotion"));
    renderer.clear.mockImplementation(() => calls.push("clear"));
    renderer.materializeScene.mockImplementation(() =>
      calls.push("materializeScene"),
    );

    player.materializeEmpty(EMPTY_SCENE);

    expect(calls).toEqual(["cancelMotion", "clear", "materializeScene"]);
    expect(renderer.materializeScene).toHaveBeenCalledWith(EMPTY_SCENE);
  });

  it("restores a retained Replay by materializing without destructive clear, then finishes", () => {
    const prepareReplayScene = vi.fn();
    const finishReplayScene = vi.fn();
    const { player, renderer } = harness({
      prepareReplayScene,
      finishReplayScene,
    });
    player.materializeEmpty(EMPTY_SCENE);

    expect(
      player.restore({
        scene: RESTORED_SCENE,
        viewport: RESTORED_VIEWPORT,
      }),
    ).toBeUndefined();

    expect(renderer.cancelMotion).toHaveBeenCalledOnce();
    expect(renderer.clear).not.toHaveBeenCalled();
    expect(renderer.materializeScene).toHaveBeenCalledOnce();
    expect(renderer.materializeScene).toHaveBeenCalledWith(RESTORED_SCENE);
    expect(renderer.materializeViewport).toHaveBeenCalledWith(
      RESTORED_VIEWPORT,
    );
    expect(finishReplayScene).toHaveBeenCalledOnce();
    player.finishReplay();
    expect(finishReplayScene).toHaveBeenCalledOnce();
  });

  it("keeps failed retained preparation recoverable without destructive clear", () => {
    const prepareReplayScene = vi.fn(() => {
      throw new Error("prepare failed atomically");
    });
    const finishReplayScene = vi.fn();
    const { player, renderer } = harness({
      prepareReplayScene,
      finishReplayScene,
    });

    expect(() => player.materializeEmpty(EMPTY_SCENE)).toThrow(
      "prepare failed atomically",
    );
    expect(
      player.restore({ scene: RESTORED_SCENE, viewport: RESTORED_VIEWPORT }),
    ).toBeUndefined();

    expect(renderer.clear).not.toHaveBeenCalled();
    expect(renderer.materializeScene).toHaveBeenCalledWith(RESTORED_SCENE);
    expect(finishReplayScene).toHaveBeenCalledOnce();
  });
});
