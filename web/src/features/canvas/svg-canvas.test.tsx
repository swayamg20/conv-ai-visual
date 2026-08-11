/** @vitest-environment happy-dom */

import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { SVGCanvas } from "@/components/svg-canvas";

import type { SVGCanvasHandle } from "./types";

const actEnvironment = globalThis as typeof globalThis & {
  IS_REACT_ACT_ENVIRONMENT: boolean;
};
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  document.body.replaceChildren();
});

describe("SVGCanvas", () => {
  it("renders direct and caller-controlled scenes through its public handle", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const canvas = createRef<SVGCanvasHandle>();

    await act(async () => {
      root.render(<SVGCanvas ref={canvas} width={320} height={220} showGrid={false} />);
    });
    act(() => {
      canvas.current?.render([
        {
          action: "text",
          id: "answer",
          text: "x = 4",
          x: 13,
          y: 31,
          animate_style: "none",
        },
      ]);
    });

    const rendered = host.querySelector<SVGGElement>("[data-element-id='answer']");
    expect(rendered?.querySelector("text")?.textContent).toBe("x = 4");
    expect(rendered?.querySelector("text")?.getAttribute("x")).toBe("20");
    expect(rendered?.querySelector("text")?.getAttribute("y")).toBe("40");

    const timeline = canvas.current?.createPausedSequence({
      steps: [
        {
          action: "text",
          target_id: "sequence-answer",
          text: "2 + 2 = 4",
          x: 100,
          y: 100,
          animate_style: "none",
        },
      ],
    });
    act(() => {
      timeline?.progress(1);
    });
    expect(
      host.querySelector("[data-element-id='sequence-answer'] text")?.textContent
    ).toBe("2 + 2 = 4");

    await act(async () => root.unmount());
  });
});
