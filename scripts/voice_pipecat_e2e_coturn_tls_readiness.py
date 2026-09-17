"""Exact bounded parser for the fixed Coturn OpenSSL readiness probe."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_coturn_host import CommandResult

_MAX_READINESS_BYTES = 65_536
_READINESS_TOKEN = object()
_TLS13_CIPHERS = {
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
}
_TLS12_CIPHERS = {
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-RSA-CHACHA20-POLY1305",
}


class OpenSslReadinessReceipt:
    """Sanitized proof of the exact private-CA readiness transcript."""

    __slots__ = ("_cipher_suite", "_protocol")

    def __init__(self, token: object, *, protocol: str, cipher_suite: str) -> None:
        if token is not _READINESS_TOKEN:
            raise TypeError("OpenSSL readiness receipt is factory-owned")
        self._protocol = protocol
        self._cipher_suite = cipher_suite

    @property
    def protocol(self) -> str:
        return self._protocol

    @property
    def cipher_suite(self) -> str:
        return self._cipher_suite

    def __repr__(self) -> str:
        return "OpenSslReadinessReceipt()"


def parse_openssl_readiness_result(result: object) -> OpenSslReadinessReceipt | None:
    """Return only allowlisted protocol/cipher evidence from ``s_client -brief``."""

    if (
        not isinstance(result, CommandResult)
        or result.returncode != 0
        or result.stdout != b""
        or not 1 <= len(result.stderr) <= _MAX_READINESS_BYTES
        or not result.stderr.endswith(b"\n")
        or b"\r" in result.stderr
        or any(byte not in {9, 10, 13} and not 32 <= byte <= 126 for byte in result.stderr)
    ):
        return None
    try:
        lines = result.stderr.decode("ascii").splitlines()
    except UnicodeError:
        return None
    if len(lines) not in {9, 10} or lines[0] != "CONNECTION ESTABLISHED":
        return None
    protocol_label = "Protocol version: "
    cipher_label = "Ciphersuite: "
    if not lines[1].startswith(protocol_label) or not lines[2].startswith(cipher_label):
        return None
    protocol = lines[1][len(protocol_label) :]
    cipher_suite = lines[2][len(cipher_label) :]
    common = [
        "Peer certificate: CN = murmur-coturn-loopback.invalid",
        "Hash used: SHA256",
        "Signature type: RSA-PSS",
        "Verification: OK",
    ]
    if lines[3:7] != common or lines[-2:] != [
        "Server Temp Key: X25519, 253 bits",
        "DONE",
    ]:
        return None
    if protocol == "TLSv1.3":
        valid = len(lines) == 9 and cipher_suite in _TLS13_CIPHERS
    elif protocol == "TLSv1.2":
        valid = (
            len(lines) == 10
            and lines[7] == "Supported Elliptic Curve Point Formats: uncompressed"
            and cipher_suite in _TLS12_CIPHERS
        )
    else:
        valid = False
    if not valid:
        return None
    return OpenSslReadinessReceipt(
        _READINESS_TOKEN,
        protocol=protocol,
        cipher_suite=cipher_suite,
    )


__all__ = [
    "OpenSslReadinessReceipt",
    "parse_openssl_readiness_result",
]
