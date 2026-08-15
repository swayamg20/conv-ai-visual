"""Process-isolated guard and HTTP tests for the dedicated Pipecat E2E app."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    COTURN_FIXTURE_PATH,
    COTURN_TURNS_URL,
    CoturnContractPaths,
    render_coturn_configuration,
)
from scripts.voice_pipecat_e2e_stack import (  # noqa: E402
    StackError,
    StackPaths,
    _require_port_available,
    build_environment,
)

STATIC_TURN_SECRET = "0123456789abcdef" * 4
TEST_CERTIFICATE_PEM = """\
-----BEGIN CERTIFICATE-----
MIIC9DCCAdygAwIBAgIJAN0Y0Nf5BhrTMA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNV
BAMMCTEyNy4wLjAuMTAeFw0yNjA4MTUxODQzMzRaFw0zNjA4MTIxODQzMzRaMBQx
EjAQBgNVBAMMCTEyNy4wLjAuMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC
ggEBALm89xqJ+knCWn4TF0//M9iECFOV4Y8h7n7ByTv1xfru1pPvLCuw2a0smxu/
Nw+RkpenNkAyXtlENtd4X+1EW5TGR4ekL78Ce5W6PtYZ0fVHpcS2idsIeILkIhYh
3CQylQyqQxziumkEfMRB5h5v8Zc/o0hUYSTIi92oYPR8uZ2tfDjD1fY62ox/DVn7
2GKu22JCCW0OI1ur5CXaMyg7cYeS2eq6iuK2z6JsXSYEr2J3T4sIr51njmFt7+OW
TKfaby9oNVnc9D6aFdiQ1Q4LxZVOW9JyFk2GJYNultjAC7KPBPDz+mowauSRO2As
+6A2qUhdwzTI0j6f9JqEJedHvKECAwEAAaNJMEcwDwYDVR0RBAgwBocEfwAAATAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwICpDATBgNVHSUEDDAKBggrBgEF
BQcDATANBgkqhkiG9w0BAQsFAAOCAQEACi0bImQ3EJChUzmlyxdC35aN/HyGJo8a
sf46nbyz4ILEP0XJS3aoGjCbwoDR5vh6SCADuhGDkbMJ4cgMchm0XoVbrij9PFpZ
iCGf3zUmW+zfnzjvPm380IUPBgbpWX/o02gPHKyw095NhS7R0AUtBkSTeiJqcOdS
dxfiPXDIzxtRTa6yDOfrJZYWSj10IqBc0c5XTaR9yQzxaJ4i/PWS7pN1xEGNPoDr
AK1RHz0iqmKuoFCTbp/UyRWgH9dhRzXmPKkZVQ0IwPMfgaKyQQKDcxfJ6841kKtD
f1dZKbTifuE8OGfJN/9l0jKcX+J07pzQm/x5TvrxfzUc1il21KFmzQ==
-----END CERTIFICATE-----
"""


def _paths(label: str) -> StackPaths:
    suffix = uuid.uuid4().hex[:12]
    run_dir = PROJECT_ROOT / "var" / "voice-pipecat-e2e" / f"pytest-{label}-{suffix}"
    return StackPaths(
        run_id=f"pytest-{label}-{suffix}",
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=PROJECT_ROOT / "var" / "evals" / f"voice-pipecat-pytest-{suffix}.jsonl",
        server_log=run_dir / "pipecat-asgi.log",
        proof=run_dir / "backend-checkpoint.json",
    )


def _run_isolated(code: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _require_relay_safe_process_output(
    result: subprocess.CompletedProcess[str],
    *,
    forbidden: tuple[str, ...],
) -> None:
    if any(value and (value in result.stdout or value in result.stderr) for value in forbidden):
        pytest.fail("isolated process output contained relay material", pytrace=False)


def _relay_environment(paths: StackPaths) -> tuple[dict[str, str], CoturnContractPaths]:
    try:
        paths.run_dir.mkdir(parents=True, mode=0o700)
        paths.run_dir.chmod(0o700)
        coturn = CoturnContractPaths.for_run_dir(paths.run_id, paths.run_dir)
        coturn.coturn_dir.mkdir(mode=0o700)
        coturn.coturn_dir.chmod(0o700)
        coturn.config.write_text(
            render_coturn_configuration(
                COTURN_FIXTURE_PATH.read_text(encoding="utf-8"),
                STATIC_TURN_SECRET,
            ),
            encoding="utf-8",
        )
        coturn.cert.write_text(TEST_CERTIFICATE_PEM, encoding="ascii")
        coturn.config.chmod(0o400)
        coturn.cert.chmod(0o400)
        environment = build_environment(
            paths,
            network="relay-tls",
            turn_configuration_file=coturn.config,
            turn_tls_ca_file=coturn.cert,
        )
    except BaseException:
        _strict_relay_cleanup(paths)
        raise
    return environment, coturn


def _cleanup(paths: StackPaths) -> None:
    shutil.rmtree(paths.run_dir, ignore_errors=True)
    paths.evidence.unlink(missing_ok=True)


def _strict_relay_cleanup(paths: StackPaths) -> None:
    if paths.run_dir.exists():
        for directory in (paths.run_dir / "coturn", paths.run_dir):
            if directory.exists():
                directory.chmod(0o700)
        shutil.rmtree(paths.run_dir)
    paths.evidence.unlink(missing_ok=True)
    assert not paths.run_dir.exists()
    assert not paths.evidence.exists()


def _guard_probe(environment: dict[str, str]) -> dict[str, object]:
    result = _run_isolated(
        r"""
