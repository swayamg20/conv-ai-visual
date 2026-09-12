import type { SVGCanvasHandle } from "@/features/canvas/types";

/** The renderer surface shared by the sealed V2 and parametric V3 runtimes. */
export type ChoreographyCanvasRenderer = Pick<
  SVGCanvasHandle,
  | "playCheckpointChoreography"
  | "materializeScene"
  | "materializeViewport"
  | "cancelMotion"
  | "prepareReplayScene"
  | "finishReplayScene"
  | "clear"
>;

/**
 * Keep a stable renderer object while React mounts and replaces the canvas ref.
 * Runtime code never needs to know about React or hold a stale canvas handle.
 */
export class ChoreographyCanvasBridge implements ChoreographyCanvasRenderer {
  private handle: SVGCanvasHandle | null = null;

  readonly attach = (handle: SVGCanvasHandle | null): void => {
    this.handle = handle;
  };

  playCheckpointChoreography: ChoreographyCanvasRenderer["playCheckpointChoreography"] =
    (plan, observer) => {
      if (!this.handle) throw new Error("The visual stage is not ready yet.");
      return this.handle.playCheckpointChoreography(plan, observer);
    };

  materializeScene: ChoreographyCanvasRenderer["materializeScene"] = (scene) => {
    if (!this.handle) throw new Error("The visual stage is not ready yet.");
    this.handle.materializeScene(scene);
  };

  materializeViewport: ChoreographyCanvasRenderer["materializeViewport"] = (
    pose,
  ) => {
    if (!this.handle) throw new Error("The visual stage is not ready yet.");
    this.handle.materializeViewport(pose);
  };

  prepareReplayScene: NonNullable<
    ChoreographyCanvasRenderer["prepareReplayScene"]
  > = (scene) => {
    if (!this.handle) throw new Error("The visual stage is not ready yet.");
    if (this.handle.prepareReplayScene && this.handle.finishReplayScene) {
      this.handle.prepareReplayScene(scene);
      return;
    }
    this.handle.cancelMotion();
    this.handle.clear();
    this.handle.materializeScene(scene);
  };

  finishReplayScene: NonNullable<
    ChoreographyCanvasRenderer["finishReplayScene"]
  > = () => {
    this.handle?.finishReplayScene?.();
  };

  cancelMotion = (): void => {
    this.handle?.cancelMotion();
  };

  clear = (): void => {
    this.handle?.clear();
  };
}
