"""Core pre-import contracts for deterministic Pipecat Coturn qualification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_coturn as coturn_contract  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    COTURN_FIXTURE_PATH,
    COTURN_IMAGE,
    COTURN_PLATFORM,
    COTURN_REALM,
    COTURN_RELAY_MAX_PORT,
    COTURN_RELAY_MEDIA_EXECUTABLE,
    COTURN_RELAY_MIN_PORT,
    COTURN_TLS_PORT,
    COTURN_TOPOLOGY_STATUS,
    COTURN_TURNS_URL,
    CoturnBridgeTopology,
    CoturnContractError,
    CoturnContractPaths,
    PipecatE2ENetworkMode,
    derive_turn_rest_credentials,
    derive_turn_rest_username,
    parse_network_mode,
    read_private_coturn_configuration,
    read_private_coturn_configuration_receipt,
    render_coturn_configuration,
    validate_coturn_fixture,
    validate_static_auth_secret,
    validate_turn_tls_ca_file,
)

STATIC_SECRET = "0123456789abcdef" * 4
VOICE_CALL_ID = "50000000-0000-4000-8000-000000000005"
EXPIRY = datetime.fromtimestamp(1_786_622_700.999999, UTC)
EXPECTED_USERNAME = "1786622700:342ace7df588b4b7"
EXPECTED_CREDENTIAL = "xSer6bE9W0OGFchAP03UVPGrJIc="
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
TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.28.44.0/29",
    gateway="172.28.44.1",
    container="172.28.44.2",
)


def _private_paths(tmp_path: Path) -> CoturnContractPaths:
    run_dir = tmp_path / "relay-test"
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    paths = CoturnContractPaths.for_run_dir("relay-test", run_dir)
    paths.coturn_dir.mkdir(mode=0o700)
    paths.coturn_dir.chmod(0o700)
    paths.config.write_text(
        render_coturn_configuration(
            COTURN_FIXTURE_PATH.read_text(encoding="utf-8"),
            STATIC_SECRET,
            TOPOLOGY,
        ),
        encoding="utf-8",
    )
    paths.cert.write_text(TEST_CERTIFICATE_PEM, encoding="ascii")
    paths.config.chmod(0o400)
    paths.cert.chmod(0o400)
    return paths


def test_network_modes_and_pinned_topology_are_exact_and_unexecutable() -> None:
    assert parse_network_mode("direct") is PipecatE2ENetworkMode.DIRECT
    assert parse_network_mode("relay-tls") is PipecatE2ENetworkMode.RELAY_TLS
    assert PipecatE2ENetworkMode.DIRECT.evidence_name == "direct-loopback"
    assert PipecatE2ENetworkMode.RELAY_TLS.evidence_name == "relay-tls"
    assert COTURN_IMAGE == (
        "coturn/coturn@sha256:75e9ebd1e19005bec0c7f591d29afe22f959916ac8d9c852452f27db8c789828"
    )
    assert COTURN_PLATFORM == "linux/amd64"
    assert COTURN_TURNS_URL == "turns:127.0.0.1:5349?transport=tcp"
    assert COTURN_REALM == "voice-pipecat-e2e.invalid"
    assert (COTURN_TLS_PORT, COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT) == (
        5349,
        49160,
        49169,
    )
    assert COTURN_TOPOLOGY_STATUS == "contract-unvalidated"
    assert COTURN_RELAY_MEDIA_EXECUTABLE is False


@pytest.mark.parametrize(
    "value",
    [None, True, 1, "", "DIRECT", "relay_tls", "relay-tls ", " direct", "unknown-secret"],
)
def test_network_mode_parser_rejects_nonexact_values_without_reflection(value: object) -> None:
    with pytest.raises(CoturnContractError) as captured:
        parse_network_mode(value)
    if str(value):
        assert str(value) not in str(captured.value)


def test_fixture_is_exact_secret_free_and_renderer_adds_one_validated_secret() -> None:
    fixture = COTURN_FIXTURE_PATH.read_text(encoding="utf-8")

    assert validate_coturn_fixture(fixture) == fixture
    assert "static-auth-secret" not in fixture
    assert "user=" not in fixture
    assert "cli-password" not in fixture
    assert "lt-cred-mech" not in fixture
    assert fixture.count("verbose") == 1
    assert fixture.count("log-min-level=info") == 1
    assert "max-allocate-lifetime" not in fixture
    assert "bps-capacity" not in fixture
    assert fixture.count("use-auth-secret") == 1
    assert "no-cli" not in fixture
    assert fixture.count("no-tcp-relay") == 1
    assert fixture.count("user-quota=2") == 1
    assert fixture.count("total-quota=2") == 1
    assert fixture.count("relay-threads=1") == 1
    assert (
        "# Call-level quota bound: at most two allocations; no endpoint attribution.\n" in fixture
    )
    assert all(f"{directive}\n" in fixture for directive in ("no-udp", "no-tcp"))
    assert "no-dtls" not in fixture
    assert "no-tlsv1" not in fixture
    assert "no-tlsv1_1" not in fixture
    assert "allow-loopback-peers" not in fixture
    assert "0.0.0.0" not in fixture
    rendered = render_coturn_configuration(fixture, STATIC_SECRET, TOPOLOGY)
    assert "{network_cidr}" not in rendered
    assert "# owned-network=172.28.44.0/29" in rendered
    assert "listening-ip=172.28.44.2" in rendered
    assert "relay-ip=172.28.44.2" in rendered
    assert "external-ip=127.0.0.1/172.28.44.2" in rendered
    assert rendered.endswith(f"static-auth-secret={STATIC_SECRET}\n")
    assert rendered.count("static-auth-secret=") == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value + "no-cli\n",
        lambda value: value + "no-tlsv1\n",
        lambda value: value + "no-tlsv1_1\n",
        lambda value: value + "user=unsafe:password\n",
        lambda value: value.replace("tls-listening-port=5349", "tls-listening-port=5350"),
        lambda value: value.replace("no-udp\n", ""),
        lambda value: value.replace("no-tcp-relay\n", ""),
        lambda value: value.replace("verbose\n", ""),
        lambda value: value.replace("log-min-level=info\n", ""),
        lambda value: value + "allow-loopback-peers\n",
        lambda value: value.replace("user-quota=2\n", ""),
        lambda value: value.replace("total-quota=2", "total-quota=3"),
        lambda value: value.replace("relay-threads=1", "relay-threads=2"),
        lambda value: value.replace("use-auth-secret", "lt-cred-mech"),
        lambda value: value.replace("\n", "\r\n"),
    ],
)
def test_fixture_rejects_every_unreviewed_shape(mutation: object) -> None:
    fixture = COTURN_FIXTURE_PATH.read_text(encoding="utf-8")
    assert callable(mutation)
    with pytest.raises(CoturnContractError, match="fixture is invalid"):
        validate_coturn_fixture(mutation(fixture))


@pytest.mark.parametrize(
    "secret",
    [None, b"a" * 32, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "0" * 63 + "\n"],
)
def test_static_auth_secret_is_exact_lowercase_hex_without_reflection(secret: object) -> None:
    with pytest.raises(CoturnContractError) as captured:
        validate_static_auth_secret(secret)
    if str(secret):
        assert str(secret) not in str(captured.value)


def test_owned_generated_configuration_and_parseable_ca_paths_are_exact(tmp_path: Path) -> None:
    paths = _private_paths(tmp_path)

    assert paths.private_key == paths.coturn_dir / "key.pem"
    assert (
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)
        == STATIC_SECRET
    )
    assert validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir) == paths.cert
    receipt = read_private_coturn_configuration_receipt(
        paths.config,
        expected_run_dir=paths.run_dir,
    )
    assert receipt.static_auth_secret == STATIC_SECRET
    assert receipt.topology == TOPOLOGY
    rendered_receipt = repr(receipt)
    assert rendered_receipt == "CoturnConfigurationReceipt()"
    assert STATIC_SECRET not in rendered_receipt
    assert str(receipt.topology.gateway) not in rendered_receipt
    with pytest.raises(CoturnContractError, match="path is invalid"):
        read_private_coturn_configuration(paths.cert, expected_run_dir=paths.run_dir)
    with pytest.raises(CoturnContractError, match="path is invalid"):
        validate_turn_tls_ca_file(paths.config, expected_run_dir=paths.run_dir)


@pytest.mark.parametrize("mode", [0o440, 0o444, 0o600, 0o644, 0o666])
def test_generated_configuration_requires_exact_container_readable_mode(
    tmp_path: Path,
    mode: int,
) -> None:
    paths = _private_paths(tmp_path)
    paths.config.chmod(mode)

    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_tls_ca_rejects_mutable_fake_or_symlinked_certificate(tmp_path: Path) -> None:
    paths = _private_paths(tmp_path)
    paths.cert.chmod(0o666)
    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)

    paths.cert.chmod(0o600)
    paths.cert.write_text(
        "-----BEGIN CERTIFICATE-----\ndGVzdA==\n-----END CERTIFICATE-----\n",
        encoding="ascii",
    )
    paths.cert.chmod(0o400)
    with pytest.raises(CoturnContractError, match="certificate is invalid"):
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)

    paths.cert.unlink()
    paths.cert.symlink_to(paths.config)
    with pytest.raises(CoturnContractError, match="certificate is invalid"):
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)


@pytest.mark.parametrize("mode", [0o440, 0o444, 0o600, 0o640, 0o644])
def test_tls_ca_requires_exact_owner_only_mode(tmp_path: Path, mode: int) -> None:
    paths = _private_paths(tmp_path)
    paths.cert.chmod(mode)

    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SSLKEYLOGFILE", "/tmp/secret-session-keys.log"),
        ("OPENSSL_CONF", "/tmp/unreviewed-openssl.cnf"),
        ("OPENSSL_MODULES", "/tmp/unreviewed-openssl-modules"),
    ],
)
def test_tls_ca_rejects_process_openssl_controls_without_reflection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    paths = _private_paths(tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(
        CoturnContractError,
        match=r"^Coturn TLS validation environment is unsafe$",
    ) as captured:
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)

    if value in str(captured.value):
        pytest.fail("Coturn TLS guard reflected an ambient path", pytrace=False)


def test_generated_configuration_rejects_duplicates_controls_and_symlinks(tmp_path: Path) -> None:
    paths = _private_paths(tmp_path)
    original = paths.config.read_text(encoding="utf-8")
    paths.config.chmod(0o600)
    paths.config.write_text(original + f"static-auth-secret={STATIC_SECRET}\n", encoding="utf-8")
    paths.config.chmod(0o400)
    with pytest.raises(CoturnContractError, match="generated configuration is invalid"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)

    paths.config.chmod(0o600)
    paths.config.write_text(
        original.replace(STATIC_SECRET, f"{STATIC_SECRET}\n#"),
        encoding="utf-8",
    )
    paths.config.chmod(0o400)
    with pytest.raises(CoturnContractError, match="generated configuration is invalid"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)

    paths.config.chmod(0o600)
    paths.config.unlink()
    paths.config.symlink_to(paths.cert)
    with pytest.raises(CoturnContractError, match="configuration is invalid"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_generated_configuration_read_detects_lstat_to_open_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _private_paths(tmp_path)
    real_open = coturn_contract.os.open

    def swap_then_open(path: object, flags: int) -> int:
        paths.config.unlink()
        paths.config.symlink_to(paths.cert)
        return real_open(path, flags)

    monkeypatch.setattr(coturn_contract.os, "open", swap_then_open)
    with pytest.raises(CoturnContractError, match="configuration is unavailable"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_generated_configuration_rejects_short_read_before_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _private_paths(tmp_path)
    reads = 0

    def short_read(_descriptor: int, _maximum_bytes: int) -> bytes:
        nonlocal reads
        reads += 1
        return b"x" if reads == 1 else b""

    monkeypatch.setattr(coturn_contract.os, "read", short_read)
    with pytest.raises(CoturnContractError, match="configuration is unavailable"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_generated_configuration_binds_mode_to_opened_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _private_paths(tmp_path)
    real_read = coturn_contract.os.read
    changed = False

    def chmod_then_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal changed
        if not changed:
            changed = True
            paths.config.chmod(0o600)
        return real_read(descriptor, maximum_bytes)

    monkeypatch.setattr(coturn_contract.os, "read", chmod_then_read)
    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_generated_configuration_rejects_wrong_owner_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _private_paths(tmp_path)
    real_fstat = coturn_contract.os.fstat

    def wrong_owner(descriptor: int) -> object:
        details = real_fstat(descriptor)
        return SimpleNamespace(
            st_dev=details.st_dev,
            st_ino=details.st_ino,
            st_mode=details.st_mode,
            st_nlink=details.st_nlink,
            st_size=details.st_size,
            st_uid=details.st_uid + 1,
        )

    monkeypatch.setattr(coturn_contract.os, "fstat", wrong_owner)
    with pytest.raises(CoturnContractError, match="configuration is unavailable"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


def test_generated_configuration_rejects_hardlinks(tmp_path: Path) -> None:
    paths = _private_paths(tmp_path)
    os.link(paths.config, paths.coturn_dir / "hardlinked.conf")

    with pytest.raises(CoturnContractError, match="configuration is invalid"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)


@pytest.mark.skipif(not hasattr(os, "chmod"), reason="requires POSIX permission bits")
def test_owned_secret_directories_reject_group_or_world_access(tmp_path: Path) -> None:
    paths = _private_paths(tmp_path)
    paths.coturn_dir.chmod(0o750)
    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        read_private_coturn_configuration(paths.config, expected_run_dir=paths.run_dir)
    with pytest.raises(CoturnContractError, match="permissions are unsafe"):
        validate_turn_tls_ca_file(paths.cert, expected_run_dir=paths.run_dir)


def test_path_contract_rejects_relative_mismatched_and_mount_delimiter_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(CoturnContractError, match="run directory is invalid"):
        CoturnContractPaths.for_run_dir("relay-test", Path("relative/relay-test"))
    with pytest.raises(CoturnContractError, match="run directory is invalid"):
        CoturnContractPaths.for_run_dir("relay-test", tmp_path / "different-name")
    with pytest.raises(CoturnContractError, match="path contract is invalid"):
        CoturnContractPaths.for_run_dir("relay-test", tmp_path / "comma,parent" / "relay-test")


def test_rest_credentials_match_known_vector_floor_microseconds_and_redact_repr() -> None:
    credentials = derive_turn_rest_credentials(
        static_auth_secret=STATIC_SECRET,
        voice_call_id=VOICE_CALL_ID,
        expires_at=EXPIRY,
        now=EXPIRY - timedelta(minutes=2),
    )

    assert credentials.expires_at_epoch_seconds == int(EXPIRY.timestamp()) == 1_786_622_700
    assert credentials.call_tag == "342ace7df588b4b7"
    assert credentials.username == EXPECTED_USERNAME
    assert credentials.credential == EXPECTED_CREDENTIAL
    assert (
        derive_turn_rest_username(
            voice_call_id=VOICE_CALL_ID,
            expires_at_epoch_seconds=credentials.expires_at_epoch_seconds,
        )
        == credentials.username
    )
    rendered = repr(credentials)
    assert rendered == "TurnRestCredentials()"
    assert STATIC_SECRET not in rendered
    assert str(credentials.expires_at_epoch_seconds) not in rendered
    assert credentials.call_tag not in rendered
    assert EXPECTED_USERNAME not in rendered
    assert EXPECTED_CREDENTIAL not in rendered
    with pytest.raises(TypeError, match="now"):
        derive_turn_rest_credentials(  # type: ignore[call-arg]
            static_auth_secret=STATIC_SECRET,
            voice_call_id=VOICE_CALL_ID,
            expires_at=EXPIRY,
        )


@pytest.mark.parametrize(
    ("call_id", "expiry", "now"),
    [
        ("not-a-call", EXPIRY, EXPIRY - timedelta(seconds=1)),
        ("50000000-0000-4000-8000-00000000000A", EXPIRY, EXPIRY - timedelta(seconds=1)),
        (VOICE_CALL_ID, EXPIRY.replace(tzinfo=None), EXPIRY - timedelta(seconds=1)),
        (VOICE_CALL_ID, EXPIRY, EXPIRY),
        (VOICE_CALL_ID, EXPIRY, EXPIRY + timedelta(seconds=1)),
    ],
)
def test_rest_credentials_reject_malformed_or_expired_claim_material(
    call_id: object,
    expiry: object,
    now: object,
) -> None:
    with pytest.raises(CoturnContractError):
        derive_turn_rest_credentials(
            static_auth_secret=STATIC_SECRET,
            voice_call_id=call_id,
            expires_at=expiry,
            now=now,
        )


@pytest.mark.parametrize(
    ("call_id", "expiry"),
    [
        (None, 1_786_622_700),
        ("not-a-call", 1_786_622_700),
        (VOICE_CALL_ID, None),
        (VOICE_CALL_ID, True),
        (VOICE_CALL_ID, 0),
        (VOICE_CALL_ID, -1),
        (VOICE_CALL_ID, 1 << 63),
    ],
)
def test_rest_username_rejects_invalid_identity_without_reflection(
    call_id: object,
    expiry: object,
) -> None:
    with pytest.raises(
        CoturnContractError,
        match=r"^TURN (?:voice call scope|credential expiry) is invalid$",
    ) as captured:
        derive_turn_rest_username(
            voice_call_id=call_id,
            expires_at_epoch_seconds=expiry,
        )

    assert str(call_id) not in str(captured.value)
    assert str(expiry) not in str(captured.value)


def test_checkpoint_a_core_makes_no_media_or_forced_relay_claim() -> None:
    contract = {
        "mode": PipecatE2ENetworkMode.RELAY_TLS.value,
        "topology_status": COTURN_TOPOLOGY_STATUS,
        "relay_media_executable": COTURN_RELAY_MEDIA_EXECUTABLE,
    }
    rendered = json.dumps(contract, sort_keys=True)

    assert contract["topology_status"] == "contract-unvalidated"
    assert contract["relay_media_executable"] is False
    assert all(word not in rendered for word in ("media_passed", "forced_relay", "bytes"))


def test_core_import_is_stdlib_only_and_does_not_load_runtime_evidence_or_murmur() -> None:
    code = r"""
