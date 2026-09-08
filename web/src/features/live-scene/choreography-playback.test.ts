import { describe, expect, it } from "vitest";

import {
  createSceneState,
  type ChoreographyLayout,
  type ViewportPoseV1,
} from "@/lib/live-scene";

import fixtureValue from "./fixtures/completing-the-square.v1.json";
import {
  decodeChoreographySceneStreamEvent,
  type ChoreographySceneCheckpointEvent,
} from "./choreography-model-stream";
import {
  EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS,
  appendChoreographyCheckpointSettled,
  appendChoreographyCueStarted,
  appendChoreographyFirstCuePresented,
  choreographyEvidenceTraceMatchesAccepted,
  choreographyReplayRecordMatches,
  createAcceptedChoreographyRevision,
  createChoreographyEvidenceTrace,
  createChoreographyFrontier,
  createChoreographySemanticSceneState,
  discardUnacceptedChoreographyEvidence,
  evaluateChoreographyPresentation,
  preflightChoreographyReplay,
  prepareChoreographyCheckpoint,
  type AcceptedChoreographyRevision,
  type ChoreographyEvidenceTraceEvent,
  type ChoreographyFrontier,
  type ChoreographyPresentationEvaluation,
  type PreparedChoreographyCheckpoint,
} from "./choreography-playback";

const EMPTY_SCENE = createSceneState({ revision: 0, nodes: [] });
const OTHER_DIGEST = "0".repeat(64);

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function checkpointEvents(): ChoreographySceneCheckpointEvent[] {
  return fixtureValue.events
    .map((event) => decodeChoreographySceneStreamEvent(event))
    .filter(
      (event): event is ChoreographySceneCheckpointEvent =>
        event.type === "choreography_scene_checkpoint",
    );
}

function adaptiveCheckpointEvents(): ChoreographySceneCheckpointEvent[] {
  return fixtureValue.adaptiveTranscript.events
    .map((event) => decodeChoreographySceneStreamEvent(event))
    .filter(
      (event): event is ChoreographySceneCheckpointEvent =>
        event.type === "choreography_scene_checkpoint",
    );
}

function emptyFrontier(): ChoreographyFrontier {
  return {
    scene: EMPTY_SCENE,
    semanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  };
}

function acceptedFrontier(
  accepted: AcceptedChoreographyRevision,
): ChoreographyFrontier {
  return {
    scene: accepted.scene,
    semanticScene: accepted.semanticScene,
    viewport: accepted.viewport,
    layout: accepted.layout,
    certificateHeadSha256: accepted.presentation.certificateSha256,
  };
}

function presentedEvaluation(
  prepared: PreparedChoreographyCheckpoint,
  status: "completed" | "cancelled_to_checkpoint" = "completed",
): Extract<ChoreographyPresentationEvaluation, { kind: "presented" }> {
  const evaluation = evaluateChoreographyPresentation(prepared, {
    status,
    firstCuePresented: true,
  });
  if (evaluation.kind !== "presented") {
    throw new Error("expected a presented checkpoint");
  }
  return evaluation;
}

function accept(
  frontier: ChoreographyFrontier,
  event: ChoreographySceneCheckpointEvent,
  layout: ChoreographyLayout = "cinematic",
): {
  prepared: PreparedChoreographyCheckpoint;
  accepted: AcceptedChoreographyRevision;
} {
  const prepared = prepareChoreographyCheckpoint(frontier, event, layout);
  const evaluation = presentedEvaluation(prepared);
  return {
    prepared,
    accepted: createAcceptedChoreographyRevision(prepared, evaluation),
  };
}

function acceptPrefix(
  length: number,
  layout: ChoreographyLayout = "cinematic",
): {
  frontier: ChoreographyFrontier;
  records: AcceptedChoreographyRevision[];
  prepared: PreparedChoreographyCheckpoint[];
} {
  let frontier = emptyFrontier();
  const records: AcceptedChoreographyRevision[] = [];
  const prepared: PreparedChoreographyCheckpoint[] = [];
  for (const event of checkpointEvents().slice(0, length)) {
    const next = accept(frontier, event, layout);
    records.push(next.accepted);
    prepared.push(next.prepared);
    frontier = acceptedFrontier(next.accepted);
  }
  return { frontier, records, prepared };
}

