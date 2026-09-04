"use client";

import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";

import { ModelSceneDemo } from "./model-scene-demo";
import {
  runSceneModelStream,
  runSemanticSceneModelStream,
  type SemanticSceneStreamRunner,
} from "./model-stream";
import {
  createSceneFixtureRunner,
  type SceneFixtureMode,
} from "./scene-stream-fixture";
import { createSemanticSceneFixtureRunner } from "./semantic-scene-stream-fixture";
import type { SceneStreamRunner } from "./stream-runtime";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type LabAuthoringMode = "semantic" | "raw";
type LabSceneSource = "fixture" | "azure";

const runDevelopmentLabSceneStream: SceneStreamRunner = async (invocation) => {
  await runSceneModelStream({
    apiUrl: API_BASE,
    endpoint: "developmentLab",
    ...invocation,
  });
};

const runDevelopmentLabSemanticSceneStream: SemanticSceneStreamRunner = async (
  invocation
) => {
  await runSemanticSceneModelStream({
    apiUrl: API_BASE,
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

const choiceClass = (selected: boolean): string =>
  cn(
    "flex min-h-11 cursor-pointer items-center justify-center rounded-lg border px-2.5 py-2 text-center text-xs transition focus-within:ring-2 focus-within:ring-ring",
    selected
      ? "border-lavender/45 bg-lavender/12 font-medium text-foreground"
      : "border-chalk-faint/20 bg-slate/20 text-muted-foreground hover:text-foreground"
  );

export function LiveSceneLab() {
  const [authoring, setAuthoring] = useState<LabAuthoringMode>("semantic");
  const [source, setSource] = useState<LabSceneSource>("fixture");
  const [mode, setMode] = useState<SceneFixtureMode>("normal");
  const rawRunner = useMemo(
    () =>
      source === "azure"
        ? runDevelopmentLabSceneStream
        : createSceneFixtureRunner({ mode }),
    [mode, source]
  );
  const semanticRunner = useMemo(
    () =>
      source === "azure"
        ? runDevelopmentLabSemanticSceneStream
        : createSemanticSceneFixtureRunner(),
    [source]
  );
  const selected =
    SCENARIOS.find((scenario) => scenario.mode === mode) ?? SCENARIOS[0];

  const sourceDescription =
    source === "fixture"
      ? authoring === "semantic"
        ? "The checked-in compiler transcript runs in this browser: no sign-in, network request, or Azure spend. Prompt edits do not change its ink."
        : "Pre-authored coordinate patches run in this browser with no sign-in, network request, or Azure spend. Prompt edits do not change their ink."
      : authoring === "semantic"
        ? "The guarded loopback development route needs no Murmur sign-in. Running it sends your prompt and consumes paid Azure quota."
        : "The guarded loopback baseline needs no Murmur sign-in. Running it sends your prompt and consumes paid Azure quota.";

  const scenarioControl = (
    <div className="mb-5 space-y-5">
      <fieldset>
        <legend className="mb-2 text-sm font-medium">Authoring contract</legend>
        <div className="grid grid-cols-2 gap-2" data-testid="authoring-mode-picker">
          {(
            [
              ["semantic", "Verified acts"],
              ["raw", "Raw coordinates"],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className={choiceClass(authoring === value)}>
              <input
                type="radio"
                name="scene-authoring"
                value={value}
                checked={authoring === value}
                onChange={() => setAuthoring(value)}
                className="sr-only"
              />
              {label}
            </label>
          ))}
        </div>
        <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
          {authoring === "semantic"
            ? "The model chooses a teaching act; the compiler owns geometry and emits one independently presentable atom at a time."
            : "Gate 1 baseline: the model authors coordinates, styles, and complete low-level patches."}
        </p>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">Lesson source</legend>
        <div className="grid grid-cols-2 gap-2" data-testid="scene-source-picker">
          {(
            [
              ["fixture", "Fixture · $0"],
              ["azure", "Azure · paid"],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className={choiceClass(source === value)}>
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
          {sourceDescription}
        </p>
      </fieldset>

      {authoring === "raw" && source === "fixture" && (
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Baseline scenario</legend>
          <div className="grid grid-cols-2 gap-2" data-testid="fixture-mode-picker">
            {SCENARIOS.map((scenario) => (
              <label
                key={scenario.mode}
                className={choiceClass(mode === scenario.mode)}
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
            {mode === "stale" &&
              " Start, interrupt after the first mark, then wait."}
          </p>
        </fieldset>
      )}
    </div>
  );

  const commonProps = {
    scenarioControl,
    backHref: "/",
    suggestions: [
      "Build a right triangle and reveal its side relationship",
      "Why is the marked angle exactly 90°?",
      "Explain Pythagoras as areas, one step at a time",
    ],
  } as const;

  if (authoring === "semantic") {
    return (
      <ModelSceneDemo
        key={`semantic:${source}`}
        {...commonProps}
        protocol="semantic"
        runStream={semanticRunner}
        sourceLabel={
          source === "azure"
            ? "Verified acts · Azure paid"
            : "Verified fixture · $0"
        }
        startLabel={
          source === "azure"
            ? "Run paid Azure lesson"
            : "Begin verified lesson"
        }
        defaultPrompt="Teach the Pythagorean area identity one verified act at a time."
      />
    );
  }

  return (
    <ModelSceneDemo
      key={`raw:${source}:${mode}`}
      {...commonProps}
      protocol="raw"
      runStream={rawRunner}
      sourceLabel={
        source === "azure"
          ? "Raw baseline · Azure paid"
          : `Raw fixture · ${selected.label} · $0`
      }
      startLabel={
        source === "azure" ? "Run paid Azure baseline" : "Run raw fixture"
      }
    />
  );
}
