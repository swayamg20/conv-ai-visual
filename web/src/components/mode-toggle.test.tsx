/** @vitest-environment happy-dom */

import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", () => ({
  motion: {
    div: ({ children }: { children?: ReactNode }) => children,
  },
}));

import { ModeToggle } from "./mode-toggle";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe("ModeToggle", () => {
  it("keeps chat usable while the voice pilot is explicitly disabled", async () => {
    const onChange = vi.fn();
    const container = document.createElement("div");
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <ModeToggle
          mode="chat"
          onChange={onChange}
          voiceDisabled
        />
      );
    });

    const voice = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Voice unavailable while the pilot is validated"]',
    );
    const chat = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Chat",
    );
    expect(voice?.disabled).toBe(true);
    expect(voice?.textContent).toContain("Voice unavailable");
    expect(chat?.disabled).toBe(false);

    voice?.click();
    expect(onChange).not.toHaveBeenCalled();
    chat?.click();
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith("chat");

    await act(async () => root.unmount());
  });
});
