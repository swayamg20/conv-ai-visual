#!/usr/bin/env node

import { validateManifest } from "./live-choreography-artifacts-lib.mjs";

validateManifest()
  .then(({ artifactRoot, manifest, digest }) => {
    process.stdout.write(
      [
        `Gate 1.5 artifacts valid: ${digest}`,
        `Commit: ${manifest.source.gitCommit}`,
        `Checkpoints: ${manifest.lesson.checkpointCount}`,
        `First-visible p95: ${manifest.evidence.timing.firstMeaningfulVisual.p95Ms} ms`,
        `Interruption p95: ${manifest.evidence.timing.interruption.p95Ms} ms`,
        `Real-time duration: ${manifest.evidence.timing.realTime.visualDurationMs} ms`,
        `Live-scene requests: ${manifest.evidence.requests.liveSceneRequestCount}`,
        `Artifact root: ${artifactRoot}`,
      ].join("\n") + "\n",
    );
  })
  .catch((error) => {
    process.stderr.write(
      `Gate 1.5 artifact validation failed: ${error.message}\n`,
    );
    process.exitCode = 1;
  });
