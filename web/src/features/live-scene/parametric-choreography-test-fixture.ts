import { CHECKPOINT_COMPILER_V3_VERSION } from "@/lib/live-scene/parametric-checkpoint";
import {
  decodeParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneCheckpointEventV3,
  type ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";

import fixtureValue from "./fixtures/completing-the-square.v1.json";

type MutableRecord = Record<string, unknown>;

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function record(value: unknown): MutableRecord {
  return value as MutableRecord;
}

const PROBLEM = Object.freeze({
  v: 1 as const,
  linearCoefficient: 6,
  rightHandSide: 7,
});
const PROBLEM_SHA256 = "6".repeat(64);

function parametricCheckpoint(
  legacy: unknown,
  generation = 1,
  sequence = 1,
  attempt = 1,
): ParametricChoreographySceneCheckpointEventV3 {
  const event = record(clone(legacy));
  const semantic = record(event.semantic);
  const legacyBeat = record(semantic.beat);
  const legacyResult = record(semantic.resultComponent);
  const receipt = record(semantic.receipt);
  const certificate = record(semantic.certificate);
  const body = record(certificate.body);

  event.type = "parametric_choreography_scene_checkpoint";
  event.generation = generation;
  event.sequence = sequence;
  event.attempt = attempt;
  semantic.problemSpec = PROBLEM;
  semantic.beat = {
    ...legacyBeat,
    v: 3,
    componentKind: "completing_square_parametric",
    problemSpec: PROBLEM,
  };
  semantic.resultComponent = {
    ...legacyResult,
    kind: "completing_square_parametric",
    problemSpec: PROBLEM,
  };
  semantic.semanticBaseCertificateSha256 = body.previousCertificateSha256;
  semantic.semanticResultCertificateSha256 = certificate.certificateSha256;
  semantic.receipt = {
    ...receipt,
    componentKind: "completing_square_parametric",
    problemSpecSha256: PROBLEM_SHA256,
  };
  body.v = 3;
  body.compilerVersion = CHECKPOINT_COMPILER_V3_VERSION;
  body.componentKind = "completing_square_parametric";
  body.problemSpecSha256 = PROBLEM_SHA256;

  const decoded = decodeParametricChoreographySceneStreamEventV3(event);
  if (decoded.type !== "parametric_choreography_scene_checkpoint") {
    throw new TypeError("Fixture did not decode as a checkpoint");
  }
  return decoded;
}

export function createParametricCheckpointFixture(
  index: number,
  generation = 1,
  sequence = index + 1,
  attempt = 1,
): ParametricChoreographySceneCheckpointEventV3 {
  const legacy = fixtureValue.events.filter(
    (event) => event.type === "choreography_scene_checkpoint",
  )[index];
  if (!legacy) throw new RangeError(`No checkpoint fixture at index ${index}`);
  return parametricCheckpoint(legacy, generation, sequence, attempt);
}

export function createParametricAdaptiveCheckpointFixture(
  index: number,
  generation: number,
  sequence = index + 1,
): ParametricChoreographySceneCheckpointEventV3 {
  const legacy = fixtureValue.adaptiveTranscript.events.filter(
    (event) => event.type === "choreography_scene_checkpoint",
  )[index];
  if (!legacy) {
    throw new RangeError(`No adaptive checkpoint fixture at index ${index}`);
  }
  return parametricCheckpoint(legacy, generation, sequence);
}

export function createParametricLifecycleFixture(
  count: number,
  generation = 1,
): readonly ParametricChoreographySceneStreamEventV3[] {
  if (!Number.isSafeInteger(count) || count < 1 || count > 8) {
    throw new RangeError("count must be between 1 and 8");
  }
  return Object.freeze([
    decodeParametricChoreographySceneStreamEventV3({
      type: "scene_stream_started",
      generation,
      attempt: 1,
      baseRevision: 0,
    }),
    ...Array.from({ length: count }, (_, index) =>
      createParametricCheckpointFixture(index, generation),
    ),
    decodeParametricChoreographySceneStreamEventV3({
      type: "scene_stream_completed",
      generation,
      finalRevision: count,
      patchCount: count,
      firstPatchMs: 12,
      totalMs: 24,
      repaired: false,
    }),
  ]);
}
