from __future__ import annotations

import pytest
from murmur.live_scene.choreography_contracts import CompletingSquareStage
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    COMPLETING_SQUARE_STAGE_PREFIXES,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
    checkpoint_prefix,
    checkpoints_through,
    introduction_stage_for,
    next_main_checkpoint,
)
from pydantic import ValidationError

EXPECTED_MAIN_CHECKPOINTS = (
    "problem",
    "area_model",
    "split_linear_term",
    "rearrange_halves",
    "missing_corner",
    "balance_and_complete",
    "factor_square",
    "solve_roots",
)


def _state(
    last_checkpoint: CompletingSquareMainCheckpoint | str | None = None,
    *,
    corner_clarified: bool = False,
) -> dict[str, object]:
    return {
        "kind": "completing_square",
        "id": "square-lesson",
        "lastMainCheckpoint": last_checkpoint,
        "cornerClarified": corner_clarified,
    }


def test_main_checkpoint_order_and_full_identity_are_closed_and_exact() -> None:
    assert tuple(checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER) == (
        EXPECTED_MAIN_CHECKPOINTS
    )
    assert tuple(checkpoint.value for checkpoint in CompletingSquareMainCheckpoint) == (
        EXPECTED_MAIN_CHECKPOINTS
    )
    assert tuple(checkpoint.value for checkpoint in CompletingSquareCheckpointId) == (
        *EXPECTED_MAIN_CHECKPOINTS,
        "corner_detail",
    )

    main_identities = tuple(
        CompletingSquareCheckpointId(checkpoint.value)
        for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER
    )
    assert main_identities == tuple(CompletingSquareCheckpointId)[:-1]
    assert CompletingSquareCheckpointId.CORNER_DETAIL not in main_identities


@pytest.mark.parametrize(
    ("stage", "prefix_length"),
    [
        (CompletingSquareStage.SETUP, 2),
        (CompletingSquareStage.SPLIT, 4),
        (CompletingSquareStage.COMPLETE, 6),
        (CompletingSquareStage.SOLVE, 8),
    ],
)
def test_each_stage_maps_to_its_exact_authoritative_prefix(
    stage: CompletingSquareStage,
    prefix_length: int,
) -> None:
    expected = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:prefix_length]

    assert checkpoints_through(stage) == expected
    assert COMPLETING_SQUARE_STAGE_PREFIXES[stage] == expected


def test_stage_prefix_mapping_is_exhaustive_nested_and_immutable() -> None:
    assert tuple(COMPLETING_SQUARE_STAGE_PREFIXES) == tuple(CompletingSquareStage)

    previous: tuple[CompletingSquareMainCheckpoint, ...] = ()
    for stage in CompletingSquareStage:
        current = checkpoints_through(stage)
        assert current[: len(previous)] == previous
        assert len(current) == len(previous) + 2
        previous = current
    assert previous == COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER

    with pytest.raises(TypeError):
        COMPLETING_SQUARE_STAGE_PREFIXES[CompletingSquareStage.SETUP] = ()  # type: ignore[index]


@pytest.mark.parametrize(
    ("checkpoint", "stage"),
    [
        (CompletingSquareMainCheckpoint.PROBLEM, CompletingSquareStage.SETUP),
        (CompletingSquareMainCheckpoint.AREA_MODEL, CompletingSquareStage.SETUP),
        (CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM, CompletingSquareStage.SPLIT),
        (CompletingSquareMainCheckpoint.REARRANGE_HALVES, CompletingSquareStage.SPLIT),
        (CompletingSquareMainCheckpoint.MISSING_CORNER, CompletingSquareStage.COMPLETE),
        (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, CompletingSquareStage.COMPLETE),
        (CompletingSquareMainCheckpoint.FACTOR_SQUARE, CompletingSquareStage.SOLVE),
        (CompletingSquareMainCheckpoint.SOLVE_ROOTS, CompletingSquareStage.SOLVE),
    ],
)
def test_each_main_checkpoint_has_one_introduction_stage(
    checkpoint: CompletingSquareMainCheckpoint,
    stage: CompletingSquareStage,
) -> None:
    assert introduction_stage_for(checkpoint) is stage


