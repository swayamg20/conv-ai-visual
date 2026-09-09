import type { RoutedChoreographyRouteV2 } from "./choreography";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type CompletingSquareCheckpointId,
} from "./checkpoint";
import {
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
} from "./patch";
import {
  PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
  decodeParametricChoreographyRoute,
  decodeParametricCompletingSquareStateV1,
  type ParametricCompletingSquareStateV1,
} from "./parametric-choreography";
import {
  createSemanticSceneState,
  type PythagoreanAreaIdentityState,
  type SemanticSceneState,
} from "./semantic";
import { createSceneState } from "./state";
import type { SceneNode, SceneState } from "./types";

export interface LegacyCompletingSquareStateV2 {
  readonly kind: "completing_square";
  readonly id: string;
  readonly lastMainCheckpoint: Exclude<
    CompletingSquareCheckpointId,
    "corner_detail"
  > | null;
  readonly cornerClarified: boolean;
}

export type ParametricBaseSemanticComponent =
  | PythagoreanAreaIdentityState
  | LegacyCompletingSquareStateV2
  | ParametricCompletingSquareStateV1;

export interface ParametricBaseSemanticSceneState {
  readonly revision: number;
  readonly components: readonly ParametricBaseSemanticComponent[];
  readonly certificateHeadSha256?: string | null;
}

interface ParametricChoreographyRequestBaseV3 {
  readonly protocol: typeof PARAMETRIC_CHOREOGRAPHY_PROTOCOL;
  readonly problemText: string | null;
  readonly generation: number;
  readonly baseScene: SceneState;
  readonly baseSemanticScene: ParametricBaseSemanticSceneState;
}

export interface ParametricChoreographyReflexRequestV3
  extends ParametricChoreographyRequestBaseV3 {
  readonly routingMode: "reflex";
  readonly requestedRoute: RoutedChoreographyRouteV2;
}

export interface ParametricChoreographyDirectorRequestV3
  extends ParametricChoreographyRequestBaseV3 {
  readonly routingMode: "director";
  readonly prompt: string;
}

export type ParametricChoreographyRequestV3 =
  | ParametricChoreographyReflexRequestV3
  | ParametricChoreographyDirectorRequestV3;

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `parametric choreography request ${message}`,
  );
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(`${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail(`${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[],
  field: string,
): void {
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`${field} is missing field ${key}`);
  }
}

function integer(value: unknown, field: string, minimum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    fail(`${field} must be a safe integer at least ${minimum}`);
  }
  return value as number;
}

function boundedText(
  value: unknown,
  field: string,
  nullable: boolean,
): string | null {
  if (nullable && value === null) return null;
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value.trim()].length > 2_000
  ) {
    fail(`${field} must be ${nullable ? "null or " : ""}a non-empty string of at most 2000 characters`);
  }
  return value.trim();
}

const COMPONENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const MAIN_CHECKPOINTS = COMPLETING_SQUARE_CHECKPOINT_IDS.filter(
  (checkpoint): checkpoint is Exclude<CompletingSquareCheckpointId, "corner_detail"> =>
    checkpoint !== "corner_detail",
);

