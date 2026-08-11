import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

import main
from funcs.models import (
    AgentRepo,
    LLMCallLogRepo,
    SessionRepo,
    UserRepo,
    VoicePipelineLogRepo,
)


class ApiSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        suffix = uuid4().hex
        self.owner = {
            "id": f"owner-{suffix}",
            "email": f"owner-{suffix}@example.com",
            "name": "Owner",
        }
        self.other_user = {
            "id": f"other-{suffix}",
            "email": f"other-{suffix}@example.com",
            "name": "Other user",
        }
        UserRepo.get_or_create(**self._user_repo_args(self.owner))
        UserRepo.get_or_create(**self._user_repo_args(self.other_user))

        self.owner_agent = AgentRepo.create(
            user_id=self.owner["id"],
            name="Owner agent",
            system_prompt="Teach the owner.",
        )
        self.other_agent = AgentRepo.create(
            user_id=self.other_user["id"],
            name="Other agent",
            system_prompt="Teach the other user.",
        )
        self.owner_session = SessionRepo.create(self.owner["id"], self.owner_agent.id)
        self.other_session = SessionRepo.create(self.other_user["id"], self.other_agent.id)

        LLMCallLogRepo.save(
            session_id=self.owner_session.id,
            user_id=self.owner["id"],
            user_message="owner-only prompt",
            llm_provider="test",
            llm_model="test-model",
            latency_total_ms=100,
        )
        LLMCallLogRepo.save(
            session_id=self.other_session.id,
            user_id=self.other_user["id"],
            user_message="other-user prompt",
            llm_provider="test",
            llm_model="test-model",
            latency_total_ms=900,
        )
        VoicePipelineLogRepo.save(
            session_id=self.owner_session.id,
            user_id=self.owner["id"],
            mode="voice",
            user_message="owner-only voice prompt",
            latency_total_ms=150,
        )
        VoicePipelineLogRepo.save(
            session_id=self.other_session.id,
            user_id=self.other_user["id"],
            mode="voice",
            user_message="other-user voice prompt",
            latency_total_ms=950,
        )

        self.current_user: dict | None = None
        self.auth_patcher = patch.object(
            main,
            "get_current_user",
            side_effect=lambda _request: self.current_user,
        )
        self.auth_patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        self.auth_patcher.stop()
        main.chat_sessions.clear()
        main.chat_session_activity.clear()

    @staticmethod
    def _user_repo_args(user: dict) -> dict:
        return {"uid": user["id"], "email": user["email"], "name": user["name"]}

    def test_sensitive_routes_require_authentication(self) -> None:
        requests = [
            ("get", "/api/logs", None),
            ("get", "/api/logs/stats", None),
            ("get", "/api/voice-logs", None),
            ("get", "/api/voice-logs/stats", None),
            ("delete", f"/chat/{self.owner_session.id}", None),
            (
                "post",
                f"/chat/{self.owner_session.id}/canvas-mode",
                {"enabled": True},
            ),
        ]

        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)

    def test_observability_is_scoped_to_the_authenticated_user(self) -> None:
        self.current_user = self.owner

        llm_logs = self.client.get("/api/logs")
        self.assertEqual(llm_logs.status_code, 200)
        self.assertEqual(
            [entry["user_message"] for entry in llm_logs.json()["logs"]],
            ["owner-only prompt"],
        )

        llm_stats = self.client.get("/api/logs/stats")
        self.assertEqual(llm_stats.status_code, 200)
        self.assertEqual(llm_stats.json()["total_calls"], 1)
        self.assertEqual(llm_stats.json()["avg_latency_ms"], 100)

        voice_logs = self.client.get("/api/voice-logs")
        self.assertEqual(voice_logs.status_code, 200)
        self.assertEqual(
            [entry["user_message"] for entry in voice_logs.json()["logs"]],
            ["owner-only voice prompt"],
        )

        voice_stats = self.client.get("/api/voice-logs/stats")
        self.assertEqual(voice_stats.status_code, 200)
        self.assertEqual(voice_stats.json()["total_turns"], 1)
        self.assertEqual(voice_stats.json()["avg_total_ms"], 150)

    def test_chat_controls_reject_cross_user_access(self) -> None:
        self.current_user = self.owner
        foreign_pipeline = SimpleNamespace(
            user_id=self.other_user["id"],
            set_canvas_mode=Mock(),
        )
        transient_session_id = f"transient-{uuid4().hex}"
        main.chat_sessions[transient_session_id] = foreign_pipeline

        clear_response = self.client.delete(f"/chat/{transient_session_id}")
        canvas_response = self.client.post(
            f"/chat/{transient_session_id}/canvas-mode",
            json={"enabled": True},
        )

        self.assertEqual(clear_response.status_code, 403)
        self.assertEqual(canvas_response.status_code, 403)
        self.assertIn(transient_session_id, main.chat_sessions)
        foreign_pipeline.set_canvas_mode.assert_not_called()

    def test_owner_can_control_an_active_chat_session(self) -> None:
        self.current_user = self.owner
        pipeline = SimpleNamespace(
            user_id=self.owner["id"],
            canvas_mode=False,
            set_canvas_mode=Mock(),
            get_tools_schema=Mock(return_value=[]),
        )
        pipeline.set_canvas_mode.side_effect = lambda enabled, _prompt: setattr(
            pipeline, "canvas_mode", enabled
        )
        transient_session_id = f"transient-{uuid4().hex}"
        main.chat_sessions[transient_session_id] = pipeline

        canvas_response = self.client.post(
            f"/chat/{transient_session_id}/canvas-mode",
            json={"enabled": True},
        )
        self.assertEqual(canvas_response.status_code, 200)
        self.assertTrue(canvas_response.json()["canvas_mode"])

        with patch.object(main, "_finalize_chat_session", new=AsyncMock()) as finalize:
            clear_response = self.client.delete(f"/chat/{transient_session_id}")

        self.assertEqual(clear_response.status_code, 200)
        finalize.assert_awaited_once()

    def test_session_creation_rejects_another_users_agent(self) -> None:
        self.current_user = self.owner

        response = self.client.post(
            "/api/sessions",
            json={"agent_id": self.other_agent.id},
        )

        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
