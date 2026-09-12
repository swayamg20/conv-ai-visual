/** @vitest-environment happy-dom */

import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SVGCanvasHandle } from "@/features/canvas/types";

const canvas = vi.hoisted(() => ({ renderProps: vi.fn() }));

vi.mock("@/components/svg-canvas", async () => {
  const React = await import("react");
  return {
    SVGCanvas: React.forwardRef(function MockCanvas(props) {
      canvas.renderProps(props);
      return React.createElement("svg", { "data-testid": "certified-canvas" });
    }),
  };
});

import { CertifiedChoreographyStage } from "./certified-choreography-stage";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

async function renderStage(
  overrides: Partial<
    React.ComponentProps<typeof CertifiedChoreographyStage>
  > = {},
): Promise<HTMLDivElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(
      <CertifiedChoreographyStage
        canvasRef={createRef<SVGCanvasHandle>()}
        phase="completed"
        layout="cinematic"
        subjectLabel="Projectile motion"
        checkpointLabel="trace ascent"
        settledMainCount={4}
        totalMainCount={6}
        settledDetailLabels={["Horizontal speed", "Apex acceleration"]}
        caption="The marker follows equal-time samples along the arc."
        rendererTrusted
        reducedMotion
        playbackRate={16}
        testId="projectile-stage"
        dataAttributes={{ "data-checkpoint-id": "trace_ascent" }}
        {...overrides}
      />,
    );
  });
  return container;
}

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  document.body.replaceChildren();
  canvas.renderProps.mockReset();
});

describe("CertifiedChoreographyStage", () => {
  it("renders an N-part rail, settled details, caption, and domain data", async () => {
    const container = await renderStage();
    const stage = container.querySelector<HTMLElement>(
      '[data-testid="projectile-stage"]',
    );
    const progress = container.querySelector(
      '[aria-label="4 of 6 main checkpoints settled"]',
    );
    const segments = progress?.querySelectorAll("span") ?? [];

    expect(stage?.dataset.phase).toBe("completed");
    expect(stage?.dataset.layout).toBe("cinematic");
    expect(stage?.dataset.checkpointId).toBe("trace_ascent");
    expect(stage?.dataset.settledMainCount).toBe("4");
    expect(segments).toHaveLength(6);
    expect(
      [...segments]
        .slice(0, 4)
        .every((item) => item.classList.contains("bg-amber")),
    ).toBe(true);
    expect(
      [...segments]
        .slice(4)
        .every((item) => item.classList.contains("bg-chalk-faint")),
    ).toBe(true);
    expect(container.textContent).toContain("Projectile motion");
    expect(container.textContent).toContain("trace ascent");
    expect(container.textContent).toContain("Horizontal speed");
    expect(container.textContent).toContain("Apex acceleration");
    expect(container.textContent).toContain("equal-time samples");
    expect(canvas.renderProps).toHaveBeenCalledWith(
      expect.objectContaining({
        reducedMotion: true,
        choreographyPlaybackRate: 16,
        viewportInteractionLocked: true,
      }),
    );
  });

  it("clamps progress, supports custom narration, and quarantines untrusted renderers", async () => {
    const container = await renderStage({
      settledMainCount: 20,
      rendererTrusted: false,
      progressAriaLabel: "All six projectile chapters settled",
      settledDetailLabels: [],
      dataAttributes: { "data-layout": "untrusted" },
    });
    const stage = container.querySelector<HTMLElement>(
      '[data-testid="projectile-stage"]',
    );

    expect(stage?.dataset.settledMainCount).toBe("6");
    expect(stage?.dataset.layout).toBe("cinematic");
    expect(
      container.querySelector(
        '[aria-label="All six projectile chapters settled"]',
      ),
    ).not.toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "Board quarantined",
    );
    expect(
      container.querySelector('[data-testid="live-choreography-board"]')
        ?.className,
    ).toContain("opacity-0");
  });
});
