import { LiveSceneProtocolError } from "./patch";
import {
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  decodePairedProjectileComparisonSpecV1,
  decodeSemanticStoryboardDirectorPromptV1,
  type PairedProjectileComparisonSpecV1,
} from "./semantic-storyboard";

export const CONVERSATION_STORYBOARD_COMMAND_VERSION = 1 as const;

export interface ConversationStoryboardCommandV1 {
  readonly v: typeof CONVERSATION_STORYBOARD_COMMAND_VERSION;
  readonly commandId: string;
  readonly protocol: typeof PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly prompt: string;
}

type UnknownRecord = Record<string, unknown>;

const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function fail(message: string): never {
  throw new LiveSceneProtocolError(
    "invalid_event",
    `conversation storyboard command ${message}`,
  );
}

function record(value: unknown): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("must be an object");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail("must be a plain object");
  }
  return value as UnknownRecord;
}

function exactKeys(value: UnknownRecord): void {
  const required = [
    "v",
    "commandId",
    "protocol",
    "problemSpec",
    "prompt",
  ] as const;
  const allowed = new Set<string>(required);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`is missing field ${key}`);
  }
}

/** Strictly decode the only command allowed to mount Gate 1.8 from chat. */
export function decodeConversationStoryboardCommandV1(
  value: unknown,
): ConversationStoryboardCommandV1 {
  const input = record(value);
  exactKeys(input);

  if (input.v !== CONVERSATION_STORYBOARD_COMMAND_VERSION) {
    fail(`v must equal ${CONVERSATION_STORYBOARD_COMMAND_VERSION}`);
  }
  if (input.protocol !== PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL) {
    fail("protocol has an unsupported value");
  }
  if (
    typeof input.commandId !== "string" ||
    !UUID_V4_PATTERN.test(input.commandId)
  ) {
    fail("commandId must be a UUIDv4 string");
  }

  return Object.freeze({
    v: CONVERSATION_STORYBOARD_COMMAND_VERSION,
    commandId: input.commandId,
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    problemSpec: decodePairedProjectileComparisonSpecV1(input.problemSpec),
    prompt: decodeSemanticStoryboardDirectorPromptV1(input.prompt),
  });
}
