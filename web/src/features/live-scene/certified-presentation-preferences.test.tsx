/** @vitest-environment happy-dom */

import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CERTIFIED_COMPACT_LAYOUT_QUERY,
  CERTIFIED_REDUCED_MOTION_QUERY,
  type CertifiedPresentationPreferenceOverrides,
  useCertifiedPresentationPreferences,
} from "./certified-presentation-preferences";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

interface PreferenceHarnessProps {
  readonly overrides?: CertifiedPresentationPreferenceOverrides;
}

function PreferenceHarness({ overrides }: PreferenceHarnessProps) {
  const preferences = useCertifiedPresentationPreferences(overrides);
  return (
    <output
      data-testid="presentation-preferences"
      data-layout={preferences?.layout ?? "pending"}
      data-reduced-motion={
        preferences ? String(preferences.reducedMotion) : "pending"
      }
    />
  );
}

async function renderHarness(
  element: ReactElement,
): Promise<{ readonly container: HTMLDivElement; readonly root: Root }> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  await act(async () => {
    root.render(element);
  });
  await act(async () => {
    await new Promise<void>((resolve) => queueMicrotask(resolve));
  });
  return { container, root };
}

function preferenceOutput(container: ParentNode): HTMLOutputElement {
  const output = container.querySelector<HTMLOutputElement>(
    '[data-testid="presentation-preferences"]',
  );
  if (!output) throw new Error("Preference harness did not render its output");
  return output;
}

function mediaQueryList(query: string, matches: boolean): MediaQueryList {
  return {
    media: query,
    matches,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  } as unknown as MediaQueryList;
}

afterEach(async () => {
  for (const root of roots.splice(0)) {
    await act(async () => root.unmount());
  }
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("useCertifiedPresentationPreferences", () => {
  it("samples compact layout and OS reduced motion once without listeners", async () => {
    let compact = true;
    let reducedMotion = true;
    const lists: MediaQueryList[] = [];
    const matchMedia = vi.fn((query: string) => {
      const list = mediaQueryList(
        query,
        query === CERTIFIED_COMPACT_LAYOUT_QUERY ? compact : reducedMotion,
      );
      lists.push(list);
      return list;
    });
    vi.stubGlobal("matchMedia", matchMedia);

    const { container } = await renderHarness(<PreferenceHarness />);
    const output = preferenceOutput(container);

    expect(output.dataset.layout).toBe("compact");
    expect(output.dataset.reducedMotion).toBe("true");
    expect(matchMedia.mock.calls).toEqual([
      [CERTIFIED_COMPACT_LAYOUT_QUERY],
      [CERTIFIED_REDUCED_MOTION_QUERY],
    ]);
    for (const list of lists) {
      expect(list.addEventListener).not.toHaveBeenCalled();
      expect(list.addListener).not.toHaveBeenCalled();
    }

    compact = false;
    reducedMotion = false;
    await act(async () => {
      globalThis.dispatchEvent(new Event("resize"));
      await Promise.resolve();
    });
    expect(output.dataset.layout).toBe("compact");
    expect(output.dataset.reducedMotion).toBe("true");
    expect(matchMedia).toHaveBeenCalledTimes(2);
  });

  it("uses complete explicit E2E overrides without consulting matchMedia", async () => {
    const matchMedia = vi.fn(() => {
      throw new Error("matchMedia must not run for complete overrides");
    });
    vi.stubGlobal("matchMedia", matchMedia);

    const { container, root } = await renderHarness(
      <PreferenceHarness
        overrides={{ layout: "compact", reducedMotion: true }}
      />,
    );
    const output = preferenceOutput(container);

    expect(output.dataset.layout).toBe("compact");
    expect(output.dataset.reducedMotion).toBe("true");
    expect(matchMedia).not.toHaveBeenCalled();

    await act(async () => {
      root.render(
        <PreferenceHarness
          overrides={{ layout: "cinematic", reducedMotion: false }}
        />,
      );
    });
    expect(output.dataset.layout).toBe("compact");
    expect(output.dataset.reducedMotion).toBe("true");
    expect(matchMedia).not.toHaveBeenCalled();
  });

  it("falls back safely when matchMedia is unavailable", async () => {
    vi.stubGlobal("matchMedia", undefined);

    const { container } = await renderHarness(<PreferenceHarness />);
    const output = preferenceOutput(container);

    expect(output.dataset.layout).toBe("cinematic");
    expect(output.dataset.reducedMotion).toBe("false");
  });
});
