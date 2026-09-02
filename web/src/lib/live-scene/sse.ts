// The model NDJSON frame is capped at 64 KiB server-side. The authoritative
// SSE envelope has a separate budget for lifecycle metadata and normalization.
export const LIVE_SCENE_MAX_SSE_EVENT_BYTES = 96 * 1024;

export type LiveSceneSseErrorCode =
  | "invalid_utf8"
  | "event_too_large"
  | "decoder_closed";

export class LiveSceneSseError extends Error {
  readonly code: LiveSceneSseErrorCode;

  constructor(code: LiveSceneSseErrorCode, message: string) {
    super(`Invalid live scene SSE stream: ${message}`);
    this.name = "LiveSceneSseError";
    this.code = code;
  }
}

export interface LiveSceneSseEvent {
  readonly event: string;
  readonly data: string;
  readonly id?: string;
}

interface ParsedLine {
  readonly line: string;
  readonly terminatorBytes: number;
}

/**
 * Stateful byte-level SSE decoder for the live-scene stream.
 *
 * It deliberately exposes complete SSE records rather than JSON so protocol
 * validation remains a separate trust boundary.
 */
export class LiveSceneSseDecoder {
  readonly maxEventBytes: number;

  private decoder = new TextDecoder("utf-8", { fatal: true });
  private readonly encoder = new TextEncoder();
  private textBuffer = "";
  private eventBytes = 0;
  private dataLines: string[] = [];
  private eventName = "";
  private pendingId: string | undefined;
  private lastEventId = "";
  private hasData = false;
  private closed = false;
  private failed = false;

  constructor(maxEventBytes = LIVE_SCENE_MAX_SSE_EVENT_BYTES) {
    if (!Number.isSafeInteger(maxEventBytes) || maxEventBytes <= 0) {
      throw new RangeError("maxEventBytes must be a positive safe integer");
    }
    this.maxEventBytes = maxEventBytes;
  }

  push(chunk: Uint8Array): readonly LiveSceneSseEvent[] {
    this.assertOpen();
    if (!(chunk instanceof Uint8Array)) {
      throw new TypeError("LiveSceneSseDecoder.push requires a Uint8Array");
    }

    try {
      this.textBuffer += this.decoder.decode(chunk, { stream: true });
    } catch {
      return this.poison("invalid_utf8", "stream contains malformed UTF-8");
    }

    const events = this.drainLines(false);
    this.assertBufferedSize();
    return Object.freeze(events);
  }

  /** Flush the TextDecoder and emit a final unterminated SSE record at EOF. */
  finish(): readonly LiveSceneSseEvent[] {
    this.assertOpen();
    this.closed = true;

    try {
      this.textBuffer += this.decoder.decode();
    } catch {
      return this.poison("invalid_utf8", "stream ended with malformed UTF-8");
    }

    const events = this.drainLines(true);
    if (this.textBuffer.length > 0) {
      const finalLine = this.textBuffer;
      this.textBuffer = "";
      this.accountLine(finalLine, 0);
      this.consumeLine(finalLine);
    }

    const finalEvent = this.dispatchEvent();
    if (finalEvent) events.push(finalEvent);
    return Object.freeze(events);
  }

  private assertOpen(): void {
    if (this.closed || this.failed) {
      throw new LiveSceneSseError("decoder_closed", "decoder cannot accept more bytes");
    }
  }

  private poison(code: LiveSceneSseErrorCode, message: string): never {
    this.failed = true;
    throw new LiveSceneSseError(code, message);
  }

  private drainLines(final: boolean): LiveSceneSseEvent[] {
    const events: LiveSceneSseEvent[] = [];
    while (true) {
      const parsed = this.takeLine(final);
      if (!parsed) break;
      this.accountLine(parsed.line, parsed.terminatorBytes);
      if (parsed.line.length === 0) {
        const event = this.dispatchEvent();
        if (event) events.push(event);
        this.eventBytes = 0;
      } else {
        this.consumeLine(parsed.line);
      }
    }
    return events;
  }

  private takeLine(final: boolean): ParsedLine | undefined {
    let index = -1;
    for (let cursor = 0; cursor < this.textBuffer.length; cursor += 1) {
      const character = this.textBuffer[cursor];
      if (character === "\n" || character === "\r") {
        index = cursor;
        break;
      }
    }
    if (index < 0) return undefined;

    const character = this.textBuffer[index];
    if (character === "\r" && index === this.textBuffer.length - 1 && !final) {
      return undefined;
    }
    const crlf = character === "\r" && this.textBuffer[index + 1] === "\n";
    const consumed = index + (crlf ? 2 : 1);
    const line = this.textBuffer.slice(0, index);
    this.textBuffer = this.textBuffer.slice(consumed);
    return { line, terminatorBytes: crlf ? 2 : 1 };
  }

  private accountLine(line: string, terminatorBytes: number): void {
    this.eventBytes += this.encoder.encode(line).byteLength + terminatorBytes;
    if (this.eventBytes > this.maxEventBytes) {
      this.poison(
        "event_too_large",
        `one event exceeds the ${this.maxEventBytes}-byte limit`
      );
    }
  }

  private assertBufferedSize(): void {
    const bufferedBytes = this.encoder.encode(this.textBuffer).byteLength;
    if (this.eventBytes + bufferedBytes > this.maxEventBytes) {
      this.poison(
        "event_too_large",
        `one event exceeds the ${this.maxEventBytes}-byte limit`
      );
    }
  }

  private consumeLine(line: string): void {
    if (line.startsWith(":")) return;

    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "data":
        this.hasData = true;
        this.dataLines.push(value);
        break;
      case "event":
        this.eventName = value;
        break;
      case "id":
        if (!value.includes("\0")) this.pendingId = value;
        break;
      default:
        // `retry` and extension fields do not affect live-scene records.
        break;
    }
  }

  private dispatchEvent(): LiveSceneSseEvent | undefined {
    if (this.pendingId !== undefined) this.lastEventId = this.pendingId;
    const event = this.hasData
      ? Object.freeze({
          event: this.eventName || "message",
          data: this.dataLines.join("\n"),
          ...(this.lastEventId === "" ? {} : { id: this.lastEventId }),
        })
      : undefined;

    this.dataLines = [];
    this.eventName = "";
    this.pendingId = undefined;
    this.hasData = false;
    return event;
  }
}
