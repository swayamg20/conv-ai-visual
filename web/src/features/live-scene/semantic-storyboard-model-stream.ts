import {
  decodeSemanticStoryboardRequestV1,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import {
  parseSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  consumeDecodedSceneStreamResponse,
  type SceneStreamEndpoint,
} from "./model-stream";

export interface SemanticStoryboardSceneStreamRunInvocation {
  readonly request: SemanticStoryboardRequestV1;
  readonly signal: AbortSignal;
  readonly onEvent: (event: SemanticStoryboardSceneStreamEventV1) => void;
}

export type SemanticStoryboardSceneStreamRunner = (
  invocation: SemanticStoryboardSceneStreamRunInvocation,
) => Promise<void>;

export type SemanticStoryboardHeaderHook = () =>
  Readonly<Record<string, string>> | Promise<Readonly<Record<string, string>>>;

export interface SemanticStoryboardTransportOptions extends SemanticStoryboardSceneStreamRunInvocation {
  readonly apiUrl: string;
  readonly endpoint: SceneStreamEndpoint;
  readonly headers?: Readonly<Record<string, string>>;
  /** Resolved immediately before fetch so product auth tokens are never cached. */
  readonly getHeaders?: SemanticStoryboardHeaderHook;
  readonly fetchImpl?: typeof fetch;
}

export interface SemanticStoryboardRunnerOptions extends Omit<
  SemanticStoryboardTransportOptions,
  "request" | "signal" | "onEvent"
> {}

const STREAM_PATHS: Readonly<Record<SceneStreamEndpoint, string>> = {
  product: "/api/live-scenes/choreography/stream",
  developmentLab: "/api/live-scenes/lab/choreography/stream",
};

/** Consume only the dedicated Gate 1.8 SSE lane through its strict decoder. */
export async function consumeSemanticStoryboardSceneStreamResponse(
  response: Response,
  onEvent: (event: SemanticStoryboardSceneStreamEventV1) => void,
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseSemanticStoryboardSceneStreamEventV1,
  );
}

/** Post one canonical storyboard request and decode every event before admission. */
export async function runSemanticStoryboardSceneModelStream(
  options: SemanticStoryboardTransportOptions,
): Promise<void> {
  const request = decodeSemanticStoryboardRequestV1(options.request);
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
  await consumeSemanticStoryboardSceneStreamResponse(response, options.onEvent);
}

/** Bind endpoint and auth plumbing once while keeping every call abortable. */
export function createSemanticStoryboardSceneStreamRunner(
  options: SemanticStoryboardRunnerOptions,
): SemanticStoryboardSceneStreamRunner {
  return (invocation) =>
    runSemanticStoryboardSceneModelStream({ ...options, ...invocation });
}
