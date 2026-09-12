import type { ChoreographyLayout, SceneState } from "@/lib/live-scene";
import {
  MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  decodePairedProjectileComparisonSpecV1,
  decodeSemanticStoryboardRecordV1,
  decodeSemanticStoryboardRequestV1,
  type AcceptedSemanticStoryboardRecordV1,
  type PairedProjectileComparisonSpecV1,
  type ProjectileStoryboardSemanticSceneStateV1,
  type SemanticStoryboardRecordV1,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import {
  SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
  decodeSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardSceneCheckpointEventV1,
  type SemanticStoryboardSceneStreamCompletedEventV1,
  type SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  prepareSemanticStoryboardCheckpoint,
  type SemanticStoryboardFrontier,
} from "./semantic-storyboard-playback";

export type SemanticStoryboardFixtureErrorCode =
  "invalid_fixture" | "request_mismatch";

export class SemanticStoryboardFixtureError extends Error {
  readonly code: SemanticStoryboardFixtureErrorCode;

  constructor(code: SemanticStoryboardFixtureErrorCode, message: string) {
    super(message);
    this.name = "SemanticStoryboardFixtureError";
    this.code = code;
  }
}

type UnknownRecord = Record<string, unknown>;
export type SemanticStoryboardFixtureLaneKind =
  "anchor" | "program" | "continuation" | "sole_abstain" | "accepted_prefix";

interface FrontierSummary {
  readonly revision: number;
  readonly acceptedRecords: readonly AcceptedSemanticStoryboardRecordV1[];
  readonly programSha256: string;
  readonly semanticSceneSha256: string;
  readonly certificateHeadSha256: string | null;
}

export interface DecodedSemanticStoryboardFixtureLane {
  readonly kind: SemanticStoryboardFixtureLaneKind;
  readonly scenarioId: string;
  readonly request: SemanticStoryboardRequestV1;
  readonly providerRecords: readonly SemanticStoryboardRecordV1[];
  readonly events: readonly SemanticStoryboardSceneStreamEventV1[];
  readonly checkpoints: readonly SemanticStoryboardSceneCheckpointEventV1[];
  readonly checkpointIds: readonly string[];
  readonly resultScene: SceneState;
  readonly resultSemanticScene: ProjectileStoryboardSemanticSceneStateV1;
  readonly resultFrontiers: Readonly<
    Record<ChoreographyLayout, SemanticStoryboardFrontier>
  >;
  readonly programId?: string;
  readonly fromProgramId?: string;
  readonly fromPrefixCount?: number;
}

const FIXTURE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const LAYOUTS = Object.freeze([
  "cinematic",
  "compact",
] as const satisfies readonly ChoreographyLayout[]);

export function failSemanticStoryboardFixture(
  code: SemanticStoryboardFixtureErrorCode,
  message: string,
): never {
  throw new SemanticStoryboardFixtureError(code, message);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must be an object`,
    );
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must be a plain object`,
    );
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  expectedKeys: readonly string[],
  field: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must contain exactly ${expected.join(", ")}`,
    );
  }
}

export function sameSemanticStoryboardFixtureValue(
  left: unknown,
  right: unknown,
): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) =>
        sameSemanticStoryboardFixtureValue(item, right[index]),
      )
    );
  }
  if (
    typeof left !== "object" ||
    left === null ||
    typeof right !== "object" ||
    right === null
  ) {
    return false;
  }
  const leftRecord = left as UnknownRecord;
  const rightRecord = right as UnknownRecord;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        sameSemanticStoryboardFixtureValue(leftRecord[key], rightRecord[key]),
    )
  );
}

function safeInteger(value: unknown, field: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must be a safe integer at least ${minimum}`,
    );
  }
  return value as number;
}

function identifier(value: unknown, field: string): string {
  if (typeof value !== "string" || !FIXTURE_ID_PATTERN.test(value)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an unsafe identifier`,
    );
  }
  return value;
}

function digest(value: unknown, field: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must be a lowercase SHA-256 digest`,
    );
  }
  return value;
}

function nullableDigest(value: unknown, field: string): string | null {
  return value === null ? null : digest(value, field);
}

