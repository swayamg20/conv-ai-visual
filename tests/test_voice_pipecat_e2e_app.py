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
EXPECTED_RELAY_CALL_ID = "50000000-0000-4000-8000-000000000005"
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
        environment["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"] = EXPECTED_RELAY_CALL_ID
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


@pytest.mark.parametrize("expected_call_id", [None, "not-a-canonical-call"])
def test_relay_guard_requires_one_canonical_expected_call_before_murmur_import(
    expected_call_id: str | None,
) -> None:
    paths = _paths("relay-call-guard")
    environment, _coturn = _relay_environment(paths)
    if expected_call_id is None:
        environment.pop("MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID")
        expected_error = "relay-tls Pipecat E2E material is unavailable"
    else:
        environment["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"] = expected_call_id
        expected_error = "relay-tls Pipecat E2E call identity is unavailable"
    try:
        payload = _guard_probe(environment)
    finally:
        _strict_relay_cleanup(paths)

    assert payload == {
        "failed": True,
        "error": expected_error,
        "murmur_imported": False,
        "ssl_imported": False,
    }
    if expected_call_id is not None:
        assert expected_call_id not in str(payload)


def test_direct_guard_rejects_relay_expected_call_before_murmur_import() -> None:
    paths = _paths("direct-call-guard")
    environment = build_environment(paths)
    environment["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"] = EXPECTED_RELAY_CALL_ID
    try:
        payload = _guard_probe(environment)
    finally:
        _cleanup(paths)

    assert payload == {
        "failed": True,
        "error": "direct Pipecat E2E does not accept relay material",
        "murmur_imported": False,
        "ssl_imported": False,
    }
    assert EXPECTED_RELAY_CALL_ID not in str(payload)


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


def test_aioice_gateway_binding_is_exact_bounded_and_non_reflective() -> None:
    paths = _paths("gateway-binding")
    environment = build_environment(paths)
    code = r"""
import json

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_relay_identity import require_aioice_gateway

topology = CoturnBridgeTopology.parse(
    network="10.255.255.0/29",
    gateway="10.255.255.1",
    container="10.255.255.2",
)
reader_calls = []

def success_reader(use_ipv4, use_ipv6):
    reader_calls.append((use_ipv4, use_ipv6))
    return ["10.255.255.1", "192.168.10.7"]

require_aioice_gateway(topology, address_reader=success_reader)
failures = []
for addresses in (
    [],
    ["10.255.255.9"],
    ["10.255.255.1", "10.255.255.1"],
    ["10.255.255.1", "10.255.255.2"],
    ["010.255.255.1"],
    ["10.255.255.1"] * 257,
    ("10.255.255.1",),
):
    try:
        require_aioice_gateway(topology, address_reader=lambda _v4, _v6, a=addresses: a)
    except RuntimeError as exc:
        failures.append(str(exc))
    else:
        failures.append("accepted")

hostile_addresses = ("198.51.100.201", "198.51.100.202")
retained = {}

class ProbeInterrupt(BaseException):
    pass

def hostile_reader(kind):
    def reader(_use_ipv4, _use_ipv6):
        addresses = list(hostile_addresses)
        iterator = iter(addresses)
        parsed = [next(iterator)]
        value = addresses[-1]
        address = value
        inner = RuntimeError("hostile gateway reader failed")
        outer = ProbeInterrupt("gateway interrupted") if kind == "interrupt" else ValueError(
            "gateway failed"
        )
        retained[kind + "_inner"] = inner
        retained[kind + "_outer"] = outer
        raise outer from inner
    return reader

def clean_app_boundary(error):
    trace = error.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        if frame.f_code.co_filename.endswith("voice_pipecat_e2e_relay_identity.py"):
            local = frame.f_locals
            if local.get("address_reader", None) is not None:
                return False
            if local.get("topology", None) is not None:
                return False
            rendered = repr(tuple(local.values()))
            if any(value in rendered for value in hostile_addresses):
                return False
        trace = trace.tb_next
    return error.__context__ is None and error.__cause__ is None

try:
    require_aioice_gateway(topology, address_reader=hostile_reader("failure"))
except RuntimeError as error:
    failure_boundary_clean = clean_app_boundary(error)
else:
    failure_boundary_clean = False

try:
    require_aioice_gateway(topology, address_reader=hostile_reader("interrupt"))
except ProbeInterrupt as error:
    interrupt_boundary_clean = clean_app_boundary(error)
else:
    interrupt_boundary_clean = False

retained_graphs_scrubbed = all(
    error.__context__ is None
    and error.__cause__ is None
    and (
        error.__traceback__ is None
        or (name == "interrupt_outer" and clean_app_boundary(error))
    )
    for name, error in retained.items()
)

print("PIPECAT_GATEWAY_RESULT=" + json.dumps({
    "reader_call_exact": reader_calls == [(True, False)],
    "all_fail_closed": failures == [
        "relay-tls Pipecat E2E gateway is unavailable",
    ] * 7,
    "failure_boundary_clean": failure_boundary_clean,
    "interrupt_boundary_clean": interrupt_boundary_clean,
    "retained_graphs_scrubbed": retained_graphs_scrubbed,
}, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _cleanup(paths)

    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_GATEWAY_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_GATEWAY_RESULT=")
    )
    assert json.loads(marker) == {
        "all_fail_closed": True,
        "failure_boundary_clean": True,
        "interrupt_boundary_clean": True,
        "reader_call_exact": True,
        "retained_graphs_scrubbed": True,
    }
    for address in (
        "10.255.255.1",
        "10.255.255.2",
        "192.168.10.7",
        "198.51.100.201",
        "198.51.100.202",
    ):
        assert address not in result.stdout
        assert address not in result.stderr


def test_relay_issuer_scrubs_hostile_credential_failure_frames() -> None:
    paths = _paths("issuer-failure-scrub")
    environment = build_environment(paths)
    secret = "fedcba9876543210" * 4
    username = "1786622700:hostile-user-tag"
    credential = "hostile-credential-must-not-survive"
    code = rf"""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import scripts.voice_pipecat_e2e_app  # noqa: F401
