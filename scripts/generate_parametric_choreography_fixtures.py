"""Generate the provider-free Gate 1.6 parametric choreography fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ClarifyCornerRouteV2,
    RoutedChoreographyRouteV2,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import (
    PutSceneOperation,
    RemoveSceneOperation,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.parametric_checkpoint_contracts import CHECKPOINT_COMPILER_V3_VERSION
from murmur.live_scene.parametric_choreography_requests import (
    PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    ParametricChoreographyReflexRequestV3,
)
from murmur.live_scene.parametric_choreography_service import ParametricChoreographyService
from murmur.live_scene.parametric_choreography_service_contracts import (
    ParametricChoreographySceneCheckpointEventV3,
    ParametricChoreographySceneStreamEventV3,
    dump_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.parametric_choreography_wire import (
    encode_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState

FIXTURE_FORMAT_VERSION: Final = 1
FIXTURE_ATTEMPT: Final = 1
PRIMARY_PROBLEM: Final = (8, 20)
OUTPUT_FILENAMES: Final = {
    (2, 80): "completing-square-parametric-b2-c80.v3.json",
    PRIMARY_PROBLEM: "completing-square-parametric-b8-c20.v3.json",
    (16, 17): "completing-square-parametric-b16-c17.v3.json",
}

_MAIN_CHECKPOINT_IDS: Final = tuple(
    checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER
)
_MISSING_CORNER_PREFIX_COUNT: Final = _MAIN_CHECKPOINT_IDS.index("missing_corner") + 1


@dataclass(frozen=True, slots=True)
class _Lane:
    request: ParametricChoreographyReflexRequestV3
    events: tuple[ParametricChoreographySceneStreamEventV3, ...]
    checkpoints: tuple[ParametricChoreographySceneCheckpointEventV3, ...]
    result_scene: SceneState
    result_semantic_scene: SemanticSceneState


def _problem_text(problem: CompletingSquareProblemSpecV1) -> str:
    return f"x² + {problem.linear_coefficient}x = {problem.right_hand_side}"


def _problem(linear_coefficient: int, right_hand_side: int) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=linear_coefficient,
        rightHandSide=right_hand_side,
    )


def _base_frontier(semantic_scene: SemanticSceneState) -> dict[str, object]:
    if not semantic_scene.components:
        checkpoint_id = None
        corner_clarified = False
    else:
        if len(semantic_scene.components) != 1 or not isinstance(
            semantic_scene.components[0], ParametricCompletingSquareStateV1
        ):
            raise RuntimeError("fixture lane must begin at one canonical V3 frontier")
        component = semantic_scene.components[0]
        checkpoint_id = (
            component.last_main_checkpoint.value
            if component.last_main_checkpoint is not None
            else None
        )
        corner_clarified = component.corner_clarified
    return {
        "revision": semantic_scene.revision,
        "checkpointId": checkpoint_id,
        "cornerClarified": corner_clarified,
        "certificateHeadSha256": semantic_scene.certificate_head_sha256,
    }


def _apply_checkpoint(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
    checkpoint: ParametricChoreographySceneCheckpointEventV3,
) -> tuple[SceneState, SemanticSceneState]:
    if (
        checkpoint.base_revision != scene.revision
        or checkpoint.semantic.semantic_base_revision != semantic_scene.revision
        or checkpoint.semantic.semantic_base_certificate_sha256
        != semantic_scene.certificate_head_sha256
    ):
        raise RuntimeError("fixture checkpoint does not join its submitted frontier")

    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        elif isinstance(operation, RemoveSceneOperation):
            if operation.id not in nodes:
                raise RuntimeError("fixture checkpoint removes an absent node")
            del nodes[operation.id]
            order.remove(operation.id)
        else:
            raise RuntimeError("fixture checkpoint contains an unknown operation")

    result_scene = SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )
    if result_scene.revision != checkpoint.result_revision:
        raise RuntimeError("fixture checkpoint changed the low-level revision join")
    result_semantic_scene = SemanticSceneState(
        revision=semantic_scene.revision + 1,
        components=(checkpoint.semantic.result_component,),
        certificate_head_sha256=checkpoint.semantic.semantic_result_certificate_sha256,
    )
    if result_semantic_scene.revision != checkpoint.semantic.semantic_result_revision:
        raise RuntimeError("fixture checkpoint changed the semantic revision join")
    return result_scene, result_semantic_scene


def _materialize_checkpoints(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
    checkpoints: tuple[ParametricChoreographySceneCheckpointEventV3, ...],
) -> tuple[SceneState, SemanticSceneState]:
    for checkpoint in checkpoints:
        scene, semantic_scene = _apply_checkpoint(scene, semantic_scene, checkpoint)
    return scene, semantic_scene


def _request(
    *,
    problem_text: str,
    generation: int,
    route: RoutedChoreographyRouteV2,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> ParametricChoreographyReflexRequestV3:
    return ParametricChoreographyReflexRequestV3.model_validate(
        {
            "protocol": PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
            "problemText": problem_text,
            "generation": generation,
            "baseScene": base_scene.model_dump(mode="json", by_alias=True),
            "baseSemanticScene": base_semantic_scene.model_dump(mode="json", by_alias=True),
            "routingMode": "reflex",
            "requestedRoute": route.model_dump(mode="json", by_alias=True),
        }
    )


async def _build_lane(
    service: ParametricChoreographyService,
    *,
    request: ParametricChoreographyReflexRequestV3,
    problem: CompletingSquareProblemSpecV1,
    expected_checkpoint_ids: tuple[str, ...],
) -> _Lane:
    events = tuple([event async for event in service.stream_events(request)])
    checkpoints = tuple(
        event for event in events if isinstance(event, ParametricChoreographySceneCheckpointEventV3)
    )
    expected_types = (
        "scene_stream_started",
        *("parametric_choreography_scene_checkpoint" for _ in expected_checkpoint_ids),
        "scene_stream_completed",
    )
    if tuple(event.type for event in events) != expected_types:
        raise RuntimeError("fixture service did not return one complete successful lifecycle")
    if not isinstance(events[0], SceneStreamStartedEvent) or not isinstance(
        events[-1], SceneStreamCompletedEvent
    ):
        raise RuntimeError("fixture lifecycle terminals changed type")
    if events[0].base_revision != request.base_scene.revision:
        raise RuntimeError("fixture started event changed the submitted base")
    if tuple(event.semantic.checkpoint_id.value for event in checkpoints) != (
        expected_checkpoint_ids
    ):
        raise RuntimeError("fixture service returned the wrong checkpoint suffix")
    if any(event.semantic.problem_spec != problem for event in checkpoints):
        raise RuntimeError("fixture service changed the bound problem")
    if tuple(event.sequence for event in checkpoints) != tuple(range(1, len(checkpoints) + 1)):
        raise RuntimeError("fixture service returned a non-canonical checkpoint sequence")
    if any(event.generation != request.generation for event in events) or any(
        event.attempt != FIXTURE_ATTEMPT for event in (events[0], *checkpoints)
    ):
        raise RuntimeError("fixture service changed lifecycle identity")

    result_scene, result_semantic_scene = _materialize_checkpoints(
        request.base_scene,
        request.base_semantic_scene,
        checkpoints,
    )
    completed = events[-1]
    if (
        completed.final_revision != result_scene.revision
        or completed.patch_count != len(checkpoints)
        or completed.first_patch_ms != 0.0
        or completed.total_ms != 0.0
        or completed.repaired
    ):
        raise RuntimeError("fixture completed event changed deterministic terminal metadata")
    for event in events:
        encode_parametric_choreography_scene_stream_event(event)
    return _Lane(
        request=request,
        events=events,
        checkpoints=checkpoints,
        result_scene=result_scene,
        result_semantic_scene=result_semantic_scene,
    )


def _dump_lane(lane: _Lane) -> dict[str, object]:
    return {
        "generation": lane.request.generation,
        "base": _base_frontier(lane.request.base_semantic_scene),
        "route": lane.request.requested_route.model_dump(mode="json", by_alias=True),
        "checkpointIds": [
            checkpoint.semantic.checkpoint_id.value for checkpoint in lane.checkpoints
        ],
        "checkpointCount": len(lane.checkpoints),
        "events": [dump_parametric_choreography_scene_stream_event(event) for event in lane.events],
    }


async def _build_fixture(
    linear_coefficient: int,
    right_hand_side: int,
) -> dict[str, object]:
    problem = _problem(linear_coefficient, right_hand_side)
    problem_text = _problem_text(problem)
    service = ParametricChoreographyService(clock=lambda: 0.0)
    empty_scene = SceneState(revision=0)
    empty_semantic_scene = SemanticSceneState(revision=0)
    solve_route = AdvanceChoreographyRouteV2(targetStage="solve")
    main = await _build_lane(
        service,
        request=_request(
            problem_text=problem_text,
            generation=1,
            route=solve_route,
            base_scene=empty_scene,
            base_semantic_scene=empty_semantic_scene,
        ),
        problem=problem,
        expected_checkpoint_ids=_MAIN_CHECKPOINT_IDS,
    )
    lanes: dict[str, object] = {"main": _dump_lane(main)}

    if (linear_coefficient, right_hand_side) == PRIMARY_PROBLEM:
        missing_corner_scene, missing_corner_semantic_scene = _materialize_checkpoints(
            empty_scene,
            empty_semantic_scene,
            main.checkpoints[:_MISSING_CORNER_PREFIX_COUNT],
        )
        clarify = await _build_lane(
            service,
            request=_request(
                problem_text=problem_text,
                generation=2,
                route=ClarifyCornerRouteV2(),
                base_scene=missing_corner_scene,
                base_semantic_scene=missing_corner_semantic_scene,
            ),
            problem=problem,
            expected_checkpoint_ids=(CompletingSquareCheckpointId.CORNER_DETAIL.value,),
        )
        continuation = await _build_lane(
            service,
            request=_request(
                problem_text=problem_text,
                generation=3,
                route=solve_route,
                base_scene=clarify.result_scene,
                base_semantic_scene=clarify.result_semantic_scene,
            ),
            problem=problem,
            expected_checkpoint_ids=_MAIN_CHECKPOINT_IDS[_MISSING_CORNER_PREFIX_COUNT:],
        )
        lanes["clarifyCorner"] = _dump_lane(clarify)
        lanes["continueAfterClarification"] = _dump_lane(continuation)

    fixture_id = f"completing-square-parametric-b{linear_coefficient}-c{right_hand_side}"
    return {
        "v": FIXTURE_FORMAT_VERSION,
        "fixtureId": fixture_id,
        "protocol": PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
        "compilerVersion": CHECKPOINT_COMPILER_V3_VERSION,
        "problemText": problem_text,
        "problemSpec": problem.model_dump(mode="json", by_alias=True),
        "providerRequestCount": 0,
        "lanes": lanes,
    }


async def _build_all_fixtures() -> dict[str, dict[str, object]]:
    return {
        filename: await _build_fixture(linear_coefficient, right_hand_side)
        for (linear_coefficient, right_hand_side), filename in OUTPUT_FILENAMES.items()
    }


def render_parametric_choreography_fixtures() -> dict[str, bytes]:
    """Return all stable compact fixture bytes without filesystem or provider I/O."""

    fixtures = asyncio.run(_build_all_fixtures())
    return {
        filename: (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for filename, payload in fixtures.items()
    }


def write_parametric_choreography_fixtures(output_directory: Path) -> None:
    """Write the exact Gate 1.6 fixture set beneath an explicit directory."""

    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in render_parametric_choreography_fixtures().items():
        (output_directory / filename).write_bytes(payload)


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
    write_parametric_choreography_fixtures(args.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
