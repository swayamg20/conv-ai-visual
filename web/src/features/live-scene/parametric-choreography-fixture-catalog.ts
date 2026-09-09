import { type ParametricChoreographyRequestV3 } from "@/lib/live-scene/parametric-choreography-request";
import {
  PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  type ParametricCompletingSquareMainCheckpoint,
} from "@/lib/live-scene/parametric-choreography";
import {
  sameCompletingSquareProblem,
  type CompletingSquareProblemSpecV1,
} from "@/lib/live-scene/parametric-problem";

import {
  EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
  prepareParametricChoreographyCheckpoint,
  type ParametricChoreographyFrontier,
} from "./parametric-choreography-playback";
import {
  decodeParametricChoreographyFixtureEnvelope,
  failParametricChoreographyFixture,
  sameParametricChoreographyFixtureValue,
  type DecodedParametricChoreographyFixtureEnvelope,
  type DecodedParametricChoreographyFixtureLane,
  type ParametricChoreographyFixtureLaneBase,
} from "./parametric-choreography-fixture-schema";

export const PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES = [
  "main",
  "adaptive",
] as const;
export type ParametricChoreographyFixtureMode =
  (typeof PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES)[number];

export interface ParametricChoreographyFixtureBatch {
  readonly fixtureId: string;
  readonly lane: "main" | "clarifyCorner" | "continueAfterClarification";
  readonly events: DecodedParametricChoreographyFixtureLane["events"];
  readonly holdOpenUntilAbort: boolean;
}

interface MaterializedFixtureLane
  extends DecodedParametricChoreographyFixtureLane {
  readonly baseFrontier: ParametricChoreographyFrontier;
  readonly resultFrontier: ParametricChoreographyFrontier;
  readonly prefixes: readonly ParametricChoreographyFrontier[];
}

interface MaterializedFixture {
  readonly fixtureId: string;
  readonly problemText: string;
  readonly problemSpec: CompletingSquareProblemSpecV1;
  readonly main: MaterializedFixtureLane;
  readonly clarifyCorner?: MaterializedFixtureLane;
  readonly continueAfterClarification?: MaterializedFixtureLane;
}

export interface ParametricChoreographyFixtureCatalog {
  select(
    request: ParametricChoreographyRequestV3,
    mode: ParametricChoreographyFixtureMode,
  ): ParametricChoreographyFixtureBatch;
}

function frontierBase(
  frontier: ParametricChoreographyFrontier,
): ParametricChoreographyFixtureLaneBase {
  const component = frontier.semanticScene.components[0];
  return Object.freeze({
    revision: frontier.scene.revision,
    checkpointId: component?.lastMainCheckpoint ?? null,
    cornerClarified: component?.cornerClarified ?? false,
    certificateHeadSha256: frontier.certificateHeadSha256,
  });
}

function materializeLane(
  lane: DecodedParametricChoreographyFixtureLane,
  expectedBase: ParametricChoreographyFrontier,
  problemSpec: CompletingSquareProblemSpecV1,
  field: string,
): MaterializedFixtureLane {
  if (
    !sameParametricChoreographyFixtureValue(
      lane.base,
      frontierBase(expectedBase),
    )
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} base metadata does not match its certified predecessor`,
    );
  }
  const firstCheckpoint = lane.checkpoints[0];
  if (
    !firstCheckpoint ||
    !sameCompletingSquareProblem(
      firstCheckpoint.semantic.problemSpec,
      problemSpec,
    )
  ) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${field} changed the fixture problem identity`,
    );
  }

  const prefixes: ParametricChoreographyFrontier[] = [expectedBase];
  let frontier = expectedBase;
  for (const checkpoint of lane.checkpoints) {
    try {
      frontier = prepareParametricChoreographyCheckpoint(
        frontier,
        checkpoint,
        "cinematic",
      ).target;
    } catch (error) {
      return failParametricChoreographyFixture(
        "invalid_fixture",
        `${field} failed certified frontier materialization: ${
          error instanceof Error ? error.message : "unknown error"
        }`,
      );
    }
    prefixes.push(frontier);
  }
  return Object.freeze({
    ...lane,
    baseFrontier: expectedBase,
    resultFrontier: frontier,
    prefixes: Object.freeze(prefixes),
  });
}

function materializeFixture(
  fixture: DecodedParametricChoreographyFixtureEnvelope,
): MaterializedFixture {
  const main = materializeLane(
    fixture.main,
    EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
    fixture.problemSpec,
    `${fixture.fixtureId} main lane`,
  );
  if (!fixture.clarifyCorner || !fixture.continueAfterClarification) {
    return Object.freeze({
      fixtureId: fixture.fixtureId,
      problemText: fixture.problemText,
      problemSpec: fixture.problemSpec,
      main,
    });
  }

  const missingCornerIndex = PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
    "missing_corner" as ParametricCompletingSquareMainCheckpoint,
  );
  const missingCorner = main.prefixes[missingCornerIndex + 1];
  if (!missingCorner) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      `${fixture.fixtureId} main lane has no missing-corner frontier`,
    );
  }
  const clarifyCorner = materializeLane(
    fixture.clarifyCorner,
    missingCorner,
    fixture.problemSpec,
    `${fixture.fixtureId} clarifyCorner lane`,
  );
  const continueAfterClarification = materializeLane(
    fixture.continueAfterClarification,
    clarifyCorner.resultFrontier,
    fixture.problemSpec,
    `${fixture.fixtureId} continueAfterClarification lane`,
  );
  return Object.freeze({
    ...fixture,
    main,
    clarifyCorner,
    continueAfterClarification,
  });
}

