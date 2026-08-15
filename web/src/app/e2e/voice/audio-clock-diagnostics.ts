const WORKLET_PROCESSOR_NAME = "murmur-audio-clock-probe";

export const AUDIO_CLOCK_QUANTUM_FRAMES = 128;
export const AUDIO_CLOCK_MAX_TRANSITIONS = 24;
export const AUDIO_CLOCK_LOCAL_ACTIVE_RMS = 0.005;
export const AUDIO_CLOCK_REMOTE_SILENCE_RMS = 0.012;
export const AUDIO_CLOCK_REQUIRED_SILENCE_MS = 200;
export const AUDIO_CLOCK_MAX_INTERRUPTION_MS = 250;
export const AUDIO_CLOCK_LOCAL_REGION_BRIDGE_MS = 500;

export type AudioClockProbeKind = "local" | "remote";
export type AudioClockSignalState = "active" | "silent";
export type AudioClockHarnessStatus =
  | "idle"
  | "connecting"
  | "awaiting_audio"
  | "observing"
  | "disconnecting"
  | "disconnected"
  | "error";
export type AudioClockFailureCode =
  | "disposed"
  | "cleanup_failed"
  | "duplicate_probe"
  | "frame_gap"
  | "inconsistent_quantum"
  | "message_gap"
  | "missing_input"
  | "probe_overflow"
  | "probe_setup_failed"
  | "sample_rate_mismatch"
  | "too_many_local_active_regions"
  | "unexpected_probe_message";

export interface AudioClockTransition {
  readonly state: AudioClockSignalState;
  readonly block_start_frame: number;
  readonly block_end_frame: number;
}

export interface AudioClockProbeEvidence {
  readonly attached: boolean;
  readonly exact_track_id: string | null;
  readonly threshold_rms: number;
  readonly silence_hold_frames: number;
  readonly processed_block_count: number;
  readonly latest_block_end_frame: number | null;
  readonly current_state: AudioClockSignalState | null;
  readonly current_state_block_count: number;
  readonly active_region_count: number;
  readonly transitions: readonly AudioClockTransition[];
  readonly overflow: boolean;
  readonly failure_code: AudioClockFailureCode | null;
}

export interface AudioClockEvidence {
  readonly schema_version: 1;
  readonly worklet_loaded: boolean;
  readonly sample_rate_hz: number;
  readonly quantum_frames: typeof AUDIO_CLOCK_QUANTUM_FRAMES;
  readonly local: AudioClockProbeEvidence;
  readonly remote: AudioClockProbeEvidence;
  readonly disposed: boolean;
}

export type AudioClockBracketFailure =
  | AudioClockFailureCode
  | "clock_not_prepared"
  | "interruption_exceeds_limit"
  | "local_active_region_count"
  | "local_probe_missing"
  | "remote_probe_missing"
  | "remote_sustained_silence_missing";

export interface AudioClockInterruptionBracket {
  readonly status: "pending" | "passed" | "failed";
  readonly failure_code: AudioClockBracketFailure | null;
  readonly sample_rate_hz: number;
  readonly quantum_frames: typeof AUDIO_CLOCK_QUANTUM_FRAMES;
  readonly required_silence_frames: number;
  readonly second_local_active_block_start_frame: number | null;
  readonly remote_silence_transition_block_end_frame: number | null;
  readonly interruption_upper_bound_frames: number | null;
  readonly interruption_upper_bound_ms: number | null;
}

interface AudioClockProbeMessage {
  readonly schema_version: 1;
  readonly kind: "observation" | "fault";
  readonly probe: AudioClockProbeKind;
  readonly message_sequence: number;
  readonly sample_rate_hz: number;
  readonly quantum_frames: number;
  readonly processed_block_count: number;
  readonly latest_block_end_frame: number;
  readonly current_state: AudioClockSignalState;
  readonly current_state_block_count: number;
  readonly active_region_count: number;
  readonly transition: AudioClockTransition | null;
  readonly failure_code?: AudioClockFailureCode;
}

