/** @vitest-environment happy-dom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { VoiceFallbackPanel } from "./voice-fallback-panel";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe("VoiceFallbackPanel", () => {
  it("shows the actionable failure and only offers retry when safe", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const onRetry = vi.fn();
    const onContinueInText = vi.fn();

    await act(async () => {
      root.render(
        <VoiceFallbackPanel
          reason={{
            code: "bootstrap_unauthenticated",
            message: "Sign in again before starting voice.",
            retryable: false,
          }}
          canRetry={false}
          onRetry={onRetry}
          onContinueInText={onContinueInText}
        />
      );
    });

    expect(container.textContent).toContain("Sign in again before starting voice.");
    expect(container.textContent).not.toContain("Retry voice");
    const continueButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Continue in text"
    );
    continueButton?.click();
    expect(onContinueInText).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();

    await act(async () => {
      root.render(
        <VoiceFallbackPanel
          reason={{
            code: "bootstrap_unavailable",
            message: "Voice is temporarily unavailable.",
            retryable: true,
          }}
          canRetry
          onRetry={onRetry}
          onContinueInText={onContinueInText}
        />
      );
    });

    const retryButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Retry voice"
    );
    retryButton?.click();
    expect(onRetry).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });
});
