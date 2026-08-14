import {
  endVoiceSession,
  VoiceSessionApiError,
  type VoiceAuthHeaderProvider,
  type VoiceSessionBootstrap,
} from "./session-api";

const RELEASE_MAX_ATTEMPTS = 3;
const RELEASE_ATTEMPT_TIMEOUT_MS = 5_000;
const RELEASE_RETRY_DELAYS_MS = [100, 250] as const;

export type VoiceAssignmentLocator = Pick<
  VoiceSessionBootstrap,
  "session_id" | "voice_call_id"
>;

interface AssignmentReleaseSchedulerOptions {
  readonly apiUrl?: string;
  readonly authHeaderProvider?: VoiceAuthHeaderProvider;
  readonly onLog?: (message: string) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : "Voice assignment release failed";
}

function createAbortError(): Error {
  const error = new Error("Voice assignment release was aborted");
  error.name = "AbortError";
  return error;
}

/** Settle even when token acquisition or a mocked request ignores its signal. */
function settleOnAbort<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(createAbortError());

  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      reject(createAbortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      }
    );
  });
}

function isRetryableReleaseFailure(error: unknown): boolean {
  if (!(error instanceof VoiceSessionApiError)) return true;
  if (error.status === undefined) return true;
  // Release conflicts encode a durable scope/state mismatch; rate limits do not.
  return error.status === 429 || error.status >= 500;
}

/**
 * Owns the bounded, best-effort lifecycle for server assignment release.
 *
 * This object intentionally has no React dependency. `mount`/`dispose` mirror
 * an owning hook's effect lifecycle, including React Strict Mode's
 * setup-cleanup-setup sequence.
 */
export class AssignmentReleaseScheduler {
  private mounted = true;
  private readonly releasedCallIds = new Set<string>();
  private readonly assignments = new Map<string, VoiceAssignmentLocator>();
  private readonly releasePromises = new Map<string, Promise<void>>();
  private readonly releaseResolvers = new Map<string, () => void>();
  private readonly retryTimers = new Map<
    string,
    ReturnType<typeof setTimeout>
  >();
  private readonly attemptTimers = new Map<
    string,
    ReturnType<typeof setTimeout>
  >();
  private readonly abortControllers = new Map<string, AbortController>();

  constructor(private options: AssignmentReleaseSchedulerOptions) {}

  configure(options: AssignmentReleaseSchedulerOptions): void {
    this.options = options;
  }

  mount(): void {
    this.mounted = true;
  }

  /** Allow a newly confirmed assignment to be released, even after an old retry. */
  markAssigned(voiceCallId: string): void {
    this.releasedCallIds.delete(voiceCallId);
  }

  release(assignment: VoiceAssignmentLocator | null): Promise<void> {
    if (!assignment) return Promise.resolve();

    const callId = assignment.voice_call_id;
    const pending = this.releasePromises.get(callId);
    if (pending) return pending;
    if (this.releasedCallIds.has(callId)) return Promise.resolve();

    this.releasedCallIds.add(callId);
    this.assignments.set(callId, assignment);
    const releasePromise = new Promise<void>((resolve) => {
      this.releaseResolvers.set(callId, resolve);
    });
    this.releasePromises.set(callId, releasePromise);

    if (!this.mounted) {
      // Fetch keepalive owns page-exit delivery; no timers may outlive the hook.
      void endVoiceSession(
        { session_id: assignment.session_id, voice_call_id: callId },
        {
          apiUrl: this.options.apiUrl,
          authHeaderProvider: this.options.authHeaderProvider,
        }
      )
        .catch(() => undefined)
        .then(() => this.finishRelease(callId));
      return releasePromise;
    }

    this.attemptRelease(assignment, 1);
    return releasePromise;
  }

  dispose(): void {
    this.mounted = false;
    const unfinishedCallIds = new Set([
      ...this.retryTimers.keys(),
      ...this.abortControllers.keys(),
    ]);
    for (const timer of this.retryTimers.values()) clearTimeout(timer);
    this.retryTimers.clear();
    for (const timer of this.attemptTimers.values()) clearTimeout(timer);
    this.attemptTimers.clear();
    for (const controller of this.abortControllers.values()) controller.abort();
    this.abortControllers.clear();
    // An aborted or not-yet-retried request did not prove server-side release.
    // Let the owning hook issue one final timer-free keepalive request for the
    // same assignment after disposal. Completed calls remain deduplicated.
    for (const callId of unfinishedCallIds) {
      const assignment = this.assignments.get(callId);
      this.releasedCallIds.delete(callId);
      this.finishRelease(callId);
      if (assignment) void this.release(assignment);
    }
  }

  private finishRelease(callId: string): void {
    const resolve = this.releaseResolvers.get(callId);
    this.releaseResolvers.delete(callId);
    this.releasePromises.delete(callId);
    this.assignments.delete(callId);
    resolve?.();
  }

  private attemptRelease(
    assignment: VoiceAssignmentLocator,
    attempt: number
  ): void {
    const callId = assignment.voice_call_id;
    const abortController = new AbortController();
    this.abortControllers.set(callId, abortController);
    const attemptTimer = setTimeout(() => {
      abortController.abort();
    }, RELEASE_ATTEMPT_TIMEOUT_MS);
    this.attemptTimers.set(callId, attemptTimer);

    const finishAttempt = () => {
      if (this.abortControllers.get(callId) === abortController) {
        this.abortControllers.delete(callId);
      }
      if (this.attemptTimers.get(callId) === attemptTimer) {
        clearTimeout(attemptTimer);
        this.attemptTimers.delete(callId);
      }
    };

    const handleFailure = (error: unknown) => {
      if (!this.mounted) return;
      const retryable = isRetryableReleaseFailure(error);
      if (attempt >= RELEASE_MAX_ATTEMPTS || !retryable) {
        if (retryable) this.releasedCallIds.delete(callId);
        this.options.onLog?.(
          `Voice assignment release failed: ${errorMessage(error)}`
        );
        this.finishRelease(callId);
        return;
      }

      const delay =
        RELEASE_RETRY_DELAYS_MS[attempt - 1] ??
        RELEASE_RETRY_DELAYS_MS[RELEASE_RETRY_DELAYS_MS.length - 1];
      const timer = setTimeout(() => {
        this.retryTimers.delete(callId);
        if (this.mounted) this.attemptRelease(assignment, attempt + 1);
      }, delay);
      this.retryTimers.set(callId, timer);
      this.options.onLog?.(
        `Voice assignment release failed; retrying ${attempt + 1}/${RELEASE_MAX_ATTEMPTS}`
      );
    };

    try {
      void settleOnAbort(
        endVoiceSession(
          { session_id: assignment.session_id, voice_call_id: callId },
          {
            apiUrl: this.options.apiUrl,
            signal: abortController.signal,
            authHeaderProvider: this.options.authHeaderProvider,
          }
        ),
        abortController.signal
      ).then(
        () => {
          finishAttempt();
          this.finishRelease(callId);
        },
        (error: unknown) => {
          finishAttempt();
          handleFailure(error);
        }
      );
    } catch (error) {
      finishAttempt();
      handleFailure(error);
    }
  }
}