function acceptedRecord(
  value: unknown,
  field: string,
): AcceptedSemanticStoryboardRecordV1 {
  const decoded = decodeSemanticStoryboardRecordV1(value);
  if (decoded.act === "abstain") {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} cannot contain abstain`,
    );
  }
  return decoded;
}

function semanticRecords(
  semanticScene: ProjectileStoryboardSemanticSceneStateV1,
): readonly AcceptedSemanticStoryboardRecordV1[] {
  return semanticScene.components[0]?.acceptedRecords ?? [];
}

function decodeFrontierSummary(
  value: unknown,
  semanticScene: ProjectileStoryboardSemanticSceneStateV1,
  field: string,
): FrontierSummary {
  const input = record(value, field);
  exactKeys(
    input,
    [
      "revision",
      "acceptedRecords",
      "programSha256",
      "semanticSceneSha256",
      "certificateHeadSha256",
    ],
    field,
  );
  if (!Array.isArray(input.acceptedRecords)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} acceptedRecords must be an array`,
    );
  }
  const acceptedRecords = Object.freeze(
    input.acceptedRecords.map((item, index) =>
      acceptedRecord(item, `${field} acceptedRecords[${index}]`),
    ),
  );
  const revision = safeInteger(input.revision, `${field} revision`);
  const certificateHeadSha256 = nullableDigest(
    input.certificateHeadSha256,
    `${field} certificateHeadSha256`,
  );
  if (
    revision !== semanticScene.revision ||
    certificateHeadSha256 !== (semanticScene.certificateHeadSha256 ?? null) ||
    !sameSemanticStoryboardFixtureValue(
      acceptedRecords,
      semanticRecords(semanticScene),
    )
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} does not describe its strict semantic scene`,
    );
  }
  return Object.freeze({
    revision,
    acceptedRecords,
    programSha256: digest(input.programSha256, `${field} programSha256`),
    semanticSceneSha256: digest(
      input.semanticSceneSha256,
      `${field} semanticSceneSha256`,
    ),
    certificateHeadSha256,
  });
}

function initialFrontier(
  request: SemanticStoryboardRequestV1,
  first: SemanticStoryboardSceneCheckpointEventV1,
  layout: ChoreographyLayout,
): SemanticStoryboardFrontier {
  const isEmpty = request.baseScene.revision === 0;
  return Object.freeze({
    scene: request.baseScene,
    semanticScene: request.baseSemanticScene,
    viewport: isEmpty
      ? null
      : first.transition.checkpoint.presentation.baseViewports[layout],
    layout: isEmpty ? null : layout,
    certificateHeadSha256:
      request.baseSemanticScene.certificateHeadSha256 ?? null,
  });
}

function materialize(
  request: SemanticStoryboardRequestV1,
  checkpoints: readonly SemanticStoryboardSceneCheckpointEventV1[],
  layout: ChoreographyLayout,
): SemanticStoryboardFrontier {
  const first = checkpoints[0];
  if (!first) {
    return Object.freeze({
      scene: request.baseScene,
      semanticScene: request.baseSemanticScene,
      viewport: null,
      layout: null,
      certificateHeadSha256:
        request.baseSemanticScene.certificateHeadSha256 ?? null,
    });
  }
  return checkpoints.reduce<SemanticStoryboardFrontier>(
    (frontier, checkpoint) =>
      prepareSemanticStoryboardCheckpoint(frontier, checkpoint, layout).target,
    initialFrontier(request, first, layout),
  );
}

function requireCompleted(
  terminal: SemanticStoryboardSceneStreamEventV1,
  reasonCode: SemanticStoryboardSceneStreamCompletedEventV1["reasonCode"],
  field: string,
): SemanticStoryboardSceneStreamCompletedEventV1 {
  if (
    terminal.type !== "semantic_storyboard_scene_stream_completed" ||
    terminal.reasonCode !== reasonCode
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} must end with ${reasonCode} completion`,
    );
  }
  return terminal;
}

function laneKeys(kind: SemanticStoryboardFixtureLaneKind): readonly string[] {
  const shared = [
    "scenarioId",
    "generation",
    "routingMode",
    "prompt",
    "providerRecords",
    "fakeProviderStreamCount",
    "tailOutcome",
    "baseScene",
    "baseSemanticScene",
    "baseFrontier",
    "checkpointIds",
    "checkpointCount",
    "events",
    "expectedTerminal",
  ];
  if (kind === "program") return [...shared, "programId"];
  if (kind === "continuation") {
    return [...shared, "fromProgramId", "fromPrefixCount"];
  }
  return shared;
}

