"use client";

import { useEffect, useMemo } from "react";

import {
  LiveChoreographyDemo,
  type ChoreographyRunnerFactory,
} from "@/features/live-scene/live-choreography-demo";

import type { ChoreographyCaptureOptions } from "./capture-options";
import {
  CHOREOGRAPHY_CAPTURE_BRIDGE_KEY,
  createChoreographyCaptureSession,
  type ChoreographyCaptureBridgeV1,
} from "./capture-runner";

declare global {
  interface Window {
    __MURMUR_CHOREOGRAPHY_CAPTURE__?: ChoreographyCaptureBridgeV1;
  }
}

interface ChoreographyCaptureClientProps {
  readonly options: ChoreographyCaptureOptions;
}

export function ChoreographyCaptureClient({
  options,
}: ChoreographyCaptureClientProps) {
  const session = useMemo(
    () => createChoreographyCaptureSession(options.pace),
    [options.pace],
  );
  const runnerFactory = useMemo<ChoreographyRunnerFactory>(
    () => () => session.runner,
    [session.runner],
  );

  useEffect(() => {
    if (window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY] !== undefined) {
      throw new Error("A choreography capture bridge is already installed");
    }
    Object.defineProperty(window, CHOREOGRAPHY_CAPTURE_BRIDGE_KEY, {
      configurable: true,
      enumerable: false,
      writable: false,
      value: session.bridge,
    });
    return () => {
      if (window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY] === session.bridge) {
        delete window[CHOREOGRAPHY_CAPTURE_BRIDGE_KEY];
      }
    };
  }, [session.bridge]);

  return (
    <LiveChoreographyDemo
      key={options.pace}
      initialPath="full"
      pathLocked
      layout={options.layout}
      reducedMotion={options.reducedMotion}
      runnerFactory={runnerFactory}
      stageOnly
      autoStart
    />
  );
}
