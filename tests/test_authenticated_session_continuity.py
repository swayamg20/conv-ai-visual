import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.persistence import get_session
from murmur.persistence.clock import utc_now
from murmur.persistence.repositories.sessions import ConversationMessageRepo, SessionRepo
from sqlalchemy import text

import main

TEST_USER = {
    "id": "continuity-test-user",
    "email": "continuity-test@example.com",
    "name": "Continuity Test",
}


def _parse_sse_text(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class _FakeLLMClient:
    async def complete(self, messages, temperature=None, max_tokens=None):
        system_prompt = messages[0]["content"] if messages else ""
        if "Summarize this tutoring session" in system_prompt:
            return (
                "The student practiced free-body diagrams on an incline and still mixed up "
                "the resolved weight components. Revisit force decomposition first next time."
            )
        if "extract the durable tutoring signals" in system_prompt:
            return json.dumps(
                [
                    {
                        "topic": "free-body diagrams",
                        "chapter": "Newtonian mechanics",
                        "signal_type": "struggled",
                        "details": "Confused weight components on an incline.",
                    }
                ]
            )
        return "ok"


async def _fake_chat_with_tools_stream(self, user_message, temperature=None, max_tokens=None):
    context = await self.get_context(user_message, include_canvas=False)
    system_prompt = context[0]["content"] if context else ""
    if "Previous session summaries:" in system_prompt:
        response = (
            "I remember you struggled with free-body diagrams on an incline. "
            "Let's revisit the weight components before solving."
        )
    else:
        response = f"Let's work through {user_message} step by step."

    if self.memory:
        self.memory.process_for_memory(user_message, response, save_semantic=False)

    yield response


class AuthenticatedSessionContinuityTest(unittest.TestCase):
    def setUp(self) -> None:
        main.runtime.chat_sessions.clear()
        with get_session() as session:
            session.exec(
                text(
                    """
                    INSERT OR IGNORE INTO users (id, email, password_hash, name, created_at, is_active)
                    VALUES (:id, :email, :password_hash, :name, :created_at, :is_active)
                    """
                ),
                params={
                    "id": TEST_USER["id"],
                    "email": TEST_USER["email"],
                    "password_hash": "test-password-hash",
                    "name": TEST_USER["name"],
                    "created_at": utc_now().isoformat(sep=" "),
                    "is_active": 1,
                },
            )
            session.commit()

        self._patchers = [
            patch(
                "murmur.llm.pipeline.create_llm_client",
                side_effect=lambda *args, **kwargs: _FakeLLMClient(),
            ),
            patch(
                "murmur.api.routers.sessions.create_llm_client",
                side_effect=lambda *args, **kwargs: _FakeLLMClient(),
            ),
            patch("funcs.memory.config.MEM0_API_KEY", ""),
            patch("murmur.llm.pipeline.config.LLM_ASYNC_CONTEXT", False),
            patch(
                "murmur.llm.pipeline.LLMPipeline.chat_with_tools_stream",
                new=_fake_chat_with_tools_stream,
            ),
        ]
        for patcher in self._patchers:
            patcher.start()

        main.app.dependency_overrides[get_authenticated_user] = lambda: TEST_USER
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer continuity-test-token"}

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self._patchers):
            patcher.stop()
        main.app.dependency_overrides.pop(get_authenticated_user, None)

        main.runtime.chat_sessions.clear()

    def test_agent_session_summary_is_reused_in_next_session(self) -> None:
        agent_response = self.client.post(
            "/api/agents",
            headers=self.headers,
            json={
                "name": "Incline Tutor",
                "description": "Physics tutor for mechanics",
                "persona": {
                    "role": "student",
                    "subject": "Physics",
                    "level": "Class 11",
                    "goals": "Master Newton's laws",
                    "learning_style": "visual",
                },
                "capabilities": ["canvas"],
                "icon": "🧪",
            },
        )
        self.assertEqual(agent_response.status_code, 201)
        agent_id = agent_response.json()["id"]

        session_one = self.client.post(
            "/api/sessions",
            headers=self.headers,
            json={"agent_id": agent_id},
        )
        self.assertEqual(session_one.status_code, 200)
        session_one_id = session_one.json()["id"]

        first_chat = self.client.post(
            "/chat",
            headers=self.headers,
            json={
                "message": "I am confused about free-body diagrams.",
                "session_id": session_one_id,
                "agent_id": agent_id,
                "canvas_mode": True,
            },
        )
        self.assertEqual(first_chat.status_code, 200)

        second_chat = self.client.post(
            "/chat",
            headers=self.headers,
            json={
                "message": "Can you help me resolve forces on an incline?",
                "session_id": session_one_id,
                "agent_id": agent_id,
                "canvas_mode": True,
            },
        )
        self.assertEqual(second_chat.status_code, 200)

        detail_before_end = self.client.get(f"/api/sessions/{session_one_id}", headers=self.headers)
        self.assertEqual(detail_before_end.status_code, 200)
        self.assertGreaterEqual(detail_before_end.json()["message_count"], 4)

        end_response = self.client.post(f"/api/sessions/{session_one_id}/end", headers=self.headers)
        self.assertEqual(end_response.status_code, 200)
        summary = end_response.json()["summary"]
        self.assertIsInstance(summary, str)
        self.assertIn("free-body diagrams", summary)

        stored_session = SessionRepo.get_by_id(session_one_id)
        self.assertIsNotNone(stored_session)
        self.assertIn("free-body diagrams", stored_session.summary or "")

        stored_messages = ConversationMessageRepo.get_recent(session_one_id, limit=10)
        self.assertGreaterEqual(len(stored_messages), 4)

        session_two = self.client.post(
            "/api/sessions",
            headers=self.headers,
            json={"agent_id": agent_id},
        )
        self.assertEqual(session_two.status_code, 200)
        session_two_id = session_two.json()["id"]

        resumed_chat = self.client.post(
            "/chat",
            headers=self.headers,
            json={
                "message": "What should we revisit from last time?",
                "session_id": session_two_id,
                "agent_id": agent_id,
                "canvas_mode": True,
            },
        )
        self.assertEqual(resumed_chat.status_code, 200)

        resumed_events = _parse_sse_text(resumed_chat.text)
        resumed_chunks = [event["text"] for event in resumed_events if event.get("type") == "chunk"]
        resumed_text = "".join(resumed_chunks)
        self.assertIn("free-body diagrams", resumed_text)
        self.assertIn("incline", resumed_text)


if __name__ == "__main__":
    unittest.main()
