#!/usr/bin/env node

import {
  REVIEW_RECORD_RELATIVE_PATH,
  validateLiveChoreographyReview,
} from "./live-choreography-review-lib.mjs";

validateLiveChoreographyReview()
  .then((result) => {
    if (result.status === "not_recorded") {
      process.stdout.write(
        `Gate 1.5 human review not yet recorded: ${REVIEW_RECORD_RELATIVE_PATH}\n`,
      );
      return;
    }
    process.stdout.write(
      [
        "Gate 1.5 human review valid",
        `Reviewed implementation: ${result.record.reviewedImplementationSha}`,
        `Manifest digest: ${result.record.manifestDigest}`,
        `Reviewers: ${result.record.reviewers.length}`,
        `Aggregate mean: ${result.metrics.aggregateMean.toFixed(3)}`,
        `CI run: ${result.record.ci.runUrl}`,
        `Artifact: ${result.record.ci.artifactName}`,
      ].join("\n") + "\n",
    );
  })
  .catch((error) => {
    process.stderr.write(`Gate 1.5 human review invalid: ${error.message}\n`);
    process.exitCode = 1;
  });