import json
import sys

try:
    import scripts.voice_pipecat_e2e_app  # noqa: F401
except Exception as exc:
    payload = {
        "failed": True,
        "error": str(exc),
        "murmur_imported": any(
            name == "murmur" or name.startswith("murmur.") for name in sys.modules
        ),
        "ssl_imported": "ssl" in sys.modules,
    }
else:
    payload = {"failed": False, "error": None, "murmur_imported": True, "ssl_imported": True}
print("PIPECAT_GUARD_RESULT=" + json.dumps(payload, sort_keys=True))
""",
        environment,
    )
    _require_relay_safe_process_output(
        result,
        forbidden=(STATIC_TURN_SECRET, COTURN_TURNS_URL),
    )
    if result.returncode != 0:
        pytest.fail("isolated guard probe failed", pytrace=False)
    marker = next(
        (
            line.removeprefix("PIPECAT_GUARD_RESULT=")
            for line in result.stdout.splitlines()
            if line.startswith("PIPECAT_GUARD_RESULT=")
        ),
        None,
    )
    if marker is None:
        pytest.fail("isolated guard probe result is unavailable", pytrace=False)
    payload = json.loads(marker)
    assert isinstance(payload, dict)
    return payload


def test_port_preflight_allows_reuse_after_close_but_rejects_live_listener() -> None:
    host = "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, 0))
        port = listener.getsockname()[1]
        listener.listen()

        with pytest.raises(StackError, match="already in use"):
            _require_port_available(host, port)

    _require_port_available(host, port)


def test_runner_strips_generic_credentials_and_forces_dotenv_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths("runner-env")
    monkeypatch.setenv("UNLISTED_VENDOR_API_KEY", "must-not-enter-child")
    monkeypatch.setenv("SECOND_VENDOR_AUTH_TOKEN", "must-not-enter-child")

    environment = build_environment(paths)

    assert "UNLISTED_VENDOR_API_KEY" not in environment
    assert "SECOND_VENDOR_AUTH_TOKEN" not in environment
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"


def test_import_guard_rejects_nonexact_browser_origins_before_murmur_import() -> None:
    paths = _paths("cors")
    environment = build_environment(paths)
    environment["ALLOWED_CORS_ORIGINS"] = "https://arbitrary.example.test"
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "exact loopback browser origins" in result.stderr
    assert "murmur.voice.pipecat_composition" not in result.stderr


def test_import_guard_rejects_broad_ambient_provider_or_firebase_credentials() -> None:
    paths = _paths("credentials")
    environment = build_environment(paths)
    environment["UNLISTED_VENDOR_API_KEY"] = "must-not-enter-fake-app"
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "does not accept provider credentials" in result.stderr
    assert environment["UNLISTED_VENDOR_API_KEY"] not in result.stderr


def test_import_guard_requires_dotenv_loading_disabled_before_murmur_import() -> None:
    paths = _paths("dotenv")
    environment = build_environment(paths)
    environment.pop("PYTHON_DOTENV_DISABLED")
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "requires disabled dotenv loading" in result.stderr
    assert "murmur.voice.pipecat_composition" not in result.stderr


@pytest.mark.parametrize("network", [None, "invalid-relay-value"])
def test_network_guard_rejects_missing_or_invalid_mode_before_any_murmur_import(
    network: str | None,
) -> None:
    paths = _paths("network-guard")
    environment = build_environment(paths)
    if network is None:
        environment.pop("MURMUR_PIPECAT_E2E_NETWORK")
    else:
        environment["MURMUR_PIPECAT_E2E_NETWORK"] = network
    try:
        payload = _guard_probe(environment)
    finally:
        _cleanup(paths)

    assert payload == {
        "failed": True,
        "error": "Pipecat E2E app network mode is invalid",
        "murmur_imported": False,
        "ssl_imported": False,
    }
    assert network not in str(payload) if network else True


def test_relay_guard_rejects_invalid_private_config_before_any_murmur_import() -> None:
    paths = _paths("relay-config-guard")
    environment, coturn = _relay_environment(paths)
    coturn.config.chmod(0o600)
    coturn.config.write_text(
        coturn.config.read_text(encoding="utf-8") + "static-auth-secret=0\n",
        encoding="utf-8",
    )
    try:
        payload = _guard_probe(environment)
    finally:
        _strict_relay_cleanup(paths)

    assert payload == {
        "failed": True,
        "error": "relay-tls Pipecat E2E material is unavailable",
        "murmur_imported": False,
        "ssl_imported": False,
    }
    assert STATIC_TURN_SECRET not in str(payload)


def test_relay_guard_rejects_invalid_ca_before_any_murmur_import() -> None:
    paths = _paths("relay-ca-guard")
    environment, coturn = _relay_environment(paths)
    coturn.cert.chmod(0o600)
    coturn.cert.write_text("not a certificate\n", encoding="ascii")
    try:
        payload = _guard_probe(environment)
    finally:
        _strict_relay_cleanup(paths)

    assert payload == {
        "failed": True,
        "error": "relay-tls Pipecat E2E material is unavailable",
        "murmur_imported": False,
        "ssl_imported": False,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TURN_PASSWORD", "ambient-turn-password"),
        ("COTURN_SHARED_SECRET", "ambient-coturn-secret"),
        ("SSL_CERT_DIR", "/tmp/ambient-ca-directory"),
        ("SSLKEYLOGFILE", "/tmp/ambient-session-key-log"),
        ("OPENSSL_CONF", "/tmp/ambient-openssl.cnf"),
        ("OPENSSL_MODULES", "/tmp/ambient-openssl-modules"),
        ("MURMUR_PIPECAT_E2E_TURN_PASSWORD", "ambient-murmur-turn-password"),
        ("UNLISTED_VENDOR_PASSWORD", "ambient-vendor-password"),
        ("UNLISTED_VENDOR_PRIVATE_KEY", "ambient-vendor-private-key"),
        ("UNLISTED_VENDOR_SECRET", "ambient-vendor-secret"),
        ("UNLISTED_VENDOR_TOKEN", "ambient-vendor-token"),
    ],
)
def test_guard_rejects_ambient_relay_or_trust_material_before_murmur_import(
    name: str,
    value: str,
) -> None:
    paths = _paths("ambient-relay-guard")
    environment = build_environment(paths)
    environment[name] = value
    try:
        payload = _guard_probe(environment)
    finally:
        _cleanup(paths)

    assert payload["failed"] is True
    assert payload["murmur_imported"] is False
    if value in str(payload):
        pytest.fail("app guard reflected ambient material", pytrace=False)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SSLKEYLOGFILE", "/tmp/relay-session-keys.log"),
        ("OPENSSL_CONF", "/tmp/relay-openssl.cnf"),
        ("OPENSSL_MODULES", "/tmp/relay-openssl-modules"),
    ],
)
def test_relay_guard_rejects_tls_policy_before_ca_parse_or_murmur_import(
    name: str,
    value: str,
) -> None:
    paths = _paths("relay-tls-policy-guard")
    environment, _coturn = _relay_environment(paths)
    environment[name] = value
    try:
        payload = _guard_probe(environment)
    finally:
        _strict_relay_cleanup(paths)

    assert payload == {
        "failed": True,
        "error": "Pipecat E2E app does not accept ambient TURN configuration",
        "murmur_imported": False,
        "ssl_imported": False,
    }
    if value in str(payload):
        pytest.fail("app TLS guard reflected an ambient path", pytrace=False)


def test_status_pass_predicate_categorically_rejects_relay_with_true_media() -> None:
    paths = _paths("status-network-gate")
    environment = build_environment(paths)
    code = r"""
