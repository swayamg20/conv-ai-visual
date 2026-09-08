import { expect, type Locator, type Page } from "@playwright/test";

import {
  decodeChoreographySceneStreamEvent,
  type ChoreographySceneCheckpointEvent,
} from "../src/features/live-scene/choreography-model-stream";
import fixtureValue from "../src/features/live-scene/fixtures/completing-the-square.v1.json";
import type {
  ChoreographyCueKind,
  ChoreographyLayout,
  CompletingSquareCheckpointId,
  ViewportPoseV1,
} from "../src/lib/live-scene";
import type { ChoreographyEvidenceTraceEvent } from "../src/features/live-scene/choreography-playback";

const CAPTURE_BRIDGE_KEY = "__MURMUR_CHOREOGRAPHY_CAPTURE__";

export interface ExpectedChoreographyCheckpoint {
  readonly ordinal: number;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
  readonly caption: string;
  readonly baseViewport: ViewportPoseV1;
  readonly resultViewport: ViewportPoseV1;
  readonly cues: readonly ChoreographyCueKind[];
  readonly nodeIds: readonly string[];
}

export interface SettledCheckpointObservation {
  readonly ordinal: number;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly caption: string;
  readonly viewBox: string;
  readonly nodeIds: readonly string[];
  readonly domIdentity: Readonly<Record<string, number>>;
  readonly rendererTrusted: true;
  readonly transientResidueCount: 0;
}

interface CaptureBridge {
  readonly version: 1;
  readonly pace: "auto" | "step";
  getState(): {
    readonly waitingFor: {
      readonly generation: number;
      readonly sequence: number;
      readonly checkpointId: CompletingSquareCheckpointId;
      readonly openedAtMs: number;
    } | null;
    readonly acknowledgedThrough: number;
    readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
  };
  acknowledgeCheckpoint(value: {
    readonly generation: number;
    readonly sequence: number;
    readonly checkpointId: CompletingSquareCheckpointId;
  }): void;
}

export interface CaptureBridgeState {
  readonly waitingFor: {
    readonly generation: number;
    readonly sequence: number;
    readonly checkpointId: CompletingSquareCheckpointId;
    readonly openedAtMs: number;
  } | null;
  readonly acknowledgedThrough: number;
  readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
}

function checkpointEvents(): readonly ChoreographySceneCheckpointEvent[] {
  const events = fixtureValue.events.map((event) =>
    decodeChoreographySceneStreamEvent(event),
  );
  const checkpoints = events.filter(
    (event): event is ChoreographySceneCheckpointEvent =>
      event.type === "choreography_scene_checkpoint",
  );
  if (checkpoints.length !== 8) {
    throw new Error(
      "The Gate 1.5 fixture must contain exactly eight main checkpoints",
    );
  }
  return checkpoints;
}

function viewBox(pose: ViewportPoseV1): string {
  return `${pose.x} ${pose.y} ${pose.width} ${pose.height}`;
}

function expectedCheckpoints(
  layout: ChoreographyLayout,
): readonly ExpectedChoreographyCheckpoint[] {
  const nodeIds: string[] = [];
  return checkpointEvents().map((event, index) => {
    for (const operation of event.patch.operations) {
      if (operation.op === "remove") {
        const nodeIndex = nodeIds.indexOf(operation.id);
        if (nodeIndex < 0) {
          throw new Error(`Fixture removes absent node ${operation.id}`);
        }
        nodeIds.splice(nodeIndex, 1);
      } else if (!nodeIds.includes(operation.node.id)) {
        nodeIds.push(operation.node.id);
      }
    }
    return Object.freeze({
      ordinal: index + 1,
      checkpointId: event.semantic.checkpointId,
      certificateSha256: event.semantic.certificate.certificateSha256,
      caption: event.semantic.presentation.checkpointNarration,
      baseViewport: event.semantic.presentation.baseViewports[layout],
      resultViewport: event.semantic.presentation.resultViewports[layout],
      cues: Object.freeze(
        event.semantic.choreography.phase.cues.map((cue) => cue.cue),
      ),
      nodeIds: Object.freeze([...nodeIds]),
    });
  });
}

export const CINEMATIC_CHECKPOINTS = expectedCheckpoints("cinematic");
export const COMPACT_CHECKPOINTS = expectedCheckpoints("compact");

export const MAIN_CHOREOGRAPHY_EVIDENCE = Object.freeze(
  CINEMATIC_CHECKPOINTS.flatMap((checkpoint) => {
    const common = {
      generation: 1,
      attempt: 1,
      sequence: checkpoint.ordinal,
      checkpointId: checkpoint.checkpointId,
      certificateSha256: checkpoint.certificateSha256,
    } as const;
    return [
      ...checkpoint.cues.map((cue) => ({
        type: "cueStarted" as const,
        ...common,
        cue,
      })),
      { type: "firstCuePresented" as const, ...common },
      {
        type: "checkpointSettled" as const,
        ...common,
        settlement: "completed" as const,
      },
    ];
  }).map((event, index) => Object.freeze({ ordinal: index + 1, ...event })),
) satisfies readonly ChoreographyEvidenceTraceEvent[];

export function choreographyStage(page: Page): Locator {
  return page.getByTestId("live-choreography-stage");
}

