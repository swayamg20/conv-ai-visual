#!/usr/bin/env node

import { validateManifest } from "./semantic-storyboard-artifacts-lib.mjs";

validateManifest()
  .then(({ artifactRoot, manifest, digest }) => {
    process.stdout.write(
      [
        `Gate 1.8 artifacts valid: ${digest}`,
        `Commit: ${manifest.source.gitCommit}`,
        `Fixture: ${manifest.fixtures.selectedFixtureId}`,
        `Program: ${manifest.fixtures.selectedProgramId}`,
        `Checkpoints: ${manifest.evidence.capture.checkpointIds.length}`,
        `Artifact root: ${artifactRoot}`,
      ].join("\n") + "\n",
    );
  })
  .catch((error) => {
    process.stderr.write(
      `Gate 1.8 artifact validation failed: ${error.message}\n`,
    );
    process.exitCode = 1;
  });
