import { describe, expect, it } from "vitest";

import {
  LIVE_SCENE_MAX_SSE_EVENT_BYTES,
  LiveSceneSseDecoder,
  LiveSceneSseError,
} from "./sse";

const encoder = new TextEncoder();

function decodeChunks(chunks: readonly Uint8Array[]) {
  const decoder = new LiveSceneSseDecoder();
  const events = chunks.flatMap((chunk) => [...decoder.push(chunk)]);
  events.push(...decoder.finish());
  return events;
}

function splitAt(bytes: Uint8Array, index: number): readonly Uint8Array[] {
  return [bytes.slice(0, index), bytes.slice(index)];
}

function errorCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneSseError ? error.code : undefined;
  }
  return undefined;
}

describe("LiveSceneSseDecoder", () => {
  it("reconstructs the same UTF-8 event at every byte split", () => {
    const wire = encoder.encode(
      'event: scene_patch\ndata: {"narration":"Draw π → 😀"}\n\n'
    );
    const expected = [
      {
        event: "scene_patch",
        data: '{"narration":"Draw π → 😀"}',
      },
    ];

    for (let index = 0; index <= wire.length; index += 1) {
      expect(decodeChunks(splitAt(wire, index))).toEqual(expected);
    }
    expect(decodeChunks(Array.from(wire, (byte) => Uint8Array.of(byte)))).toEqual(
      expected
    );
  });

  it("decodes multiple CRLF events, comments, multiline data, and persistent IDs", () => {
    const decoder = new LiveSceneSseDecoder();
    const first = decoder.push(
      encoder.encode(
        ": heartbeat\r\nid: patch-7\r\nevent: scene_patch\r\ndata: first\r\ndata: second\r\n\r\n" +
          "event: completed\r\ndata: done\r\n\r\n"
      )
    );

    expect(first).toEqual([
      {
        event: "scene_patch",
        data: "first\nsecond",
        id: "patch-7",
      },
      {
        event: "completed",
        data: "done",
        id: "patch-7",
      },
    ]);
    expect(Object.isFrozen(first)).toBe(true);
    expect(first.every(Object.isFrozen)).toBe(true);
    expect(decoder.finish()).toEqual([]);
  });

  it("preserves multiple event boundaries at every network split", () => {
    const wire = encoder.encode(
      "event: scene_patch\r\ndata: one 😀\r\n\r\n" +
        "event: scene_patch\r\ndata: two π\r\n\r\n"
    );
    const expected = [
      { event: "scene_patch", data: "one 😀" },
      { event: "scene_patch", data: "two π" },
    ];

    for (let index = 0; index <= wire.length; index += 1) {
      expect(decodeChunks(splitAt(wire, index))).toEqual(expected);
    }
  });

  it("treats CR, LF, and CRLF as line endings even when CRLF is split", () => {
    const decoder = new LiveSceneSseDecoder();

    expect(decoder.push(encoder.encode("event: scene_patch\r"))).toEqual([]);
    expect(decoder.push(encoder.encode("\ndata: one\r\rdata: two\n\n"))).toEqual([
      { event: "scene_patch", data: "one" },
      { event: "message", data: "two" },
    ]);
    expect(decoder.finish()).toEqual([]);
  });

  it("emits an unterminated final event at EOF", () => {
    const decoder = new LiveSceneSseDecoder();

    expect(decoder.push(encoder.encode("event: scene_patch\ndata: final"))).toEqual([]);
    expect(decoder.finish()).toEqual([
      { event: "scene_patch", data: "final" },
    ]);
  });

  it("emits explicit empty data and ignores records without data", () => {
    const decoder = new LiveSceneSseDecoder();

    expect(
      decoder.push(
        encoder.encode("event: ignored\nretry: 100\n\nevent: empty\ndata:\n\n")
      )
    ).toEqual([{ event: "empty", data: "" }]);
    expect(decoder.finish()).toEqual([]);
  });

  it("applies the byte budget per event rather than per network chunk", () => {
    const decoder = new LiveSceneSseDecoder(11);
    expect(decoder.push(encoder.encode("data: one\n\ndata: two\n\n"))).toEqual([
      { event: "message", data: "one" },
      { event: "message", data: "two" },
    ]);

    const exact = new LiveSceneSseDecoder(11);
    expect(exact.push(encoder.encode("data: abc\n\n"))).toEqual([
      { event: "message", data: "abc" },
    ]);

    const oversized = new LiveSceneSseDecoder(10);
    expect(() => oversized.push(encoder.encode("data: abc\n\n"))).toThrow(
      "exceeds the 10-byte limit"
    );
    expect(errorCode(() => oversized.push(Uint8Array.of()))).toBe("decoder_closed");
  });

  it("counts UTF-8 bytes and bounds unterminated input", () => {
    const emojiFrame = encoder.encode("data: 😀\n\n");
    expect(emojiFrame.byteLength).toBe(12);
    expect(new LiveSceneSseDecoder(12).push(emojiFrame)).toHaveLength(1);
    expect(() => new LiveSceneSseDecoder(11).push(emojiFrame)).toThrow(
      "11-byte limit"
    );

    const unterminated = new LiveSceneSseDecoder(8);
    expect(() => unterminated.push(encoder.encode("data: abc"))).toThrow(
      "8-byte limit"
    );
  });

  it("rejects malformed UTF-8 and cannot be reused after failure or EOF", () => {
    const invalid = new LiveSceneSseDecoder();
    expect(errorCode(() => invalid.push(Uint8Array.of(0xc3, 0x28)))).toBe(
      "invalid_utf8"
    );
    expect(errorCode(() => invalid.finish())).toBe("decoder_closed");

    const closed = new LiveSceneSseDecoder();
    expect(closed.finish()).toEqual([]);
    expect(errorCode(() => closed.push(Uint8Array.of()))).toBe("decoder_closed");
    expect(errorCode(() => closed.finish())).toBe("decoder_closed");
  });

  it("uses a 64 KiB default and requires a positive safe custom limit", () => {
    expect(new LiveSceneSseDecoder().maxEventBytes).toBe(
      LIVE_SCENE_MAX_SSE_EVENT_BYTES
    );
    for (const value of [0, -1, 1.5, Number.POSITIVE_INFINITY]) {
      expect(() => new LiveSceneSseDecoder(value)).toThrow(RangeError);
    }
  });
});