interface MutableProbeState {
  attached: boolean;
  exactTrackId: string | null;
  thresholdRms: number;
  silenceHoldFrames: number;
  processedBlockCount: number;
  latestBlockEndFrame: number | null;
  currentState: AudioClockSignalState | null;
  currentStateBlockCount: number;
  activeRegionCount: number;
  transitions: AudioClockTransition[];
  overflow: boolean;
  failureCode: AudioClockFailureCode | null;
  expectedMessageSequence: number;
}

interface AudioClockPortLike {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  start?: () => void;
  close: () => void;
}

interface AudioClockNodeLike extends AudioNode {
  readonly port: AudioClockPortLike;
}

interface AudioClockRuntimeDependencies {
  readonly createModuleUrl: (source: string) => string;
  readonly revokeModuleUrl: (url: string) => void;
  readonly createWorkletNode: (
    context: AudioContext,
    name: string,
    options: AudioWorkletNodeOptions
  ) => AudioClockNodeLike;
}

interface AttachedProbe {
  readonly source: MediaStreamAudioSourceNode;
  readonly node: AudioClockNodeLike;
  readonly sink: GainNode;
}

export interface AudioClockDiagnostics {
  readonly attach: (
    probe: AudioClockProbeKind,
    track: MediaStreamTrack,
    thresholdRms: number
  ) => void;
  readonly read: () => AudioClockEvidence;
  readonly dispose: () => Promise<void>;
}

export function audioClockCleanupComplete(evidence: AudioClockEvidence): boolean {
  return (
    evidence.disposed &&
    evidence.local.failure_code === null &&
    evidence.remote.failure_code === null
  );
}

export function settleAudioClockHarnessStatus(
  currentStatus: AudioClockHarnessStatus,
  terminalConditionsMet: boolean
): AudioClockHarnessStatus {
  if (terminalConditionsMet && currentStatus !== "error") return "disconnected";
  return currentStatus;
}

function emptyProbe(thresholdRms: number, silenceHoldFrames = 0): MutableProbeState {
  return {
    attached: false,
    exactTrackId: null,
    thresholdRms,
    silenceHoldFrames,
    processedBlockCount: 0,
    latestBlockEndFrame: null,
    currentState: null,
    currentStateBlockCount: 0,
    activeRegionCount: 0,
    transitions: [],
    overflow: false,
    failureCode: null,
    expectedMessageSequence: 1,
  };
}

function freezeProbe(state: MutableProbeState): AudioClockProbeEvidence {
  return Object.freeze({
    attached: state.attached,
    exact_track_id: state.exactTrackId,
    threshold_rms: state.thresholdRms,
    silence_hold_frames: state.silenceHoldFrames,
    processed_block_count: state.processedBlockCount,
    latest_block_end_frame: state.latestBlockEndFrame,
    current_state: state.currentState,
    current_state_block_count: state.currentStateBlockCount,
    active_region_count: state.activeRegionCount,
    transitions: state.transitions.map((transition) => Object.freeze({ ...transition })),
    overflow: state.overflow,
    failure_code: state.failureCode,
  });
}

function safeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

const FAILURE_CODES = new Set<AudioClockFailureCode>([
  "disposed",
  "cleanup_failed",
  "duplicate_probe",
  "frame_gap",
  "inconsistent_quantum",
  "message_gap",
  "missing_input",
  "probe_overflow",
  "probe_setup_failed",
  "sample_rate_mismatch",
  "too_many_local_active_regions",
  "unexpected_probe_message",
]);

function isFailureCode(value: unknown): value is AudioClockFailureCode {
  return typeof value === "string" && FAILURE_CODES.has(value as AudioClockFailureCode);
}

function isProbeMessage(value: unknown): value is AudioClockProbeMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Partial<AudioClockProbeMessage>;
  return (
    message.schema_version === 1 &&
    (message.kind === "observation" || message.kind === "fault") &&
    (message.probe === "local" || message.probe === "remote") &&
    safeInteger(message.message_sequence) &&
    typeof message.sample_rate_hz === "number" &&
    safeInteger(message.quantum_frames) &&
    safeInteger(message.processed_block_count) &&
    safeInteger(message.latest_block_end_frame) &&
    (message.current_state === "active" || message.current_state === "silent") &&
    safeInteger(message.current_state_block_count) &&
    safeInteger(message.active_region_count)
  );
}

