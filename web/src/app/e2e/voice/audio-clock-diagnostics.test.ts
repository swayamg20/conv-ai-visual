import { runInNewContext } from "node:vm";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUDIO_CLOCK_LOCAL_ACTIVE_RMS,
  AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
  AUDIO_CLOCK_MAX_TRANSITIONS,
  AUDIO_CLOCK_QUANTUM_FRAMES,
  AUDIO_CLOCK_REMOTE_SILENCE_RMS,
  audioClockCleanupComplete,
  audioClockFailureCapsule,
  audioClockFailureMessage,
  interruptionClockBracket,
  prepareAudioClockDiagnostics,
  settleAudioClockHarnessStatus,
  type AudioClockEvidence,
  type AudioClockProbeEvidence,
  type AudioClockSignalState,
  type AudioClockTransition,
} from "./audio-clock-diagnostics";

interface FakePort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  readonly close: ReturnType<typeof vi.fn>;
  readonly start: ReturnType<typeof vi.fn>;
  readonly postMessage: ReturnType<typeof vi.fn>;
}

interface FakeNode {
  readonly connect: ReturnType<typeof vi.fn>;
  readonly disconnect: ReturnType<typeof vi.fn>;
}

interface FakeWorkletNode extends FakeNode {
  readonly port: FakePort;
}

function fakeNode(disconnect?: () => void): FakeNode {
  return {
    connect: vi.fn((destination: unknown) => destination),
    disconnect: vi.fn(disconnect),
  };
}

function fakeWorkletNode(): FakeWorkletNode {
  return {
    ...fakeNode(),
    port: {
      onmessage: null,
      close: vi.fn(),
      start: vi.fn(),
      postMessage: vi.fn(),
    },
  };
}

function runtimeHarness(options?: {
  readonly throwWhileCreatingNode?: boolean;
  readonly throwOnFirstSourceDisconnect?: boolean;
}) {
  let contextState: AudioContextState = "running";
  let moduleSource = "";
  const sources: FakeNode[] = [];
  const sinks: Array<FakeNode & { gain: { value: number } }> = [];
  const worklets: FakeWorkletNode[] = [];
  const destination = fakeNode();
  const addModule = vi.fn(async (_url: string) => undefined);
  const close = vi.fn(async () => {
    contextState = "closed";
  });
  const context = {
    sampleRate: 48_000,
    get state() {
      return contextState;
    },
    audioWorklet: { addModule },
    destination,
    createMediaStreamSource: vi.fn(() => {
      const source = fakeNode(
        sources.length === 0 && options?.throwOnFirstSourceDisconnect
          ? () => {
              throw new Error("synthetic cleanup failure");
            }
          : undefined
      );
      sources.push(source);
      return source;
    }),
    createGain: vi.fn(() => {
      const sink = { ...fakeNode(), gain: { value: 1 } };
      sinks.push(sink);
      return sink;
    }),
    close,
  } as unknown as AudioContext;
  const createModuleUrl = vi.fn((source: string) => {
    moduleSource = source;
    return "blob:audio-clock-test";
  });
  const revokeModuleUrl = vi.fn();
  const createWorkletNode = vi.fn(
    (_context: AudioContext, _name: string, _options: AudioWorkletNodeOptions) => {
      if (options?.throwWhileCreatingNode) {
        throw new Error("synthetic node setup failure");
      }
      const node = fakeWorkletNode();
      worklets.push(node);
      return node;
    }
  );
  const dependencies = {
    createModuleUrl,
    revokeModuleUrl,
    createWorkletNode,
  } as unknown as Parameters<typeof prepareAudioClockDiagnostics>[1];
  return {
    addModule,
    close,
    context,
    createModuleUrl,
    createWorkletNode,
    dependencies,
    get moduleSource() {
      return moduleSource;
    },
    revokeModuleUrl,
    sinks,
    sources,
    worklets,
  };
}

function track(id: string): MediaStreamTrack {
  return { id } as MediaStreamTrack;
}

function transition(state: AudioClockSignalState, blockStartFrame: number): AudioClockTransition {
  return {
    state,
    block_start_frame: blockStartFrame,
    block_end_frame: blockStartFrame + AUDIO_CLOCK_QUANTUM_FRAMES,
  };
}

function containsTypedArray(value: unknown): boolean {
  if (ArrayBuffer.isView(value)) return true;
  if (Array.isArray(value)) return value.some(containsTypedArray);
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some(containsTypedArray);
}

function probe(overrides: Partial<AudioClockProbeEvidence> = {}): AudioClockProbeEvidence {
  return {
    attached: true,
    exact_track_id: "track",
    threshold_rms: AUDIO_CLOCK_LOCAL_ACTIVE_RMS,
    silence_hold_frames: 24_064,
    processed_block_count: 548,
    latest_block_end_frame: 70_144,
    current_state: "active",
    current_state_block_count: 48,
    active_region_count: 2,
    transitions: [
      transition("silent", 0),
      transition("active", 128),
      transition("silent", 12_800),
      transition("active", 48_000),
    ],
    overflow: false,
    failure_code: null,
    failure_message_sequence: null,
    expected_block_start_frame: null,
    observed_block_start_frame: null,
    frame_delta_frames: null,
    last_observed_block_start_frame: null,
    context_state_at_message_delivery: null,
    stale_frame_correction_count: 0,
    last_stale_observed_block_start_frame: null,
    last_stale_logical_block_start_frame: null,
    stale_frame_correction_pending: false,
    ...overrides,
  };
}