export async function readCaptureBridgeState(
  page: Page,
): Promise<CaptureBridgeState> {
  return page.evaluate((key) => {
    const bridge = (window as typeof window & Record<string, unknown>)[key] as
      CaptureBridge | undefined;
    if (
      !bridge ||
      bridge.version !== 1 ||
      (bridge.pace !== "auto" && bridge.pace !== "step")
    ) {
      throw new Error("The choreography capture bridge is unavailable");
    }
    return bridge.getState();
  }, CAPTURE_BRIDGE_KEY);
}

export async function waitForCaptureEvidence(
  page: Page,
  expected: readonly ChoreographyEvidenceTraceEvent[],
): Promise<readonly ChoreographyEvidenceTraceEvent[]> {
  await expect
    .poll(async () => (await readCaptureBridgeState(page)).evidence)
    .toEqual(expected);
  return (await readCaptureBridgeState(page)).evidence;
}

async function waitForStablePaint(stage: Locator): Promise<void> {
  await stage.evaluate(async (element) => {
    await document.fonts.ready;
    const animations = element.getAnimations({ subtree: true });
    await Promise.all(
      animations.map((animation) => animation.finished.catch(() => undefined)),
    );
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}

/** Read one quiescent, renderer-trusted checkpoint after the capture runner has gated its successor. */
export async function observeSettledCheckpoint(
  page: Page,
  expected: ExpectedChoreographyCheckpoint,
): Promise<SettledCheckpointObservation> {
  const stage = choreographyStage(page);
  await expect(stage).toHaveAttribute(
    "data-settled-main-count",
    String(expected.ordinal),
  );
  await expect(stage).toHaveAttribute(
    "data-checkpoint-id",
    expected.checkpointId,
  );
  await expect(stage).toHaveAttribute("data-renderer-trusted", "true");
  await waitForStablePaint(stage);

  const observation = await stage.evaluate((element, checkpoint) => {
    interface IdentityRegistry {
      next: number;
      readonly tokens: WeakMap<Element, number>;
    }
    const owner = window as typeof window & {
      __MURMUR_CHOREOGRAPHY_DOM_IDENTITY__?: IdentityRegistry;
    };
    const registry = owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ ?? {
      next: 1,
      tokens: new WeakMap<Element, number>(),
    };
    owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ = registry;

    const svg = element.querySelector("svg");
    if (!svg) throw new Error("The choreography stage has no SVG canvas");
    const nodes = Array.from(
      svg.querySelectorAll<SVGElement>(":scope > [data-element-id]"),
    );
    const nodeIds = nodes.map((node) => node.dataset.elementId ?? "");
    const domIdentity = Object.fromEntries(
      nodes.map((node) => {
        let token = registry.tokens.get(node);
        if (token === undefined) {
          token = registry.next;
          registry.next += 1;
          registry.tokens.set(node, token);
        }
        return [node.dataset.elementId ?? "", token];
      }),
    );
    const transientSelector = [
      "[id$='--incoming']",
      "[id$='--outgoing']",
      "[data-element-id$='--incoming']",
      "[data-element-id$='--outgoing']",
      "[transform]",
      "[filter]",
      "[stroke-dasharray]",
      "[stroke-dashoffset]",
    ].join(",");
    const styleResidue = nodes.filter((node) => {
      const style = getComputedStyle(node);
      return style.transform !== "none" || style.filter !== "none";
    }).length;
    const caption = element.querySelector("figcaption")?.textContent?.trim();
    if (!caption) throw new Error("The settled checkpoint has no caption");

    return {
      ordinal: checkpoint.ordinal,
      checkpointId: checkpoint.checkpointId,
      caption,
      viewBox: svg.getAttribute("viewBox") ?? "",
      nodeIds,
      domIdentity,
      rendererTrusted: element.getAttribute("data-renderer-trusted") === "true",
      transientResidueCount:
        svg.querySelectorAll(transientSelector).length + styleResidue,
    };
  }, expected);

  expect(observation.caption).toBe(expected.caption);
  expect(observation.viewBox).toBe(viewBox(expected.resultViewport));
  expect(observation.nodeIds).toEqual(expected.nodeIds);
  expect(new Set(observation.nodeIds).size).toBe(observation.nodeIds.length);
  expect(observation.rendererTrusted).toBe(true);
  expect(observation.transientResidueCount).toBe(0);
  return observation as SettledCheckpointObservation;
}

export async function acknowledgeCheckpoint(
  page: Page,
  checkpoint: ExpectedChoreographyCheckpoint,
): Promise<void> {
  await page.evaluate(
    ({ key, expected }) => {
      const bridge = (window as typeof window & Record<string, unknown>)[
        key
      ] as CaptureBridge | undefined;
      if (!bridge || bridge.version !== 1 || bridge.pace !== "step") {
        throw new Error(
          "The step-gated choreography capture bridge is unavailable",
        );
      }
      bridge.acknowledgeCheckpoint({
        generation: 1,
        sequence: expected.ordinal,
        checkpointId: expected.checkpointId,
      });
    },
    { key: CAPTURE_BRIDGE_KEY, expected: checkpoint },
  );
}

export function assertRetainedDomIdentity(
  previous: SettledCheckpointObservation,
  current: SettledCheckpointObservation,
): void {
  for (const id of previous.nodeIds) {
    if (current.domIdentity[id] !== undefined) {
      expect(current.domIdentity[id], `DOM identity changed for ${id}`).toBe(
        previous.domIdentity[id],
      );
    }
  }
}
