/** @vitest-environment happy-dom */

import { afterEach, describe, expect, it } from "vitest";

import { animateDrawOn, resolveCssColor } from "./gsap-setup";

afterEach(() => {
  document.documentElement.style.removeProperty("--test-fill");
  document.body.replaceChildren();
});

describe("animateDrawOn", () => {
  it("animates a theme-token fill without handing an unresolved color to GSAP", () => {
    document.documentElement.style.setProperty("--test-fill", "38 91% 55%");
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "hsl(var(--test-fill))");
    path.getTotalLength = () => 100;
    group.appendChild(path);
    document.body.appendChild(group);

    expect(resolveCssColor("hsl(var(--test-fill))")).toBe("hsl(38, 91%, 55%)");

    const timeline = animateDrawOn(group);

    expect(() => timeline.progress(1)).not.toThrow();
    expect(path.getAttribute("fill")).toBe("hsl(var(--test-fill))");
  });
});