function decodeLane(
  value: unknown,
  problemSpec: PairedProjectileComparisonSpecV1,
  kind: SemanticStoryboardFixtureLaneKind,
  field: string,
): DecodedSemanticStoryboardFixtureLane {
  const input = record(value, field);
  exactKeys(input, laneKeys(kind), field);
  const scenarioId = identifier(input.scenarioId, `${field} scenarioId`);
  const generation = safeInteger(input.generation, `${field} generation`, 1);
  const routingMode = kind === "anchor" ? "reflex" : "director";
  if (input.routingMode !== routingMode) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has the wrong routingMode`,
    );
  }
  if (
    (routingMode === "reflex" && input.prompt !== null) ||
    (routingMode === "director" && typeof input.prompt !== "string")
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an invalid prompt`,
    );
  }
  const request = decodeSemanticStoryboardRequestV1({
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    routingMode,
    problemSpec,
    generation,
    baseScene: input.baseScene,
    baseSemanticScene: input.baseSemanticScene,
    ...(routingMode === "director" ? { prompt: input.prompt } : {}),
  });
  const baseSummary = decodeFrontierSummary(
    input.baseFrontier,
    request.baseSemanticScene,
    `${field} baseFrontier`,
  );
  if (!Array.isArray(input.providerRecords)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} providerRecords must be an array`,
    );
  }
  if (input.providerRecords.length > MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has too many provider records`,
    );
  }
  const providerRecords = Object.freeze(
    input.providerRecords.map((item) => decodeSemanticStoryboardRecordV1(item)),
  );
  const expectedFakeStreams = routingMode === "reflex" ? 0 : 1;
  if (
    safeInteger(
      input.fakeProviderStreamCount,
      `${field} fakeProviderStreamCount`,
    ) !== expectedFakeStreams
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an invalid fake stream count`,
    );
  }
  const expectedTail = kind === "accepted_prefix" ? "malformed_json" : null;
  if (input.tailOutcome !== expectedTail) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an invalid tail outcome`,
    );
  }
  if (!Array.isArray(input.events) || input.events.length < 2) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} events must be a lifecycle array`,
    );
  }
  const events = Object.freeze(
    input.events.map((event) =>
      decodeSemanticStoryboardSceneStreamEventV1(event),
    ),
  );
  if (
    events.some(
      (event) => event.generation !== generation || event.attempt !== 1,
    ) ||
    events[0].type !== "semantic_storyboard_scene_stream_started" ||
    events[0].baseRevision !== request.baseScene.revision
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an invalid stream start`,
    );
  }
  const terminal = events.at(-1)!;
  const middle = events.slice(1, -1);
  if (
    middle.some(
      (event) => event.type !== "semantic_storyboard_scene_checkpoint",
    )
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} contains a non-checkpoint body event`,
    );
  }
  const checkpoints = Object.freeze(
    middle as readonly SemanticStoryboardSceneCheckpointEventV1[],
  );
  if (
    safeInteger(input.checkpointCount, `${field} checkpointCount`) !==
    checkpoints.length
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} checkpointCount disagrees`,
    );
  }
  if (!Array.isArray(input.checkpointIds)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} checkpointIds must be an array`,
    );
  }
  const checkpointIds = Object.freeze(
    input.checkpointIds.map((item, index) =>
      identifier(item, `${field} checkpointIds[${index}]`),
    ),
  );
  if (
    !sameSemanticStoryboardFixtureValue(
      checkpointIds,
      checkpoints.map(
        (checkpoint) => checkpoint.transition.checkpoint.checkpointId,
      ),
    ) ||
    checkpoints.some((checkpoint, index) => checkpoint.sequence !== index + 1)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} checkpoint order disagrees`,
    );
  }

  if (kind === "anchor") {
    const completed = requireCompleted(terminal, "anchor", field);
    if (
      providerRecords.length !== 0 ||
      checkpoints.length !== 1 ||
      checkpoints[0].transition.checkpoint.checkpointOrigin !== "anchor" ||
      checkpoints[0].transition.checkpoint.beat !== null ||
      completed.acceptedPrefixCause !== null
    ) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `${field} is not one provider-free anchor`,
      );
    }
  } else if (kind === "sole_abstain") {
    if (
      providerRecords.length !== 1 ||
      providerRecords[0].act !== "abstain" ||
      checkpoints.length !== 0 ||
      terminal.type !== "semantic_storyboard_scene_stream_declined" ||
      terminal.reasonCode !== providerRecords[0].reasonCode
    ) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `${field} is not one clean sole abstention`,
      );
    }
  } else {
    const completed = requireCompleted(
      terminal,
      kind === "accepted_prefix" ? "accepted_prefix" : "model_stop",
      field,
    );
    if (
      providerRecords.some((item) => item.act === "abstain") ||
      providerRecords.length !== checkpoints.length ||
      (kind === "accepted_prefix"
        ? completed.acceptedPrefixCause !== "invalid_model_stream"
        : completed.acceptedPrefixCause !== null)
    ) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `${field} has an invalid Director terminal`,
      );
    }
    for (const [index, checkpoint] of checkpoints.entries()) {
      const compiled = checkpoint.transition.checkpoint;
      if (
        compiled.checkpointOrigin !== "model_record" ||
        compiled.beat === null ||
        !sameSemanticStoryboardFixtureValue(
          compiled.beat.record,
          providerRecords[index],
        )
      ) {
        return failSemanticStoryboardFixture(
          "invalid_fixture",
          `${field} checkpoint does not expose its exact provider record`,
        );
      }
    }
  }

  if (
    terminal.type === "semantic_storyboard_scene_stream_completed" &&
    (terminal.baseRevision !== request.baseScene.revision ||
      terminal.checkpointCount !== checkpoints.length ||
      terminal.firstCheckpointMs !== 0 ||
      terminal.totalMs !== 0)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has a non-deterministic completion`,
    );
  }
  if (
    terminal.type === "semantic_storyboard_scene_stream_declined" &&
    (terminal.baseRevision !== request.baseScene.revision ||
      terminal.finalRevision !== request.baseScene.revision)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} decline mutated its base`,
    );
  }

  const resultFrontiers = Object.freeze(
    Object.fromEntries(
      LAYOUTS.map((layout) => [
        layout,
        materialize(request, checkpoints, layout),
      ]),
    ) as Record<ChoreographyLayout, SemanticStoryboardFrontier>,
  );
  const result = resultFrontiers.cinematic;
  if (
    !sameSemanticStoryboardFixtureValue(
      result.scene,
      resultFrontiers.compact.scene,
    ) ||
    !sameSemanticStoryboardFixtureValue(
      result.semanticScene,
      resultFrontiers.compact.semanticScene,
    )
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} layouts reach different frontiers`,
    );
  }
  const expected = record(input.expectedTerminal, `${field} expectedTerminal`);
  exactKeys(
    expected,
    ["scene", "semanticScene", "frontier"],
    `${field} expectedTerminal`,
  );
  if (
    !sameSemanticStoryboardFixtureValue(expected.scene, result.scene) ||
    !sameSemanticStoryboardFixtureValue(
      expected.semanticScene,
      result.semanticScene,
    )
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} expected terminal disagrees`,
    );
  }
  const resultSummary = decodeFrontierSummary(
    expected.frontier,
    result.semanticScene,
    `${field} expectedTerminal frontier`,
  );
  if (
    terminal.type === "semantic_storyboard_scene_stream_completed" &&
    terminal.finalRevision !== result.scene.revision
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} completion revision disagrees`,
    );
  }
  const firstBody = checkpoints[0]?.transition.checkpoint.certificate.body;
  const lastBody = checkpoints.at(-1)?.transition.checkpoint.certificate.body;
  if (
    firstBody &&
    (firstBody.baseProgramSha256 !== baseSummary.programSha256 ||
      firstBody.baseSemanticSceneSha256 !== baseSummary.semanticSceneSha256)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} base digest bindings disagree`,
    );
  }
  if (
    lastBody &&
    (lastBody.resultProgramSha256 !== resultSummary.programSha256 ||
      lastBody.resultSemanticSceneSha256 !== resultSummary.semanticSceneSha256)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} result digest bindings disagree`,
    );
  }
  if (
    !lastBody &&
    !sameSemanticStoryboardFixtureValue(baseSummary, resultSummary)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} no-op frontier changed`,
    );
  }

  const programId =
    kind === "program"
      ? identifier(input.programId, `${field} programId`)
      : undefined;
  const fromProgramId =
    kind === "continuation"
      ? identifier(input.fromProgramId, `${field} fromProgramId`)
      : undefined;
  const fromPrefixCount =
    kind === "continuation"
      ? safeInteger(input.fromPrefixCount, `${field} fromPrefixCount`)
      : undefined;
  if (
    (kind === "anchor" && scenarioId !== "anchor") ||
    (kind === "program" && programId !== scenarioId) ||
    (kind === "continuation" &&
      scenarioId !==
        `continue_${fromProgramId}_prefix_${String(fromPrefixCount)}`) ||
    (kind === "sole_abstain" && scenarioId !== "sole_abstain") ||
    (kind === "accepted_prefix" &&
      scenarioId !== "accepted_prefix_malformed_tail")
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} has an inconsistent story identity`,
    );
  }
  return Object.freeze({
    kind,
    scenarioId,
    request,
    providerRecords,
    events,
    checkpoints,
    checkpointIds,
    resultScene: result.scene,
    resultSemanticScene: result.semanticScene,
    resultFrontiers,
    ...(programId ? { programId } : {}),
    ...(fromProgramId ? { fromProgramId } : {}),
    ...(fromPrefixCount === undefined ? {} : { fromPrefixCount }),
  });
}

export interface DecodedSemanticStoryboardFixtureEnvelope {
  readonly fixtureId: string;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly fakeProviderStreamCount: number;
  readonly anchor: DecodedSemanticStoryboardFixtureLane;
  readonly programs: readonly DecodedSemanticStoryboardFixtureLane[];
  readonly continuations: readonly DecodedSemanticStoryboardFixtureLane[];
  readonly soleAbstain?: DecodedSemanticStoryboardFixtureLane;
  readonly acceptedPrefix?: DecodedSemanticStoryboardFixtureLane;
}

/** Strictly decode fixture-owned metadata and each lane before qualification. */
export function decodeSemanticStoryboardFixtureEnvelope(
  value: unknown,
): DecodedSemanticStoryboardFixtureEnvelope {
  const input = record(value, "fixture");
  exactKeys(
    input,
    [
      "v",
      "fixtureId",
      "protocol",
      "compilerVersion",
      "scenario",
      "problemSpec",
      "coverage",
      "externalProviderRequestCount",
      "fakeProviderStreamCount",
      "anchor",
      "programs",
      "continuations",
      "soleAbstain",
      "acceptedPrefixMalformedTail",
    ],
    "fixture",
  );
  if (
    input.v !== 1 ||
    input.protocol !== PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL ||
    input.compilerVersion !== SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION ||
    input.scenario !== "qualified_semantic_storyboard" ||
    input.externalProviderRequestCount !== 0
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture qualification metadata is invalid",
    );
  }

  const fixtureId = identifier(input.fixtureId, "fixture fixtureId");
  const problemSpec = decodePairedProjectileComparisonSpecV1(input.problemSpec);
  const expectedFixtureId = `semantic-storyboard-v${problemSpec.speedMps}-a${problemSpec.anglesDeg[0]}-a${problemSpec.anglesDeg[1]}`;
  if (fixtureId !== expectedFixtureId) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixtureId does not bind its problem",
    );
  }

  const coverage = record(input.coverage, "fixture coverage");
  exactKeys(
    coverage,
    ["anglePair", "isComplementary", "expectedRangeRelation"],
    "fixture coverage",
  );
  const complementary =
    problemSpec.anglesDeg[0] + problemSpec.anglesDeg[1] === 90;
  if (
    !sameSemanticStoryboardFixtureValue(
      coverage.anglePair,
      problemSpec.anglesDeg,
    ) ||
    coverage.isComplementary !== complementary ||
    coverage.expectedRangeRelation !==
      (complementary ? "equal_range" : "unequal_range")
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture coverage does not match its problem",
    );
  }
  if (!Array.isArray(input.programs) || !Array.isArray(input.continuations)) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture programs and continuations must be arrays",
    );
  }

  const anchor = decodeLane(
    input.anchor,
    problemSpec,
    "anchor",
    "fixture anchor",
  );
  const programs = Object.freeze(
    input.programs.map((lane, index) =>
      decodeLane(lane, problemSpec, "program", `fixture programs[${index}]`),
    ),
  );
  const continuations = Object.freeze(
    input.continuations.map((lane, index) =>
      decodeLane(
        lane,
        problemSpec,
        "continuation",
        `fixture continuations[${index}]`,
      ),
    ),
  );
  const soleAbstain =
    input.soleAbstain === null
      ? undefined
      : decodeLane(
          input.soleAbstain,
          problemSpec,
          "sole_abstain",
          "fixture soleAbstain",
        );
  const acceptedPrefix =
    input.acceptedPrefixMalformedTail === null
      ? undefined
      : decodeLane(
          input.acceptedPrefixMalformedTail,
          problemSpec,
          "accepted_prefix",
          "fixture acceptedPrefixMalformedTail",
        );

  return Object.freeze({
    fixtureId,
    problemSpec,
    fakeProviderStreamCount: safeInteger(
      input.fakeProviderStreamCount,
      "fixture fakeProviderStreamCount",
    ),
    anchor,
    programs,
    continuations,
    ...(soleAbstain ? { soleAbstain } : {}),
    ...(acceptedPrefix ? { acceptedPrefix } : {}),
  });
}