function failProbe(state: MutableProbeState, failureCode: AudioClockFailureCode): void {
  state.failureCode ??= failureCode;
}

function applyAudioClockProbeMessage(
  state: MutableProbeState,
  expectedProbe: AudioClockProbeKind,
  expectedSampleRate: number,
  value: unknown
): void {
  if (!isProbeMessage(value) || value.probe !== expectedProbe) {
    failProbe(state, "unexpected_probe_message");
    return;
  }
  if (value.message_sequence !== state.expectedMessageSequence) {
    failProbe(state, "message_gap");
    return;
  }
  state.expectedMessageSequence += 1;
  if (value.quantum_frames !== AUDIO_CLOCK_QUANTUM_FRAMES) {
    failProbe(state, "inconsistent_quantum");
    return;
  }
  if (value.sample_rate_hz !== expectedSampleRate) {
    failProbe(state, "sample_rate_mismatch");
    return;
  }
  if (
    value.processed_block_count < state.processedBlockCount ||
    value.active_region_count < state.activeRegionCount ||
    (state.latestBlockEndFrame !== null && value.latest_block_end_frame < state.latestBlockEndFrame)
  ) {
    failProbe(state, "frame_gap");
    return;
  }
  if (value.kind === "fault") {
    failProbe(
      state,
      isFailureCode(value.failure_code) ? value.failure_code : "unexpected_probe_message"
    );
    return;
  }
  state.processedBlockCount = value.processed_block_count;
  state.latestBlockEndFrame = value.latest_block_end_frame;
  state.currentState = value.current_state;
  state.currentStateBlockCount = value.current_state_block_count;
  state.activeRegionCount = value.active_region_count;
  if (expectedProbe === "local" && value.active_region_count > 2) {
    failProbe(state, "too_many_local_active_regions");
    return;
  }
  if (value.transition) {
    const previousTransition = state.transitions.at(-1);
    if (
      !safeInteger(value.transition.block_start_frame) ||
      !safeInteger(value.transition.block_end_frame) ||
      value.transition.block_end_frame - value.transition.block_start_frame !==
        AUDIO_CLOCK_QUANTUM_FRAMES ||
      value.transition.block_start_frame % AUDIO_CLOCK_QUANTUM_FRAMES !== 0 ||
      value.transition.block_end_frame > value.latest_block_end_frame ||
      (previousTransition !== undefined &&
        value.transition.block_start_frame < previousTransition.block_end_frame) ||
      (value.transition.state !== "active" && value.transition.state !== "silent")
    ) {
      failProbe(state, "inconsistent_quantum");
      return;
    }
    if (state.transitions.length >= AUDIO_CLOCK_MAX_TRANSITIONS) {
      state.overflow = true;
      failProbe(state, "probe_overflow");
      return;
    }
    state.transitions.push(Object.freeze({ ...value.transition }));
  }
}

function probeFailure(evidence: AudioClockEvidence): AudioClockFailureCode | null {
  return evidence.local.failure_code ?? evidence.remote.failure_code;
}

function transitionRunFrames(
  transitions: readonly AudioClockTransition[],
  index: number,
  latestBlockEndFrame: number | null
): number {
  const transition = transitions[index];
  if (!transition) return 0;
  const next = transitions[index + 1];
  const runEnd = next?.block_start_frame ?? latestBlockEndFrame;
  return runEnd === null ? 0 : runEnd - transition.block_start_frame;
}