function evidence(
  localOverrides: Partial<AudioClockProbeEvidence> = {},
  remoteOverrides: Partial<AudioClockProbeEvidence> = {}
): AudioClockEvidence {
  return {
    schema_version: 1,
    worklet_loaded: true,
    sample_rate_hz: 48_000,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    local: probe(localOverrides),
    remote: probe({
      exact_track_id: "remote-track",
      threshold_rms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
      silence_hold_frames: 9_600,
      active_region_count: 2,
      transitions: [
        transition("silent", 0),
        transition("active", 12_800),
        transition("silent", 50_560),
        transition("active", 64_000),
      ],
      ...remoteOverrides,
    }),
    disposed: false,
  };
}

function observation(input: {
  readonly probe: "local" | "remote";
  readonly sequence: number;
  readonly state: AudioClockSignalState;
  readonly blockStartFrame: number;
  readonly activeRegions: number;
  readonly quantumFrames?: number;
  readonly processedBlocks?: number;
  readonly includeTransition?: boolean;
  readonly staleFrameCorrectionCount?: number;
  readonly lastStaleObservedBlockStartFrame?: number | null;
  readonly lastStaleLogicalBlockStartFrame?: number | null;
  readonly staleFrameCorrectionPending?: boolean;
}) {
  return {
    schema_version: 1,
    kind: "observation",
    probe: input.probe,
    message_sequence: input.sequence,
    sample_rate_hz: 48_000,
    quantum_frames: input.quantumFrames ?? AUDIO_CLOCK_QUANTUM_FRAMES,
    processed_block_count: input.processedBlocks ?? input.sequence,
    latest_block_end_frame: input.blockStartFrame + AUDIO_CLOCK_QUANTUM_FRAMES,
    current_state: input.state,
    current_state_block_count: 1,
    active_region_count: input.activeRegions,
    transition:
      input.includeTransition === false
        ? null
        : transition(input.state, input.blockStartFrame),
    failure_code: undefined,
    expected_block_start_frame: null,
    observed_block_start_frame: null,
    frame_delta_frames: null,
    last_observed_block_start_frame: null,
    stale_frame_correction_count: input.staleFrameCorrectionCount ?? 0,
    last_stale_observed_block_start_frame:
      input.lastStaleObservedBlockStartFrame ?? null,
    last_stale_logical_block_start_frame:
      input.lastStaleLogicalBlockStartFrame ?? null,
    stale_frame_correction_pending: input.staleFrameCorrectionPending ?? false,
  };
}

