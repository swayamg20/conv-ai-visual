import type { ChoreographyLayout } from "@/lib/live-scene";
import type {
  PairedProjectileComparisonSpecV1,
  SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import {
  decodeSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  decodeSemanticStoryboardFixtureEnvelope,
  failSemanticStoryboardFixture,
  sameSemanticStoryboardFixtureValue,
  SemanticStoryboardFixtureError,
  type DecodedSemanticStoryboardFixtureEnvelope,
  type DecodedSemanticStoryboardFixtureLane,
} from "./semantic-storyboard-fixture-schema";

const EXPECTED_ANGLE_PAIRS = Object.freeze(["30:45", "30:60", "45:60"]);
const LAYOUTS = Object.freeze([
  "cinematic",
  "compact",
] as const satisfies readonly ChoreographyLayout[]);

export interface SemanticStoryboardFixtureBatch {
  readonly fixtureId: string;
  readonly scenarioId: string;
  readonly events: readonly SemanticStoryboardSceneStreamEventV1[];
  readonly checkpointIds: readonly string[];
  readonly holdOpenUntilAbort: false;
}

interface QualifiedSemanticStoryboardFixture extends DecodedSemanticStoryboardFixtureEnvelope {
  readonly lanes: readonly DecodedSemanticStoryboardFixtureLane[];
}

export interface SemanticStoryboardFixtureCatalog {
  select(request: SemanticStoryboardRequestV1): SemanticStoryboardFixtureBatch;
}

function exactAnchorBase(
  anchor: DecodedSemanticStoryboardFixtureLane,
  lane: DecodedSemanticStoryboardFixtureLane,
  field: string,
): void {
  if (
    !sameSemanticStoryboardFixtureValue(
      lane.request.baseScene,
      anchor.resultScene,
    ) ||
    !sameSemanticStoryboardFixtureValue(
      lane.request.baseSemanticScene,
      anchor.resultSemanticScene,
    )
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `${field} does not start at the anchor frontier`,
    );
  }
  const first = lane.checkpoints[0];
  if (!first) return;
  for (const layout of LAYOUTS) {
    if (
      !sameSemanticStoryboardFixtureValue(
        first.transition.checkpoint.presentation.baseViewports[layout],
        anchor.resultFrontiers[layout].viewport,
      )
    ) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `${field} does not join the anchor ${layout} viewport`,
      );
    }
  }
}

function qualifyFixture(
  fixture: DecodedSemanticStoryboardFixtureEnvelope,
): QualifiedSemanticStoryboardFixture {
  for (const [index, lane] of fixture.programs.entries()) {
    exactAnchorBase(fixture.anchor, lane, `fixture programs[${index}]`);
  }
  if (fixture.soleAbstain) {
    exactAnchorBase(fixture.anchor, fixture.soleAbstain, "fixture soleAbstain");
  }
  if (fixture.acceptedPrefix) {
    exactAnchorBase(
      fixture.anchor,
      fixture.acceptedPrefix,
      "fixture acceptedPrefixMalformedTail",
    );
  }

  for (const [index, lane] of fixture.continuations.entries()) {
    const source = fixture.programs.find(
      (program) => program.programId === lane.fromProgramId,
    );
    const prefix = lane.fromPrefixCount;
    if (!source || prefix === undefined || prefix > source.checkpoints.length) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `fixture continuations[${index}] has no valid source prefix`,
      );
    }
    const prefixScene =
      prefix === 0
        ? source.request.baseScene
        : source.checkpoints[prefix - 1].transition.resultScene;
    const prefixSemanticScene =
      prefix === 0
        ? source.request.baseSemanticScene
        : source.checkpoints[prefix - 1].transition.resultSemanticScene;
    if (
      !sameSemanticStoryboardFixtureValue(
        lane.request.baseScene,
        prefixScene,
      ) ||
      !sameSemanticStoryboardFixtureValue(
        lane.request.baseSemanticScene,
        prefixSemanticScene,
      ) ||
      lane.checkpoints.length !== 1
    ) {
      return failSemanticStoryboardFixture(
        "invalid_fixture",
        `fixture continuations[${index}] does not join its exact source prefix`,
      );
    }

    const first = lane.checkpoints[0];
    for (const layout of LAYOUTS) {
      const sourceViewport =
        prefix === 0
          ? source.checkpoints[0]?.transition.checkpoint.presentation
              .baseViewports[layout]
          : source.checkpoints[prefix - 1].transition.checkpoint.presentation
              .resultViewports[layout];
      if (
        !first ||
        !sourceViewport ||
        !sameSemanticStoryboardFixtureValue(
          first.transition.checkpoint.presentation.baseViewports[layout],
          sourceViewport,
        )
      ) {
        return failSemanticStoryboardFixture(
          "invalid_fixture",
          `fixture continuations[${index}] loses its ${layout} viewport`,
        );
      }
    }
  }

  const lanes = Object.freeze([
    fixture.anchor,
    ...fixture.programs,
    ...fixture.continuations,
    ...(fixture.soleAbstain ? [fixture.soleAbstain] : []),
    ...(fixture.acceptedPrefix ? [fixture.acceptedPrefix] : []),
  ]);
  const scenarioIds = lanes.map((lane) => lane.scenarioId);
  const programIds = fixture.programs.map((lane) => lane.programId);
  if (
    new Set(scenarioIds).size !== scenarioIds.length ||
    new Set(programIds).size !== programIds.length
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture contains duplicate story identities",
    );
  }
  const fakeStreams = lanes.reduce(
    (count, lane) => count + (lane.request.routingMode === "director" ? 1 : 0),
    0,
  );
  if (fixture.fakeProviderStreamCount !== fakeStreams) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture fake stream total disagrees",
    );
  }
  return Object.freeze({ ...fixture, lanes });
}