import scripts.voice_pipecat_e2e_relay_identity as relay_identity
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceLeaseUnavailable
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind

secret = "{secret}"
username = "{username}"
credential = "{credential}"
retained = {{}}

def hostile_derive(**values):
    static_auth_secret = values["static_auth_secret"]
    derived_username = username
    derived_credential = credential
    inner = RuntimeError("credential helper failed")
    outer = ValueError("credential construction failed")
    retained["inner"] = inner
    retained["outer"] = outer
    raise outer from inner

def clean_relay_boundary(error):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if isinstance(linked, BaseException)
        )
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith(
                "voice_pipecat_e2e_relay_identity.py"
            ):
                values = tuple(trace.tb_frame.f_locals.values())
                if any(isinstance(value, PipecatIceLease) for value in values):
                    return False
                rendered = repr(values)
                if any(value in rendered for value in (secret, username, credential)):
                    return False
            trace = trace.tb_next
    return error.__context__ is None and error.__cause__ is None

now = datetime.now(UTC)
claims = VoiceCallClaims(
    user_id="voice-e2e-user",
    session_id="a4f4328e-185e-4c65-b3f7-101e04a37578",
    agent_id="90bd1253-90a6-459a-bf37-365bc3039a76",
    voice_call_id="50000000-0000-4000-8000-000000000005",
    trace_id="70000000-0000-4000-8000-000000000007",
    runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
    profile_id="pipecat-fake-rtc-v1",
    issued_at=now,
    expires_at=now + timedelta(seconds=120),
)
issuer = relay_identity.RelayTlsIceLeaseIssuer(secret, claims.voice_call_id)
original_derive = relay_identity.derive_turn_rest_credentials
relay_identity.derive_turn_rest_credentials = hostile_derive
try:
    try:
        asyncio.run(issuer.issue(claims))
    except PipecatIceLeaseUnavailable as error:
        boundary_clean = clean_relay_boundary(error)
        fixed_error = str(error) == "Pipecat relay ICE lease is unavailable"
    else:
        boundary_clean = False
        fixed_error = False
finally:
    relay_identity.derive_turn_rest_credentials = original_derive

