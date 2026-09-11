import {
  decodeScenePatchDraft,
  LIVE_SCENE_MAX_NODES,
  LIVE_SCENE_MAX_PATCH_OPERATIONS,
  LiveSceneProtocolError,
} from "./patch";
import {
  decodeProjectileMotionProblemSpecV1,
  decodeProjectileMotionRouteV1,
  decodeProjectileMotionStateV1,
  sameProjectileMotionProblem,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
  type ProjectileMotionStateV1,
} from "./projectile-motion";
import { createSceneState } from "./state";
import type { SceneNode, SceneState } from "./types";

export const PROJECTILE_CHOREOGRAPHY_PROTOCOL =
  "projectile_choreography_v1" as const;

export interface ProjectileMotionSemanticSceneState {
  readonly revision: number;
  readonly components: readonly ProjectileMotionStateV1[];
  readonly certificateHeadSha256?: string | null;
}

interface ProjectileMotionRequestBaseV1 {
  readonly protocol: typeof PROJECTILE_CHOREOGRAPHY_PROTOCOL;
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly generation: number;
  readonly baseScene: SceneState;
  readonly baseSemanticScene: ProjectileMotionSemanticSceneState;
}

export interface ProjectileMotionReflexRequestV1
  extends ProjectileMotionRequestBaseV1 {
  readonly routingMode: "reflex";
  readonly requestedRoute: ProjectileMotionRouteV1;
}

export interface ProjectileMotionDirectorRequestV1
  extends ProjectileMotionRequestBaseV1 {
  readonly routingMode: "director";
  readonly prompt: string;
}

export type ProjectileMotionRequestV1 =
  | ProjectileMotionReflexRequestV1
  | ProjectileMotionDirectorRequestV1;

type UnknownRecord = Record<string, unknown>;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `projectile choreography request ${message}`,
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

function boundedPrompt(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value.trim()].length > 2_000
  ) {
    fail("prompt must be a non-empty string of at most 2000 characters");
  }
  return value.trim();
}

const SHA256_PATTERN = /^[0-9a-f]{64}$/;

function decodeBaseScene(value: unknown): SceneState {
  const input = record(value, "baseScene");
  exactKeys(input, ["revision", "nodes"], [], "baseScene");
  const revision = integer(input.revision, "baseScene revision", 0);
  if (!Array.isArray(input.nodes)) fail("baseScene nodes must be an array");
  if (input.nodes.length > LIVE_SCENE_MAX_NODES) {
    throw new LiveSceneProtocolError(
      "budget_exceeded",
      `projectile choreography request baseScene exceeds ${LIVE_SCENE_MAX_NODES} nodes`,
    );
  }

  const nodes: SceneNode[] = [];
  for (
    let offset = 0;
    offset < input.nodes.length;
    offset += LIVE_SCENE_MAX_PATCH_OPERATIONS
  ) {
    const chunk = input.nodes.slice(
      offset,
      offset + LIVE_SCENE_MAX_PATCH_OPERATIONS,
    );
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

function decodeBaseSemanticScene(
  value: unknown,
): ProjectileMotionSemanticSceneState {
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
  if (input.components.length > 1) {
    fail("baseSemanticScene supports at most one projectile component");
  }
  const components = Object.freeze(
    input.components.map(decodeProjectileMotionStateV1),
  );
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
      fail(
        "baseSemanticScene certificateHeadSha256 must be null or a lowercase SHA-256 digest",
      );
    }
  }
  return Object.freeze({
    revision,
    components,
    ...(hasCertificateHead ? { certificateHeadSha256 } : {}),
  });
}

/** Decode either exact projectile request mode before it crosses the wire. */
export function decodeProjectileMotionRequestV1(
  value: unknown,
): ProjectileMotionRequestV1 {
  const input = record(value, "request");
  const shared = [
    "protocol",
    "routingMode",
    "problemSpec",
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
  if (input.protocol !== PROJECTILE_CHOREOGRAPHY_PROTOCOL) {
    fail(`protocol must equal ${PROJECTILE_CHOREOGRAPHY_PROTOCOL}`);
  }

  const problemSpec = decodeProjectileMotionProblemSpecV1(input.problemSpec);
  const generation = integer(input.generation, "generation", 1);
  const baseScene = decodeBaseScene(input.baseScene);
  const baseSemanticScene = decodeBaseSemanticScene(input.baseSemanticScene);
  if (baseScene.revision !== baseSemanticScene.revision) {
    fail("baseScene and baseSemanticScene revisions must match");
  }
  const existing = baseSemanticScene.components[0];
  if (
    existing &&
    !sameProjectileMotionProblem(existing.problemSpec, problemSpec)
  ) {
    fail("problemSpec must match the accepted projectile problem");
  }

  if (input.routingMode === "reflex") {
    const requestedRoute = decodeProjectileMotionRouteV1(input.requestedRoute);
    if (!existing && requestedRoute.intent !== "advance") {
      fail("a fresh projectile request must use an advance route");
    }
    return Object.freeze({
      protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemSpec,
      generation,
      baseScene,
      baseSemanticScene,
      requestedRoute,
    });
  }
  return Object.freeze({
    protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    routingMode: "director",
    problemSpec,
    generation,
    baseScene,
    baseSemanticScene,
    prompt: boundedPrompt(input.prompt),
  });
}