function nested(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return value[key] as Record<string, unknown>;
}

describe("choreography playback state", () => {
  it("strictly constructs and deeply freezes mixed semantic state", () => {
    const state = createChoreographySemanticSceneState({
      revision: 2,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "pythagoras",
          revealedRoles: ["triangle"],
        },
        {
          kind: "completing_square",
          id: "square-lesson",
          lastMainCheckpoint: "problem",
          cornerClarified: false,
        },
      ],
      certificateHeadSha256: "a".repeat(64),
    });

    expect(state).toEqual({
      revision: 2,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "pythagoras",
          revealedRoles: ["triangle"],
        },
        {
          kind: "completing_square",
          id: "square-lesson",
          lastMainCheckpoint: "problem",
          cornerClarified: false,
        },
      ],
      certificateHeadSha256: "a".repeat(64),
    });
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(state.components)).toBe(true);
    expect(Object.isFrozen(state.components[0])).toBe(true);
    expect(Object.isFrozen(state.components[1])).toBe(true);
  });

  it.each([
    [
      "unknown state fields",
      {
        revision: 0,
        components: [],
        modelNote: "trust me",
      },
      /unknown field modelNote/,
    ],
    [
      "duplicate cross-kind component IDs",
      {
        revision: 1,
        components: [
          {
            kind: "pythagorean_area_identity",
            id: "shared",
            revealedRoles: ["triangle"],
          },
          {
            kind: "completing_square",
            id: "shared",
            lastMainCheckpoint: "problem",
            cornerClarified: false,
          },
        ],
        certificateHeadSha256: "a".repeat(64),
      },
      /duplicated/,
    ],
    [
      "an orphan revision-zero head",
      {
        revision: 0,
        components: [],
        certificateHeadSha256: "a".repeat(64),
      },
      /revision zero must have no components or certificate head/,
    ],
    [
      "a missing committed head",
      {
        revision: 1,
        components: [
          {
            kind: "completing_square",
            id: "square-lesson",
            lastMainCheckpoint: "problem",
            cornerClarified: false,
          },
        ],
      },
      /requires components and a certificate head/,
    ],
    [
      "clarification before its legal frontier",
      {
        revision: 1,
        components: [
          {
            kind: "completing_square",
            id: "square-lesson",
            lastMainCheckpoint: "problem",
            cornerClarified: true,
          },
        ],
        certificateHeadSha256: "a".repeat(64),
      },
      /cannot clarify before missing_corner/,
    ],
    [
      "a committed null checkpoint frontier",
      {
        revision: 1,
        components: [
          {
            kind: "completing_square",
            id: "square-lesson",
            lastMainCheckpoint: null,
            cornerClarified: false,
          },
        ],
        certificateHeadSha256: "a".repeat(64),
      },
      /requires a checkpoint frontier/,
    ],
  ])("rejects %s", (_label, value, expected) => {
    expect(() => createChoreographySemanticSceneState(value)).toThrow(expected);
  });

  it("rejects mismatched paired revisions, heads, and nonempty null-camera frontiers", () => {
    const { records } = acceptPrefix(1);
    const first = records[0];

    expect(() =>
      createChoreographyFrontier({
        ...acceptedFrontier(first),
        semanticScene: {
          ...first.semanticScene,
          revision: 2,
        },
      }),
    ).toThrow(/low-level and semantic revisions must match/);
    expect(() =>
      createChoreographyFrontier({
        ...acceptedFrontier(first),
        certificateHeadSha256: OTHER_DIGEST,
      }),
    ).toThrow(/explicit certificate head must match/);
    expect(() =>
      createChoreographyFrontier({
        ...acceptedFrontier(first),
        viewport: null,
      }),
    ).toThrow(/only an empty revision-zero frontier may omit its viewport/);
    expect(() =>
      createChoreographyFrontier({
        ...emptyFrontier(),
        viewport:
          checkpointEvents()[0].semantic.presentation.baseViewports.cinematic,
      }),
    ).toThrow(/viewport and locked layout/);
    expect(() =>
      createChoreographyFrontier({
        ...emptyFrontier(),
        debugId: "model-owned-id",
      }),
    ).toThrow(/unknown field debugId/);
  });
});