retained_scrubbed = all(
    error.__traceback__ is None
    and error.__context__ is None
    and error.__cause__ is None
    for error in retained.values()
)
print("PIPECAT_ISSUER_SCRUB_RESULT=" + json.dumps({{
    "boundary_clean": boundary_clean,
    "fixed_error": fixed_error,
    "issued_once": issuer.issue_count == 1,
    "repr_redacted": all(
        value not in repr(issuer) for value in (secret, username, credential)
    ),
    "retained_scrubbed": retained_scrubbed,
}}, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _cleanup(paths)

    _require_relay_safe_process_output(
        result,
        forbidden=(secret, username, credential),
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_ISSUER_SCRUB_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_ISSUER_SCRUB_RESULT=")
    )
    assert json.loads(marker) == {
        "boundary_clean": True,
        "fixed_error": True,
        "issued_once": True,
        "repr_redacted": True,
        "retained_scrubbed": True,
    }


def test_relay_prebootstrap_scrubs_post_lease_signaling_failure() -> None:
    paths = _paths("signal-fail-scrub")
    environment, _coturn = _relay_environment(paths)
    username = "1786622800:hostile-signaling-user"
    credential = "hostile-signaling-credential-must-not-survive"
    code = rf"""
import io
import json
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient
with patch("aioice.ice.get_host_addresses", return_value=["10.255.255.1"]):
    from scripts.voice_pipecat_e2e_app import (
        E2E_SESSION_ID,
        _composition,
        _ice_lease_issuer,
        app,
    )
import scripts.voice_pipecat_e2e_relay_identity as relay_identity
from murmur.api.routers.pipecat_voice import PipecatHttpError
from murmur.voice.pipecat_ice import PipecatIceLease
from murmur.voice.pipecat_signaling import PipecatSignalingUnavailable
from scripts.voice_pipecat_e2e_coturn import TurnRestCredentials

username = "{username}"
credential = "{credential}"
retained = {{}}
captured = {{}}
original_reserve = _composition.signaling_service.reserve
original_handler = app.exception_handlers[PipecatHttpError]
original_derive = relay_identity.derive_turn_rest_credentials

def fixed_credentials(**_values):
    return TurnRestCredentials(
        expires_at_epoch_seconds=1786622800,
        call_tag="hostile-signaling-user",
        username=username,
        credential=credential,
    )

async def hostile_reserve(self, claims, ice_lease):
    assert isinstance(ice_lease, PipecatIceLease)
    server = ice_lease.ice_servers[0]
    raw_username = server.username.get_secret_value()
    raw_credential = server.credential.get_secret_value()
    inner = RuntimeError("signaling dependency failed")
    outer = PipecatSignalingUnavailable("signaling unavailable")
    retained["inner"] = inner
    retained["outer"] = outer
    retained["material_matches"] = (
        raw_username == username and raw_credential == credential
    )
    raise outer from inner

async def capture_handler(request, error):
    captured["boundary"] = error
    return await original_handler(request, error)

def graph_is_clean(error):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if isinstance(linked, BaseException)
        )
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith(
                "voice_pipecat_e2e_relay_identity.py"
            ):
                values = tuple(trace.tb_frame.f_locals.values())
                if any(isinstance(value, PipecatIceLease) for value in values):
                    return False
                rendered = repr(values)
                if any(value in rendered for value in (username, credential)):
                    return False
                for name in (
                    "result",
                    "body",
                    "owner",
                    "request",
                    "bootstrap_service",
                    "self",
                    "identity",
                ):
                    if trace.tb_frame.f_locals.get(name) is not None:
                        return False
            trace = trace.tb_next
    return error.__context__ is None and error.__cause__ is None

_composition.signaling_service.reserve = types.MethodType(
    hostile_reserve,
    _composition.signaling_service,
)
relay_identity.derive_turn_rest_credentials = fixed_credentials
app.add_exception_handler(PipecatHttpError, capture_handler)
headers = {{"Authorization": "Bearer voice-e2e"}}
body = {{
    "session_id": E2E_SESSION_ID,
    "voice_call_id": "{EXPECTED_RELAY_CALL_ID}",
}}
stdout = io.StringIO()
stderr = io.StringIO()
try:
    with redirect_stdout(stdout), redirect_stderr(stderr):
        with TestClient(app) as client:
            response = client.post(
                "/_e2e/pipecat/prebootstrap",
                headers=headers,
                json=body,
            )
finally:
    _composition.signaling_service.reserve = original_reserve
    relay_identity.derive_turn_rest_credentials = original_derive

boundary = captured.get("boundary")
original_graphs_scrubbed = all(
    retained[name].__traceback__ is None
    and retained[name].__context__ is None
    and retained[name].__cause__ is None
    for name in ("inner", "outer")
)
process_capture = stdout.getvalue() + stderr.getvalue()
print("PIPECAT_SIGNALING_SCRUB_RESULT=" + json.dumps({{
    "boundary_clean": isinstance(boundary, PipecatHttpError)
        and graph_is_clean(boundary),
    "fixed_response": response.status_code == 503
        and response.json() == {{"error": "Voice assignment is unavailable"}},
    "material_reached_signaling": retained.get("material_matches") is True,
    "no_retained_bootstrap_state": (
        _ice_lease_issuer.issue_count == 1
        and not _composition.bootstrap_service._records
        and not _composition.signaling_service._reservations
    ),
    "original_graphs_scrubbed": original_graphs_scrubbed,
    "process_capture_redacted": all(
        value not in process_capture for value in (username, credential)
    ),
}}, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _strict_relay_cleanup(paths)

    _require_relay_safe_process_output(
        result,
        forbidden=(STATIC_TURN_SECRET, username, credential, COTURN_TURNS_URL),
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_SIGNALING_SCRUB_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_SIGNALING_SCRUB_RESULT=")
    )
    assert json.loads(marker) == {
        "boundary_clean": True,
        "fixed_response": True,
        "material_reached_signaling": True,
        "no_retained_bootstrap_state": True,
        "original_graphs_scrubbed": True,
        "process_capture_redacted": True,
    }


def test_relay_prebootstrap_cancellation_waits_for_owned_cleanup_and_scrubs() -> None:
    paths = _paths("cancel-scrub")
    environment, _coturn = _relay_environment(paths)
    username = "1786622900:hostile-cancel-user"
    credential = "hostile-cancel-credential-must-not-survive"
    code = rf"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

with patch("aioice.ice.get_host_addresses", return_value=["10.255.255.1"]):
    from scripts.voice_pipecat_e2e_app import (
        E2E_AGENT_ID,
        E2E_SESSION_ID,
        E2E_USER_ID,
        _composition,
        _e2e_relay_prebootstrap,
        app,
    )
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceServer
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind
from starlette.requests import Request

username = "{username}"
credential = "{credential}"
retained = {{}}

class PendingBootstrapOwner:
    def __init__(self):
        self.cleanup_allowed = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.reserve_started = asyncio.Event()
        self.live_lease = None
        self.provision_task = None
        self.raw_result = None

    async def _provision(self):
        now = datetime.now(UTC)
        claims = VoiceCallClaims(
            user_id=E2E_USER_ID,
            session_id=E2E_SESSION_ID,
            agent_id=E2E_AGENT_ID,
            voice_call_id="{EXPECTED_RELAY_CALL_ID}",
            trace_id="70000000-0000-4000-8000-000000000007",
            runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
            profile_id="pipecat-fake-rtc-v1",
            issued_at=now,
            expires_at=now + timedelta(seconds=120),
        )
        lease = PipecatIceLease(
            claims=claims,
            provider_id="e2e-coturn-rest-v1",
            expires_at=claims.expires_at,
            ice_servers=(
                PipecatIceServer(
                    urls=("turns:127.0.0.1:5349?transport=tcp",),
                    username=username,
                    credential=credential,
                ),
            ),
        )
        result = SimpleNamespace(
            assignment=SimpleNamespace(expires_at=claims.expires_at),
            ice_lease=lease,
        )
        self.live_lease = lease
        self.raw_result = result
        self.reserve_started.set()
        await asyncio.Event().wait()

    async def bootstrap(self, *, user_id, session_id, voice_call_id):
        assert (user_id, session_id, voice_call_id) == (
            E2E_USER_ID,
            E2E_SESSION_ID,
            "{EXPECTED_RELAY_CALL_ID}",
        )
        self.provision_task = asyncio.create_task(
            self._provision(), name="hostile-owned-provision"
        )
        try:
            return await asyncio.shield(self.provision_task)
        except asyncio.CancelledError as error:
            retained["bootstrap_cancel"] = error
            raise

    async def release(self, *, user_id, session_id, voice_call_id):
        assert (user_id, session_id, voice_call_id) == (
            E2E_USER_ID,
            E2E_SESSION_ID,
            "{EXPECTED_RELAY_CALL_ID}",
        )
        self.cleanup_started.set()
        await self.cleanup_allowed.wait()
        task = self.provision_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.provision_task = None
        self.live_lease = None
        self.raw_result = None

def request_for_prebootstrap():
    payload = json.dumps({{
        "session_id": E2E_SESSION_ID,
        "voice_call_id": "{EXPECTED_RELAY_CALL_ID}",
    }}).encode("utf-8")
    messages = [{{"type": "http.request", "body": payload, "more_body": False}}]

    async def receive():
        return messages.pop(0)

    scope = {{
        "type": "http",
        "asgi": {{"version": "3.0"}},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/_e2e/pipecat/prebootstrap",
        "raw_path": b"/_e2e/pipecat/prebootstrap",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8101),
        "state": {{"pipecat_user": {{"id": E2E_USER_ID}}}},
        "app": app,
    }}
    return Request(scope, receive)

def cancellation_boundary_clean(error):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if isinstance(linked, BaseException)
        )
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith(
                "voice_pipecat_e2e_relay_identity.py"
            ):
                values = tuple(trace.tb_frame.f_locals.values())
                if any(isinstance(value, PipecatIceLease) for value in values):
                    return False
                rendered = repr(values)
                if any(value in rendered for value in (username, credential)):
                    return False
                for name in (
                    "result",
                    "body",
                    "owner",
                    "request",
                    "bootstrap_service",
                    "self",
                    "identity",
                ):
                    if trace.tb_frame.f_locals.get(name) is not None:
                        return False
            trace = trace.tb_next
    return error.__context__ is None and error.__cause__ is None

async def scenario():
    original_service = _composition.bootstrap_service
    service = PendingBootstrapOwner()
    _composition.bootstrap_service = service
    try:
        handler_task = asyncio.create_task(
            _e2e_relay_prebootstrap(request_for_prebootstrap()),
            name="hostile-prebootstrap-request",
        )
        await service.reserve_started.wait()
        handler_task.cancel()
        await service.cleanup_started.wait()
        live_before_cleanup = (
            not handler_task.done()
            and service.provision_task is not None
            and not service.provision_task.done()
            and isinstance(service.live_lease, PipecatIceLease)
            and service.raw_result is not None
        )
        handler_task.cancel()
        await asyncio.sleep(0)
        repeated_cancel_waited = not handler_task.done()
        service.cleanup_allowed.set()
        try:
            await handler_task
        except asyncio.CancelledError as error:
            cancellation_preserved = True
            boundary_clean = cancellation_boundary_clean(error)
            retained["delivered_cancel"] = error
        else:
            cancellation_preserved = False
            boundary_clean = False
        pending_owned_tasks = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and task.get_name()
            in {{
                "hostile-owned-provision",
                "hostile-prebootstrap-request",
                "pipecat-e2e-prebootstrap-cleanup",
            }}
        ]
        return {{
            "boundary_clean": boundary_clean,
            "cancellation_preserved": cancellation_preserved,
            "live_before_cleanup": live_before_cleanup,
            "owned_state_cleared": (
                service.provision_task is None
                and service.live_lease is None
                and service.raw_result is None
                and not pending_owned_tasks
            ),
            "repeated_cancel_waited": repeated_cancel_waited,
            "same_cancel_scrubbed": (
                retained.get("bootstrap_cancel") is retained.get("delivered_cancel")
                and cancellation_boundary_clean(retained["bootstrap_cancel"])
            ),
        }}
    finally:
        _composition.bootstrap_service = original_service

print("PIPECAT_CANCEL_SCRUB_RESULT=" + json.dumps(
    asyncio.run(scenario()), sort_keys=True
))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _strict_relay_cleanup(paths)

    _require_relay_safe_process_output(
        result,
        forbidden=(STATIC_TURN_SECRET, username, credential, COTURN_TURNS_URL),
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_CANCEL_SCRUB_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_CANCEL_SCRUB_RESULT=")
    )
    assert json.loads(marker) == {
        "boundary_clean": True,
        "cancellation_preserved": True,
        "live_before_cleanup": True,
        "owned_state_cleared": True,
        "repeated_cancel_waited": True,
        "same_cancel_scrubbed": True,
    }


def test_relay_cancellation_retries_production_release_before_delivery() -> None:
    paths = _paths("release-retry")
    environment, _coturn = _relay_environment(paths)
    username = "1786623000:hostile-release-user"
    credential = "hostile-release-credential-must-not-survive"
    code = rf"""
import asyncio
import json
import types
from unittest.mock import patch

with patch("aioice.ice.get_host_addresses", return_value=["10.255.255.1"]):
    from scripts.voice_pipecat_e2e_app import (
        E2E_SESSION_ID,
        E2E_USER_ID,
        _composition,
        _e2e_relay_prebootstrap,
        _ice_lease_issuer,
        _seed_owned_scope,
        app,
    )
import scripts.voice_pipecat_e2e_relay_identity as relay_identity
from murmur.voice.pipecat_bootstrap import PipecatBootstrapUnavailable
from murmur.voice.pipecat_ice import PipecatIceLease
from scripts.voice_pipecat_e2e_coturn import TurnRestCredentials
from starlette.requests import Request

username = "{username}"
credential = "{credential}"

def fixed_credentials(**_values):
    return TurnRestCredentials(
        expires_at_epoch_seconds=1786623000,
        call_tag="hostile-release-user",
        username=username,
        credential=credential,
    )

def request_for_prebootstrap():
    payload = json.dumps({{
        "session_id": E2E_SESSION_ID,
        "voice_call_id": "{EXPECTED_RELAY_CALL_ID}",
    }}).encode("utf-8")
    messages = [{{"type": "http.request", "body": payload, "more_body": False}}]

    async def receive():
        return messages.pop(0)

    return Request({{
        "type": "http",
        "asgi": {{"version": "3.0"}},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/_e2e/pipecat/prebootstrap",
        "raw_path": b"/_e2e/pipecat/prebootstrap",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8101),
        "state": {{"pipecat_user": {{"id": E2E_USER_ID}}}},
        "app": app,
    }}, receive)

def cancellation_boundary_clean(error):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if isinstance(linked, BaseException)
        )
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename.endswith(
                "voice_pipecat_e2e_relay_identity.py"
            ):
                values = tuple(trace.tb_frame.f_locals.values())
                if any(isinstance(value, PipecatIceLease) for value in values):
                    return False
                rendered = repr(values)
                if any(value in rendered for value in (username, credential)):
                    return False
                for name in (
                    "result",
                    "body",
                    "owner",
                    "request",
                    "bootstrap_service",
                    "self",
                    "identity",
                    "cleanup_task",
                ):
                    if trace.tb_frame.f_locals.get(name) is not None:
                        return False
            trace = trace.tb_next
    return error.__context__ is None and error.__cause__ is None

