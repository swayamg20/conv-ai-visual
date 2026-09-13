#!/usr/bin/env node

import { validateCaptureBundle } from "./semantic-storyboard-artifacts-lib.mjs";

validateCaptureBundle()
  .then(({ artifactRoot, observation }) => {
    process.stdout.write(
      [
        "Gate 1.8 normal-speed capture and checkpoint contact sheet are valid.",
        `Fixture: ${observation.selectedFixtureId}`,
        `Program: ${observation.selectedProgramId}`,
        `Checkpoints: ${observation.expectedCheckpointIds.length}`,
        `Artifact root: ${artifactRoot}`,
      ].join("\n") + "\n",
    );
  })
  .catch((error) => {
    process.stderr.write(
      `Semantic storyboard capture check failed: ${error.message}\n`,
    );
    process.exitCode = 1;
  });