export function interruptionClockBracket(
  evidence: AudioClockEvidence,
  requiredSilenceMs = AUDIO_CLOCK_REQUIRED_SILENCE_MS,
  maximumInterruptionMs = AUDIO_CLOCK_MAX_INTERRUPTION_MS
): AudioClockInterruptionBracket {
  const requiredSilenceFrames =
    Math.ceil((requiredSilenceMs * evidence.sample_rate_hz) / 1000 / AUDIO_CLOCK_QUANTUM_FRAMES) *
    AUDIO_CLOCK_QUANTUM_FRAMES;
  const pending = (
    failureCode: AudioClockBracketFailure,
    status: "pending" | "failed" = "pending"
  ): AudioClockInterruptionBracket => ({
    status,
    failure_code: failureCode,
    sample_rate_hz: evidence.sample_rate_hz,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    required_silence_frames: requiredSilenceFrames,
    second_local_active_block_start_frame: null,
    remote_silence_transition_block_end_frame: null,
    interruption_upper_bound_frames: null,
    interruption_upper_bound_ms: null,
  });

  if (!evidence.worklet_loaded || evidence.disposed) {
    return pending(evidence.disposed ? "disposed" : "clock_not_prepared", "failed");
  }
  if (
    evidence.quantum_frames !== AUDIO_CLOCK_QUANTUM_FRAMES ||
    !Number.isFinite(evidence.sample_rate_hz) ||
    evidence.sample_rate_hz <= 0
  ) {
    return pending("inconsistent_quantum", "failed");
  }
  const failure = probeFailure(evidence);
  if (failure) return pending(failure, "failed");
  if (!evidence.local.attached) return pending("local_probe_missing");
  if (!evidence.remote.attached) return pending("remote_probe_missing");
  if (evidence.local.active_region_count > 2) {
    return pending("too_many_local_active_regions", "failed");
  }
  const localActiveTransitions = evidence.local.transitions.filter(
    ({ state }) => state === "active"
  );
  if (evidence.local.active_region_count !== 2 || localActiveTransitions.length !== 2) {
    return pending("local_active_region_count");
  }
  const secondLocalStart = localActiveTransitions[1]?.block_start_frame;
  if (secondLocalStart === undefined) {
    return pending("local_active_region_count");
  }
  const remoteSilenceIndex = evidence.remote.transitions.findIndex(
    (transition, index, transitions) =>
      transition.state === "silent" &&
      transition.block_end_frame >= secondLocalStart &&
      transitionRunFrames(transitions, index, evidence.remote.latest_block_end_frame) >=
        requiredSilenceFrames
  );
  const remoteSilence = evidence.remote.transitions[remoteSilenceIndex];
  if (!remoteSilence) return pending("remote_sustained_silence_missing");

  const interruptionFrames = remoteSilence.block_end_frame - secondLocalStart;
  const interruptionMs =
    Math.ceil((interruptionFrames * 1_000_000) / evidence.sample_rate_hz) / 1_000;
  const base = {
    sample_rate_hz: evidence.sample_rate_hz,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    required_silence_frames: requiredSilenceFrames,
    second_local_active_block_start_frame: secondLocalStart,
    remote_silence_transition_block_end_frame: remoteSilence.block_end_frame,
    interruption_upper_bound_frames: interruptionFrames,
    interruption_upper_bound_ms: interruptionMs,
  } as const;
  if (interruptionFrames < 0 || interruptionMs > maximumInterruptionMs) {
    return {
      status: "failed",
      failure_code: "interruption_exceeds_limit",
      ...base,
    };
  }
  return { status: "passed", failure_code: null, ...base };
}