import json
from scripts.voice_pipecat_e2e_app import _qualification_passed
from scripts.voice_pipecat_e2e_coturn import PipecatE2ENetworkMode

facts = {
    "reservation_terminal": True,
    "cleanup_complete": True,
    "control_plane_clean": True,
    "media_contract_satisfied": True,
}
print(json.dumps({
    "direct": _qualification_passed(network=PipecatE2ENetworkMode.DIRECT, **facts),
    "relay_tls": _qualification_passed(network=PipecatE2ENetworkMode.RELAY_TLS, **facts),
}, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _cleanup(paths)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "direct": True,
        "relay_tls": False,
    }


def test_guarded_app_uses_production_bootstrap_release_and_sanitized_status() -> None:
    paths = _paths("http")
    paths.run_dir.mkdir(parents=True)
    environment = build_environment(paths)
    code = r"""
import json
from fastapi.testclient import TestClient
from scripts.voice_pipecat_e2e_app import E2E_AGENT_ID, E2E_SESSION_ID, app

headers = {"Authorization": "Bearer voice-e2e"}
voice_call_id = "50000000-0000-4000-8000-000000000005"
body = {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id}
with TestClient(app) as client:
    unauthorized = client.get("/_e2e/health")
    health = client.get("/_e2e/health", headers=headers)
    bootstrap = client.post("/api/voice/session", headers=headers, json=body)
    release = client.post("/api/voice/session/end", headers=headers, json=body)
    status = client.post("/_e2e/pipecat/status", headers=headers, json=body)
result = {
    "unauthorized": unauthorized.status_code,
    "health": health.json(),
    "bootstrap_status": bootstrap.status_code,
    "assignment": bootstrap.json(),
    "release_status": release.status_code,
    "status_status": status.status_code,
    "terminal": status.json(),
    "expected_agent_id": E2E_AGENT_ID,
}
print("PIPECAT_E2E_RESULT=" + json.dumps(result, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
        marker = next(
            (
                line.removeprefix("PIPECAT_E2E_RESULT=")
                for line in result.stdout.splitlines()
                if line.startswith("PIPECAT_E2E_RESULT=")
            ),
            None,
        )
        assert result.returncode == 0, result.stderr
        assert marker is not None, result.stdout
        payload = json.loads(marker)
    finally:
        _cleanup(paths)

    assert payload["unauthorized"] == 401
    assert payload["health"] == {
        "schema_version": 1,
        "ok": True,
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": "pipecat-fake-rtc-v1",
        "agent_id": payload["expected_agent_id"],
        "session_id": "a4f4328e-185e-4c65-b3f7-101e04a37578",
        "network": "direct-loopback",
        "providers": "fake",
        "livekit_imported": False,
        "cost": "unmeasured",
    }
    assert payload["bootstrap_status"] == 200
    assignment = payload["assignment"]
    assert assignment["runtime"] == "pipecat_smallwebrtc_v1"
    assert assignment["profile_id"] == "pipecat-fake-rtc-v1"
    assert assignment["ice_servers"] == []
    assert assignment["webrtc_url"].startswith("http://127.0.0.1:8101/api/voice/pipecat/signal/")
    assert payload["release_status"] == 204
    assert payload["status_status"] == 200
    terminal = payload["terminal"]
    assert terminal["status"] == "pending"
    assert terminal["reservation"] == {
        "state": "terminal",
        "cleanup_complete": True,
        "terminal_reason": "user_ended",
        "retryable": False,
    }
    assert terminal["control_plane"] == {
        "bootstrap_active_assignment_count": 0,
        "bootstrap_active_lock_count": 0,
        "signaling_active_call_count": 0,
        "runtime_handle_retained": False,
        "cleanup_retry_pending": False,
        "runtime_observer_pending": False,
        "expiry_pending": False,
        "trusted_release_pending": False,
    }
    assert terminal["fake_media"]["media_contract_satisfied"] is False


def test_guarded_relay_app_projects_claim_bound_turn_lease_without_media_claim() -> None:
    paths = _paths("relay-http")
    environment, coturn = _relay_environment(paths)
    voice_call_id = "50000000-0000-4000-8000-000000000005"
    call_tag = hashlib.sha256(voice_call_id.encode("ascii")).hexdigest()[:16]
    code = rf"""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from scripts.voice_pipecat_e2e_app import (
    E2E_AGENT_ID,
    E2E_SESSION_ID,
    _composition,
    _guarded_environment,
    _ice_lease_issuer,
    app,
)
from scripts.voice_pipecat_e2e_coturn import (
    COTURN_TURNS_URL,
    derive_turn_rest_credentials,
    read_private_coturn_configuration,
)

headers = {{"Authorization": "Bearer voice-e2e"}}
voice_call_id = "{voice_call_id}"
body = {{"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id}}
captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
    with TestClient(app) as client:
        health = client.get("/_e2e/health", headers=headers)
        bootstrap = client.post("/api/voice/session", headers=headers, json=body)
        assignment = bootstrap.json()
        record = _composition.bootstrap_service._records[voice_call_id]
        internal = record.result
        lease = internal.ice_lease
        server = lease.ice_servers[0]
        username = server.username.get_secret_value()
        credential = server.credential.get_secret_value()
        config_path = Path(os.environ["MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE"])
        secret = read_private_coturn_configuration(
            config_path,
            expected_run_dir=config_path.parent.parent,
        )
        expected = derive_turn_rest_credentials(
            static_auth_secret=secret,
            voice_call_id=voice_call_id,
            expires_at=lease.claims.expires_at,
            now=lease.claims.issued_at,
        )
        release = client.post("/api/voice/session/end", headers=headers, json=body)
        status = client.post("/_e2e/pipecat/status", headers=headers, json=body)

captured = captured_stdout.getvalue() + captured_stderr.getvalue()
projected = assignment["ice_servers"]
result = {{
    "health_status": health.status_code,
    "health_network": health.json()["network"],
    "health_qualification": health.json()["qualification"],
    "health_topology_status": health.json()["topology_status"],
    "bootstrap_status": bootstrap.status_code,
    "assignment_scope_exact": (
        assignment["voice_call_id"] == voice_call_id
        and assignment["session_id"] == E2E_SESSION_ID
        and assignment["agent_id"] == E2E_AGENT_ID
    ),
    "lease_claim_bound": (
        internal.assignment.claims == lease.claims
        and lease.claims.voice_call_id == voice_call_id
        and lease.expires_at == lease.claims.expires_at == internal.assignment.expires_at
    ),
    "one_turns_server": (
        len(projected) == 1
        and projected[0]["urls"] == [COTURN_TURNS_URL]
        and projected[0]["credentialType"] == "password"
    ),
    "username_expiry_exact_floor": (
        username.split(":", 1)[0] == str(int(lease.claims.expires_at.timestamp()))
        and 0 <= (lease.claims.expires_at.timestamp() - int(username.split(":", 1)[0])) < 1
    ),
    "username_call_tag_exact": username.split(":", 1)[1] == "{call_tag}",
    "rest_credential_exact": (
        username == expected.username and credential == expected.credential
    ),
    "issuer_repr_redacted": (
        secret not in repr(_guarded_environment)
        and secret not in repr(_ice_lease_issuer)
        and username not in repr(_ice_lease_issuer)
        and credential not in repr(_ice_lease_issuer)
        and COTURN_TURNS_URL not in repr(_ice_lease_issuer)
    ),
    "captured_logs_redacted": all(
        value not in captured
        for value in (secret, username, credential, COTURN_TURNS_URL)
    ),
    "release_status": release.status_code,
    "status_status": status.status_code,
    "terminal_status": status.json()["status"],
    "terminal_cleanup": status.json()["reservation"]["cleanup_complete"],
    "control_plane_clean": all(
        value in (0, False) for value in status.json()["control_plane"].values()
    ),
}}
print("PIPECAT_RELAY_RESULT=" + json.dumps(result, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
        _require_relay_safe_process_output(
            result,
            forbidden=(STATIC_TURN_SECRET, COTURN_TURNS_URL, call_tag),
        )
        marker = next(
            (
                line.removeprefix("PIPECAT_RELAY_RESULT=")
                for line in result.stdout.splitlines()
                if line.startswith("PIPECAT_RELAY_RESULT=")
            ),
            None,
        )
        if result.returncode != 0:
            pytest.fail("isolated relay app failed", pytrace=False)
        if marker is None:
            pytest.fail("isolated relay app result is unavailable", pytrace=False)
        payload = json.loads(marker)
    finally:
        _strict_relay_cleanup(paths)

    assert payload == {
        "health_status": 200,
        "health_network": "relay-tls",
        "health_qualification": "unavailable",
        "health_topology_status": "contract-unvalidated",
        "bootstrap_status": 200,
        "assignment_scope_exact": True,
        "lease_claim_bound": True,
        "one_turns_server": True,
        "username_expiry_exact_floor": True,
        "username_call_tag_exact": True,
        "rest_credential_exact": True,
        "issuer_repr_redacted": True,
        "captured_logs_redacted": True,
        "release_status": 204,
        "status_status": 200,
        "terminal_status": "pending",
        "terminal_cleanup": True,
        "control_plane_clean": True,
    }
    assert "media" not in marker
    assert "allocation" not in marker
    assert "bytes" not in marker
    assert not coturn.config.exists()
