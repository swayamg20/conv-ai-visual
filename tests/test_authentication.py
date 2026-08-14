"""Provider-free regressions for the legacy application's Firebase boundary."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from murmur.api import authentication
from murmur.api import dependencies as api_dependencies
from murmur.api.errors import ApiError, api_error_handler
from murmur.persistence.database import get_session
from murmur.persistence.models import UserModel
from murmur.persistence.repositories.identities import UserRepo
from sqlmodel import select
from starlette.requests import Request

_MISSING = object()
_INVALID_FIREBASE_NAMES = (
    pytest.param(_MISSING, id="missing"),
    pytest.param(None, id="null"),
    pytest.param(123, id="non-string"),
    pytest.param("control\nname", id="control"),
    pytest.param("x" * 257, id="oversized"),
)


def _request_with_headers(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/auth/me",
            "raw_path": b"/api/auth/me",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _request(token: str = "browser-id-token") -> Request:
    return _request_with_headers([(b"authorization", f"Bearer {token}".encode())])


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "uid": "firebase-user",
        "email": "user@example.test",
        "email_verified": False,
        "name": "Firebase user",
    }
    claims.update(overrides)
    return claims


def _user_ids() -> list[str]:
    with get_session() as session:
        return list(session.exec(select(UserModel.id).order_by(UserModel.id)).all())


@pytest.mark.parametrize(
    ("service_account_path", "project_id", "expected_credential"),
    [
        ("/secure/firebase.json", "firebase-project", True),
        (None, "firebase-project", False),
        (None, None, False),
    ],
)
def test_firebase_initialization_bounds_http_in_every_configuration_branch(
    service_account_path: str | None,
    project_id: str | None,
    expected_credential: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    credential = object()
    certificate = Mock(return_value=credential)
    initialize_app = Mock(return_value=app)
    monkeypatch.setattr(authentication, "_firebase_app", None)
    monkeypatch.setattr(authentication, "_firebase_init_attempted", False)
    monkeypatch.setattr(
        authentication.config,
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        service_account_path,
    )
    monkeypatch.setattr(authentication.config, "FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setattr(authentication.credentials, "Certificate", certificate)
    monkeypatch.setattr(authentication.firebase_admin, "initialize_app", initialize_app)

    assert authentication._ensure_firebase() is app

    expected_options: dict[str, object] = {"httpTimeout": 2.0}
    if project_id:
        expected_options["projectId"] = project_id
    if expected_credential:
        certificate.assert_called_once_with(service_account_path)
        initialize_app.assert_called_once_with(credential, options=expected_options)
    else:
        certificate.assert_not_called()
        initialize_app.assert_called_once_with(options=expected_options)


def test_concurrent_firebase_initialization_is_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    initialization_entered = Event()
    initialization_release = Event()
    second_call_started = Event()

    def initialize_app(*_args: object, **_kwargs: object) -> object:
        initialization_entered.set()
        assert initialization_release.wait(timeout=5)
        return app

    def ensure_after_start() -> object:
        second_call_started.set()
        return authentication._ensure_firebase()

    initializer = Mock(side_effect=initialize_app)
    monkeypatch.setattr(authentication, "_firebase_app", None)
    monkeypatch.setattr(authentication, "_firebase_init_attempted", False)
    monkeypatch.setattr(authentication.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    monkeypatch.setattr(authentication.config, "FIREBASE_PROJECT_ID", "firebase-project")
    monkeypatch.setattr(authentication.firebase_admin, "initialize_app", initializer)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(authentication._ensure_firebase)
        assert initialization_entered.wait(timeout=5)
        second = executor.submit(ensure_after_start)
        assert second_call_started.wait(timeout=5)
        initialization_release.set()
        assert first.result(timeout=5) is app
        assert second.result(timeout=5) is app

    initializer.assert_called_once_with(
        options={"httpTimeout": 2.0, "projectId": "firebase-project"}
    )


def test_failed_firebase_initialization_is_fixed_unavailable_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "firebase-init-secret-detail"
    initializer = Mock(side_effect=RuntimeError(secret))
    monkeypatch.setattr(authentication, "_firebase_app", None)
    monkeypatch.setattr(authentication, "_firebase_init_attempted", False)
    monkeypatch.setattr(authentication.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    monkeypatch.setattr(authentication.config, "FIREBASE_PROJECT_ID", "firebase-project")
    monkeypatch.setattr(authentication.firebase_admin, "initialize_app", initializer)
    caplog.set_level(logging.WARNING, logger="murmur.api.authentication")

    for _attempt in range(2):
        with pytest.raises(authentication.FirebaseAuthenticationUnavailable) as captured:
            authentication._ensure_firebase()
        assert str(captured.value) == "Authentication is unavailable"
        assert captured.value.__cause__ is None
        assert captured.value.__suppress_context__ is True

    initializer.assert_called_once_with(
        options={"httpTimeout": 2.0, "projectId": "firebase-project"}
    )
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)


def test_firebase_verification_checks_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    claims = _claims()
    verifier = Mock(return_value=claims)
    monkeypatch.setattr(authentication, "_ensure_firebase", lambda: app)
    monkeypatch.setattr(firebase_auth, "verify_id_token", verifier)

    assert authentication.verify_firebase_token("browser-id-token") == claims
    verifier.assert_called_once_with(
        "browser-id-token",
        app=app,
        check_revoked=True,
    )


@pytest.mark.parametrize(
    "rejection",
    [
        pytest.param(firebase_auth.InvalidIdTokenError("invalid"), id="invalid"),
        pytest.param(
            firebase_auth.ExpiredIdTokenError("expired", ValueError("expired")),
            id="expired",
        ),
        pytest.param(firebase_auth.RevokedIdTokenError("revoked"), id="revoked"),
        pytest.param(firebase_auth.UserDisabledError("disabled"), id="disabled"),
        pytest.param(firebase_auth.UserNotFoundError("not-found"), id="user-not-found"),
    ],
)
def test_firebase_credential_rejections_return_none(
    rejection: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authentication, "_ensure_firebase", lambda: object())
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        Mock(side_effect=rejection),
    )

    assert authentication.verify_firebase_token("browser-id-token") is None


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            firebase_auth.CertificateFetchError(
                "certificate-secret",
                RuntimeError("certificate-cause-secret"),
            ),
            id="certificate-fetch",
        ),
        pytest.param(
            firebase_auth.ConfigurationNotFoundError("configuration-secret"),
            id="configuration-not-found",
        ),
        pytest.param(
            firebase_exceptions.DeadlineExceededError("deadline-secret"),
            id="deadline",
        ),
        pytest.param(
            firebase_exceptions.UnavailableError("network-secret"),
            id="network",
        ),
        pytest.param(
            firebase_exceptions.InternalError("service-secret"),
            id="service",
        ),
        pytest.param(ValueError("sdk-configuration-secret"), id="sdk-value-error"),
        pytest.param(RuntimeError("unexpected-secret"), id="unexpected"),
    ],
)
def test_firebase_availability_failures_raise_fixed_unavailable(
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(authentication, "_ensure_firebase", lambda: object())
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        Mock(side_effect=failure),
    )
    caplog.set_level(logging.WARNING, logger="murmur.api.authentication")

    with pytest.raises(authentication.FirebaseAuthenticationUnavailable) as captured:
        authentication.verify_firebase_token("browser-id-token")

    assert str(captured.value) == "Authentication is unavailable"
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert str(failure) not in logs
    assert "browser-id-token" not in logs


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param([], id="missing"),
        pytest.param([(b"authorization", b"Basic token")], id="wrong-scheme"),
        pytest.param([(b"authorization", b"bearer token")], id="wrong-case"),
        pytest.param([(b"authorization", b"Bearer ")], id="empty"),
        pytest.param([(b"authorization", b"Bearer leading token")], id="space"),
        pytest.param([(b"authorization", b"Bearer token\t")], id="tab"),
        pytest.param([(b"authorization", b"Bearer token\x7f")], id="non-printable"),
        pytest.param(
            [(b"authorization", b"Bearer " + (b"x" * 16_001))],
            id="oversized",
        ),
        pytest.param(
            [
                (b"authorization", b"Bearer first-token"),
                (b"authorization", b"Bearer second-token"),
            ],
            id="duplicate",
        ),
    ],
)
def test_invalid_bearer_is_rejected_before_firebase(
    headers: list[tuple[bytes, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = Mock(side_effect=AssertionError("Firebase must not receive an invalid bearer"))
    monkeypatch.setattr(authentication, "verify_firebase_token", verifier)

    assert authentication.get_current_user(_request_with_headers(headers)) is None
    verifier.assert_not_called()


def test_unknown_unverified_email_is_denied_without_inheriting_or_creating_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = UserRepo.get_or_create(
        uid="legacy-victim",
        email="victim@example.test",
        name="Victim",
    )
    monkeypatch.setattr(
        authentication,
        "verify_firebase_token",
        lambda _token: _claims(
            uid="firebase-attacker",
            email="victim@example.test",
            email_verified=False,
            name="Unverified caller",
        ),
    )

    assert authentication.get_current_user(_request()) is None
    assert UserRepo.get_by_id("firebase-attacker") is None
    assert UserRepo.get_by_email("victim@example.test") == victim
    assert _user_ids() == ["legacy-victim"]


@pytest.mark.parametrize(
    ("email", "email_verified"),
    [
        ("victim@example.test", False),
        ("victim@example.test", "true"),
        ("victim@example.test", _MISSING),
        ("malformed-email", True),
        ("two@@example.test", True),
        (" spaced@example.test", True),
        (None, True),
    ],
)
def test_unknown_unverified_or_malformed_email_performs_only_exact_uid_lookup(
    email: object,
    email_verified: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _claims(email=email)
    if email_verified is _MISSING:
        claims.pop("email_verified")
    else:
        claims["email_verified"] = email_verified
    lookup = Mock(return_value=None)
    exact_create = Mock(side_effect=AssertionError("placeholder creation must not run"))
    email_link = Mock(side_effect=AssertionError("email linking must not run"))
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: claims)
    monkeypatch.setattr(UserRepo, "get_by_id", lookup)
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact_create)
    monkeypatch.setattr(UserRepo, "get_or_create", email_link)

    assert authentication.get_current_user(_request()) is None
    lookup.assert_called_once_with("firebase-user")
    exact_create.assert_not_called()
    email_link.assert_not_called()


def test_verified_email_preserves_intended_legacy_identity_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = UserRepo.get_or_create(
        uid="legacy-user",
        email="verified@example.test",
        name="Legacy profile",
    )
    monkeypatch.setattr(
        authentication,
        "verify_firebase_token",
        lambda _token: _claims(
            uid="firebase-verified",
            email="verified@example.test",
            email_verified=True,
            name="Verified profile",
        ),
    )

    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": legacy.id,
        "email": "verified@example.test",
        "name": "Verified profile",
    }
    assert UserRepo.get_by_id("firebase-verified") is None
    assert _user_ids() == ["legacy-user"]


@pytest.mark.parametrize("name", _INVALID_FIREBASE_NAMES)
def test_invalid_verified_name_preserves_existing_exact_uid_display_name(
    name: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = UserRepo.get_or_create(
        uid="firebase-verified-existing-name",
        email="verified-existing-name@example.test",
        name="Stored exact profile",
    )
    claims = _claims(
        uid=existing.id,
        email=existing.email,
        email_verified=True,
        name=name,
    )
    if name is _MISSING:
        claims.pop("name")
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: claims)

    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": existing.id,
        "email": existing.email,
        "name": "Stored exact profile",
    }
    persisted = UserRepo.get_by_id(existing.id)
    assert persisted is not None
    assert persisted.name == "Stored exact profile"
    assert _user_ids() == [existing.id]


@pytest.mark.parametrize("name", _INVALID_FIREBASE_NAMES)
def test_invalid_verified_name_preserves_legacy_email_link_display_name(
    name: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = UserRepo.get_or_create(
        uid="legacy-verified-existing-name",
        email="verified-legacy-name@example.test",
        name="Stored legacy profile",
    )
    claims = _claims(
        uid="firebase-linking-without-valid-name",
        email=legacy.email,
        email_verified=True,
        name=name,
    )
    if name is _MISSING:
        claims.pop("name")
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: claims)

    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": legacy.id,
        "email": legacy.email,
        "name": "Stored legacy profile",
    }
    assert UserRepo.get_by_id("firebase-linking-without-valid-name") is None
    persisted = UserRepo.get_by_id(legacy.id)
    assert persisted is not None
    assert persisted.name == "Stored legacy profile"
    assert _user_ids() == [legacy.id]


@pytest.mark.parametrize(
    ("email", "email_verified"),
    [
        ("victim@example.test", False),
        ("victim@example.test", "true"),
        ("victim@example.test", _MISSING),
        ("malformed-email", True),
    ],
)
def test_existing_exact_uid_accepts_unverified_or_malformed_email_without_mutation(
    email: object,
    email_verified: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = UserRepo.get_or_create(
        uid="firebase-existing",
        email="verified-existing@example.test",
        name="Original profile",
    )
    claims = _claims(
        uid=existing.id,
        email=email,
        name="Untrusted replacement",
    )
    if email_verified is _MISSING:
        claims.pop("email_verified")
    else:
        claims["email_verified"] = email_verified
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: claims)

    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": existing.id,
        "email": "verified-existing@example.test",
        "name": "Original profile",
    }
    persisted = UserRepo.get_by_id(existing.id)
    assert persisted is not None
    assert persisted.email == "verified-existing@example.test"
    assert persisted.name == "Original profile"
    assert _user_ids() == [existing.id]


def test_denied_uid_can_later_verify_and_link_without_placeholder_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = UserRepo.get_or_create(
        uid="legacy-later-verified",
        email="later-verified@example.test",
        name="Legacy profile",
    )
    claims = _claims(
        uid="firebase-later-verified",
        email="later-verified@example.test",
        email_verified=False,
        name="Firebase profile",
    )
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: claims)

    assert authentication.get_current_user(_request()) is None
    assert UserRepo.get_by_id("firebase-later-verified") is None
    assert _user_ids() == [legacy.id]

    claims["email_verified"] = True
    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": legacy.id,
        "email": "later-verified@example.test",
        "name": "Firebase profile",
    }
    assert UserRepo.get_by_id("firebase-later-verified") is None
    assert _user_ids() == [legacy.id]


def test_concurrent_unknown_unverified_requests_never_create_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = 8
    barrier = Barrier(workers)
    exact_create = Mock(side_effect=AssertionError("placeholder creation must not run"))
    email_link = Mock(side_effect=AssertionError("email linking must not run"))

    def verify(_token: str) -> dict[str, object]:
        barrier.wait(timeout=5)
        return _claims(uid="firebase-concurrent-denied")

    monkeypatch.setattr(authentication, "verify_firebase_token", verify)
    monkeypatch.setattr(UserRepo, "get_by_id", lambda _uid: None)
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact_create)
    monkeypatch.setattr(UserRepo, "get_or_create", email_link)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(lambda _index: authentication.get_current_user(_request()), range(workers))
        )

    assert results == [None] * workers
    exact_create.assert_not_called()
    email_link.assert_not_called()
    assert _user_ids() == []


def test_mismatched_exact_uid_lookup_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: _claims())
    monkeypatch.setattr(
        UserRepo,
        "get_by_id",
        Mock(
            return_value=SimpleNamespace(
                id="different-local-user",
                email="victim@example.test",
                name="Victim",
            )
        ),
    )

    assert authentication.get_current_user(_request()) is None


def test_existing_uid_cannot_be_relinked_by_a_mismatched_verified_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        authentication,
        "verify_firebase_token",
        lambda _token: _claims(
            email="verified@example.test",
            email_verified=True,
        ),
    )
    monkeypatch.setattr(
        UserRepo,
        "get_by_id",
        Mock(return_value=SimpleNamespace(id="firebase-user")),
    )
    monkeypatch.setattr(
        UserRepo,
        "get_or_create",
        Mock(
            return_value=SimpleNamespace(
                id="different-local-user",
                email="verified@example.test",
                name="Different user",
            )
        ),
    )

    assert authentication.get_current_user(_request()) is None


@pytest.mark.parametrize(
    "uid",
    [None, 123, "", " leading", "trailing ", "control\nuid", "x" * 129],
)
def test_invalid_uid_fails_before_any_repository_access(
    uid: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = Mock(side_effect=AssertionError("invalid UID must not be queried"))
    exact_create = Mock(side_effect=AssertionError("invalid UID must not be provisioned"))
    email_link = Mock(side_effect=AssertionError("invalid UID must not be provisioned"))
    monkeypatch.setattr(
        authentication,
        "verify_firebase_token",
        lambda _token: _claims(uid=uid),
    )
    monkeypatch.setattr(UserRepo, "get_by_id", lookup)
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact_create)
    monkeypatch.setattr(UserRepo, "get_or_create", email_link)

    assert authentication.get_current_user(_request()) is None
    lookup.assert_not_called()
    exact_create.assert_not_called()
    email_link.assert_not_called()


@pytest.mark.parametrize("name", [123, "control\nname", "x" * 257])
def test_invalid_name_cannot_mutate_an_existing_unverified_identity(
    name: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(
        id="firebase-user",
        email="stored@example.test",
        name="Stored name",
    )
    lookup = Mock(return_value=existing)
    email_link = Mock(side_effect=AssertionError("profile mutation must not run"))
    monkeypatch.setattr(
        authentication,
        "verify_firebase_token",
        lambda _token: _claims(name=name),
    )
    monkeypatch.setattr(UserRepo, "get_by_id", lookup)
    monkeypatch.setattr(UserRepo, "get_or_create", email_link)

    resolved = authentication.get_current_user(_request())

    assert resolved == {
        "id": "firebase-user",
        "email": "stored@example.test",
        "name": "Stored name",
    }
    lookup.assert_called_once_with("firebase-user")
    email_link.assert_not_called()


def test_verification_failure_does_not_log_raw_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "provider-secret-detail"
    monkeypatch.setattr(authentication, "_ensure_firebase", lambda: object())
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        Mock(side_effect=RuntimeError(secret)),
    )
    caplog.set_level(logging.WARNING, logger="murmur.api.authentication")

    with pytest.raises(authentication.FirebaseAuthenticationUnavailable) as captured:
        authentication.verify_firebase_token("browser-id-token")

    assert str(captured.value) == "Authentication is unavailable"
    assert captured.value.__cause__ is None
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)


def test_identity_lookup_failure_does_not_log_raw_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "database-secret-detail"
    monkeypatch.setattr(authentication, "verify_firebase_token", lambda _token: _claims())
    monkeypatch.setattr(
        UserRepo,
        "get_by_id",
        Mock(side_effect=RuntimeError(secret)),
    )
    caplog.set_level(logging.WARNING, logger="murmur.api.authentication")

    with pytest.raises(authentication.FirebaseAuthenticationUnavailable) as captured:
        authentication.get_current_user(_request())

    assert str(captured.value) == "Authentication is unavailable"
    assert captured.value.__cause__ is None
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("resolved_user", "expected_status", "expected_message"),
    [
        (None, 401, "Not authenticated"),
        (
            authentication.FirebaseAuthenticationUnavailable("upstream-secret-must-not-escape"),
            503,
            "Authentication is unavailable",
        ),
    ],
)
def test_authenticated_dependency_maps_fixed_401_and_503(
    resolved_user: object,
    expected_status: int,
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(resolved_user, Exception):
        resolver = Mock(side_effect=resolved_user)
    else:
        resolver = Mock(return_value=resolved_user)
    monkeypatch.setattr(api_dependencies, "get_current_user", resolver)

    with pytest.raises(ApiError) as captured:
        api_dependencies.get_authenticated_user(_request())

    assert captured.value.status_code == expected_status
    assert captured.value.message == expected_message
    assert captured.value.__cause__ is None
    assert "upstream-secret" not in captured.value.message


@pytest.mark.parametrize(
    ("resolved_user", "expected_status", "expected_body"),
    [
        (None, 401, {"error": "Not authenticated"}),
        (
            authentication.FirebaseAuthenticationUnavailable("upstream-secret-must-not-escape"),
            503,
            {"error": "Authentication is unavailable"},
        ),
    ],
)
def test_current_user_dependency_returns_fixed_http_401_and_503(
    resolved_user: object,
    expected_status: int,
    expected_body: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(resolved_user, Exception):
        resolver = Mock(side_effect=resolved_user)
    else:
        resolver = Mock(return_value=resolved_user)
    monkeypatch.setattr(api_dependencies, "get_current_user", resolver)
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)

    @app.get("/protected")
    async def protected(_user: api_dependencies.CurrentUserDependency) -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer browser-id-token"},
        )

    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert "upstream-secret" not in response.text


@pytest.mark.parametrize(
    ("resolved_user", "expected_status", "expected_detail"),
    [
        (None, 401, "Not authenticated"),
        (
            authentication.FirebaseAuthenticationUnavailable("upstream-secret-must-not-escape"),
            503,
            "Authentication is unavailable",
        ),
    ],
)
def test_require_auth_maps_fixed_401_and_503(
    resolved_user: object,
    expected_status: int,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(resolved_user, Exception):
        resolver = Mock(side_effect=resolved_user)
    else:
        resolver = Mock(return_value=resolved_user)
    monkeypatch.setattr(authentication, "get_current_user", resolver)

    with pytest.raises(HTTPException) as captured:
        authentication.require_auth(_request())

    assert captured.value.status_code == expected_status
    assert captured.value.detail == expected_detail
    assert captured.value.__cause__ is None
    assert "upstream-secret" not in str(captured.value.detail)
