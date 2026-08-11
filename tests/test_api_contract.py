"""Stable route-surface checks for the modular FastAPI composition."""

import main


def test_product_route_contract_is_registered_once() -> None:
    expected_methods = {
        "/api/agents": {"get", "post"},
        "/api/agents/{agent_id}": {"delete", "get", "put"},
        "/api/agents/{agent_id}/mastery": {"get"},
        "/api/agents/{agent_id}/resources": {"get", "post"},
        "/api/agents/{agent_id}/resources/{resource_id}": {"delete"},
        "/api/auth/me": {"get"},
        "/api/logs": {"get"},
        "/api/logs/stats": {"get"},
        "/api/sessions": {"get", "post"},
        "/api/sessions/{session_id}": {"get"},
        "/api/sessions/{session_id}/end": {"post"},
        "/api/voice-logs": {"get"},
        "/api/voice-logs/stats": {"get"},
        "/chat": {"post"},
        "/chat/{session_id}": {"delete"},
        "/chat/{session_id}/canvas-mode": {"post"},
        "/offer": {"post"},
    }

    actual_methods = {path: set(methods) for path, methods in main.app.openapi()["paths"].items()}
    assert actual_methods == expected_methods
