import {
  decodeProjectileMotionRequestV1,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import {
  parseProjectileChoreographySceneStreamEventV1,
  type ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";

import {
  consumeDecodedSceneStreamResponse,
  type SceneStreamEndpoint,
} from "./model-stream";

export interface ProjectileChoreographySceneStreamRunInvocation {
  readonly request: ProjectileMotionRequestV1;
  readonly signal: AbortSignal;
  readonly onEvent: (event: ProjectileChoreographySceneStreamEventV1) => void;
}

export type ProjectileChoreographySceneStreamRunner = (
  invocation: ProjectileChoreographySceneStreamRunInvocation,
) => Promise<void>;

export type ProjectileChoreographyHeaderHook = () =>
  | Readonly<Record<string, string>>
  | Promise<Readonly<Record<string, string>>>;

export interface ProjectileChoreographyTransportOptions
  extends ProjectileChoreographySceneStreamRunInvocation {
  readonly apiUrl: string;
  readonly endpoint: SceneStreamEndpoint;
  readonly headers?: Readonly<Record<string, string>>;
  /** Resolved immediately before fetch so product auth tokens are never cached. */
  readonly getHeaders?: ProjectileChoreographyHeaderHook;
  readonly fetchImpl?: typeof fetch;
}

export interface ProjectileChoreographyRunnerOptions
  extends Omit<
    ProjectileChoreographyTransportOptions,
    "request" | "signal" | "onEvent"
  > {}

const STREAM_PATHS: Readonly<Record<SceneStreamEndpoint, string>> = {
  product: "/api/live-scenes/choreography/stream",
  developmentLab: "/api/live-scenes/lab/choreography/stream",
};

/** Consume the projectile SSE lane through its strict event decoder. */
export async function consumeProjectileChoreographySceneStreamResponse(
  response: Response,
  onEvent: (event: ProjectileChoreographySceneStreamEventV1) => void,
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseProjectileChoreographySceneStreamEventV1,
  );
}

/** Post one exact Gate 1.7 request and decode every event before admission. */
export async function runProjectileChoreographySceneModelStream(
  options: ProjectileChoreographyTransportOptions,
): Promise<void> {
  const request = decodeProjectileMotionRequestV1(options.request);
  const liveHeaders = options.getHeaders ? await options.getHeaders() : {};
  const requestFetch = options.fetchImpl ?? fetch;
  const endpointPath = STREAM_PATHS[options.endpoint];
  const response = await requestFetch(
    `${options.apiUrl.replace(/\/$/, "")}${endpointPath}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
        ...liveHeaders,
      },
      body: JSON.stringify(request),
      signal: options.signal,
    },
  );
  await consumeProjectileChoreographySceneStreamResponse(
    response,
    options.onEvent,
  );
}

/** Bind endpoint and auth plumbing once while keeping each call abortable. */
export function createProjectileChoreographySceneStreamRunner(
  options: ProjectileChoreographyRunnerOptions,
): ProjectileChoreographySceneStreamRunner {
  return (invocation) =>
    runProjectileChoreographySceneModelStream({ ...options, ...invocation });
}
