import {
  decodeParametricChoreographyRequestV3,
  type ParametricChoreographyRequestV3,
} from "@/lib/live-scene/parametric-choreography-request";
import {
  parseParametricChoreographySceneStreamEventV3,
  type ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";

import {
  consumeDecodedSceneStreamResponse,
  type SceneStreamEndpoint,
} from "./model-stream";

export interface ParametricChoreographySceneStreamRunInvocation {
  readonly request: ParametricChoreographyRequestV3;
  readonly signal: AbortSignal;
  readonly onEvent: (event: ParametricChoreographySceneStreamEventV3) => void;
}

export type ParametricChoreographySceneStreamRunner = (
  invocation: ParametricChoreographySceneStreamRunInvocation,
) => Promise<void>;

export type ParametricChoreographyHeaderHook = () =>
  Readonly<Record<string, string>> | Promise<Readonly<Record<string, string>>>;

export interface ParametricChoreographyTransportOptions extends ParametricChoreographySceneStreamRunInvocation {
  readonly apiUrl: string;
  readonly endpoint: SceneStreamEndpoint;
  readonly headers?: Readonly<Record<string, string>>;
  /** Resolved immediately before fetch so product auth tokens are never cached. */
  readonly getHeaders?: ParametricChoreographyHeaderHook;
  readonly fetchImpl?: typeof fetch;
}

export interface ParametricChoreographyRunnerOptions extends Omit<
  ParametricChoreographyTransportOptions,
  "request" | "signal" | "onEvent"
> {}

const STREAM_PATHS: Readonly<Record<SceneStreamEndpoint, string>> = {
  product: "/api/live-scenes/choreography/stream",
  developmentLab: "/api/live-scenes/lab/choreography/stream",
};

/** Consume the V3 SSE lane through its strict event decoder. */
export async function consumeParametricChoreographySceneStreamResponse(
  response: Response,
  onEvent: (event: ParametricChoreographySceneStreamEventV3) => void,
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseParametricChoreographySceneStreamEventV3,
  );
}

/** Post one exact V3 request and decode every event before runtime admission. */
export async function runParametricChoreographySceneModelStream(
  options: ParametricChoreographyTransportOptions,
): Promise<void> {
  const request = decodeParametricChoreographyRequestV3(options.request);
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
  await consumeParametricChoreographySceneStreamResponse(
    response,
    options.onEvent,
  );
}

/** Bind endpoint/auth plumbing once while keeping each runtime call abortable. */
export function createParametricChoreographySceneStreamRunner(
  options: ParametricChoreographyRunnerOptions,
): ParametricChoreographySceneStreamRunner {
  return (invocation) =>
    runParametricChoreographySceneModelStream({ ...options, ...invocation });
}