def test_nullable_frontier_helpers_cover_every_prefix_and_next_checkpoint() -> None:
    assert checkpoint_prefix(None) == ()
    assert next_main_checkpoint(None) is CompletingSquareMainCheckpoint.PROBLEM

    for index, checkpoint in enumerate(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER):
        assert checkpoint_prefix(checkpoint) == COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[: index + 1]
        expected_next = (
            None
            if index + 1 == len(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER)
            else COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[index + 1]
        )
        assert next_main_checkpoint(checkpoint) is expected_next


@pytest.mark.parametrize(
    "last_checkpoint",
    [None, *COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER],
)
def test_every_unclarified_main_frontier_is_a_valid_immutable_state(
    last_checkpoint: CompletingSquareMainCheckpoint | None,
) -> None:
    payload = _state(last_checkpoint)

    state = CompletingSquareState.model_validate(payload)

    assert state.model_dump(mode="json", by_alias=True) == payload
    assert state.last_main_checkpoint is last_checkpoint
    assert state.corner_clarified is False
    assert set(CompletingSquareState.model_fields) == {
        "kind",
        "id",
        "last_main_checkpoint",
        "corner_clarified",
    }

    with pytest.raises(ValidationError, match="frozen"):
        state.corner_clarified = True


@pytest.mark.parametrize(
    "last_checkpoint",
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[
        COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(
            CompletingSquareMainCheckpoint.MISSING_CORNER
        ) :
    ],
)
def test_clarified_state_is_valid_at_or_after_missing_corner(
    last_checkpoint: CompletingSquareMainCheckpoint,
) -> None:
    state = CompletingSquareState.model_validate(_state(last_checkpoint, corner_clarified=True))

    assert state.last_main_checkpoint is last_checkpoint
    assert state.corner_clarified is True


@pytest.mark.parametrize(
    "last_checkpoint",
    [
        None,
        *COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[
            : COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(
                CompletingSquareMainCheckpoint.MISSING_CORNER
            )
        ],
    ],
)
def test_clarification_before_missing_corner_is_not_a_valid_state(
    last_checkpoint: CompletingSquareMainCheckpoint | None,
) -> None:
    with pytest.raises(ValidationError, match="at or after missing_corner"):
        CompletingSquareState.model_validate(_state(last_checkpoint, corner_clarified=True))


def test_state_validity_does_not_encode_service_only_transition_history() -> None:
    solved_without_detour = CompletingSquareState.model_validate(
        _state(CompletingSquareMainCheckpoint.SOLVE_ROOTS)
    )
    solved_after_detour = CompletingSquareState.model_validate(
        _state(CompletingSquareMainCheckpoint.SOLVE_ROOTS, corner_clarified=True)
    )

    assert solved_without_detour.corner_clarified is False
    assert solved_after_detour.corner_clarified is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "pythagorean_area_identity", "literal_error"),
        ("id", "c" * 33, "at most 32 characters"),
        ("lastMainCheckpoint", "corner_detail", "enum"),
        ("lastMainCheckpoint", "unknown", "enum"),
        ("cornerClarified", "true", "bool_type"),
    ],
)
def test_state_rejects_wrong_component_or_noncanonical_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _state()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        CompletingSquareState.model_validate(payload)


def test_state_rejects_extra_or_missing_exact_keys() -> None:
    extra = _state()
    extra["revision"] = 1
    with pytest.raises(ValidationError, match="Extra inputs"):
        CompletingSquareState.model_validate(extra)

    missing = _state()
    del missing["id"]
    with pytest.raises(ValidationError, match="Field required"):
        CompletingSquareState.model_validate(missing)
