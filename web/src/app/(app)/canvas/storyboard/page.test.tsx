import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/live-scene/live-semantic-storyboard", () => ({
  LiveSemanticStoryboard: () => null,
}));

import { LiveSemanticStoryboard } from "@/features/live-scene/live-semantic-storyboard";

import SemanticStoryboardCanvasPage, { metadata } from "./page";

describe("SemanticStoryboardCanvasPage", () => {
  it("mounts the authenticated default runner with product metadata", () => {
    const storyboard = SemanticStoryboardCanvasPage();

    expect(storyboard.type).toBe(LiveSemanticStoryboard);
    expect(storyboard.props).toEqual({});
    expect(metadata).toMatchObject({
      title: "Live semantic storyboard · Murmur",
      description: expect.stringContaining("interruptible visual explanation"),
    });
  });
});