const WORKLET_SOURCE = `
class MurmurAudioClockProbe extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.probe = config.probe;
    this.threshold = config.thresholdRms;
    this.silenceHoldFrames = config.silenceHoldFrames;
    this.expectedFrame = null;
    this.processedBlocks = 0;
    this.state = null;
    this.stateBlocks = 0;
    this.activeRegions = 0;
    this.messageSequence = 0;
    this.transitionCount = 0;
    this.pendingSilenceStartFrame = null;
    this.pendingSilenceBlocks = 0;
    this.failed = false;
  }

  emit(kind, transition, failureCode) {
    this.messageSequence += 1;
    this.port.postMessage({
      schema_version: 1,
      kind,
      probe: this.probe,
      message_sequence: this.messageSequence,
      sample_rate_hz: sampleRate,
      quantum_frames: ${AUDIO_CLOCK_QUANTUM_FRAMES},
      processed_block_count: this.processedBlocks,
      latest_block_end_frame: currentFrame + ${AUDIO_CLOCK_QUANTUM_FRAMES},
      current_state: this.state || "silent",
      current_state_block_count: this.stateBlocks,
      active_region_count: this.activeRegions,
      transition,
      failure_code: failureCode,
    });
  }

  fail(code) {
    if (!this.failed) this.emit("fault", null, code);
    this.failed = true;
    return false;
  }

  process(inputs) {
    if (this.failed) return false;
    const block = inputs[0] && inputs[0][0];
    if (!block) return this.fail("missing_input");
    if (block.length !== ${AUDIO_CLOCK_QUANTUM_FRAMES}) {
      return this.fail("inconsistent_quantum");
    }
    if (this.expectedFrame !== null && currentFrame !== this.expectedFrame) {
      return this.fail("frame_gap");
    }
    this.expectedFrame = currentFrame + ${AUDIO_CLOCK_QUANTUM_FRAMES};
    this.processedBlocks += 1;
    let squared = 0;
    for (let index = 0; index < block.length; index += 1) {
      squared += block[index] * block[index];
    }
    const rawState = Math.sqrt(squared / block.length) >= this.threshold
      ? "active"
      : "silent";
    let transition = null;
    let nextState = this.state;
    let transitionStartFrame = currentFrame;
    if (rawState === "active") {
      this.pendingSilenceStartFrame = null;
      this.pendingSilenceBlocks = 0;
      nextState = "active";
    } else if (this.state === "silent") {
      nextState = "silent";
    } else {
      if (this.pendingSilenceStartFrame === null) {
        this.pendingSilenceStartFrame = currentFrame;
        this.pendingSilenceBlocks = 1;
      } else {
        this.pendingSilenceBlocks += 1;
      }
      const pendingFrames =
        this.pendingSilenceBlocks * ${AUDIO_CLOCK_QUANTUM_FRAMES};
      if (pendingFrames >= this.silenceHoldFrames) {
        nextState = "silent";
        transitionStartFrame = this.pendingSilenceStartFrame;
      }
    }
    if (nextState !== null && nextState !== this.state) {
      this.state = nextState;
      this.stateBlocks = nextState === "silent" ? this.pendingSilenceBlocks : 1;
      this.transitionCount += 1;
      if (nextState === "active") this.activeRegions += 1;
      transition = {
        state: nextState,
        block_start_frame: transitionStartFrame,
        block_end_frame: transitionStartFrame + ${AUDIO_CLOCK_QUANTUM_FRAMES},
      };
      if (this.transitionCount > ${AUDIO_CLOCK_MAX_TRANSITIONS}) {
        return this.fail("probe_overflow");
      }
      if (this.probe === "local" && this.activeRegions > 2) {
        return this.fail("too_many_local_active_regions");
      }
      if (nextState === "silent") {
        this.pendingSilenceStartFrame = null;
        this.pendingSilenceBlocks = 0;
      }
    } else if (this.state !== null) {
      this.stateBlocks += 1;
    }
    if (transition || (this.state !== null && this.processedBlocks % 8 === 0)) {
      this.emit("observation", transition, undefined);
    }
    return true;
  }
}

registerProcessor("${WORKLET_PROCESSOR_NAME}", MurmurAudioClockProbe);
`;

const DEFAULT_DEPENDENCIES: AudioClockRuntimeDependencies = {
  createModuleUrl: (source) => URL.createObjectURL(new Blob([source], { type: "text/javascript" })),
  revokeModuleUrl: (url) => URL.revokeObjectURL(url),
  createWorkletNode: (context, name, options) => new AudioWorkletNode(context, name, options),
};