async def scenario():
    service = _composition.bootstrap_service
    signaling = _composition.signaling_service
    original_bootstrap = service.bootstrap
    original_release = service.release
    original_reserve = signaling.reserve
    original_derive = relay_identity.derive_turn_rest_credentials
    reserve_started = asyncio.Event()
    reserve_allowed = asyncio.Event()
    first_release_failed = asyncio.Event()
    counts = {{"bootstrap": 0, "release": 0}}
    live = {{"lease": None}}
    retained = {{}}

    async def counted_bootstrap(self, **values):
        counts["bootstrap"] += 1
        return await original_bootstrap(**values)

    async def gated_reserve(self, claims, ice_lease):
        server = ice_lease.ice_servers[0]
        assert server.username.get_secret_value() == username
        assert server.credential.get_secret_value() == credential
        live["lease"] = ice_lease
        reserve_started.set()
        try:
            await reserve_allowed.wait()
            return await original_reserve(claims, ice_lease)
        finally:
            live["lease"] = None

    async def flaky_release(self, **values):
        counts["release"] += 1
        if counts["release"] == 1:
            lease = live["lease"]
            assert isinstance(lease, PipecatIceLease)
            server = lease.ice_servers[0]
            raw_username = server.username.get_secret_value()
            raw_credential = server.credential.get_secret_value()
            inner = RuntimeError("trusted release dependency failed")
            outer = PipecatBootstrapUnavailable("trusted release unavailable")
            retained["inner"] = inner
            retained["outer"] = outer
            retained["material_matches"] = (
                raw_username == username and raw_credential == credential
            )
            first_release_failed.set()
            raise outer from inner
        return await original_release(**values)

    service.bootstrap = types.MethodType(counted_bootstrap, service)
    service.release = types.MethodType(flaky_release, service)
    signaling.reserve = types.MethodType(gated_reserve, signaling)
    relay_identity.derive_turn_rest_credentials = fixed_credentials
    handler_task = None
    try:
        handler_task = asyncio.create_task(
            _e2e_relay_prebootstrap(request_for_prebootstrap()),
            name="hostile-production-prebootstrap",
        )
        await asyncio.wait_for(reserve_started.wait(), timeout=5)
        handler_task.cancel()
        await asyncio.wait_for(first_release_failed.wait(), timeout=5)
        records = tuple(service._records.values())
        record = records[0] if len(records) == 1 else None
        state_live_after_failure = (
            not handler_task.done()
            and record is not None
            and record.provision_task is not None
            and not record.provision_task.done()
            and isinstance(live["lease"], PipecatIceLease)
        )
        records = ()
        record = None
        handler_task.cancel()
        await asyncio.sleep(0)
        repeated_cancel_waited = not handler_task.done()
        reserve_allowed.set()
        try:
            await asyncio.wait_for(asyncio.shield(handler_task), timeout=5)
        except asyncio.CancelledError as error:
            cancellation_preserved = True
            boundary_clean = cancellation_boundary_clean(error)
        else:
            cancellation_preserved = False
            boundary_clean = False
        pending_owned_tasks = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and (
                task.get_name() == "pipecat-e2e-prebootstrap-cleanup"
                or task.get_name().startswith("pipecat-bootstrap-provision-")
            )
        ]
        retained_scrubbed = all(
            retained[name].__traceback__ is None
            and retained[name].__context__ is None
            and retained[name].__cause__ is None
            for name in ("inner", "outer")
        )
        return {{
            "authoritative_state_zero": (
                not service._records
                and service.active_assignment_count == 0
                and service.active_lock_count == 0
                and signaling.active_call_count == 0
                and live["lease"] is None
                and not pending_owned_tasks
            ),
            "boundary_clean": boundary_clean,
            "cancellation_preserved": cancellation_preserved,
            "exact_attempt_counts": (
                counts == {{"bootstrap": 1, "release": 2}}
                and _ice_lease_issuer.issue_count == 1
            ),
            "material_reached_failure": retained.get("material_matches") is True,
            "repeated_cancel_waited": repeated_cancel_waited,
            "retained_scrubbed": retained_scrubbed,
            "state_live_after_failure": state_live_after_failure,
        }}
    finally:
        service.bootstrap = original_bootstrap
        service.release = original_release
        signaling.reserve = original_reserve
        relay_identity.derive_turn_rest_credentials = original_derive