import json
import sys
import scripts.voice_pipecat_e2e_coturn as core

print(json.dumps({
    "core": core.__name__ in sys.modules,
    "runtime": "scripts.voice_pipecat_e2e_coturn_runtime" in sys.modules,
    "evidence": "scripts.voice_pipecat_e2e_coturn_evidence" in sys.modules,
    "murmur": any(name == "murmur" or name.startswith("murmur.") for name in sys.modules),
    "ssl": "ssl" in sys.modules,
}, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "core": True,
        "runtime": False,
        "evidence": False,
        "murmur": False,
        "ssl": False,
    }


def test_core_public_surface_is_exact() -> None:
    assert coturn_contract.__all__ == [
        "COTURN_FIXTURE_PATH",
        "COTURN_IMAGE",
        "COTURN_PLATFORM",
        "COTURN_REALM",
        "COTURN_RELAY_MAX_PORT",
        "COTURN_RELAY_MEDIA_EXECUTABLE",
        "COTURN_RELAY_MIN_PORT",
        "COTURN_TLS_PORT",
        "COTURN_TOPOLOGY_STATUS",
        "COTURN_TURNS_URL",
        "CoturnBridgeTopology",
        "CoturnConfigurationReceipt",
        "CoturnContractError",
        "CoturnContractPaths",
        "PipecatE2ENetworkMode",
        "TurnRestCredentials",
        "derive_turn_rest_credentials",
        "derive_turn_rest_username",
        "parse_network_mode",
        "read_private_coturn_configuration",
        "read_private_coturn_configuration_receipt",
        "render_coturn_configuration",
        "validate_coturn_fixture",
        "validate_static_auth_secret",
        "validate_turn_tls_ca_file",
    ]
