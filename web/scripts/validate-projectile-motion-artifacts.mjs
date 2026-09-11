#!/usr/bin/env node

import { validateManifest } from "./projectile-motion-artifacts-lib.mjs";

validateManifest()
  .then(({ artifactRoot, manifest, digest }) => {
    process.stdout.write(
      [
        `Gate 1.7 artifacts valid: ${digest}`,
        `Commit: ${manifest.source.gitCommit}`,
        `Fixture: ${manifest.fixtures.selectedFixtureId}`,
        `Checkpoints: ${manifest.evidence.runtime.evidence.settledCheckpointIds.length}`,
        `Visual duration: ${manifest.evidence.timing.visualDurationMs} ms`,
        `Provider requests: ${manifest.evidence.network.providerRequestCount}`,
        `Artifact root: ${artifactRoot}`,
      ].join("\n") + "\n",
    );
  })
  .catch((error) => {
    process.stderr.write(
      `Gate 1.7 artifact validation failed: ${error.message}\n`,
    );
    process.exitCode = 1;
  });
