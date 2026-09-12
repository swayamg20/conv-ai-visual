#!/usr/bin/env node

import {
  prepareArtifactRoot,
  resolveArtifactRoot,
  writeManifest,
} from "./projectile-motion-artifacts-lib.mjs";

function usage() {
  return "Usage: node scripts/projectile-motion-artifacts.mjs <prepare|finalize>";
}

async function main() {
  const [command, ...extra] = process.argv.slice(2);
  if (extra.length > 0 || !["prepare", "finalize"].includes(command)) {
    throw new Error(usage());
  }

  const artifactRoot = resolveArtifactRoot();
  if (command === "prepare") {
    await prepareArtifactRoot(artifactRoot);
    process.stdout.write(`Prepared ${artifactRoot}\n`);
    return;
  }

  const result = await writeManifest(artifactRoot);
  process.stdout.write(
    `Finalized Gate 1.7 manifest ${result.digest} at ${result.artifactRoot}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(
    `Projectile motion artifacts failed: ${error.message}\n`,
  );
  process.exitCode = 1;
});
