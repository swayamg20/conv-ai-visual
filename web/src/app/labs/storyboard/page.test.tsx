import { afterEach, describe, expect, it, vi } from "vitest";

const route = vi.hoisted(() => ({
  fixtureRunner: vi.fn(),
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  runner: vi.fn(),
}));

vi.mock("next/navigation", () => ({ notFound: route.notFound }));

vi.mock(
  "@/features/live-scene/semantic-storyboard-scene-stream-fixture",
  () => ({
    createSemanticStoryboardFixtureRunner: route.fixtureRunner.mockReturnValue(
      route.runner,
    ),
  }),
);

vi.mock("@/features/live-scene/live-semantic-storyboard", () => ({
  LiveSemanticStoryboard: () => null,
}));

import { LiveSemanticStoryboard } from "@/features/live-scene/live-semantic-storyboard";

import { SemanticStoryboardLabClient } from "./client";
import SemanticStoryboardLabPage, { metadata } from "./page";

afterEach(() => {
  vi.unstubAllEnvs();
  route.notFound.mockClear();
});

describe("SemanticStoryboardLabPage", () => {
  it("requires development mode even when the lab flag is enabled", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("MURMUR_SCENE_LAB", "1");

    expect(() => SemanticStoryboardLabPage()).toThrow("NEXT_NOT_FOUND");
    expect(route.notFound).toHaveBeenCalledOnce();
  });

  it("requires the explicit scene-lab flag in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("MURMUR_SCENE_LAB", "0");

    expect(() => SemanticStoryboardLabPage()).toThrow("NEXT_NOT_FOUND");
    expect(route.notFound).toHaveBeenCalledOnce();
  });

  it("mounts the provider-free client and accelerates only in E2E mode", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("MURMUR_SCENE_LAB", "1");
    vi.stubEnv("MURMUR_E2E_MODE", "1");

    const page = SemanticStoryboardLabPage();

    expect(page.type).toBe(SemanticStoryboardLabClient);
    expect(page.props).toEqual({ playbackRate: 16 });
  });

  it("uses normal presentation timing outside E2E mode", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("MURMUR_SCENE_LAB", "1");
    vi.stubEnv("MURMUR_E2E_MODE", "0");

    expect(SemanticStoryboardLabPage().props).toEqual({ playbackRate: 1 });
  });

  it("keeps fixture transport in the client boundary", () => {
    const storyboard = SemanticStoryboardLabClient({ playbackRate: 1 });

    expect(route.fixtureRunner).toHaveBeenCalledOnce();
    expect(storyboard.type).toBe(LiveSemanticStoryboard);
    expect(storyboard.props).toEqual({
      backHref: "/",
      playbackRate: 1,
      runStream: route.runner,
    });
    expect(metadata).toMatchObject({
      title: "Live semantic storyboard lab · Murmur",
      robots: { index: false, follow: false },
    });
  });
});
