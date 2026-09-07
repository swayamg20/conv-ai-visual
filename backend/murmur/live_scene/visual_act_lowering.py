"""Pure lowering from a resolved visual act to the verified compiler input."""

from __future__ import annotations

from types import MappingProxyType

from murmur.live_scene.choreography_contracts import RoutedChoreographyBeatV2
from murmur.live_scene.contracts import MAX_SAFE_SEQUENCE
from murmur.live_scene.semantic_contracts import (
    PythagoreanAreaIdentityDirective,
    PythagoreanStage,
    TeachingAct,
    TeachingBeatDraft,
)
from murmur.live_scene.visual_act_router import ResolvedChoreographyAct, ResolvedVisualAct

_STAGE_BEAT_CONTENT = MappingProxyType(
    {
        PythagoreanStage.TRIANGLE: (
            TeachingAct.INTRODUCE,
            "Start with the right triangle.",
        ),
        PythagoreanStage.AREAS: (
            TeachingAct.DERIVE,
            "Compare the squares built on the triangle's three sides.",
        ),
        PythagoreanStage.IDENTITY: (
            TeachingAct.CONNECT,
            "Connect the three square areas into the Pythagorean relationship.",
        ),
        PythagoreanStage.PROOF: (
            TeachingAct.DERIVE,
            "Project the altitude through the hypotenuse square to prove its two regions have areas a² and b².",
        ),
    }
)


def _server_beat_id(generation: int) -> str:
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise TypeError("generation must be an integer")
    if not 1 <= generation <= MAX_SAFE_SEQUENCE:
        raise ValueError(f"generation must be between 1 and {MAX_SAFE_SEQUENCE}")
    return f"route-{generation:x}"


def lower_resolved_visual_act(
    resolved: ResolvedVisualAct,
    *,
    generation: int,
) -> TeachingBeatDraft:
    """Create the complete server-authored input expected by the existing compiler."""

    if not isinstance(resolved, ResolvedVisualAct):
        raise TypeError("resolved must be a ResolvedVisualAct")

    act, narration = _STAGE_BEAT_CONTENT[resolved.target_stage]
    return TeachingBeatDraft(
        beat_id=_server_beat_id(generation),
        narration=narration,
        act=act,
        directive=PythagoreanAreaIdentityDirective(
            kind=resolved.component_kind,
            id=resolved.component_id,
            reveal_through=resolved.target_stage,
        ),
    )


def lower_resolved_choreography_act(
    resolved: ResolvedChoreographyAct,
    *,
    generation: int,
) -> RoutedChoreographyBeatV2:
    """Create the presentation-free V2 input expected by the choreography compiler."""

    if not isinstance(resolved, ResolvedChoreographyAct):
        raise TypeError("resolved must be a ResolvedChoreographyAct")
    return RoutedChoreographyBeatV2(
        beat_id=_server_beat_id(generation),
        component_kind=resolved.component_kind,
        component_id=resolved.component_id,
        route=resolved.route,
    )


__all__ = ["lower_resolved_choreography_act", "lower_resolved_visual_act"]