describe("checkpoint preparation and replay records", () => {
  it("bootstraps only the empty r0 frontier to the selected certified viewport", () => {
    const first = checkpointEvents()[0];
    const cinematic = prepareChoreographyCheckpoint(
      emptyFrontier(),
      first,
      "cinematic",
    );
    const compact = prepareChoreographyCheckpoint(
      emptyFrontier(),
      first,
      "compact",
    );

    expect(cinematic.bootstrappedViewport).toBe(true);
    expect(cinematic.base.viewport).toEqual(
      first.semantic.presentation.baseViewports.cinematic,
    );
    expect(compact.base.viewport).toEqual(
      first.semantic.presentation.baseViewports.compact,
    );
    expect(cinematic.target.viewport).toEqual(
      first.semantic.presentation.resultViewports.cinematic,
    );
    expect(cinematic.base.scene).toEqual(EMPTY_SCENE);
    expect(cinematic.target.scene.revision).toBe(1);
    expect(cinematic.target.semanticScene.revision).toBe(1);
    expect(cinematic.target.certificateHeadSha256).toBe(
      first.semantic.certificate.certificateSha256,
    );
    expect(Object.isFrozen(cinematic)).toBe(true);
    expect(Object.isFrozen(cinematic.target)).toBe(true);
  });

  it("rejects a supplied viewport mismatch and a broken certificate-chain join", () => {
    const { frontier } = acceptPrefix(1);
    const second = checkpointEvents()[1];
    const wrongViewport: ViewportPoseV1 = {
      ...(frontier.viewport as ViewportPoseV1),
      x: (frontier.viewport as ViewportPoseV1).x + 1,
    };
    expect(() =>
      prepareChoreographyCheckpoint(
        { ...frontier, viewport: wrongViewport },
        second,
        "cinematic",
      ),
    ).toThrow(/current viewport does not join/);

    const broken = clone(second) as unknown as Record<string, unknown>;
    const body = nested(
      nested(nested(broken, "semantic"), "certificate"),
      "body",
    );
    body.previousCertificateSha256 = OTHER_DIGEST;
    expect(() =>
      prepareChoreographyCheckpoint(frontier, broken, "cinematic"),
    ).toThrow(/previous certificate does not join/);
  });

  it("enforces exact main-checkpoint predecessors before geometry planning", () => {
    const { frontier } = acceptPrefix(1);
    const skipped = clone(checkpointEvents()[2]) as unknown as Record<
      string,
      unknown
    >;
    skipped.baseRevision = 1;
    skipped.resultRevision = 2;
    const semantic = nested(skipped, "semantic");
    semantic.semanticBaseRevision = 1;
    semantic.semanticResultRevision = 2;
    const body = nested(nested(semantic, "certificate"), "body");
    body.baseRevision = 1;
    body.resultRevision = 2;
    body.previousCertificateSha256 = frontier.certificateHeadSha256;

    expect(() =>
      prepareChoreographyCheckpoint(frontier, skipped, "cinematic"),
    ).toThrow(/not the exact next semantic predecessor/);
  });

  it("accepts corner_detail only at the unclarified missing-corner frontier", () => {
    const prefix = acceptPrefix(5);
    const corner = adaptiveCheckpointEvents()[0];
    const prepared = prepareChoreographyCheckpoint(
      prefix.frontier,
      corner,
      "cinematic",
    );

    expect(prepared.target.semanticScene.components).toContainEqual({
      kind: "completing_square",
      id: "square-lesson",
      lastMainCheckpoint: "missing_corner",
      cornerClarified: true,
    });
    expect(() =>
      prepareChoreographyCheckpoint(
        acceptedFrontier(
          createAcceptedChoreographyRevision(
            prepared,
            presentedEvaluation(prepared),
          ),
        ),
        corner,
        "cinematic",
      ),
    ).toThrow(/checkpoint does not join|only at the unclarified/);

    const early = acceptPrefix(4);
    const rebased = clone(corner) as unknown as Record<string, unknown>;
    rebased.baseRevision = 4;
    rebased.resultRevision = 5;
    const semantic = nested(rebased, "semantic");
    semantic.semanticBaseRevision = 4;
    semantic.semanticResultRevision = 5;
    const body = nested(nested(semantic, "certificate"), "body");
    body.baseRevision = 4;
    body.resultRevision = 5;
    body.previousCertificateSha256 = early.frontier.certificateHeadSha256;
    expect(() =>
      prepareChoreographyCheckpoint(early.frontier, rebased, "cinematic"),
    ).toThrow(/only at the unclarified missing_corner frontier/);
  });

  it("rejects advance checkpoints beyond the independently routed target stage", () => {
    const prefix = acceptPrefix(7);
    const solve = clone(checkpointEvents()[7]) as unknown as Record<
      string,
      unknown
    >;
    nested(nested(solve, "semantic"), "beat").route = {
      intent: "advance",
      targetStage: "setup",
    };

    expect(() =>
      prepareChoreographyCheckpoint(prefix.frontier, solve, "cinematic"),
    ).toThrow(/exceeds the routed target stage/);
  });

  it("locks the selected layout even when the alternative viewport is identical", () => {
    const prefix = acceptPrefix(1);
    const second = clone(checkpointEvents()[1]) as unknown as Record<
      string,
      unknown
    >;
    const semantic = nested(second, "semantic");
    const presentation = nested(semantic, "presentation");
    const baseViewports = nested(presentation, "baseViewports");
    const resultViewports = nested(presentation, "resultViewports");
    baseViewports.compact = clone(baseViewports.cinematic);
    resultViewports.compact = clone(resultViewports.cinematic);
    nested(nested(semantic, "certificate"), "body").presentationCheckpoint =
      clone(presentation);

    expect(() =>
      prepareChoreographyCheckpoint(
        {
          ...prefix.frontier,
          viewport: clone(baseViewports.compact) as ViewportPoseV1,
        },
        second,
        "compact",
      ),
    ).toThrow(/cannot change the locked choreography layout/);
  });

  it("rejects main-route drift and loss of an already-set clarification bit", () => {
    const first = clone(checkpointEvents()[0]) as unknown as Record<
      string,
      unknown
    >;
    nested(nested(first, "semantic"), "beat").route = {
      intent: "clarify_corner",
    };
    expect(() =>
      prepareChoreographyCheckpoint(emptyFrontier(), first, "cinematic"),
    ).toThrow(/main checkpoints require an advance route/);

    const prefix = acceptPrefix(5);
    const component = prefix.frontier.semanticScene.components[0];
    const clarifiedSemantic = createChoreographySemanticSceneState({
      ...prefix.frontier.semanticScene,
      components: [{ ...component, cornerClarified: true }],
    });
    expect(() =>
      prepareChoreographyCheckpoint(
        { ...prefix.frontier, semanticScene: clarifiedSemantic },
        checkpointEvents()[5],
        "cinematic",
      ),
    ).toThrow(/must preserve the corner clarification bit/);
  });

  it("will not start or continue V2 choreography beside another semantic component", () => {
    const prefix = acceptPrefix(1);
    const mixedSemantic = createChoreographySemanticSceneState({
      ...prefix.frontier.semanticScene,
      components: [
        ...prefix.frontier.semanticScene.components,
        {
          kind: "pythagorean_area_identity",
          id: "pythagoras",
          revealedRoles: ["triangle"],
        },
      ],
    });

    expect(() =>
      prepareChoreographyCheckpoint(
        { ...prefix.frontier, semanticScene: mixedSemantic },
        checkpointEvents()[1],
        "cinematic",
      ),
    ).toThrow(/sole accepted completing-square component/);
  });

  it("creates receipts only from coherent terminal post-paint outcomes", () => {
    const prepared = prepareChoreographyCheckpoint(
      emptyFrontier(),
      checkpointEvents()[0],
      "cinematic",
    );
    const evaluated = evaluateChoreographyPresentation(prepared, {
      status: "cancelled_to_checkpoint",
      firstCuePresented: true,
    });
    expect(evaluated.kind).toBe("presented");
    if (evaluated.kind !== "presented") throw new Error("unreachable");
    const receipt = evaluated.receipt;
    const accepted = createAcceptedChoreographyRevision(prepared, evaluated);

    expect(receipt).toEqual({
      type: "choreography_checkpoint_presented",
      checkpointId: "problem",
      certificateSha256: prepared.event.semantic.certificate.certificateSha256,
      sceneRevision: 1,
      semanticRevision: 1,
      layout: "cinematic",
      resultViewport: prepared.target.viewport,
      settlement: "cancelled_to_checkpoint",
    });
    expect(accepted.scene).toBe(prepared.target.scene);
    expect(accepted.semanticScene).toBe(prepared.target.semanticScene);
    expect(Object.isFrozen(receipt)).toBe(true);
    expect(Object.isFrozen(accepted)).toBe(true);
    expect(() =>
      createAcceptedChoreographyRevision(prepared, {
        kind: "presented",
        receipt: { ...receipt, sceneRevision: 2 },
      }),
    ).toThrow(/requires this checkpoint's terminal presented evaluation/);
    expect(() =>
      createAcceptedChoreographyRevision(prepared, {
        kind: "presented",
        receipt: { ...receipt },
      }),
    ).toThrow(/requires this checkpoint's terminal presented evaluation/);

    expect(
      evaluateChoreographyPresentation(prepared, {
        status: "cancelled_before_presented",
        firstCuePresented: false,
      }),
    ).toEqual({ kind: "not_presented" });
    expect(
      evaluateChoreographyPresentation(prepared, {
        status: "failed",
        firstCuePresented: true,
        error: "late terminal failure",
      }),
    ).toEqual({ kind: "invalid" });
    expect(
      evaluateChoreographyPresentation(prepared, {
        status: "completed",
        firstCuePresented: false,
      }),
    ).toEqual({ kind: "invalid" });
  });

  it("re-prepares and exact-matches the complete eight-checkpoint ledger", () => {
    const original = acceptPrefix(8);
    let frontier = emptyFrontier();

    for (const record of original.records) {
      const replay = prepareChoreographyCheckpoint(
        frontier,
        record.event,
        record.layout,
      );
      expect(choreographyReplayRecordMatches(record, replay)).toBe(true);
      frontier = replay.target;
    }
    expect(frontier.scene).toEqual(original.frontier.scene);
    expect(frontier.semanticScene).toEqual(original.frontier.semanticScene);
    expect(frontier.viewport).toEqual(original.frontier.viewport);
    expect(frontier.certificateHeadSha256).toBe(
      original.frontier.certificateHeadSha256,
    );
  });

  it("preflights the complete ledger before replay and rejects later drift atomically", () => {
    const original = acceptPrefix(8);
    const preflight = preflightChoreographyReplay(original.records);
    expect(preflight.checkpoints).toHaveLength(8);
    expect(preflight.records).toEqual(original.records);
    expect(preflight.frontier).toEqual(original.frontier);

    const corrupted = clone(original.records) as unknown as Record<
      string,
      unknown
    >[];
    nested(corrupted[7], "presentation").certificateSha256 = OTHER_DIGEST;
    const before = clone(corrupted);
    expect(() => preflightChoreographyReplay(corrupted)).toThrow(
      /does not exact-match/,
    );
    expect(corrupted).toEqual(before);
  });

  it.each(["cinematic", "compact"] as const)(
    "preflights the real nine-record adaptive transcript in %s layout",
    (selectedLayout) => {
      const prefix = acceptPrefix(5, selectedLayout);
      let frontier = prefix.frontier;
      const records = [...prefix.records];
      for (const event of adaptiveCheckpointEvents()) {
        const next = accept(frontier, event, selectedLayout);
        records.push(next.accepted);
        frontier = acceptedFrontier(next.accepted);
      }

      expect(
        records.map((record) => record.event.semantic.checkpointId),
      ).toEqual([
        "problem",
        "area_model",
        "split_linear_term",
        "rearrange_halves",
        "missing_corner",
        "corner_detail",
        "balance_and_complete",
        "factor_square",
        "solve_roots",
      ]);
      expect(records.map((record) => record.event.sequence)).toEqual([
        1, 2, 3, 4, 5, 1, 1, 2, 3,
      ]);
      const replay = preflightChoreographyReplay(records);
      expect(replay.frontier.scene.revision).toBe(9);
      expect(replay.frontier.semanticScene.components).toContainEqual({
        kind: "completing_square",
        id: "square-lesson",
        lastMainCheckpoint: "solve_roots",
        cornerClarified: true,
      });
      expect(replay.frontier.layout).toBe(selectedLayout);

      let trace: readonly ChoreographyEvidenceTraceEvent[] = [];
      replay.checkpoints.forEach((prepared, index) => {
        for (const cue of prepared.plan.choreographyPlan.phase.cues) {
          trace = appendChoreographyCueStarted(trace, prepared, cue.cue);
        }
        trace = appendChoreographyFirstCuePresented(trace, prepared);
        trace = appendChoreographyCheckpointSettled(
          trace,
          prepared,
          replay.records[index],
        );
      });
      expect(choreographyEvidenceTraceMatchesAccepted(trace, records)).toBe(
        true,
      );
      expect(trace.length).toBeLessThanOrEqual(
        LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS,
      );
    },
  );

  it.each([
    [
      "selected layout",
      (record: Record<string, unknown>) => (record.layout = "compact"),
    ],
    [
      "low-level target",
      (record: Record<string, unknown>) => {
        nested(record, "scene").revision = 2;
      },
    ],
    [
      "same-revision node order",
      (record: Record<string, unknown>) => {
        const scene = nested(record, "scene");
        scene.nodes = [...(scene.nodes as unknown[])].reverse();
      },
    ],
    [
      "semantic target",
      (record: Record<string, unknown>) => {
        nested(record, "semanticScene").certificateHeadSha256 = OTHER_DIGEST;
      },
    ],
    [
      "semantic predecessor value",
      (record: Record<string, unknown>) => {
        const semanticScene = nested(record, "semanticScene");
        const components = semanticScene.components as Record<
          string,
          unknown
        >[];
        components[0].lastMainCheckpoint = "area_model";
      },
    ],
    [
      "terminal viewport",
      (record: Record<string, unknown>) => {
        nested(record, "viewport").x = 1;
      },
    ],
    [
      "checkpoint event payload",
      (record: Record<string, unknown>) => {
        nested(nested(record, "event"), "patch").narration = "drift";
      },
    ],
    [
      "presentation receipt",
      (record: Record<string, unknown>) => {
        nested(record, "presentation").certificateSha256 = OTHER_DIGEST;
      },
    ],
    [
      "unknown record payload",
      (record: Record<string, unknown>) => {
        record.debug = "model text";
      },
    ],
  ])("rejects replay drift in the %s", (_label, mutate) => {
    const original = acceptPrefix(1);
    const record = clone(original.records[0]) as unknown as Record<
      string,
      unknown
    >;
    mutate(record);
    const replay = prepareChoreographyCheckpoint(
      emptyFrontier(),
      original.records[0].event,
      "cinematic",
    );
    expect(choreographyReplayRecordMatches(record, replay)).toBe(false);
  });
});

describe("closed choreography evidence", () => {
  function preparedFirst(): PreparedChoreographyCheckpoint {
    return prepareChoreographyCheckpoint(
      emptyFrontier(),
      checkpointEvents()[0],
      "cinematic",
    );
  }

  it("records only canonical cue order, presentation, and whole-checkpoint settlement", () => {
    const prepared = preparedFirst();
    let trace: readonly ChoreographyEvidenceTraceEvent[] =
      createChoreographyEvidenceTrace();
    for (const cue of prepared.plan.choreographyPlan.phase.cues) {
      trace = appendChoreographyCueStarted(trace, prepared, cue.cue);
    }
    trace = appendChoreographyFirstCuePresented(trace, prepared);
    const accepted = createAcceptedChoreographyRevision(
      prepared,
      presentedEvaluation(prepared),
    );
    trace = appendChoreographyCheckpointSettled(trace, prepared, accepted);

    expect(trace.map((event) => event.type)).toEqual([
      "cueStarted",
      "cueStarted",
      "firstCuePresented",
      "checkpointSettled",
    ]);
    expect(trace.map((event) => event.ordinal)).toEqual([1, 2, 3, 4]);
    expect(trace.filter((event) => event.type === "cueStarted")).toEqual([
      expect.objectContaining({ cue: "enter" }),
      expect.objectContaining({ cue: "focus" }),
    ]);
    const encoded = JSON.stringify(trace);
    expect(encoded).not.toContain("checkpointNarration");
    expect(encoded).not.toContain("targetIds");
    expect(encoded).not.toContain("square-lesson__");
    expect(Object.isFrozen(trace)).toBe(true);
    expect(trace.every(Object.isFrozen)).toBe(true);
    expect(trace[0]).toEqual(
      expect.objectContaining({
        attempt: prepared.event.attempt,
        certificateSha256:
          prepared.event.semantic.certificate.certificateSha256,
      }),
    );
    expect(choreographyEvidenceTraceMatchesAccepted(trace, [accepted])).toBe(
      true,
    );
  });

  it("rejects cue reordering and presentation before the complete cue-start set", () => {
    const prepared = preparedFirst();
    expect(() => appendChoreographyCueStarted([], prepared, "focus")).toThrow(
      /does not match the next certified cue/,
    );

    const partial = appendChoreographyCueStarted([], prepared, "enter");
    expect(() =>
      appendChoreographyFirstCuePresented(partial, prepared),
    ).toThrow(/exact certified cue-start order/);
  });

  it("rejects arbitrary trace payloads, invalid lifecycle order, and overflow", () => {
    expect(() =>
      createChoreographyEvidenceTrace([
        {
          type: "cueStarted",
          ordinal: 1,
          generation: 1,
          attempt: 1,
          sequence: 1,
          checkpointId: "problem",
          certificateSha256: "a".repeat(64),
          cue: "enter",
          targetId: "square-lesson__foreign",
        },
      ]),
    ).toThrow(/unknown field targetId/);
    expect(() =>
      createChoreographyEvidenceTrace([
        {
          type: "checkpointSettled",
          ordinal: 1,
          generation: 1,
          attempt: 1,
          sequence: 1,
          checkpointId: "problem",
          certificateSha256: "a".repeat(64),
          settlement: "completed",
        },
      ]),
    ).toThrow(/requires firstCuePresented/);

    const oversized = Array.from(
      { length: LIVE_CHOREOGRAPHY_MAX_EVIDENCE_EVENTS + 1 },
      (_, index) => ({
        type: "cueStarted",
        ordinal: index + 1,
        generation: index + 1,
        attempt: 1,
        sequence: 1,
        checkpointId: "problem",
        certificateSha256: "a".repeat(64),
        cue: "enter",
      }),
    );
    expect(() => createChoreographyEvidenceTrace(oversized)).toThrow(
      /evidence trace exceeds/,
    );
  });

  it("rejects noncanonical cues, interleaved groups, and incomplete ledger evidence", () => {
    const base = {
      generation: 1,
      attempt: 1,
      sequence: 1,
      checkpointId: "problem" as const,
      certificateSha256: "a".repeat(64),
    };
    expect(() =>
      createChoreographyEvidenceTrace([
        { type: "cueStarted", ordinal: 1, ...base, cue: "focus" },
        { type: "cueStarted", ordinal: 2, ...base, cue: "enter" },
      ]),
    ).toThrow(/noncanonical/);
    expect(() =>
      createChoreographyEvidenceTrace([
        { type: "cueStarted", ordinal: 1, ...base, cue: "enter" },
        {
          type: "cueStarted",
          ordinal: 2,
          ...base,
          sequence: 2,
          certificateSha256: "b".repeat(64),
          cue: "enter",
        },
      ]),
    ).toThrow(/cannot interleave/);

    const original = acceptPrefix(1);
    const prepared = original.prepared[0];
    let partial: readonly ChoreographyEvidenceTraceEvent[] = [];
    for (const cue of prepared.plan.choreographyPlan.phase.cues) {
      partial = appendChoreographyCueStarted(partial, prepared, cue.cue);
    }
    partial = appendChoreographyFirstCuePresented(partial, prepared);
    expect(
      choreographyEvidenceTraceMatchesAccepted(partial, original.records),
    ).toBe(false);
    const accepted = original.records[0];
    const insufficient = createChoreographyEvidenceTrace([
      partial[0],
      { ...partial[partial.length - 1], ordinal: 2 },
    ]);
    expect(() =>
      appendChoreographyCheckpointSettled(insufficient, prepared, accepted),
    ).toThrow(/exact certified cue set/);
  });

  it("discards only an unaccepted active suffix so a retry can start", () => {
    const first = preparedFirst();
    let settled: readonly ChoreographyEvidenceTraceEvent[] = [];
    for (const cue of first.plan.choreographyPlan.phase.cues) {
      settled = appendChoreographyCueStarted(settled, first, cue.cue);
    }
    settled = appendChoreographyFirstCuePresented(settled, first);
    const accepted = createAcceptedChoreographyRevision(
      first,
      presentedEvaluation(first),
    );
    settled = appendChoreographyCheckpointSettled(settled, first, accepted);

    const second = prepareChoreographyCheckpoint(
      acceptedFrontier(accepted),
      checkpointEvents()[1],
      "cinematic",
    );
    const withOpenSuffix = appendChoreographyCueStarted(
      settled,
      second,
      second.plan.choreographyPlan.phase.cues[0].cue,
    );
    const cleaned = discardUnacceptedChoreographyEvidence(
      withOpenSuffix,
      second,
      { status: "cancelled_before_presented", firstCuePresented: false },
    );
    expect(cleaned).toEqual(settled);

    const retryEvent = clone(checkpointEvents()[1]) as unknown as Record<
      string,
      unknown
    >;
    retryEvent.attempt = 2;
    const retry = prepareChoreographyCheckpoint(
      acceptedFrontier(accepted),
      retryEvent,
      "cinematic",
    );
    expect(() =>
      appendChoreographyCueStarted(
        cleaned,
        retry,
        retry.plan.choreographyPlan.phase.cues[0].cue,
      ),
    ).not.toThrow();

    let failedSuffix = withOpenSuffix;
    for (const cue of second.plan.choreographyPlan.phase.cues.slice(1)) {
      failedSuffix = appendChoreographyCueStarted(
        failedSuffix,
        second,
        cue.cue,
      );
    }
    failedSuffix = appendChoreographyFirstCuePresented(failedSuffix, second);
    expect(
      discardUnacceptedChoreographyEvidence(failedSuffix, second, {
        status: "failed",
        firstCuePresented: true,
        error: "terminal rollback",
      }),
    ).toEqual(settled);
  });
});
