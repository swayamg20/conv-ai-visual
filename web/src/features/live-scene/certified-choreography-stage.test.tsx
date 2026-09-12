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
type LegacyStageProps = Extract<
  React.ComponentProps<typeof CertifiedChoreographyStage>,
  { readonly progress?: undefined }
>;

async function renderStageElement(
  element: React.ReactElement,
): Promise<HTMLDivElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(element);
  });
  return container;
}

async function renderStage(
  overrides: Partial<LegacyStageProps> = {},
): Promise<HTMLDivElement> {
  return renderStageElement(
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

  it("keeps the canonical bounded progress model DOM-identical to legacy props", async () => {
    const legacy = await renderStage();
    const canonical = await renderStageElement(
      <CertifiedChoreographyStage
        canvasRef={createRef<SVGCanvasHandle>()}
        phase="completed"
        layout="cinematic"
        subjectLabel="Projectile motion"
        checkpointLabel="trace ascent"
        progress={{
          kind: "bounded",
          settledMainCount: 4,
          totalMainCount: 6,
          settledDetailLabels: ["Horizontal speed", "Apex acceleration"],
        }}
        caption="The marker follows equal-time samples along the arc."
        rendererTrusted
        reducedMotion
        playbackRate={16}
        testId="projectile-stage"
        dataAttributes={{ "data-checkpoint-id": "trace_ascent" }}
      />,
    );

    expect(canonical.innerHTML).toBe(legacy.innerHTML);
  });

  it("renders an honest open frontier without inventing a total", async () => {
    const container = await renderStageElement(
      <CertifiedChoreographyStage
        canvasRef={createRef<SVGCanvasHandle>()}
        phase="streaming"
        layout="compact"
        subjectLabel="Live storyboard"
        checkpointLabel="A relationship takes shape"
        progress={{
          kind: "open",
          settledBeatCount: 3,
          frontierStatus: "live",
          recentCertifiedLabels: ["Question anchored", "Cause connected"],
        }}
        caption="A new relation extends the accepted visual frontier."
        rendererTrusted
        testId="storyboard-stage"
      />,
    );
    const stage = container.querySelector<HTMLElement>(
      '[data-testid="storyboard-stage"]',
    );
    const progress = container.querySelector<HTMLElement>(
      '[data-testid="open-choreography-progress"]',
    );

    expect(stage?.dataset.settledBeatCount).toBe("3");
    expect(stage?.hasAttribute("data-settled-main-count")).toBe(false);
    expect(progress?.getAttribute("aria-label")).toBe(
      "3 certified beats settled; frontier live",
    );
    expect(progress?.textContent).toContain("3 beats settled");
    expect(progress?.textContent).toContain("Frontier live");
    expect(
      progress?.querySelectorAll('[data-certified-label="true"]'),
    ).toHaveLength(2);
    expect(progress?.textContent).toContain("Question anchored");
    expect(progress?.textContent).toContain("Cause connected");
    expect(progress?.textContent).not.toContain(" of ");
    expect(progress?.querySelectorAll(".w-3")).toHaveLength(0);
  });
});