_seed_owned_scope()
print("PIPECAT_RELEASE_RETRY_RESULT=" + json.dumps(
    asyncio.run(scenario()), sort_keys=True
))
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _strict_relay_cleanup(paths)

    _require_relay_safe_process_output(
        result,
        forbidden=(STATIC_TURN_SECRET, username, credential, COTURN_TURNS_URL),
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_RELEASE_RETRY_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_RELEASE_RETRY_RESULT=")
    )
    assert json.loads(marker) == {
        "authoritative_state_zero": True,
        "boundary_clean": True,
        "cancellation_preserved": True,
        "exact_attempt_counts": True,
        "material_reached_failure": True,
        "repeated_cancel_waited": True,
        "retained_scrubbed": True,
        "state_live_after_failure": True,
    }


def test_relay_persistent_release_failure_keeps_cancellation_pending() -> None:
    paths = _paths("release-pending")
    environment, _coturn = _relay_environment(paths)
    username = "1786623100:hostile-persistent-user"
    credential = "hostile-persistent-credential-must-not-survive"
    code = rf"""
import asyncio
import json
import os
import sys
import types
from unittest.mock import patch

with patch("aioice.ice.get_host_addresses", return_value=["10.255.255.1"]):
    from scripts.voice_pipecat_e2e_app import (
        E2E_SESSION_ID,
        E2E_USER_ID,
        _composition,
        _e2e_relay_prebootstrap,
        _ice_lease_issuer,
        _seed_owned_scope,
        app,
    )
import scripts.voice_pipecat_e2e_relay_identity as relay_identity
from murmur.voice.pipecat_bootstrap import PipecatBootstrapUnavailable
from murmur.voice.pipecat_ice import PipecatIceLease
from scripts.voice_pipecat_e2e_coturn import TurnRestCredentials
from starlette.requests import Request

username = "{username}"
credential = "{credential}"

def fixed_credentials(**_values):
    return TurnRestCredentials(
        expires_at_epoch_seconds=1786623100,
        call_tag="hostile-persistent-user",
        username=username,
        credential=credential,
    )

def request_for_prebootstrap():
    payload = json.dumps({{
        "session_id": E2E_SESSION_ID,
        "voice_call_id": "{EXPECTED_RELAY_CALL_ID}",
    }}).encode("utf-8")
    messages = [{{"type": "http.request", "body": payload, "more_body": False}}]

    async def receive():
        return messages.pop(0)

    return Request({{
        "type": "http",
        "asgi": {{"version": "3.0"}},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/_e2e/pipecat/prebootstrap",
        "raw_path": b"/_e2e/pipecat/prebootstrap",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8101),
        "state": {{"pipecat_user": {{"id": E2E_USER_ID}}}},
        "app": app,
    }}, receive)

async def scenario():
    service = _composition.bootstrap_service
    signaling = _composition.signaling_service
    original_bootstrap = service.bootstrap
    original_reserve = signaling.reserve
    reserve_started = asyncio.Event()
    attempts_observed = asyncio.Event()
    never = asyncio.Event()
    counts = {{"bootstrap": 0, "release": 0}}
    live = {{"lease": None}}
    retained = []
    delivered = []
    attempt_times = []

    async def counted_bootstrap(self, **values):
        counts["bootstrap"] += 1
        return await original_bootstrap(**values)

    async def blocked_reserve(self, claims, ice_lease):
        server = ice_lease.ice_servers[0]
        assert server.username.get_secret_value() == username
        assert server.credential.get_secret_value() == credential
        live["lease"] = ice_lease
        reserve_started.set()
        await never.wait()

    async def persistent_release(self, **_values):
        counts["release"] += 1
        attempt_times.append(asyncio.get_running_loop().time())
        lease = live["lease"]
        assert isinstance(lease, PipecatIceLease)
        server = lease.ice_servers[0]
        raw_username = server.username.get_secret_value()
        raw_credential = server.credential.get_secret_value()
        if counts["release"] == 1:
            try:
                await never.wait()
            except asyncio.CancelledError as error:
                retained.append(error)
                raise
        inner = RuntimeError("persistent release dependency failed")
        outer = PipecatBootstrapUnavailable("persistent release unavailable")
        retained.extend((inner, outer))
        assert raw_username == username and raw_credential == credential
        if counts["release"] >= 3:
            attempts_observed.set()
        raise outer from inner

    async def observed_handler():
        try:
            return await _e2e_relay_prebootstrap(request_for_prebootstrap())
        except BaseException as error:
            delivered.append(error)
            raise

    service.bootstrap = types.MethodType(counted_bootstrap, service)
    service.release = types.MethodType(persistent_release, service)
    signaling.reserve = types.MethodType(blocked_reserve, signaling)
    relay_identity.derive_turn_rest_credentials = fixed_credentials
    relay_identity._PREBOOTSTRAP_RELEASE_ATTEMPT_TIMEOUT_SECONDS = 0.05
    relay_identity._PREBOOTSTRAP_RELEASE_RETRY_BACKOFF_SECONDS = 0.05
    handler_task = asyncio.create_task(
        observed_handler(), name="hostile-persistent-prebootstrap"
    )
    await asyncio.wait_for(reserve_started.wait(), timeout=5)
    handler_task.cancel()
    await asyncio.sleep(0)
    handler_task.cancel()
    await asyncio.wait_for(attempts_observed.wait(), timeout=5)
    await asyncio.sleep(0.02)
    records = tuple(service._records.values())
    record = records[0] if len(records) == 1 else None
    gaps = [
        later - earlier
        for earlier, later in zip(attempt_times, attempt_times[1:])
    ]
    retained_scrubbed = all(
        error.__traceback__ is None
        and error.__context__ is None
        and error.__cause__ is None
        for error in retained
    )
    result = {{
        "attempts_bounded_and_backed_off": (
            3 <= counts["release"] <= 4
            and len(gaps) >= 2
            and gaps[0] >= 0.08
            and all(gap >= 0.035 for gap in gaps[1:])
        ),
        "exact_single_bootstrap_and_issue": (
            counts["bootstrap"] == 1 and _ice_lease_issuer.issue_count == 1
        ),
        "no_boundary_delivered": not delivered and not handler_task.done(),
        "persistent_state_truthful": (
            record is not None
            and record.provision_task is not None
            and not record.provision_task.done()
            and isinstance(live["lease"], PipecatIceLease)
            and service.active_assignment_count == 1
            and signaling.active_call_count == 0
        ),
        "release_graphs_scrubbed": retained_scrubbed,
    }}
    print("PIPECAT_RELEASE_PENDING_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

_seed_owned_scope()
asyncio.run(scenario())
"""
    try:
        result = _run_isolated(code, environment)
    finally:
        _strict_relay_cleanup(paths)

    _require_relay_safe_process_output(
        result,
        forbidden=(STATIC_TURN_SECRET, username, credential, COTURN_TURNS_URL),
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("PIPECAT_RELEASE_PENDING_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("PIPECAT_RELEASE_PENDING_RESULT=")
    )
    assert json.loads(marker) == {
        "attempts_bounded_and_backed_off": True,
        "exact_single_bootstrap_and_issue": True,
        "no_boundary_delivered": True,
        "persistent_state_truthful": True,
        "release_graphs_scrubbed": True,
    }


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
    prebootstrap = client.post("/_e2e/pipecat/prebootstrap", headers=headers, json=body)
    bootstrap = client.post("/api/voice/session", headers=headers, json=body)
    release = client.post("/api/voice/session/end", headers=headers, json=body)
    status = client.post("/_e2e/pipecat/status", headers=headers, json=body)
result = {
    "unauthorized": unauthorized.status_code,
    "health": health.json(),
    "prebootstrap_status": prebootstrap.status_code,
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
    assert payload["prebootstrap_status"] == 404
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
    voice_call_id = EXPECTED_RELAY_CALL_ID
    call_tag = hashlib.sha256(voice_call_id.encode("ascii")).hexdigest()[:16]
    secret_sentinel = "relay-prebootstrap-secret-must-not-reflect"
    code = rf"""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import call, patch

from fastapi.testclient import TestClient
with patch("aioice.ice.get_host_addresses", return_value=["10.255.255.1"]) as address_reader:
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
wrong_body = {{
    "session_id": E2E_SESSION_ID,
    "voice_call_id": "60000000-0000-4000-8000-000000000006",
}}
secret_sentinel = "{secret_sentinel}"
captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
    with TestClient(app) as client:
        health = client.get("/_e2e/health", headers=headers)
        unauthorized_prebootstrap = client.post(
            "/_e2e/pipecat/prebootstrap", json=body
        )
        wrong_prebootstrap = client.post(
            "/_e2e/pipecat/prebootstrap", headers=headers, json=wrong_body
        )
        json_headers = {{**headers, "Content-Type": "application/json"}}
        invalid_prebootstrap = [
            client.post(
                "/_e2e/pipecat/prebootstrap",
                headers=json_headers,
                content=json.dumps({{"voice_call_id": secret_sentinel}})[:-1],
            ),
            client.post(
                "/_e2e/pipecat/prebootstrap",
                headers={{**json_headers, "Content-Length": "1100001"}},
                content="{{}}",
            ),
            client.post(
                "/_e2e/pipecat/prebootstrap",
                headers=headers,
                json={{**body, "unexpected": secret_sentinel}},
            ),
            client.post(
                "/_e2e/pipecat/prebootstrap",
                headers=headers,
                json={{"session_id": E2E_SESSION_ID}},
            ),
            client.post(
                "/_e2e/pipecat/prebootstrap",
                headers=headers,
                json={{
                    "session_id": E2E_SESSION_ID,
                    "voice_call_id": [secret_sentinel],
                }},
            ),
        ]
        no_issue_before_valid = (
            _ice_lease_issuer.issue_count == 0
            and not _composition.bootstrap_service._records
            and not _composition.signaling_service._reservations
        )
        prebootstrap = client.post(
            "/_e2e/pipecat/prebootstrap", headers=headers, json=body
        )
        prepared_record = _composition.bootstrap_service._records[voice_call_id]
        prepared_internal = prepared_record.result
        prepared_reservation_count = len(_composition.signaling_service._reservations)
        bootstrap = client.post("/api/voice/session", headers=headers, json=body)
        assignment = bootstrap.json()
        record = _composition.bootstrap_service._records[voice_call_id]
        internal = record.result
        cached_identity_exact = internal is prepared_internal
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
    "gateway_read_once": address_reader.call_args_list == [call(True, False)],
    "health_status": health.status_code,
    "health_network": health.json()["network"],
    "health_qualification": health.json()["qualification"],
    "health_topology_status": health.json()["topology_status"],
    "health_topology_bound": all(
        health.json()[name]
        for name in (
            "config_topology_bound",
            "aioice_gateway_gatherable",
            "aioice_container_absent",
        )
    ),
    "unauthorized_prebootstrap_status": unauthorized_prebootstrap.status_code,
    "wrong_prebootstrap_status": wrong_prebootstrap.status_code,
    "invalid_prebootstrap_fail_closed": (
        [(response.status_code, response.json()) for response in invalid_prebootstrap]
        == [
            (400, {{"error": "Request body is invalid JSON"}}),
            (413, {{"error": "Request body is too large"}}),
            (422, {{"error": "Request body is invalid"}}),
            (422, {{"error": "Request body is invalid"}}),
            (422, {{"error": "Request body is invalid"}}),
        ]
        and all(secret_sentinel not in response.text for response in invalid_prebootstrap)
    ),
    "no_issue_before_valid": no_issue_before_valid,
    "prebootstrap_status": prebootstrap.status_code,
    "prebootstrap_schema_exact": (
        set(prebootstrap.json()) == {{
            "schema_version",
            "status",
            "expires_at_epoch_seconds",
        }}
        and prebootstrap.json()["schema_version"] == 1
        and prebootstrap.json()["status"] == "prepared"
        and type(prebootstrap.json()["expires_at_epoch_seconds"]) is int
        and prebootstrap.json()["expires_at_epoch_seconds"]
            == int(prepared_internal.assignment.expires_at.timestamp())
    ),
    "bootstrap_status": bootstrap.status_code,
    "cached_identity_exact": cached_identity_exact,
    "one_issue_and_reservation": (
        _ice_lease_issuer.issue_count == 1
        and prepared_reservation_count == 1
        and len(_composition.signaling_service._reservations) == 1
    ),
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
        for value in (secret, username, credential, COTURN_TURNS_URL, secret_sentinel)
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
            forbidden=(STATIC_TURN_SECRET, COTURN_TURNS_URL, call_tag, secret_sentinel),
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
        "gateway_read_once": True,
        "health_status": 200,
        "health_network": "relay-tls",
        "health_qualification": "unavailable",
        "health_topology_status": "contract-unvalidated",
        "health_topology_bound": True,
        "unauthorized_prebootstrap_status": 401,
        "wrong_prebootstrap_status": 404,
        "invalid_prebootstrap_fail_closed": True,
        "no_issue_before_valid": True,
        "prebootstrap_status": 200,
        "prebootstrap_schema_exact": True,
        "bootstrap_status": 200,
        "cached_identity_exact": True,
        "one_issue_and_reservation": True,
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