function decodeCatalog(
  values: readonly unknown[],
): readonly QualifiedSemanticStoryboardFixture[] {
  if (values.length !== EXPECTED_ANGLE_PAIRS.length) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture catalog must contain all three angle pairs",
    );
  }

  let fixtures: readonly QualifiedSemanticStoryboardFixture[];
  try {
    fixtures = Object.freeze(
      values.map((value) =>
        qualifyFixture(decodeSemanticStoryboardFixtureEnvelope(value)),
      ),
    );
  } catch (error) {
    if (error instanceof SemanticStoryboardFixtureError) throw error;
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      `fixture failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }

  const ids = fixtures.map((fixture) => fixture.fixtureId);
  const pairs = fixtures
    .map(
      (fixture) =>
        `${fixture.problemSpec.anglesDeg[0]}:${fixture.problemSpec.anglesDeg[1]}`,
    )
    .sort();
  if (
    new Set(ids).size !== ids.length ||
    !sameSemanticStoryboardFixtureValue(pairs, EXPECTED_ANGLE_PAIRS)
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture catalog has duplicate or missing problems",
    );
  }

  const programs = fixtures.flatMap((fixture) => fixture.programs);
  const signatures = programs.map((lane) =>
    JSON.stringify(lane.providerRecords),
  );
  const lengths = new Set(programs.map((lane) => lane.checkpoints.length));
  const idOrders = new Set(
    programs.map((lane) => JSON.stringify(lane.checkpointIds)),
  );
  if (
    programs.length < 6 ||
    new Set(signatures).size < 6 ||
    lengths.size < 2 ||
    idOrders.size < 2
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture catalog does not prove six distinct variable-order stories",
    );
  }

  const continuationFixture = fixtures.find(
    (fixture) => fixture.continuations.length > 0,
  );
  const continuationSource = continuationFixture?.programs.find(
    (program) =>
      program.programId === continuationFixture.continuations[0]?.fromProgramId,
  );
  if (
    !continuationFixture ||
    !continuationSource ||
    !sameSemanticStoryboardFixtureValue(
      continuationFixture.continuations.map((lane) => lane.fromPrefixCount),
      Array.from(
        { length: continuationSource.checkpoints.length + 1 },
        (_, index) => index,
      ),
    ) ||
    !continuationFixture.soleAbstain ||
    !continuationFixture.acceptedPrefix
  ) {
    return failSemanticStoryboardFixture(
      "invalid_fixture",
      "fixture catalog lacks every-prefix and safe-terminal qualification",
    );
  }
  return fixtures;
}

function sameProblem(
  left: PairedProjectileComparisonSpecV1,
  right: PairedProjectileComparisonSpecV1,
): boolean {
  return sameSemanticStoryboardFixtureValue(left, right);
}

function requestMatches(
  request: SemanticStoryboardRequestV1,
  fixture: QualifiedSemanticStoryboardFixture,
  lane: DecodedSemanticStoryboardFixtureLane,
): boolean {
  return (
    request.routingMode === lane.request.routingMode &&
    sameProblem(request.problemSpec, fixture.problemSpec) &&
    sameSemanticStoryboardFixtureValue(
      request.baseScene,
      lane.request.baseScene,
    ) &&
    sameSemanticStoryboardFixtureValue(
      request.baseSemanticScene,
      lane.request.baseSemanticScene,
    ) &&
    (request.routingMode === "reflex" ||
      (lane.request.routingMode === "director" &&
        request.prompt === lane.request.prompt))
  );
}

function rebaseGeneration(
  event: SemanticStoryboardSceneStreamEventV1,
  generation: number,
): SemanticStoryboardSceneStreamEventV1 {
  return decodeSemanticStoryboardSceneStreamEventV1({
    ...event,
    generation,
  });
}

function selectBatch(
  request: SemanticStoryboardRequestV1,
  fixtures: readonly QualifiedSemanticStoryboardFixture[],
): SemanticStoryboardFixtureBatch {
  const matches = fixtures.flatMap((fixture) =>
    fixture.lanes.flatMap((lane) =>
      requestMatches(request, fixture, lane) ? [{ fixture, lane }] : [],
    ),
  );
  if (matches.length !== 1) {
    return failSemanticStoryboardFixture(
      "request_mismatch",
      "request does not match one exact fixture problem, prompt, and certified frontier",
    );
  }
  const { fixture, lane } = matches[0];
  return Object.freeze({
    fixtureId: fixture.fixtureId,
    scenarioId: lane.scenarioId,
    events: Object.freeze(
      lane.events.map((event) => rebaseGeneration(event, request.generation)),
    ),
    checkpointIds: lane.checkpointIds,
    holdOpenUntilAbort: false,
  });
}

/** Build a preflighted, immutable fixture selector once per runner session. */
export function createSemanticStoryboardFixtureCatalog(
  values: readonly unknown[],
): SemanticStoryboardFixtureCatalog {
  const fixtures = decodeCatalog(values);
  return Object.freeze({
    select: (request: SemanticStoryboardRequestV1) =>
      selectBatch(request, fixtures),
  });
}
