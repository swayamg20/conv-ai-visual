"""Generate provider-free Gate 1.8 semantic-storyboard fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from murmur.live_scene.contracts import SceneState
from murmur.live_scene.semantic_storyboard_checkpoint_contracts import (
    SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    AbstainStoryboardRecordV1,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    SemanticStoryboardRecordV1,
    StoryboardAbstainReasonCode,
    StoryboardClaimId,
    StoryboardConceptId,
    StoryboardEvidenceId,
    StoryboardTrajectoryId,
    TraceStoryboardRecordV1,
    semantic_storyboard_program_sha256,
    semantic_storyboard_scene_sha256,
)
from murmur.live_scene.semantic_storyboard_requests import (
    SEMANTIC_STORYBOARD_PROTOCOL,
    SemanticStoryboardDirectorRequestV1,
    SemanticStoryboardReflexRequestV1,
)
from murmur.live_scene.semantic_storyboard_service import SemanticStoryboardService
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SemanticStoryboardCompletionReason,
    SemanticStoryboardSceneCheckpointEventV1,
    SemanticStoryboardSceneStreamCompletedEventV1,
    SemanticStoryboardSceneStreamDeclinedEventV1,
    SemanticStoryboardSceneStreamEventV1,
    SemanticStoryboardSceneStreamStartedEventV1,
    dump_semantic_storyboard_scene_stream_event,
)
from murmur.live_scene.semantic_storyboard_wire import (
    encode_semantic_storyboard_scene_stream_event,
)

FIXTURE_FORMAT_VERSION: Final = 1
FIXTURE_SPEED_MPS: Final = 20
PRIMARY_ANGLE_PAIR: Final = (30, 60)
OUTPUT_FILENAMES: Final = {
    (30, 45): "semantic-storyboard-v20-a30-a45.v1.json",
    PRIMARY_ANGLE_PAIR: "semantic-storyboard-v20-a30-a60.v1.json",
    (45, 60): "semantic-storyboard-v20-a45-a60.v1.json",
}

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_VISUAL_FIXTURE_DIRECTORY: Final = (
    _REPOSITORY_ROOT / "web" / "src" / "features" / "live-scene" / "fixtures"
)
_GATE_18_FIXTURE_DIRECTORY: Final = _VISUAL_FIXTURE_DIRECTORY / "semantic-storyboard-v1"


@dataclass(frozen=True, slots=True)
class _Program:
    program_id: str
    prompt: str
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...]


@dataclass(frozen=True, slots=True)
class _NegativeScenario:
    scenario_id: str
    prompt: str
    reason_code: StoryboardAbstainReasonCode


@dataclass(frozen=True, slots=True)
class _Lane:
    scenario_id: str
    generation: int
    routing_mode: str
    prompt: str | None
    provider_records: tuple[SemanticStoryboardRecordV1, ...]
    base_scene: SceneState
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1
    events: tuple[SemanticStoryboardSceneStreamEventV1, ...]
    checkpoints: tuple[SemanticStoryboardSceneCheckpointEventV1, ...]
    result_scene: SceneState
    result_semantic_scene: ProjectileStoryboardSemanticSceneStateV1
    fake_provider_stream_count: int
    tail_outcome: str | None = None


class _FixtureStream:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = list(chunks)
        self.close_count = 0

    def __aiter__(self) -> _FixtureStream:
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        self.close_count += 1


class _FixtureClient:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.stream_instance = _FixtureStream(chunks)
        self.calls = 0

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str | bytes]:
        if self.calls or len(messages) != 2 or temperature != 0.0 or max_tokens <= 0:
            raise RuntimeError("fixture Director dispatch changed its bounded call contract")
        self.calls += 1
        return self.stream_instance


def _zero_clock() -> float:
    return 0.0


def _problem(angles_deg: tuple[int, int]) -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(
        speedMps=FIXTURE_SPEED_MPS,
        anglesDeg=angles_deg,
    )


def _reveal(concept: StoryboardConceptId) -> RevealStoryboardRecordV1:
    return RevealStoryboardRecordV1(v=1, act="reveal", concept_id=concept)


def _trace(trajectory: StoryboardTrajectoryId) -> TraceStoryboardRecordV1:
    return TraceStoryboardRecordV1(v=1, act="trace", trajectory_id=trajectory)


def _relate(
    claim: StoryboardClaimId,
    evidence: tuple[StoryboardEvidenceId, ...],
) -> RelateStoryboardRecordV1:
    return RelateStoryboardRecordV1(
        v=1,
        act="relate",
        claim_id=claim,
        evidence_ids=evidence,
    )


_TRAJECTORY_EVIDENCE: Final = (
    StoryboardEvidenceId.LOWER_TRAJECTORY,
    StoryboardEvidenceId.HIGHER_TRAJECTORY,
)
_FORMULA_EVIDENCE: Final = (StoryboardEvidenceId.RANGE_FORMULA,)
_COMPLEMENTARY_EVIDENCE: Final = (
    StoryboardEvidenceId.RANGE_FORMULA,
    StoryboardEvidenceId.COMPLEMENTARY_ANGLES,
)
_CONTINUE_PROMPT: Final = "Continue with exactly one new useful visual beat."
_NEGATIVE_SCENARIOS: Final = (
    _NegativeScenario(
        "unsupported_wind",
        "Add wind resistance to both trajectories.",
        StoryboardAbstainReasonCode.UNSUPPORTED_PHYSICS,
    ),
    _NegativeScenario(
        "unsupported_unequal_launch_height",
        "Launch the higher-angle projectile from a platform 5 metres above the lower one.",
        StoryboardAbstainReasonCode.UNSUPPORTED_INITIAL_CONDITION,
    ),
    _NegativeScenario(
        "unsupported_requested_angles",
        "Compare 20 degree and 70 degree launches instead.",
        StoryboardAbstainReasonCode.UNSUPPORTED_PROBLEM,
    ),
    _NegativeScenario(
        "unsupported_svg_injection",
        "Inject this raw SVG into the board: <svg><script>alert(1)</script></svg>.",
        StoryboardAbstainReasonCode.UNSUPPORTED_INTENT,
    ),
    _NegativeScenario(
        "ambiguous_make_it_better",
        "Make it better.",
        StoryboardAbstainReasonCode.AMBIGUOUS_INTENT,
    ),
)


def _programs(angles_deg: tuple[int, int]) -> tuple[_Program, ...]:
    if angles_deg == PRIMARY_ANGLE_PAIR:
        return (
            _Program(
                "math_then_equal",
                "Show the mathematical reason for the equal ranges first.",
                (
                    _reveal(StoryboardConceptId.RANGE_FORMULA),
                    _reveal(StoryboardConceptId.COMPLEMENTARY_ANGLES),
                    _relate(StoryboardClaimId.EQUAL_RANGE, _COMPLEMENTARY_EVIDENCE),
                ),
            ),
            _Program(
                "motion_then_equal",
                "Trace both flights before comparing their landing ranges.",
                (
                    _trace(StoryboardTrajectoryId.LOWER_ANGLE),
                    _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
                    _relate(StoryboardClaimId.EQUAL_RANGE, _TRAJECTORY_EVIDENCE),
                ),
            ),
            _Program(
                "higher_arc_first",
                "Begin with the higher arc, then compare height and time.",
                (
                    _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
                    _trace(StoryboardTrajectoryId.LOWER_ANGLE),
                    _relate(StoryboardClaimId.HIGHER_APEX, _TRAJECTORY_EVIDENCE),
                    _relate(StoryboardClaimId.LONGER_FLIGHT, _TRAJECTORY_EVIDENCE),
                ),
            ),
        )
    if angles_deg == (30, 45):
        return (
            _Program(
                "formula_inequality",
                "Use the range formula to show that these distances differ.",
                (
                    _reveal(StoryboardConceptId.RANGE_FORMULA),
                    _relate(StoryboardClaimId.UNEQUAL_RANGE, _FORMULA_EVIDENCE),
                ),
            ),
            _Program(
                "motion_inequality",
                "Trace the steeper flight first, then compare the ranges.",
                (
                    _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
                    _trace(StoryboardTrajectoryId.LOWER_ANGLE),
                    _relate(StoryboardClaimId.UNEQUAL_RANGE, _TRAJECTORY_EVIDENCE),
                ),
            ),
        )
    if angles_deg == (45, 60):
        return (
            _Program(
                "height_then_flight",
                "Trace the lower arc first, then compare height and flight time.",
                (
                    _trace(StoryboardTrajectoryId.LOWER_ANGLE),
                    _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
                    _relate(StoryboardClaimId.HIGHER_APEX, _TRAJECTORY_EVIDENCE),
                    _relate(StoryboardClaimId.LONGER_FLIGHT, _TRAJECTORY_EVIDENCE),
                ),
            ),
        )
    raise ValueError("unsupported fixture angle pair")


def _canonical_line(record: SemanticStoryboardRecordV1) -> bytes:
    return (
        json.dumps(
            record.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _incremental_chunks(payload: bytes) -> tuple[bytes, ...]:
    sizes = (1, 7, 3, 19, 5, 11)
    chunks: list[bytes] = []
    offset = 0
    index = 0
    while offset < len(payload):
        size = sizes[index % len(sizes)]
        chunks.append(payload[offset : offset + size])
        offset += size
        index += 1
    return tuple(chunks)


def _accepted_records(
    scene: ProjectileStoryboardSemanticSceneStateV1,
) -> tuple[AcceptedSemanticStoryboardRecordV1, ...]:
    return () if not scene.components else scene.components[0].accepted_records


def _result_frontier(
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
    checkpoints: tuple[SemanticStoryboardSceneCheckpointEventV1, ...],
) -> tuple[SceneState, ProjectileStoryboardSemanticSceneStateV1]:
    scene = base_scene
    semantic_scene = base_semantic_scene
    for checkpoint in checkpoints:
        transition = checkpoint.transition
        if transition.base_scene != scene or transition.base_semantic_scene != semantic_scene:
            raise RuntimeError("fixture checkpoint did not join the accepted frontier")
        scene = transition.result_scene
        semantic_scene = transition.result_semantic_scene
    return scene, semantic_scene


async def _anchor_lane(
    problem: PairedProjectileComparisonSpecV1,
) -> _Lane:
    request = SemanticStoryboardReflexRequestV1(
        protocol=SEMANTIC_STORYBOARD_PROTOCOL,
        routing_mode="reflex",
        problem_spec=problem,
        generation=1,
        base_scene=SceneState(revision=0),
        base_semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
    )
    events = tuple(
        [
            event
            async for event in SemanticStoryboardService(clock=_zero_clock).stream_events(request)
        ]
    )
    checkpoints = tuple(
        event for event in events if isinstance(event, SemanticStoryboardSceneCheckpointEventV1)
    )
    if (
        len(events) != 3
        or not isinstance(events[0], SemanticStoryboardSceneStreamStartedEventV1)
        or len(checkpoints) != 1
        or not isinstance(events[-1], SemanticStoryboardSceneStreamCompletedEventV1)
        or events[-1].reason_code is not SemanticStoryboardCompletionReason.ANCHOR
    ):
        raise RuntimeError("fixture anchor changed its provider-free lifecycle")
    result_scene, result_semantic_scene = _result_frontier(
        request.base_scene,
        request.base_semantic_scene,
        checkpoints,
    )
    return _Lane(
        scenario_id="anchor",
        generation=1,
        routing_mode="reflex",
        prompt=None,
        provider_records=(),
        base_scene=request.base_scene,
        base_semantic_scene=request.base_semantic_scene,
        events=events,
        checkpoints=checkpoints,
        result_scene=result_scene,
        result_semantic_scene=result_semantic_scene,
        fake_provider_stream_count=0,
    )


async def _director_lane(
    problem: PairedProjectileComparisonSpecV1,
    *,
    scenario_id: str,
    generation: int,
    prompt: str,
    records: tuple[SemanticStoryboardRecordV1, ...],
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
    malformed_tail: bool = False,
) -> _Lane:
    payload = b"".join(_canonical_line(record) for record in records)
    tail_outcome = None
    if malformed_tail:
        # The rejected bytes are deliberately absent from the serialized fixture.
        payload += b'{"v":1,"act":broken}\n'
        chunks = (payload,)
        tail_outcome = "malformed_json"
    else:
        chunks = _incremental_chunks(payload)
    client = _FixtureClient(chunks)
    admission_count = 0

    async def admit() -> None:
        nonlocal admission_count
        admission_count += 1

    request = SemanticStoryboardDirectorRequestV1(
        protocol=SEMANTIC_STORYBOARD_PROTOCOL,
        routing_mode="director",
        prompt=prompt,
        problem_spec=problem,
        generation=generation,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    events = tuple(
        [
            event
            async for event in SemanticStoryboardService(
                client,
                clock=_zero_clock,
                before_provider_dispatch=admit,
            ).stream_events(request)
        ]
    )
    if client.calls != 1 or admission_count != 1 or client.stream_instance.close_count != 1:
        raise RuntimeError("fixture Director did not use one admitted fake provider stream")
    if not events or not isinstance(events[0], SemanticStoryboardSceneStreamStartedEventV1):
        raise RuntimeError("fixture Director lifecycle omitted its started event")
    checkpoints = tuple(
        event for event in events if isinstance(event, SemanticStoryboardSceneCheckpointEventV1)
    )
    result_scene, result_semantic_scene = _result_frontier(
        base_scene,
        base_semantic_scene,
        checkpoints,
    )
    for event in events:
        encode_semantic_storyboard_scene_stream_event(event)
    return _Lane(
        scenario_id=scenario_id,
        generation=generation,
        routing_mode="director",
        prompt=prompt,
        provider_records=records,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
        events=events,
        checkpoints=checkpoints,
        result_scene=result_scene,
        result_semantic_scene=result_semantic_scene,
        fake_provider_stream_count=1,
        tail_outcome=tail_outcome,
    )


def _frontier_summary(
    problem: PairedProjectileComparisonSpecV1,
    scene: ProjectileStoryboardSemanticSceneStateV1,
) -> dict[str, object]:
    records = _accepted_records(scene)
    return {
        "revision": scene.revision,
        "acceptedRecords": [record.model_dump(mode="json", by_alias=True) for record in records],
        "programSha256": semantic_storyboard_program_sha256(problem, records),
        "semanticSceneSha256": semantic_storyboard_scene_sha256(scene),
        "certificateHeadSha256": scene.certificate_head_sha256,
    }


def _dump_lane(
    problem: PairedProjectileComparisonSpecV1,
    lane: _Lane,
    **metadata: object,
) -> dict[str, object]:
    return {
        "scenarioId": lane.scenario_id,
        "generation": lane.generation,
        "routingMode": lane.routing_mode,
        "prompt": lane.prompt,
        "providerRecords": [
            record.model_dump(mode="json", by_alias=True) for record in lane.provider_records
        ],
        "fakeProviderStreamCount": lane.fake_provider_stream_count,
        "tailOutcome": lane.tail_outcome,
        "baseScene": lane.base_scene.model_dump(mode="json", by_alias=True),
        "baseSemanticScene": lane.base_semantic_scene.model_dump(mode="json", by_alias=True),
        "baseFrontier": _frontier_summary(problem, lane.base_semantic_scene),
        "checkpointIds": [
            checkpoint.transition.checkpoint.checkpoint_id for checkpoint in lane.checkpoints
        ],
        "checkpointCount": len(lane.checkpoints),
        "events": [dump_semantic_storyboard_scene_stream_event(event) for event in lane.events],
        "expectedTerminal": {
            "scene": lane.result_scene.model_dump(mode="json", by_alias=True),
            "semanticScene": lane.result_semantic_scene.model_dump(mode="json", by_alias=True),
            "frontier": _frontier_summary(problem, lane.result_semantic_scene),
        },
        **metadata,
    }


def _prefix_frontiers(
    lane: _Lane,
) -> tuple[tuple[SceneState, ProjectileStoryboardSemanticSceneStateV1], ...]:
    frontiers = [(lane.base_scene, lane.base_semantic_scene)]
    frontiers.extend(
        (checkpoint.transition.result_scene, checkpoint.transition.result_semantic_scene)
        for checkpoint in lane.checkpoints
    )
    return tuple(frontiers)


async def _build_fixture(angles_deg: tuple[int, int]) -> dict[str, object]:
    problem = _problem(angles_deg)
    anchor = await _anchor_lane(problem)
    program_lanes: list[tuple[_Program, _Lane]] = []
    for offset, program in enumerate(_programs(angles_deg), start=10):
        lane = await _director_lane(
            problem,
            scenario_id=program.program_id,
            generation=offset,
            prompt=program.prompt,
            records=program.records,
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
        )
        program_lanes.append((program, lane))

    continuations: list[dict[str, object]] = []
    negative_lanes: list[_Lane] = []
    sole_abstain: dict[str, object] | None = None
    accepted_prefix: dict[str, object] | None = None
    if angles_deg == PRIMARY_ANGLE_PAIR:
        source_program, source_lane = next(
            item for item in program_lanes if item[0].program_id == "higher_arc_first"
        )
        continuation_records = (
            *source_program.records,
            _reveal(StoryboardConceptId.RANGE_FORMULA),
        )
        for prefix_count, (scene, semantic_scene) in enumerate(_prefix_frontiers(source_lane)):
            lane = await _director_lane(
                problem,
                scenario_id=f"continue_higher_arc_first_prefix_{prefix_count}",
                generation=100 + prefix_count,
                prompt=_CONTINUE_PROMPT,
                records=(continuation_records[prefix_count],),
                base_scene=scene,
                base_semantic_scene=semantic_scene,
            )
            continuations.append(
                _dump_lane(
                    problem,
                    lane,
                    fromProgramId=source_program.program_id,
                    fromPrefixCount=prefix_count,
                )
            )

        abstain = await _director_lane(
            problem,
            scenario_id="sole_abstain",
            generation=200,
            prompt="Do nothing if this request has no supported forward step.",
            records=(
                AbstainStoryboardRecordV1(
                    v=1,
                    act="abstain",
                    reason_code=StoryboardAbstainReasonCode.UNSUPPORTED_INTENT,
                ),
            ),
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
        )
        if not isinstance(abstain.events[-1], SemanticStoryboardSceneStreamDeclinedEventV1):
            raise RuntimeError("fixture sole abstention was not held through clean EOF")
        sole_abstain = _dump_lane(problem, abstain)

        malformed = await _director_lane(
            problem,
            scenario_id="accepted_prefix_malformed_tail",
            generation=201,
            prompt="Show one formula, then stop safely if later output is malformed.",
            records=(_reveal(StoryboardConceptId.RANGE_FORMULA),),
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
            malformed_tail=True,
        )
        terminal = malformed.events[-1]
        if (
            not isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
            or terminal.reason_code is not SemanticStoryboardCompletionReason.ACCEPTED_PREFIX
        ):
            raise RuntimeError("fixture malformed tail did not retain its accepted prefix")
        accepted_prefix = _dump_lane(problem, malformed)

        recovery = await _director_lane(
            problem,
            scenario_id="continue_accepted_prefix_malformed_tail_prefix_1",
            generation=202,
            prompt=_CONTINUE_PROMPT,
            records=(_reveal(StoryboardConceptId.COMPLEMENTARY_ANGLES),),
            base_scene=malformed.result_scene,
            base_semantic_scene=malformed.result_semantic_scene,
        )
        continuations.append(
            _dump_lane(
                problem,
                recovery,
                fromScenarioId=malformed.scenario_id,
                fromPrefixCount=len(malformed.checkpoints),
            )
        )

        for generation, scenario in enumerate(_NEGATIVE_SCENARIOS, start=210):
            lane = await _director_lane(
                problem,
                scenario_id=scenario.scenario_id,
                generation=generation,
                prompt=scenario.prompt,
                records=(
                    AbstainStoryboardRecordV1(
                        v=1,
                        act="abstain",
                        reason_code=scenario.reason_code,
                    ),
                ),
                base_scene=anchor.result_scene,
                base_semantic_scene=anchor.result_semantic_scene,
            )
            if (
                lane.checkpoints
                or lane.result_scene != anchor.result_scene
                or lane.result_semantic_scene != anchor.result_semantic_scene
                or not isinstance(lane.events[-1], SemanticStoryboardSceneStreamDeclinedEventV1)
                or lane.events[-1].reason_code is not scenario.reason_code
            ):
                raise RuntimeError(
                    f"fixture negative lane {scenario.scenario_id} mutated its frontier"
                )
            negative_lanes.append(lane)

    fixture_id = f"semantic-storyboard-v20-a{angles_deg[0]}-a{angles_deg[1]}"
    fake_stream_count = sum(lane.fake_provider_stream_count for _, lane in program_lanes)
    fake_stream_count += len(continuations)
    fake_stream_count += len(negative_lanes)
    fake_stream_count += int(sole_abstain is not None) + int(accepted_prefix is not None)
    return {
        "v": FIXTURE_FORMAT_VERSION,
        "fixtureId": fixture_id,
        "protocol": SEMANTIC_STORYBOARD_PROTOCOL,
        "compilerVersion": SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
        "scenario": "qualified_semantic_storyboard",
        "problemSpec": problem.model_dump(mode="json", by_alias=True),
        "coverage": {
            "anglePair": list(angles_deg),
            "isComplementary": problem.has_complementary_angles,
            "expectedRangeRelation": (
                StoryboardClaimId.EQUAL_RANGE.value
                if problem.has_complementary_angles
                else StoryboardClaimId.UNEQUAL_RANGE.value
            ),
        },
        "externalProviderRequestCount": 0,
        "fakeProviderStreamCount": fake_stream_count,
        "anchor": _dump_lane(problem, anchor),
        "programs": [
            _dump_lane(problem, lane, programId=program.program_id)
            for program, lane in program_lanes
        ],
        "continuations": continuations,
        "negativeLanes": [_dump_lane(problem, lane) for lane in negative_lanes],
        "soleAbstain": sole_abstain,
        "acceptedPrefixMalformedTail": accepted_prefix,
    }


async def _build_all_fixtures() -> dict[str, dict[str, object]]:
    return {OUTPUT_FILENAMES[angles]: await _build_fixture(angles) for angles in OUTPUT_FILENAMES}


def render_semantic_storyboard_fixtures() -> dict[str, bytes]:
    """Return stable compact fixture bytes without filesystem or provider I/O."""

    fixtures = asyncio.run(_build_all_fixtures())
    return {
        filename: (
            json.dumps(
                fixture,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for filename, fixture in fixtures.items()
    }


def write_semantic_storyboard_fixtures(output_directory: Path) -> None:
    """Write only the Gate 1.8 fixture set beneath an explicit safe directory."""

    resolved_output = output_directory.resolve()
    sealed_root = _VISUAL_FIXTURE_DIRECTORY.resolve()
    if resolved_output == sealed_root or (
        sealed_root in resolved_output.parents
        and resolved_output != _GATE_18_FIXTURE_DIRECTORY.resolve()
    ):
        raise ValueError("Gate 1.8 generation may not target sealed visual fixtures")
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in render_semantic_storyboard_fixtures().items():
        output_directory.joinpath(filename).write_bytes(payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="Directory for the three generated compact JSON fixtures.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    write_semantic_storyboard_fixtures(args.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
