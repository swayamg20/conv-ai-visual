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
      suggestions={[
        "Build a right triangle and reveal its side relationship",
        "Continue the Pythagorean area identity one step at a time",
        "Show the complete relationship between a², b², and c²",
      ]}
    />
  );
}
