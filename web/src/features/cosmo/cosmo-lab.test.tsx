/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ClientToolJob } from "cosmo-ai";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ConnectRequest } from "./contracts";
import { CosmoLab } from "./cosmo-lab";

const fixtures = vi.hoisted(() => ({
  request: null as ConnectRequest | null,
  signedIn: true,
  end: vi.fn(async () => {}),
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: fixtures.signedIn ? { id: "user", email: "test@example.test" } : null,
    isLoading: false,
  }),
  signInWithGoogle: vi.fn(async () => {}),
}));
vi.mock("./sdk", () => ({
  connectCosmo: async (request: ConnectRequest) => {
    fixtures.request = request;
    request.observer.ready("cosmo-session");
    return {
      end: fixtures.end,
      sendText: vi.fn(async () => {}),
      setMuted: async (muted: boolean) => request.observer.media(muted, false),
      resumeAudioPlayback: vi.fn(async () => {}),
    };
  },
}));

let root: Root;
let host: HTMLDivElement;
const button = (text: string) =>
  [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(text),
  )!;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Element.prototype.scrollIntoView = vi.fn();
  fixtures.request = null;
  fixtures.signedIn = true;
  fixtures.end.mockClear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

it("requires sign-in before starting a provider session", async () => {
  fixtures.signedIn = false;
  await act(async () => root.render(<CosmoLab />));
  expect(button("Start conversation").disabled).toBe(true);
  expect(host.textContent).toContain("Sign in to start");
  expect(fixtures.request).toBeNull();
});

it("connects, renders a background tool before completing it, mutes, and cleans up", async () => {
  await act(async () => root.render(<CosmoLab />));
  await act(async () => button("Start conversation").click());
  expect(host.textContent).toContain("Connected");
  const request = fixtures.request!;
  expect(request.options.storeRecording).toBe(false);
  const tool = request.tools.find(
    (item) => item.kind === "client" && item.name === "pin_thought",
  );
  if (!tool || tool.kind !== "client" || !tool.background || !tool.handler)
    throw new Error("Missing background tool");
  const ack = vi.fn();
  const complete = vi.fn(async () => {
    expect(host.querySelector("[data-thought-id]")?.textContent).toContain(
      "Try a small experiment",
    );
  });
  const fail = vi.fn(async () => {});
  let pin: Promise<void>;
  await act(async () => {
    pin = tool.handler!(
      { title: "Next step", body: "Try a small experiment" },
      { ack, complete, fail } as unknown as ClientToolJob,
    );
    await vi.waitFor(() => expect(ack).toHaveBeenCalled());
  });
  await act(async () => {
    await pin!;
  });
  expect(complete).toHaveBeenCalledTimes(1);
  expect(ack.mock.invocationCallOrder[0]).toBeLessThan(complete.mock.invocationCallOrder[0]);
  expect(fail).not.toHaveBeenCalled();
  await act(async () => button("Mute").click());
  expect(button("Unmute")).toBeDefined();
  await act(async () => button("End call").click());
  expect(fixtures.end).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Call ended");
  expect(request.signal.aborted).toBe(true);
});

it("completes a quadratic only after the actual Murmur SVG renderer draws the curve", async () => {
  await act(async () => root.render(<CosmoLab />));
  await act(async () => button("Start conversation").click());
  const request = fixtures.request!;
  const tool = request.tools.find((item) => item.kind === "client" && item.name === "show_quadratic");
  if (!tool || tool.kind !== "client" || tool.background || !tool.handler) throw new Error("Missing quadratic tool");
  const complete = vi.fn(async (result: unknown) => {
    const graph = host.querySelector('[data-visual="quadratic"] g[id^="plot_"] path');
    expect(graph?.getAttribute("d")?.length).toBeGreaterThan(2000);
    expect(host.textContent).toContain("x² − 5x + 6 = 0");
    expect(host.textContent).toContain("2 and 3");
    expect(result).toMatchObject({ rendered: true, roots: [2, 3] });
  });
  let pending!: Promise<void>;
  await act(async () => {
    pending = tool.handler!({ a: 1, b: -5, c: 6 }).then(complete);
  });
  await act(async () => { await pending; });
  expect(complete).toHaveBeenCalledTimes(1);
  await act(async () => button("End call").click());
});