function componentId(value: unknown, field: string): string {
  if (typeof value !== "string" || !COMPONENT_ID_PATTERN.test(value)) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

function decodeBaseScene(value: unknown): SceneState {
  const input = record(value, "baseScene");
  exactKeys(input, ["revision", "nodes"], [], "baseScene");
  const revision = integer(input.revision, "baseScene revision", 0);
  if (!Array.isArray(input.nodes)) fail("baseScene nodes must be an array");
  if (input.nodes.length > LIVE_SCENE_MAX_NODES) {
    throw new LiveSceneProtocolError(
      "budget_exceeded",
      `parametric choreography request baseScene exceeds ${LIVE_SCENE_MAX_NODES} nodes`,
    );
  }

  const nodes: SceneNode[] = [];
  for (
    let offset = 0;
    offset < input.nodes.length;
    offset += LIVE_SCENE_MAX_PATCH_OPERATIONS
  ) {
    const chunk = input.nodes.slice(offset, offset + LIVE_SCENE_MAX_PATCH_OPERATIONS);
    const decoded = decodeScenePatchDraft({
      v: 1,
      patchId: `browserValidation_${offset}`,
      narration: "Validate the accepted browser scene.",
      operations: chunk.map((node) => ({ op: "put", node })),
    });
    for (const operation of decoded.operations) {
      if (operation.op !== "put") return fail("baseScene node validation failed");
      nodes.push(operation.node);
    }
  }
  return createSceneState({ revision, nodes });
}

function decodeLegacyCompletingSquareState(
  value: unknown,
): LegacyCompletingSquareStateV2 {
  const input = record(value, "base semantic component");
  exactKeys(
    input,
    ["kind", "id", "lastMainCheckpoint", "cornerClarified"],
    [],
    "base semantic component",
  );
  if (input.kind !== "completing_square") {
    fail("base semantic component kind must equal completing_square");
  }
  const lastMainCheckpoint =
    input.lastMainCheckpoint === null
      ? null
      : MAIN_CHECKPOINTS.find(
          (checkpoint) => checkpoint === input.lastMainCheckpoint,
        );
  if (lastMainCheckpoint === undefined) {
    fail("base semantic component lastMainCheckpoint is unsupported");
  }
  if (typeof input.cornerClarified !== "boolean") {
    fail("base semantic component cornerClarified must be a boolean");
  }
  if (
    input.cornerClarified &&
    (lastMainCheckpoint === null ||
      MAIN_CHECKPOINTS.indexOf(lastMainCheckpoint) <
        MAIN_CHECKPOINTS.indexOf("missing_corner"))
  ) {
    fail("base semantic component cornerClarified requires missing_corner");
  }
  return Object.freeze({
    kind: "completing_square",
    id: componentId(input.id, "base semantic component id"),
    lastMainCheckpoint,
    cornerClarified: input.cornerClarified,
  });
}

function decodePythagoreanState(value: unknown): PythagoreanAreaIdentityState {
  const decoded = createSemanticSceneState({
    revision: 0,
    components: [value],
  } as unknown as SemanticSceneState);
  return decoded.components[0];
}

function decodeBaseSemanticScene(
  value: unknown,
): ParametricBaseSemanticSceneState {
  const input = record(value, "baseSemanticScene");
  exactKeys(
    input,
    ["revision", "components"],
    ["certificateHeadSha256"],
    "baseSemanticScene",
  );
  const revision = integer(
    input.revision,
    "baseSemanticScene revision",
    0,
  );
  if (!Array.isArray(input.components)) {
    fail("baseSemanticScene components must be an array");
  }
  if (input.components.length > LIVE_SCENE_MAX_NODES) {
    throw new LiveSceneProtocolError(
      "budget_exceeded",
      `parametric choreography request baseSemanticScene exceeds ${LIVE_SCENE_MAX_NODES} components`,
    );
  }
  const components = input.components.map((component) => {
    const candidate = record(component, "base semantic component");
    if (candidate.kind === "completing_square_parametric") {
      return decodeParametricCompletingSquareStateV1(candidate);
    }
    if (candidate.kind === "completing_square") {
      return decodeLegacyCompletingSquareState(candidate);
    }
    if (candidate.kind === "pythagorean_area_identity") {
      return decodePythagoreanState(candidate);
    }
    return fail("base semantic component kind is unsupported");
  });
  const ids = new Set<string>();
  for (const component of components) {
    if (ids.has(component.id)) {
      fail(`baseSemanticScene component id ${component.id} is duplicated`);
    }
    ids.add(component.id);
  }

  const hasCertificateHead = Object.hasOwn(input, "certificateHeadSha256");
  const rawCertificateHead = input.certificateHeadSha256;
  let certificateHeadSha256: string | null | undefined;
  if (hasCertificateHead) {
    if (rawCertificateHead === null) {
      certificateHeadSha256 = null;
    } else if (
      typeof rawCertificateHead === "string" &&
      SHA256_PATTERN.test(rawCertificateHead)
    ) {
      certificateHeadSha256 = rawCertificateHead;
    } else {
      fail("baseSemanticScene certificateHeadSha256 must be null or a lowercase SHA-256 digest");
    }
  }
  return Object.freeze({
    revision,
    components: Object.freeze(components),
    ...(hasCertificateHead ? { certificateHeadSha256 } : {}),
  });
}

/** Decode either exact V3 request mode before it crosses the browser wire. */
export function decodeParametricChoreographyRequestV3(
  value: unknown,
): ParametricChoreographyRequestV3 {
  const input = record(value, "request");
  const shared = [
    "protocol",
    "routingMode",
    "problemText",
    "generation",
    "baseScene",
    "baseSemanticScene",
  ] as const;
  if (input.routingMode === "reflex") {
    exactKeys(input, [...shared, "requestedRoute"], [], "request");
  } else if (input.routingMode === "director") {
    exactKeys(input, [...shared, "prompt"], [], "request");
  } else {
    return fail("routingMode has an unsupported value");
  }
  if (input.protocol !== PARAMETRIC_CHOREOGRAPHY_PROTOCOL) {
    fail(`protocol must equal ${PARAMETRIC_CHOREOGRAPHY_PROTOCOL}`);
  }
  const generation = integer(input.generation, "generation", 1);
  const problemText = boundedText(input.problemText, "problemText", true);
  const baseScene = decodeBaseScene(input.baseScene);
  const baseSemanticScene = decodeBaseSemanticScene(input.baseSemanticScene);
  if (baseScene.revision !== baseSemanticScene.revision) {
    fail("baseScene and baseSemanticScene revisions must match");
  }
  if (input.routingMode === "reflex") {
    return Object.freeze({
      protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemText,
      generation,
      baseScene,
      baseSemanticScene,
      requestedRoute: decodeParametricChoreographyRoute(input.requestedRoute),
    });
  }
  return Object.freeze({
    protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    routingMode: "director",
    problemText,
    generation,
    baseScene,
    baseSemanticScene,
    prompt: boundedText(input.prompt, "prompt", false) as string,
  });
}
