"use client";

import { getAuthHeaders } from "@/lib/firebase";

import { ModelSceneDemo } from "./model-scene-demo";
import { runSceneModelStream } from "./model-stream";
import type { SceneStreamRunner } from "./stream-runtime";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const runAuthenticatedSceneStream: SceneStreamRunner = async (invocation) => {
  const headers = await getAuthHeaders();
  if (!headers.Authorization) {
    throw new Error("Sign in again to start a live visual explanation.");
  }
  await runSceneModelStream({
    apiUrl: API_BASE,
    headers,
    ...invocation,
  });
};

export function LiveModelScene() {
  return (
    <ModelSceneDemo
      runStream={runAuthenticatedSceneStream}
      sourceLabel="Live model"
      suggestions={[
        "Explain a binary search tree",
        "Show how gradient descent walks downhill",
        "Why is the derivative a slope?",
      ]}
    />
  );
}