function frameGapFault(input: {
  readonly probe: "local" | "remote";
  readonly sequence: number;
  readonly processedBlocks: number;
  readonly expectedBlockStartFrame: number;
  readonly observedBlockStartFrame: number;
  readonly lastObservedBlockStartFrame: number;
  readonly staleFrameCorrectionCount?: number;
  readonly lastStaleObservedBlockStartFrame?: number | null;
  readonly lastStaleLogicalBlockStartFrame?: number | null;
  readonly staleFrameCorrectionPending?: boolean;
}) {
  return {
    schema_version: 1,
    kind: "fault",
    probe: input.probe,
    message_sequence: input.sequence,
    sample_rate_hz: 48_000,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    processed_block_count: input.processedBlocks,
    latest_block_end_frame:
      input.observedBlockStartFrame + AUDIO_CLOCK_QUANTUM_FRAMES,
    current_state: "silent",
    current_state_block_count: input.processedBlocks,
    active_region_count: 0,
    transition: null,
    failure_code: "frame_gap",
    expected_block_start_frame: input.expectedBlockStartFrame,
    observed_block_start_frame: input.observedBlockStartFrame,
    frame_delta_frames:
      input.observedBlockStartFrame - input.expectedBlockStartFrame,
    last_observed_block_start_frame: input.lastObservedBlockStartFrame,
    stale_frame_correction_count: input.staleFrameCorrectionCount ?? 0,
    last_stale_observed_block_start_frame:
      input.lastStaleObservedBlockStartFrame ?? null,
    last_stale_logical_block_start_frame:
      input.lastStaleLogicalBlockStartFrame ?? null,
    stale_frame_correction_pending: input.staleFrameCorrectionPending ?? false,
  } as const;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("audio sample-clock bracket", () => {
  it("builds a fixed sanitized failure capsule without track or PCM evidence", () => {
    const capsule = audioClockFailureCapsule(
      evidence({
        failure_code: "frame_gap",
        failure_message_sequence: 17,
        processed_block_count: 1_272,
        latest_block_end_frame: 199_680,
        expected_block_start_frame: 199_680,
        observed_block_start_frame: 199_552,
        frame_delta_frames: -128,
        last_observed_block_start_frame: 199_552,
        context_state_at_message_delivery: "running",
      })
    );

    expect(Object.keys(capsule).sort()).toEqual(
      ["local", "quantum_frames", "remote", "sample_rate_hz", "schema_version"].sort()
    );
    expect(Object.keys(capsule.local).sort()).toEqual(
      [
        "context_state_at_message_delivery",
        "expected_block_start_frame",
        "failure_code",
        "failure_message_sequence",
        "frame_delta_frames",
        "last_observed_block_start_frame",
        "last_stale_logical_block_start_frame",
        "last_stale_observed_block_start_frame",
        "last_successful_block_end_frame",
        "last_successful_processed_block_count",
        "observed_block_start_frame",
        "stale_frame_correction_count",
        "stale_frame_correction_pending",
      ].sort()
    );
    expect(capsule.local).toMatchObject({
      failure_code: "frame_gap",
      failure_message_sequence: 17,
      expected_block_start_frame: 199_680,
      observed_block_start_frame: 199_552,
      frame_delta_frames: -128,
      last_observed_block_start_frame: 199_552,
      context_state_at_message_delivery: "running",
      stale_frame_correction_count: 0,
      last_stale_observed_block_start_frame: null,
      last_stale_logical_block_start_frame: null,
      stale_frame_correction_pending: false,
    });
    const serialized = JSON.stringify(capsule);
    expect(serialized.length).toBeLessThan(2_048);
    expect(serialized).not.toMatch(
      /exact_track_id|channel_samples|raw_pcm|audio_samples|voice_call|session|trace|sdp|ice/i
    );
    expect(containsTypedArray(capsule)).toBe(false);
    const failureMessage = audioClockFailureMessage(
      evidence({
        failure_code: "frame_gap",
        failure_message_sequence: 17,
        expected_block_start_frame: 199_680,
        observed_block_start_frame: 199_552,
        frame_delta_frames: -128,
        last_observed_block_start_frame: 199_552,
        context_state_at_message_delivery: "running",
      }),
      "frame_gap"
    );
    expect(failureMessage).toContain(
      'Audio sample-clock proof failed: frame_gap; diagnostics={"schema_version":1'
    );
    expect(failureMessage.length).toBeLessThan(2_048);
    expect(failureMessage).not.toMatch(
      /exact_track_id|channel_samples|raw_pcm|audio_samples|voice_call|session|trace|sdp|ice/i
    );

    const correctionCapsule = audioClockFailureCapsule(
      evidence({
        failure_code: "frame_gap",
        failure_message_sequence: 3,
        expected_block_start_frame: 205_056,
        observed_block_start_frame: 204_928,
        frame_delta_frames: -128,
        last_observed_block_start_frame: 204_800,
        context_state_at_message_delivery: "running",
        stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
        last_stale_observed_block_start_frame: 204_800,
        last_stale_logical_block_start_frame: 204_928,
        stale_frame_correction_pending: true,
      })
    );
    expect(correctionCapsule.local).toMatchObject({
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: 204_800,
      last_stale_logical_block_start_frame: 204_928,
      stale_frame_correction_pending: true,
    });
    expect(JSON.stringify(correctionCapsule)).not.toMatch(
      /exact_track_id|channel_samples|raw_pcm|audio_samples|voice_call|session|trace|sdp|ice/i
    );
    expect(containsTypedArray(correctionCapsule)).toBe(false);
  });

  it("never lets terminal cleanup overwrite a harness error", () => {
    expect(settleAudioClockHarnessStatus("error", true)).toBe("error");
    expect(settleAudioClockHarnessStatus("disconnecting", true)).toBe(
      "disconnected"
    );
    expect(settleAudioClockHarnessStatus("observing", false)).toBe(
      "observing"
    );
  });

  it("uses the second local block start and sustained remote-silence block end", () => {
    const bracket = interruptionClockBracket(evidence());

    expect(bracket).toEqual({
      status: "passed",
      failure_code: null,
      sample_rate_hz: 48_000,
      quantum_frames: 128,
      required_silence_frames: 9_600,
      second_local_active_block_start_frame: 48_000,
      remote_silence_transition_block_end_frame: 50_688,
      interruption_upper_bound_frames: 2_688,
      interruption_upper_bound_ms: 56,
    });
  });

  it("refuses a pending correction and preserves settled correction SLA math", () => {
    const baseline = interruptionClockBracket(evidence());
    const correction = {
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: 47_872,
      last_stale_logical_block_start_frame: 48_000,
    } as const;

    expect(
      interruptionClockBracket(
        evidence({ ...correction, stale_frame_correction_pending: true })
      )
    ).toMatchObject({
      status: "pending",
      failure_code: "stale_frame_correction_pending",
    });
    expect(
      interruptionClockBracket(
        evidence({ ...correction, stale_frame_correction_pending: false })
      )
    ).toEqual(baseline);
    expect(baseline.interruption_upper_bound_ms).toBe(56);
  });

  it("hard-fails when the conservative block bracket exceeds 250ms", () => {
    const lateSilence = transition("silent", 60_032);
    const result = interruptionClockBracket(
      evidence(
        {},
        {
          transitions: [transition("silent", 0), transition("active", 12_800), lateSilence],
          latest_block_end_frame: 70_144,
          current_state: "silent",
        }
      )
    );

    expect(result.status).toBe("failed");
    expect(result.failure_code).toBe("interruption_exceeds_limit");
    expect(result.interruption_upper_bound_ms).toBeGreaterThan(250);
  });

  it.each([
    ["frame gap", { failure_code: "frame_gap" }, {}, "frame_gap"],
    ["overflow", { failure_code: "probe_overflow", overflow: true }, {}, "probe_overflow"],
    [
      "third local region",
      {
        active_region_count: 3,
        transitions: [
          transition("active", 128),
          transition("silent", 12_800),
          transition("active", 24_064),
          transition("silent", 36_864),
          transition("active", 48_000),
        ],
      },
      {},
      "too_many_local_active_regions",
    ],
  ] as const)("fails closed on %s", (_label, localOverrides, remoteOverrides, failureCode) => {
    const result = interruptionClockBracket(evidence(localOverrides, remoteOverrides));
    expect(result.status).toBe("failed");
    expect(result.failure_code).toBe(failureCode);
  });

  it("stays pending when either exact-track probe is missing", () => {
    expect(interruptionClockBracket(evidence({ attached: false })).failure_code).toBe(
      "local_probe_missing"
    );
    expect(interruptionClockBracket(evidence({}, { attached: false })).failure_code).toBe(
      "remote_probe_missing"
    );
  });

  it("fails closed on inconsistent render quantum metadata", () => {
    const inconsistent = {
      ...evidence(),
      quantum_frames: 256,
    } as unknown as AudioClockEvidence;

    expect(interruptionClockBracket(inconsistent)).toMatchObject({
      status: "failed",
      failure_code: "inconsistent_quantum",
    });
  });
});

describe("audio sample-clock worklet", () => {
  it("bridges semantic regions and enforces the one-shot stale-frame correction", async () => {
    const harness = runtimeHarness();
    const diagnostics = await prepareAudioClockDiagnostics(harness.context, harness.dependencies);
    const posted: unknown[] = [];
    let Processor!: new (options: { processorOptions: Record<string, unknown> }) => {
      readonly port: { postMessage: (message: unknown) => void };
      process: (inputs: readonly (readonly Float32Array[])[]) => boolean;
    };
    class FakeAudioWorkletProcessor {
      readonly port = {
        postMessage: (message: unknown) => posted.push(message),
      };
    }
    const sandbox = {
      AudioWorkletProcessor: FakeAudioWorkletProcessor,
      currentFrame: 0,
      registerProcessor: (_name: string, constructor: typeof Processor) => {
        Processor = constructor;
      },
      sampleRate: 48_000,
    };
    runInNewContext(harness.moduleSource, sandbox);
    const processor = new Processor({
      processorOptions: {
        probe: "local",
        thresholdRms: AUDIO_CLOCK_LOCAL_ACTIVE_RMS,
        silenceHoldFrames: 24_064,
      },
    });
    let frame = 0;
    const feed = (amplitude: number, blocks: number): boolean => {
      let running = true;
      for (let count = 0; count < blocks; count += 1) {
        sandbox.currentFrame = frame;
        const samples = new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES);
        samples.fill(amplitude);
        running = processor.process([[samples]]);
        frame += AUDIO_CLOCK_QUANTUM_FRAMES;
      }
      return running;
    };

    feed(0.1, 8);
    feed(0, 100);
    feed(0.1, 8);
    expect(
      posted.filter(
        (message) =>
          (message as { transition?: AudioClockTransition }).transition?.state === "active"
      )
    ).toHaveLength(1);

    feed(0, 188);
    feed(0.1, 8);
    const activeTransitions = posted.filter(
      (message) => (message as { transition?: AudioClockTransition }).transition?.state === "active"
    );
    expect(activeTransitions).toHaveLength(2);
    expect((activeTransitions[1] as { active_region_count: number }).active_region_count).toBe(2);

    feed(0, 188);
    expect(feed(0.1, 1)).toBe(false);
    expect(posted.at(-1)).toMatchObject({
      kind: "fault",
      failure_code: "too_many_local_active_regions",
      active_region_count: 3,
    });

    posted.length = 0;
    const remoteProcessor = new Processor({
      processorOptions: {
        probe: "remote",
        thresholdRms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
        silenceHoldFrames: 9_600,
      },
    });
    for (let index = 0; index < 74; index += 1) {
      sandbox.currentFrame = index * AUDIO_CLOCK_QUANTUM_FRAMES;
      expect(
        remoteProcessor.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]])
      ).toBe(true);
    }
    expect(posted).toEqual([]);
    sandbox.currentFrame = 74 * AUDIO_CLOCK_QUANTUM_FRAMES;
    expect(
      remoteProcessor.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]])
    ).toBe(true);
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({
      kind: "observation",
      latest_block_end_frame: 9_600,
      processed_block_count: 75,
      transition: {
        state: "silent",
        block_start_frame: 0,
        block_end_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      },
    });
    const expectedWorkletMessageKeys = [
        "active_region_count",
        "current_state",
        "current_state_block_count",
        "expected_block_start_frame",
        "failure_code",
        "frame_delta_frames",
        "kind",
        "last_observed_block_start_frame",
        "last_stale_logical_block_start_frame",
        "last_stale_observed_block_start_frame",
        "latest_block_end_frame",
        "message_sequence",
        "observed_block_start_frame",
        "probe",
        "processed_block_count",
        "quantum_frames",
        "sample_rate_hz",
        "schema_version",
        "stale_frame_correction_count",
        "stale_frame_correction_pending",
        "transition",
      ].sort();
    expect(Object.keys(posted[0] as object).sort()).toEqual(expectedWorkletMessageKeys);
    expect(containsTypedArray(posted[0])).toBe(false);
    expect(JSON.stringify(posted[0])).not.toMatch(/channel_samples|raw_pcm|audio_samples/i);

    posted.length = 0;
    const gapProcessor = new Processor({
      processorOptions: {
        probe: "remote",
        thresholdRms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
        silenceHoldFrames: 9_600,
      },
    });
    sandbox.currentFrame = 0;
    expect(
      gapProcessor.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]])
    ).toBe(true);
    sandbox.currentFrame = AUDIO_CLOCK_QUANTUM_FRAMES;
    expect(
      gapProcessor.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]])
    ).toBe(true);
    sandbox.currentFrame = AUDIO_CLOCK_QUANTUM_FRAMES * 3;
    expect(
      gapProcessor.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]])
    ).toBe(false);
    expect(posted.at(-1)).toMatchObject({
      kind: "fault",
      failure_code: "frame_gap",
      message_sequence: 1,
      processed_block_count: 2,
      expected_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
      frame_delta_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
    });
    expect(Object.keys(posted.at(-1) as object).sort()).toEqual(
      expectedWorkletMessageKeys
    );
    expect(containsTypedArray(posted.at(-1))).toBe(false);

    posted.length = 0;
    const correctedFrameProcessor = new Processor({
      processorOptions: {
        probe: "remote",
        thresholdRms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
        silenceHoldFrames: AUDIO_CLOCK_QUANTUM_FRAMES,
      },
    });
    const correctedSequence = [
      { frame: 0, amplitude: 0.1 },
      { frame: AUDIO_CLOCK_QUANTUM_FRAMES, amplitude: 0.1 },
      { frame: AUDIO_CLOCK_QUANTUM_FRAMES, amplitude: 0 },
      { frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3, amplitude: 0.1 },
    ] as const;
    for (const { frame: observedFrame, amplitude } of correctedSequence) {
      sandbox.currentFrame = observedFrame;
      const samples = new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES);
      samples.fill(amplitude);
      expect(
        correctedFrameProcessor.process([[samples]])
      ).toBe(true);
    }
    expect(posted).toHaveLength(3);
    expect(posted[1]).toMatchObject({
      kind: "observation",
      message_sequence: 2,
      processed_block_count: 3,
      latest_block_end_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_stale_logical_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      stale_frame_correction_pending: true,
      transition: transition("silent", AUDIO_CLOCK_QUANTUM_FRAMES * 2),
    });
    expect(posted[2]).toMatchObject({
      kind: "observation",
      message_sequence: 3,
      processed_block_count: 4,
      latest_block_end_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 4,
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_stale_logical_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      stale_frame_correction_pending: false,
      transition: transition("active", AUDIO_CLOCK_QUANTUM_FRAMES * 3),
    });

    const executeFrameSequence = (frames: readonly number[]) => {
      posted.length = 0;
      const candidate = new Processor({
        processorOptions: {
          probe: "remote",
          thresholdRms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
          silenceHoldFrames: 9_600,
        },
      });
      let running = true;
      for (const observedFrame of frames) {
        sandbox.currentFrame = observedFrame;
        running = candidate.process([[new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES)]]);
        if (!running) break;
      }
      return { messages: [...posted], running };
    };
    const forwardGap = executeFrameSequence([0, 128, 384]);
    expect(forwardGap.running).toBe(false);
    expect(forwardGap.messages.at(-1)).toMatchObject({
      failure_code: "frame_gap",
      expected_block_start_frame: 256,
      observed_block_start_frame: 384,
      frame_delta_frames: 128,
    });
    const backwardReset = executeFrameSequence([0, 128, 0]);
    expect(backwardReset.running).toBe(false);
    expect(backwardReset.messages.at(-1)).toMatchObject({
      failure_code: "frame_gap",
      expected_block_start_frame: 256,
      observed_block_start_frame: 0,
      frame_delta_frames: -256,
    });
    const secondConsecutiveRepeat = executeFrameSequence([0, 128, 128, 128]);
    expect(secondConsecutiveRepeat.running).toBe(false);
    expect(secondConsecutiveRepeat.messages.at(-1)).toMatchObject({
      failure_code: "frame_gap",
      expected_block_start_frame: 384,
      observed_block_start_frame: 128,
      frame_delta_frames: -256,
      stale_frame_correction_pending: true,
    });
    for (const wrongCatchUp of [256, 512]) {
      const result = executeFrameSequence([0, 128, 128, wrongCatchUp]);
      expect(result.running).toBe(false);
      expect(result.messages.at(-1)).toMatchObject({
        failure_code: "frame_gap",
        expected_block_start_frame: 384,
        observed_block_start_frame: wrongCatchUp,
        frame_delta_frames: wrongCatchUp - 384,
        stale_frame_correction_pending: true,
      });
    }
    const correctionBudgetExceeded = executeFrameSequence([0, 128, 128, 384, 384]);
    expect(correctionBudgetExceeded.running).toBe(false);
    expect(correctionBudgetExceeded.messages.at(-1)).toMatchObject({
      failure_code: "frame_gap",
      expected_block_start_frame: 512,
      observed_block_start_frame: 384,
      frame_delta_frames: -128,
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      stale_frame_correction_pending: false,
    });

    posted.length = 0;
    const periodicBoundaryProcessor = new Processor({
      processorOptions: {
        probe: "remote",
        thresholdRms: AUDIO_CLOCK_REMOTE_SILENCE_RMS,
        silenceHoldFrames: 9_600,
      },
    });
    for (const observedFrame of [0, 128, 256, 384, 512, 640, 768, 768, 1_024]) {
      sandbox.currentFrame = observedFrame;
      const activeBlock = new Float32Array(AUDIO_CLOCK_QUANTUM_FRAMES);
      activeBlock.fill(0.1);
      expect(periodicBoundaryProcessor.process([[activeBlock]])).toBe(true);
    }
    expect(posted).toHaveLength(3);
    expect(posted[1]).toMatchObject({
      message_sequence: 2,
      processed_block_count: 8,
      latest_block_end_frame: 1_024,
      stale_frame_correction_pending: true,
    });
    expect(posted[2]).toMatchObject({
      message_sequence: 3,
      processed_block_count: 9,
      latest_block_end_frame: 1_152,
      stale_frame_correction_pending: false,
    });
    expect(posted.every((message) => !containsTypedArray(message))).toBe(true);
    expect(JSON.stringify(posted)).not.toMatch(/channel_samples|raw_pcm|audio_samples/i);
    await diagnostics.dispose();
  });
});