export async function prepareAudioClockDiagnostics(
  context: AudioContext,
  dependencies: AudioClockRuntimeDependencies = DEFAULT_DEPENDENCIES
): Promise<AudioClockDiagnostics> {
  const moduleUrl = dependencies.createModuleUrl(WORKLET_SOURCE);
  try {
    await context.audioWorklet.addModule(moduleUrl);
  } finally {
    dependencies.revokeModuleUrl(moduleUrl);
  }

  const alignedFrames = (milliseconds: number): number =>
    Math.max(
      AUDIO_CLOCK_QUANTUM_FRAMES,
      Math.ceil((milliseconds * context.sampleRate) / 1000 / AUDIO_CLOCK_QUANTUM_FRAMES) *
        AUDIO_CLOCK_QUANTUM_FRAMES
    );
  const states: Record<AudioClockProbeKind, MutableProbeState> = {
    local: emptyProbe(
      AUDIO_CLOCK_LOCAL_ACTIVE_RMS,
      alignedFrames(AUDIO_CLOCK_LOCAL_REGION_BRIDGE_MS)
    ),
    remote: emptyProbe(
      AUDIO_CLOCK_REMOTE_SILENCE_RMS,
      alignedFrames(AUDIO_CLOCK_REQUIRED_SILENCE_MS)
    ),
  };
  const attached = new Map<AudioClockProbeKind, AttachedProbe>();
  let disposed = false;

  const attach = (
    probe: AudioClockProbeKind,
    track: MediaStreamTrack,
    thresholdRms: number
  ): void => {
    const state = states[probe];
    if (disposed) {
      failProbe(state, "disposed");
      return;
    }
    if (attached.has(probe)) {
      if (state.exactTrackId === track.id && state.thresholdRms === thresholdRms) {
        return;
      }
      failProbe(state, "duplicate_probe");
      return;
    }
    let source: MediaStreamAudioSourceNode | null = null;
    let node: AudioClockNodeLike | null = null;
    let sink: GainNode | null = null;
    try {
      source = context.createMediaStreamSource(new MediaStream([track]));
      node = dependencies.createWorkletNode(context, WORKLET_PROCESSOR_NAME, {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: {
          probe,
          thresholdRms,
          silenceHoldFrames: state.silenceHoldFrames,
        },
      });
      sink = context.createGain();
      sink.gain.value = 0;
      source.connect(node);
      node.connect(sink);
      sink.connect(context.destination);
      node.port.onmessage = (event) => {
        applyAudioClockProbeMessage(state, probe, context.sampleRate, event.data);
      };
      node.port.start?.();
      state.attached = true;
      state.exactTrackId = track.id;
      state.thresholdRms = thresholdRms;
      attached.set(probe, { source, node, sink });
    } catch {
      if (node) node.port.onmessage = null;
      try {
        node?.port.close();
      } catch {
        // Continue releasing every partially created node.
      }
      for (const candidate of [source, node, sink]) {
        try {
          candidate?.disconnect();
        } catch {
          // The generic setup failure below is the only persisted error.
        }
      }
      failProbe(state, "probe_setup_failed");
    }
  };

  const read = (): AudioClockEvidence =>
    Object.freeze({
      schema_version: 1,
      worklet_loaded: true,
      sample_rate_hz: context.sampleRate,
      quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
      local: freezeProbe(states.local),
      remote: freezeProbe(states.remote),
      disposed,
    });

  const dispose = async (): Promise<void> => {
    if (disposed) return;
    disposed = true;
    let cleanupFailed = false;
    for (const probe of attached.values()) {
      probe.node.port.onmessage = null;
      const cleanup = [
        () => probe.node.port.close(),
        () => probe.source.disconnect(),
        () => probe.node.disconnect(),
        () => probe.sink.disconnect(),
      ];
      for (const release of cleanup) {
        try {
          release();
        } catch {
          cleanupFailed = true;
        }
      }
    }
    attached.clear();
    try {
      if (context.state !== "closed") await context.close();
    } catch {
      cleanupFailed = true;
    }
    if (cleanupFailed) {
      failProbe(states.local, "cleanup_failed");
      failProbe(states.remote, "cleanup_failed");
    }
  };

  return Object.freeze({ attach, read, dispose });
}

export function emptyAudioClockEvidence(sampleRateHz = 0): AudioClockEvidence {
  return Object.freeze({
    schema_version: 1,
    worklet_loaded: false,
    sample_rate_hz: sampleRateHz,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    local: freezeProbe(emptyProbe(AUDIO_CLOCK_LOCAL_ACTIVE_RMS)),
    remote: freezeProbe(emptyProbe(AUDIO_CLOCK_REMOTE_SILENCE_RMS)),
    disposed: false,
  });
}
