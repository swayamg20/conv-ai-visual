"""Thin LiveKit Server API adapter for the Voice V2 control plane."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from datetime import timedelta
from types import ModuleType

from murmur.core.config import config
from murmur.voice.bootstrap import (
    VOICE_V2_EVENT_TOPIC,
    CreateDispatchSpec,
    CreateRoomSpec,
    DispatchRecord,
    ParticipantTokenSpec,
    RoomRecord,
    UnavailableVoiceBootstrapService,
    VoiceBootstrapper,
    VoiceBootstrapService,
    VoiceBootstrapSettings,
    VoiceBootstrapUnavailable,
    normalize_server_url,
)


@dataclass(frozen=True)
class LiveKitCredentials:
    server_url: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


class LiveKitControlPlane:
    """Translate SDK-neutral bootstrap operations to ``livekit-api`` 1.2."""

    def __init__(
        self,
        credentials: LiveKitCredentials,
        *,
        sdk: ModuleType | None = None,
    ) -> None:
        self._credentials = credentials
        # Keeping this import here makes Voice V2 a genuine optional dependency.
        self._sdk = sdk or importlib.import_module("livekit.api")
        self._api = None
        self._api_lock = asyncio.Lock()
        self._closed = False

    async def _client(self):
        if self._closed:
            raise RuntimeError("LiveKit control-plane client is closed")
        if self._api is not None:
            return self._api
        async with self._api_lock:
            if self._closed:
                raise RuntimeError("LiveKit control-plane client is closed")
            if self._api is None:
                self._api = self._sdk.LiveKitAPI(
                    url=self._credentials.server_url,
                    api_key=self._credentials.api_key,
                    api_secret=self._credentials.api_secret,
                )
        return self._api

    async def aclose(self) -> None:
        """Close the one process-owned SDK client exactly once."""
        async with self._api_lock:
            self._closed = True
            api, self._api = self._api, None
        if api is not None:
            await api.aclose()

    async def get_room(self, room_name: str) -> RoomRecord | None:
        client = await self._client()
        response = await client.room.list_rooms(self._sdk.ListRoomsRequest(names=[room_name]))
        rooms = list(response.rooms)
        if not rooms:
            return None
        if len(rooms) != 1:
            raise RuntimeError("LiveKit returned multiple rooms for one exact room name")
        room = rooms[0]
        return RoomRecord(
            name=room.name,
            metadata=room.metadata,
            num_participants=room.num_participants,
        )

    async def create_room(self, spec: CreateRoomSpec) -> RoomRecord:
        request = self._sdk.CreateRoomRequest(
            name=spec.name,
            metadata=spec.metadata,
            empty_timeout=spec.empty_timeout_seconds,
            departure_timeout=spec.departure_timeout_seconds,
            max_participants=spec.max_participants,
        )
        client = await self._client()
        room = await client.room.create_room(request)
        return RoomRecord(
            name=room.name,
            metadata=room.metadata,
            num_participants=room.num_participants,
        )

    async def list_dispatches(self, room_name: str) -> list[DispatchRecord]:
        client = await self._client()
        dispatches = await client.agent_dispatch.list_dispatch(room_name)
        return [self._dispatch_record(dispatch) for dispatch in dispatches]

    async def create_dispatch(self, spec: CreateDispatchSpec) -> DispatchRecord:
        request = self._sdk.CreateAgentDispatchRequest(
            room=spec.room_name,
            agent_name=spec.agent_name,
            metadata=spec.metadata,
            restart_policy=self._sdk.JobRestartPolicy.JRP_NEVER,
        )
        client = await self._client()
        dispatch = await client.agent_dispatch.create_dispatch(request)
        return self._dispatch_record(dispatch)

    async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
        client = await self._client()
        await client.agent_dispatch.delete_dispatch(dispatch_id, room_name)

    async def delete_room(self, room_name: str) -> None:
        client = await self._client()
        await client.room.delete_room(self._sdk.DeleteRoomRequest(room=room_name))

    def issue_participant_token(self, spec: ParticipantTokenSpec) -> str:
        grants = spec.grants
        video_grants = self._sdk.VideoGrants(
            room=grants.room_name,
            room_join=grants.room_join,
            can_publish=grants.can_publish,
            can_subscribe=grants.can_subscribe,
            can_publish_data=grants.can_publish_data,
            can_update_own_metadata=grants.can_update_own_metadata,
            can_publish_sources=list(grants.can_publish_sources),
            room_create=grants.room_create,
            room_list=grants.room_list,
            room_admin=grants.room_admin,
            room_record=grants.room_record,
            ingress_admin=grants.ingress_admin,
            agent=grants.agent,
            can_manage_agent_session=grants.can_manage_agent_session,
        )
        ttl = spec.expires_at - spec.issued_at
        if ttl <= timedelta(0):
            raise ValueError("participant token expiry must be after issuance")
        return (
            self._sdk.AccessToken(
                api_key=self._credentials.api_key,
                api_secret=self._credentials.api_secret,
            )
            .with_identity(spec.identity)
            .with_name(spec.name)
            .with_metadata(spec.metadata)
            .with_ttl(ttl)
            .with_grants(video_grants)
            .to_jwt()
        )

    @staticmethod
    def _dispatch_record(dispatch) -> DispatchRecord:
        return DispatchRecord(
            id=dispatch.id,
            room_name=dispatch.room,
            agent_name=dispatch.agent_name,
            metadata=dispatch.metadata,
            deleted_at=dispatch.state.deleted_at,
        )


def create_default_voice_bootstrap_service() -> VoiceBootstrapper:
    """Build the configured bootstrap service without failing legacy startup."""
    runtime = str(getattr(config, "VOICE_RUNTIME", "legacy")).strip().lower()
    if runtime != "livekit_v2":
        return UnavailableVoiceBootstrapService("Voice V2 is disabled by VOICE_RUNTIME")

    values = {
        "LIVEKIT_URL": str(getattr(config, "LIVEKIT_URL", "") or "").strip(),
        "LIVEKIT_API_KEY": str(getattr(config, "LIVEKIT_API_KEY", "") or "").strip(),
        "LIVEKIT_API_SECRET": str(getattr(config, "LIVEKIT_API_SECRET", "") or "").strip(),
        "VOICE_V2_SIGNING_SECRET": str(
            getattr(config, "VOICE_V2_SIGNING_SECRET", "") or ""
        ).strip(),
        "MURMUR_ENVIRONMENT": str(getattr(config, "MURMUR_ENVIRONMENT", "") or "").strip(),
        "VOICE_V2_PROFILE_ID": str(getattr(config, "VOICE_V2_PROFILE_ID", "") or "").strip(),
        "VOICE_V2_WORKER_NAME": str(getattr(config, "VOICE_V2_WORKER_NAME", "") or "").strip(),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        return UnavailableVoiceBootstrapService(
            "Voice V2 configuration is incomplete: " + ", ".join(missing)
        )

    try:
        settings = VoiceBootstrapSettings(
            server_url=normalize_server_url(values["LIVEKIT_URL"]),
            environment=values["MURMUR_ENVIRONMENT"],
            profile_id=values["VOICE_V2_PROFILE_ID"],
            worker_name=values["VOICE_V2_WORKER_NAME"],
            event_topic=VOICE_V2_EVENT_TOPIC,
            signing_secret=values["VOICE_V2_SIGNING_SECRET"],
            token_ttl_seconds=int(config.VOICE_V2_TOKEN_TTL_SECONDS),
            job_metadata_ttl_seconds=int(config.VOICE_V2_JOB_METADATA_TTL_SECONDS),
            room_empty_timeout_seconds=int(config.VOICE_V2_ROOM_EMPTY_TIMEOUT_SECONDS),
            room_departure_timeout_seconds=int(config.VOICE_V2_ROOM_DEPARTURE_TIMEOUT_SECONDS),
            control_plane_timeout_seconds=float(config.VOICE_V2_CONTROL_PLANE_TIMEOUT_SECONDS),
            repository_timeout_seconds=float(config.VOICE_V2_REPOSITORY_TIMEOUT_SECONDS),
            max_concurrent_bootstraps=int(config.VOICE_V2_MAX_CONCURRENT_BOOTSTRAPS),
            max_active_calls=int(config.VOICE_V2_MAX_ACTIVE_CALLS),
            max_call_assignments=int(config.VOICE_V2_MAX_CALL_ASSIGNMENTS),
        )
        control_plane = LiveKitControlPlane(
            LiveKitCredentials(
                server_url=settings.server_url,
                api_key=values["LIVEKIT_API_KEY"],
                api_secret=values["LIVEKIT_API_SECRET"],
            )
        )
    except (ImportError, TypeError, ValueError, VoiceBootstrapUnavailable) as exc:
        return UnavailableVoiceBootstrapService(f"Voice V2 configuration is invalid: {exc}")
    return VoiceBootstrapService(control_plane, settings)
