"use client";

import { getAuthHeaders } from "@/lib/firebase";

import { ModelSceneDemo } from "./model-scene-demo";
import {
  runSemanticSceneModelStream,
  type SemanticSceneStreamRunner,
} from "./model-stream";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const runAuthenticatedSemanticSceneStream: SemanticSceneStreamRunner = async (
  invocation
) => {
  const headers = await getAuthHeaders();
  if (!headers.Authorization) {
    throw new Error("Sign in again to start a live visual explanation.");
  }
  await runSemanticSceneModelStream({
    apiUrl: API_BASE,
    endpoint: "product",
    headers,
    ...invocation,
  });
};

export function LiveModelScene() {
  return (
    <ModelSceneDemo
      protocol="semantic"
      runStream={runAuthenticatedSemanticSceneStream}
      sourceLabel="Verified live model"
      startLabel="Begin verified lesson"
      defaultPrompt="Show the Pythagorean area identity one verified step at a time."
      suggestions={[
        "Build a right triangle and reveal its side relationship",
        "I do not understand why the areas are equal; dissect the large square",
        "Continue the proof from the exact visible step",
      ]}
    />
  );
}