function decodeCatalog(values: readonly unknown[]): readonly MaterializedFixture[] {
  if (values.length === 0) {
    return failParametricChoreographyFixture(
      "invalid_fixture",
      "fixture catalog cannot be empty",
    );
  }
  const fixtures = Object.freeze(
    values.map((value) =>
      materializeFixture(decodeParametricChoreographyFixtureEnvelope(value)),
    ),
  );
  const ids = new Set<string>();
  for (const fixture of fixtures) {
    if (ids.has(fixture.fixtureId)) {
      return failParametricChoreographyFixture(
        "invalid_fixture",
        `fixtureId ${fixture.fixtureId} is duplicated`,
      );
    }
    ids.add(fixture.fixtureId);
  }
  return fixtures;
}

export function decodeParametricChoreographyFixtureMode(
  value: unknown,
): ParametricChoreographyFixtureMode {
  if (
    typeof value !== "string" ||
    !PARAMETRIC_CHOREOGRAPHY_FIXTURE_MODES.some((mode) => mode === value)
  ) {
    throw new TypeError("fixture mode must be main or adaptive");
  }
  return value as ParametricChoreographyFixtureMode;
}

function requestMatchesFrontier(
  request: ParametricChoreographyRequestV3,
  frontier: ParametricChoreographyFrontier,
): boolean {
  return (
    sameParametricChoreographyFixtureValue(request.baseScene, frontier.scene) &&
    sameParametricChoreographyFixtureValue(
      request.baseSemanticScene,
      frontier.semanticScene,
    )
  );
}

function laneForRequest(
  request: ParametricChoreographyRequestV3,
  fixtures: readonly MaterializedFixture[],
): {
  readonly fixture: MaterializedFixture;
  readonly laneName: ParametricChoreographyFixtureBatch["lane"];
  readonly lane: MaterializedFixtureLane;
} {
  if (request.routingMode !== "reflex") {
    return failParametricChoreographyFixture(
      "request_mismatch",
      "deterministic fixtures accept only zero-model Reflex requests",
    );
  }
  const candidates = fixtures.flatMap((fixture) =>
    (
      [
        ["main", fixture.main],
        ["clarifyCorner", fixture.clarifyCorner],
        ["continueAfterClarification", fixture.continueAfterClarification],
      ] as const
    ).flatMap(([laneName, lane]) =>
      lane &&
      lane.generation === request.generation &&
      sameParametricChoreographyFixtureValue(
        lane.route,
        request.requestedRoute,
      ) &&
      requestMatchesFrontier(request, lane.baseFrontier)
        ? [{ fixture, laneName, lane }]
        : [],
    ),
  );
  const matchingProblem = candidates.filter(({ fixture, laneName }) =>
    laneName === "main"
      ? request.problemText === fixture.problemText
      : request.problemText === null,
  );
  if (matchingProblem.length !== 1) {
    return failParametricChoreographyFixture(
      "request_mismatch",
      "request does not match one exact fixture problem, generation, route, and certified base",
    );
  }
  return matchingProblem[0];
}

function selectBatch(
  request: ParametricChoreographyRequestV3,
  modeValue: unknown,
  fixtures: readonly MaterializedFixture[],
): ParametricChoreographyFixtureBatch {
  const mode = decodeParametricChoreographyFixtureMode(modeValue);
  const selected = laneForRequest(request, fixtures);
  if (mode === "adaptive" && selected.laneName === "main") {
    if (!selected.fixture.clarifyCorner) {
      return failParametricChoreographyFixture(
        "request_mismatch",
        "adaptive playback is available only for the primary fixture",
      );
    }
    const missingCornerIndex = selected.lane.checkpointIds.indexOf(
      "missing_corner",
    );
    return Object.freeze({
      fixtureId: selected.fixture.fixtureId,
      lane: "main",
      events: Object.freeze(
        selected.lane.events.slice(0, missingCornerIndex + 2),
      ),
      holdOpenUntilAbort: true,
    });
  }
  return Object.freeze({
    fixtureId: selected.fixture.fixtureId,
    lane: selected.laneName,
    events: selected.lane.events,
    holdOpenUntilAbort: false,
  });
}

/** Build a preflighted, immutable fixture selector once per runner session. */
export function createParametricChoreographyFixtureCatalog(
  values: readonly unknown[],
): ParametricChoreographyFixtureCatalog {
  const fixtures = decodeCatalog(values);
  return Object.freeze({
    select: (
      request: ParametricChoreographyRequestV3,
      mode: ParametricChoreographyFixtureMode,
    ) => selectBatch(request, mode, fixtures),
  });
}