describe("audio sample-clock lifecycle", () => {
  it("revokes the Blob URL when AudioWorklet module loading is rejected", async () => {
    const harness = runtimeHarness();
    harness.addModule.mockRejectedValueOnce(new Error("synthetic CSP rejection"));

    await expect(
      prepareAudioClockDiagnostics(harness.context, harness.dependencies)
    ).rejects.toThrow("synthetic CSP rejection");

    expect(harness.createModuleUrl).toHaveBeenCalledOnce();
    expect(harness.revokeModuleUrl).toHaveBeenCalledWith("blob:audio-clock-test");
  });

  it("publishes a pending correction and only settles it on exact catch-up", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});
    const harness = runtimeHarness();
    const diagnostics = await prepareAudioClockDiagnostics(
      harness.context,
      harness.dependencies
    );
    diagnostics.attach("remote", track("remote"), AUDIO_CLOCK_REMOTE_SILENCE_RMS);
    const deliver = (data: unknown) =>
      harness.worklets[0]?.port.onmessage?.({ data } as MessageEvent<unknown>);

    deliver(
      observation({
        probe: "remote",
        sequence: 1,
        state: "active",
        blockStartFrame: 0,
        activeRegions: 1,
        processedBlocks: 1,
      })
    );
    deliver(
      observation({
        probe: "remote",
        sequence: 2,
        state: "active",
        blockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        activeRegions: 1,
        processedBlocks: 3,
        includeTransition: false,
        staleFrameCorrectionCount: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
        lastStaleObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
        lastStaleLogicalBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        staleFrameCorrectionPending: true,
      })
    );
    expect(diagnostics.read().remote).toMatchObject({
      failure_code: null,
      processed_block_count: 3,
      latest_block_end_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_stale_logical_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      stale_frame_correction_pending: true,
    });

    deliver(
      observation({
        probe: "remote",
        sequence: 3,
        state: "active",
        blockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
        activeRegions: 1,
        processedBlocks: 4,
        includeTransition: false,
        staleFrameCorrectionCount: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
        lastStaleObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
        lastStaleLogicalBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        staleFrameCorrectionPending: false,
      })
    );
    expect(diagnostics.read().remote).toMatchObject({
      failure_code: null,
      processed_block_count: 4,
      latest_block_end_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 4,
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_stale_logical_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      stale_frame_correction_pending: false,
    });
    await diagnostics.dispose();
  });

  it("retains bounded frame-gap diagnostics and context state from the fault", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});
    const harness = runtimeHarness();
    const diagnostics = await prepareAudioClockDiagnostics(
      harness.context,
      harness.dependencies
    );
    diagnostics.attach("remote", track("remote"), AUDIO_CLOCK_REMOTE_SILENCE_RMS);

    harness.worklets[0]?.port.onmessage?.({
      data: frameGapFault({
        probe: "remote",
        sequence: 1,
        processedBlocks: 2,
        expectedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        observedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
        lastObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
      }),
    } as MessageEvent<unknown>);

    const fault = diagnostics.read().remote;
    expect(fault).toMatchObject({
      failure_code: "frame_gap",
      failure_message_sequence: 1,
      expected_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
      frame_delta_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      context_state_at_message_delivery: "running",
    });
    expect(Object.keys(fault).sort()).toEqual(
      [
        "active_region_count",
        "attached",
        "context_state_at_message_delivery",
        "current_state",
        "current_state_block_count",
        "exact_track_id",
        "expected_block_start_frame",
        "failure_code",
        "failure_message_sequence",
        "frame_delta_frames",
        "last_observed_block_start_frame",
        "last_stale_logical_block_start_frame",
        "last_stale_observed_block_start_frame",
        "latest_block_end_frame",
        "observed_block_start_frame",
        "overflow",
        "processed_block_count",
        "silence_hold_frames",
        "stale_frame_correction_count",
        "stale_frame_correction_pending",
        "threshold_rms",
        "transitions",
      ].sort()
    );
    expect(containsTypedArray(fault)).toBe(false);
    expect(JSON.stringify(fault)).not.toMatch(/channel_samples|raw_pcm|audio_samples/i);

    diagnostics.attach("local", track("local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    harness.worklets[1]?.port.onmessage?.({
      data: observation({
        probe: "local",
        sequence: 1,
        state: "silent",
        blockStartFrame: 0,
        activeRegions: 0,
        processedBlocks: 1,
      }),
    } as MessageEvent<unknown>);
    harness.worklets[1]?.port.onmessage?.({
      data: observation({
        probe: "local",
        sequence: 2,
        state: "silent",
        blockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        activeRegions: 0,
        processedBlocks: 3,
        includeTransition: false,
        staleFrameCorrectionCount: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
        lastStaleObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
        lastStaleLogicalBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        staleFrameCorrectionPending: true,
      }),
    } as MessageEvent<unknown>);
    harness.worklets[1]?.port.onmessage?.({
      data: frameGapFault({
        probe: "local",
        sequence: 3,
        processedBlocks: 3,
        expectedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
        observedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        lastObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
        staleFrameCorrectionCount: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
        lastStaleObservedBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES,
        lastStaleLogicalBlockStartFrame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
        staleFrameCorrectionPending: true,
      }),
    } as MessageEvent<unknown>);
    expect(diagnostics.read().local).toMatchObject({
      failure_code: "frame_gap",
      failure_message_sequence: 3,
      expected_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 3,
      observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      frame_delta_frames: -AUDIO_CLOCK_QUANTUM_FRAMES,
      last_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      context_state_at_message_delivery: "running",
      stale_frame_correction_count: AUDIO_CLOCK_MAX_STALE_FRAME_CORRECTIONS,
      last_stale_observed_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES,
      last_stale_logical_block_start_frame: AUDIO_CLOCK_QUANTUM_FRAMES * 2,
      stale_frame_correction_pending: true,
    });
    await diagnostics.dispose();
  });

  it("reuses repeated callbacks for the exact track and rejects replacement", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});
    const harness = runtimeHarness();
    const diagnostics = await prepareAudioClockDiagnostics(harness.context, harness.dependencies);
    const exactTrack = track("exact-local");

    diagnostics.attach("local", exactTrack, AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    diagnostics.attach("local", exactTrack, AUDIO_CLOCK_LOCAL_ACTIVE_RMS);

    expect(harness.sources).toHaveLength(1);
    expect(harness.worklets).toHaveLength(1);
    expect(harness.sinks).toHaveLength(1);
    expect(diagnostics.read().local.failure_code).toBeNull();

    diagnostics.attach("local", track("replacement-local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    expect(harness.worklets).toHaveLength(1);
    expect(diagnostics.read().local.failure_code).toBe("duplicate_probe");
    await diagnostics.dispose();
  });

  it("revokes the module URL and releases every node, port, and context once", async () => {
    vi.stubGlobal(
      "MediaStream",
      class FakeMediaStream {
        constructor(readonly tracks: readonly MediaStreamTrack[]) {}
      }
    );
    const harness = runtimeHarness();
    const diagnostics = await prepareAudioClockDiagnostics(harness.context, harness.dependencies);

    expect(harness.addModule).toHaveBeenCalledWith("blob:audio-clock-test");
    expect(harness.revokeModuleUrl).toHaveBeenCalledOnce();
    diagnostics.attach("local", track("exact-local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    diagnostics.attach("remote", track("exact-remote"), AUDIO_CLOCK_REMOTE_SILENCE_RMS);
    const attachedEvidence = diagnostics.read();
    expect(attachedEvidence).toMatchObject({
      worklet_loaded: true,
      local: {
        attached: true,
        exact_track_id: "exact-local",
        silence_hold_frames: 24_064,
      },
      remote: {
        attached: true,
        exact_track_id: "exact-remote",
        silence_hold_frames: 9_600,
      },
    });
    expect(containsTypedArray(attachedEvidence)).toBe(false);
    expect(JSON.stringify(attachedEvidence)).not.toMatch(
      /channel_samples|raw_pcm|audio_samples/i
    );

    await diagnostics.dispose();
    await diagnostics.dispose();

    expect(harness.close).toHaveBeenCalledOnce();
    for (const source of harness.sources) {
      expect(source.disconnect).toHaveBeenCalledOnce();
    }
    for (const worklet of harness.worklets) {
      expect(worklet.port.close).toHaveBeenCalledOnce();
      expect(worklet.disconnect).toHaveBeenCalledOnce();
      expect(worklet.port.onmessage).toBeNull();
    }
    for (const sink of harness.sinks) {
      expect(sink.disconnect).toHaveBeenCalledOnce();
    }
    expect(diagnostics.read().disposed).toBe(true);
    expect(audioClockCleanupComplete(diagnostics.read())).toBe(true);
  });

  it("continues cleanup and fails closed if one release throws", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});
    const harness = runtimeHarness({ throwOnFirstSourceDisconnect: true });
    const diagnostics = await prepareAudioClockDiagnostics(harness.context, harness.dependencies);
    diagnostics.attach("local", track("local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    diagnostics.attach("remote", track("remote"), AUDIO_CLOCK_REMOTE_SILENCE_RMS);

    await diagnostics.dispose();

    expect(harness.close).toHaveBeenCalledOnce();
    expect(harness.worklets[0]?.port.close).toHaveBeenCalledOnce();
    expect(harness.worklets[1]?.port.close).toHaveBeenCalledOnce();
    expect(harness.sinks[0]?.disconnect).toHaveBeenCalledOnce();
    expect(harness.sinks[1]?.disconnect).toHaveBeenCalledOnce();
    expect(diagnostics.read()).toMatchObject({
      disposed: true,
      local: { failure_code: "cleanup_failed" },
      remote: { failure_code: "cleanup_failed" },
    });
    expect(audioClockCleanupComplete(diagnostics.read())).toBe(false);
  });

  it("releases partial setup and records only a generic setup failure", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});
    const harness = runtimeHarness({ throwWhileCreatingNode: true });
    const diagnostics = await prepareAudioClockDiagnostics(harness.context, harness.dependencies);

    diagnostics.attach("local", track("local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);

    expect(harness.sources[0]?.disconnect).toHaveBeenCalledOnce();
    expect(diagnostics.read().local).toMatchObject({
      attached: false,
      exact_track_id: null,
      failure_code: "probe_setup_failed",
    });
    await diagnostics.dispose();
    expect(harness.close).toHaveBeenCalledOnce();
  });

  it("detects message gaps, quantum drift, and transition overflow", async () => {
    vi.stubGlobal("MediaStream", class FakeMediaStream {});

    const gapHarness = runtimeHarness();
    const gapDiagnostics = await prepareAudioClockDiagnostics(
      gapHarness.context,
      gapHarness.dependencies
    );
    gapDiagnostics.attach("local", track("local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    gapHarness.worklets[0]?.port.onmessage?.({
      data: observation({
        probe: "local",
        sequence: 2,
        state: "active",
        blockStartFrame: 0,
        activeRegions: 1,
      }),
    } as MessageEvent<unknown>);
    expect(gapDiagnostics.read().local.failure_code).toBe("message_gap");
    await gapDiagnostics.dispose();

    const quantumHarness = runtimeHarness();
    const quantumDiagnostics = await prepareAudioClockDiagnostics(
      quantumHarness.context,
      quantumHarness.dependencies
    );
    quantumDiagnostics.attach("local", track("local"), AUDIO_CLOCK_LOCAL_ACTIVE_RMS);
    quantumHarness.worklets[0]?.port.onmessage?.({
      data: observation({
        probe: "local",
        sequence: 1,
        state: "active",
        blockStartFrame: 0,
        activeRegions: 1,
        quantumFrames: 256,
      }),
    } as MessageEvent<unknown>);
    expect(quantumDiagnostics.read().local.failure_code).toBe("inconsistent_quantum");
    await quantumDiagnostics.dispose();

    const overflowHarness = runtimeHarness();
    const overflowDiagnostics = await prepareAudioClockDiagnostics(
      overflowHarness.context,
      overflowHarness.dependencies
    );
    overflowDiagnostics.attach("remote", track("remote"), AUDIO_CLOCK_REMOTE_SILENCE_RMS);
    for (let index = 0; index <= AUDIO_CLOCK_MAX_TRANSITIONS; index += 1) {
      overflowHarness.worklets[0]?.port.onmessage?.({
        data: observation({
          probe: "remote",
          sequence: index + 1,
          state: index % 2 === 0 ? "active" : "silent",
          blockStartFrame: index * AUDIO_CLOCK_QUANTUM_FRAMES,
          activeRegions: Math.floor(index / 2) + 1,
        }),
      } as MessageEvent<unknown>);
    }
    expect(overflowDiagnostics.read().remote).toMatchObject({
      overflow: true,
      failure_code: "probe_overflow",
    });
    expect(overflowDiagnostics.read().remote.transitions).toHaveLength(AUDIO_CLOCK_MAX_TRANSITIONS);
    await overflowDiagnostics.dispose();
  });
});
