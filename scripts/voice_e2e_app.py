"""Loopback-only FastAPI composition for the deterministic Voice V2 RTC test.

This module is an executable test boundary, not an application backdoor.  It
refuses to import unless the stack runner supplies the exact E2E guard and a
file-backed SQLite database.  The only overridden dependency is authentication;
the production Voice V2 router, bootstrap service, repositories, token grants,
and LiveKit control-plane adapter remain in the request path.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from sqlmodel import Session, SQLModel


def _require_guarded_environment() -> None:
    if os.getenv("MURMUR_E2E_MODE") != "1":
        raise RuntimeError("voice E2E app requires MURMUR_E2E_MODE=1")
    if os.getenv("MURMUR_ENVIRONMENT") != "test":
        raise RuntimeError("voice E2E app requires MURMUR_ENVIRONMENT=test")
    if os.getenv("VOICE_RUNTIME") != "livekit_v2":
        raise RuntimeError("voice E2E app requires VOICE_RUNTIME=livekit_v2")
    if os.getenv("VOICE_V2_PROFILE_ID") != "fake-rtc-v1":
        raise RuntimeError("voice E2E app requires the fake-rtc-v1 profile")

    livekit_url = urlsplit(os.getenv("LIVEKIT_URL", ""))
    if livekit_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("voice E2E app requires a loopback LiveKit URL")

    database_url = os.getenv("MURMUR_DATABASE_URL", "")
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        raise RuntimeError("voice E2E app requires a file-backed SQLite database")
    database_path = Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()
    if "voice-e2e" not in database_path.parts or "var" not in database_path.parts:
        raise RuntimeError("voice E2E database must live under var/voice-e2e")


_require_guarded_environment()

from murmur.api.application import create_application  # noqa: E402
from murmur.api.dependencies import CurrentUser, get_authenticated_user  # noqa: E402
from murmur.persistence.database import engine  # noqa: E402
from murmur.persistence.models import AgentModel, SessionModel, UserModel  # noqa: E402

E2E_USER_ID = "voice-e2e-user"
E2E_USER_EMAIL = "voice-e2e@localhost.invalid"
E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76"
E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"


def _seed_owned_scope() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if session.get(UserModel, E2E_USER_ID) is None:
            session.add(
                UserModel(
                    id=E2E_USER_ID,
                    email=E2E_USER_EMAIL,
                    name="Voice E2E",
                )
            )
        if session.get(AgentModel, E2E_AGENT_ID) is None:
            session.add(
                AgentModel(
                    id=E2E_AGENT_ID,
                    user_id=E2E_USER_ID,
                    name="Deterministic RTC agent",
                    description="Loopback-only media validation agent",
                    system_prompt="Respond using the deterministic Voice V2 RTC profile.",
                    capabilities_json="[]",
                )
            )
        if session.get(SessionModel, E2E_SESSION_ID) is None:
            session.add(
                SessionModel(
                    id=E2E_SESSION_ID,
                    user_id=E2E_USER_ID,
                    agent_id=E2E_AGENT_ID,
                    title="Voice V2 RTC validation",
                )
            )
        session.commit()


def _e2e_authenticated_user() -> CurrentUser:
    return {
        "id": E2E_USER_ID,
        "email": E2E_USER_EMAIL,
        "name": "Voice E2E",
    }


_seed_owned_scope()
app = create_application()
app.dependency_overrides[get_authenticated_user] = _e2e_authenticated_user


@app.get("/_e2e/health", include_in_schema=False)
async def e2e_health() -> dict[str, object]:
    return {
        "ok": True,
        "agent_id": E2E_AGENT_ID,
        "session_id": E2E_SESSION_ID,
    }
