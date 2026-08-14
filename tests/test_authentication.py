"""Provider-free regressions for the legacy application's Firebase boundary."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from firebase_admin import auth as firebase_auth
from murmur.api import authentication
from murmur.persistence.database import get_session
from murmur.persistence.models import UserModel
from murmur.persistence.repositories.identities import UserRepo
from sqlmodel import select
from starlette.requests import Request

_MISSING = object()


def _request(token: str = "browser-id-token") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/auth/me",
            "raw_path": b"/api/auth/me",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


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

    assert authentication.verify_firebase_token("browser-id-token") is None
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

    assert authentication.get_current_user(_request()) is None
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)
