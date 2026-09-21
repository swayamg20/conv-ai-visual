/** @vitest-environment happy-dom */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import LandingPage from "./page";

function expectVisibleBeforeHydration(element: Element | null, boundary: Element): void {
  expect(element).not.toBeNull();

  let current = element;
  while (current && current !== boundary) {
    const style = current.getAttribute("style") ?? "";
    expect(style).not.toMatch(/(?:^|;)\s*opacity:\s*0(?:;|$)/);
    expect(style).not.toMatch(/translate(?:X|Y|3d)?\(/);
    current = current.parentElement;
  }
}

describe("marketing landing hero", () => {
  it("keeps its critical copy and action visible before hydration", () => {
    document.body.innerHTML = renderToStaticMarkup(<LandingPage />);

    const hero = document.querySelector("section");
    expect(hero).not.toBeNull();
    if (!hero) {
      return;
    }

    const heading = hero.querySelector("h1");
    const description = hero.querySelector("p");
    const primaryAction = hero.querySelector('a[href="/register"]');

    expect(heading?.textContent).toContain("Your AI tutor that draws while it talks");
    expect(description?.textContent).toContain("Murmur listens");
    expect(primaryAction?.textContent).toContain("Get Started");
    expectVisibleBeforeHydration(heading, hero);
    expectVisibleBeforeHydration(description, hero);
    expectVisibleBeforeHydration(primaryAction, hero);
  });

  it("disables the hero badge pulse for reduced-motion users", () => {
    document.body.innerHTML = renderToStaticMarkup(<LandingPage />);

    const pulse = document.querySelector(".animate-pulse");
    expect(pulse?.classList.contains("motion-reduce:animate-none")).toBe(true);
  });
});
