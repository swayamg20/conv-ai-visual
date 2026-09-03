"use client";

import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";

import { ModelSceneDemo } from "./model-scene-demo";
import { runSceneModelStream } from "./model-stream";
import {
  createSceneFixtureRunner,
  type SceneFixtureMode,
} from "./scene-stream-fixture";
import type { SceneStreamRunner } from "./stream-runtime";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type LabSceneSource = "fixture" | "azure";

const runDevelopmentLabSceneStream: SceneStreamRunner = async (invocation) => {
  await runSceneModelStream({
    apiUrl: API_BASE,
    endpoint: "developmentLab",
    ...invocation,
  });
};

const SCENARIOS: readonly {
  readonly mode: SceneFixtureMode;
  readonly label: string;
  readonly description: string;
}[] = [
  {
    mode: "normal",
    label: "Normal",
    description: "Four useful patches arrive before completion.",
  },
  {
    mode: "repair",
    label: "Repair",
    description: "The first draft is rejected, then one repair succeeds.",
  },
  {
    mode: "failure",
    label: "Failure",
    description: "Both drafts fail while the last safe board stays intact.",
  },
  {
    mode: "stale",
    label: "Late output",
    description: "The source ignores cancellation so stale-token rejection is visible.",
  },
] as const;

export function LiveSceneLab() {
  const [source, setSource] = useState<LabSceneSource>("fixture");
  const [mode, setMode] = useState<SceneFixtureMode>("normal");
  const runner = useMemo(
    () =>
      source === "azure"
        ? runDevelopmentLabSceneStream
        : createSceneFixtureRunner({ mode }),
    [mode, source]
  );
  const selected = SCENARIOS.find((scenario) => scenario.mode === mode) ?? SCENARIOS[0];

  const scenarioControl = (
    <div className="mb-5 space-y-5">
      <fieldset>
        <legend className="mb-2 text-sm font-medium">Scene source</legend>
        <div className="grid grid-cols-2 gap-2" data-testid="scene-source-picker">
          {(
            [
              ["fixture", "Fixture"],
              ["azure", "Azure model"],
            ] as const
          ).map(([value, label]) => (
            <label
              key={value}
              className={cn(
                "flex min-h-11 cursor-pointer items-center justify-center rounded-lg border px-2.5 py-2 text-center text-xs transition focus-within:ring-2 focus-within:ring-ring",
                source === value
                  ? "border-lavender/45 bg-lavender/12 font-medium text-foreground"
                  : "border-chalk-faint/20 bg-slate/20 text-muted-foreground hover:text-foreground"
              )}
            >
              <input
                type="radio"
                name="scene-source"
                value={value}
                checked={source === value}
                onChange={() => setSource(value)}
                className="sr-only"
              />
              {label}
            </label>
          ))}
        </div>
        <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
          {source === "azure"
            ? "Uses the real backend model stream. Your edited prompt is sent with each generation."
            : "Uses pre-authored patches to test streaming behavior; changing the prompt does not change fixture ink."}
        </p>
      </fieldset>

      {source === "fixture" && (
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Deterministic scenario</legend>
          <div className="grid grid-cols-2 gap-2" data-testid="fixture-mode-picker">
            {SCENARIOS.map((scenario) => (
              <label
                key={scenario.mode}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center justify-center rounded-lg border px-2.5 py-2 text-center text-xs transition focus-within:ring-2 focus-within:ring-ring",
                  mode === scenario.mode
                    ? "border-lavender/45 bg-lavender/12 font-medium text-foreground"
                    : "border-chalk-faint/20 bg-slate/20 text-muted-foreground hover:text-foreground"
                )}
              >
                <input
                  type="radio"
                  name="fixture-mode"
                  value={scenario.mode}
                  checked={mode === scenario.mode}
                  onChange={() => setMode(scenario.mode)}
                  className="sr-only"
                />
                {scenario.label}
              </label>
            ))}
          </div>
          <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
            {selected.description}
            {mode === "stale" && " Start, interrupt after the first mark, then wait."}
          </p>
        </fieldset>
      )}
    </div>
  );

  return (
    <ModelSceneDemo
      key={`${source}:${mode}`}
      runStream={runner}
      sourceLabel={
        source === "azure" ? "Azure · live backend" : `Fixture · ${selected.label}`
      }
      scenarioControl={scenarioControl}
      backHref="/"
      suggestions={[
        "Build a right triangle and reveal its side relationship",
        "Why is the marked angle exactly 90°?",
        "Explain Pythagoras as areas, one step at a time",
      ]}
    />
  );
}
