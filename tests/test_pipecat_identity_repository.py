"""Real-database regressions for Pipecat's exact-Firebase-UID provisioning seam."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from murmur.persistence.database import create_database_engine, get_session
from murmur.persistence.models import UserModel
from murmur.persistence.repositories import identities
from murmur.persistence.repositories.identities import UserRepo
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select


def _placeholder(uid: str) -> str:
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()
    return f"firebase-unverified-{digest}@murmur.invalid"


def _all_users() -> list[UserModel]:
    with get_session() as session:
        return list(session.exec(select(UserModel).order_by(UserModel.id)).all())


def test_exact_uid_never_inherits_a_victim_legacy_email_or_identity() -> None:
    victim = UserRepo.get_or_create(
        uid="legacy-victim",
        email="victim@example.test",
        name="Victim",
    )

    created = UserRepo.get_or_create_exact_uid(
        uid="firebase-attacker",
        name="Unverified caller",
    )

    assert created.id == "firebase-attacker"
    assert created.email == _placeholder("firebase-attacker")
    assert created.id != victim.id
    assert created.email != victim.email
    assert UserRepo.get_by_email("victim@example.test") == victim
    assert [user.id for user in _all_users()] == ["firebase-attacker", "legacy-victim"]


def test_exact_uid_does_not_mutate_an_existing_uid_email_or_profile() -> None:
    existing = UserRepo.get_or_create(
        uid="firebase-existing",
        email="verified-existing@example.test",
        name="Original name",
    )

    returned = UserRepo.get_or_create_exact_uid(
        uid="firebase-existing",
        name="Untrusted replacement name",
    )

    assert returned.id == existing.id
    assert returned.email == "verified-existing@example.test"
    assert returned.name == "Original name"
    persisted = UserRepo.get_by_id("firebase-existing")
    assert persisted is not None
    assert persisted.email == "verified-existing@example.test"
    assert persisted.name == "Original name"
    assert len(_all_users()) == 1


def test_repeated_exact_uid_calls_are_idempotent_and_keep_the_first_row() -> None:
    first = UserRepo.get_or_create_exact_uid(uid="firebase-repeat", name="First name")
    second = UserRepo.get_or_create_exact_uid(uid="firebase-repeat", name="Second name")

    assert first.id == second.id == "firebase-repeat"
    assert first.email == second.email == _placeholder("firebase-repeat")
    assert second.name == "First name"
    assert len(_all_users()) == 1


def test_verified_unused_email_replaces_placeholder_on_the_same_exact_uid() -> None:
    uid = "firebase-later-verified"
    placeholder = _placeholder(uid)
    initial = UserRepo.get_or_create_exact_uid(uid=uid, name="Initial profile")

    verified = UserRepo.get_or_create(
        uid=uid,
        email="later-verified@example.test",
        name="Verified profile",
    )

    assert initial.id == verified.id == uid
    assert initial.email == placeholder
    assert verified.email == "later-verified@example.test"
    assert verified.name == "Verified profile"
    assert UserRepo.get_by_email(placeholder) is None
    assert [user.id for user in _all_users()] == [uid]


def test_none_name_preserves_existing_exact_uid_display_name() -> None:
    existing = UserRepo.get_or_create(
        uid="firebase-existing-name",
        email="old-address@example.test",
        name="Stored exact profile",
    )

    returned = UserRepo.get_or_create(
        uid=existing.id,
        email="verified-address@example.test",
        name=None,
    )

    assert returned.id == existing.id
    assert returned.email == "verified-address@example.test"
    assert returned.name == "Stored exact profile"
    persisted = UserRepo.get_by_id(existing.id)
    assert persisted is not None
    assert persisted.email == "verified-address@example.test"
    assert persisted.name == "Stored exact profile"


def test_none_name_preserves_legacy_email_link_display_name() -> None:
    legacy = UserRepo.get_or_create(
        uid="legacy-existing-name",
        email="legacy-name@example.test",
        name="Stored legacy profile",
    )

    returned = UserRepo.get_or_create(
        uid="firebase-linking-name",
        email="legacy-name@example.test",
        name=None,
    )

    assert returned.id == legacy.id
    assert returned.name == "Stored legacy profile"
    assert UserRepo.get_by_id("firebase-linking-name") is None
    persisted = UserRepo.get_by_id(legacy.id)
    assert persisted is not None
    assert persisted.name == "Stored legacy profile"


def test_none_name_is_retained_for_a_genuinely_new_user() -> None:
    created = UserRepo.get_or_create(
        uid="firebase-new-without-name",
        email="new-without-name@example.test",
        name=None,
    )

    assert created.id == "firebase-new-without-name"
    assert created.name is None


def test_verified_email_collision_fails_closed_without_relinking_either_row() -> None:
    uid = "firebase-verified-collision"
    placeholder = _placeholder(uid)
    exact = UserRepo.get_or_create_exact_uid(uid=uid, name="Exact profile")
    victim = UserRepo.get_or_create(
        uid="legacy-verified-victim",
        email="already-linked@example.test",
        name="Legacy profile",
    )

    with pytest.raises(IntegrityError):
        UserRepo.get_or_create(
            uid=uid,
            email="already-linked@example.test",
            name="Untrusted replacement",
        )

    persisted_exact = UserRepo.get_by_id(exact.id)
    persisted_victim = UserRepo.get_by_id(victim.id)
    assert persisted_exact is not None
    assert persisted_exact.email == placeholder
    assert persisted_exact.name == "Exact profile"
    assert persisted_victim is not None
    assert persisted_victim.email == "already-linked@example.test"
    assert persisted_victim.name == "Legacy profile"
    assert {user.id for user in _all_users()} == {uid, "legacy-verified-victim"}


def test_two_exact_uids_receive_distinct_local_identities_and_placeholders() -> None:
    first = UserRepo.get_or_create_exact_uid(uid="firebase-one", name=None)
    second = UserRepo.get_or_create_exact_uid(uid="firebase-two", name=None)

    assert first.id == "firebase-one"
    assert second.id == "firebase-two"
    assert first.id != second.id
    assert first.email == _placeholder(first.id)
    assert second.email == _placeholder(second.id)
    assert first.email != second.email
    assert {user.id for user in _all_users()} == {"firebase-one", "firebase-two"}


def test_placeholder_collision_fails_closed_without_mutating_or_linking_victim() -> None:
    target_uid = "firebase-collision-target"
    victim = UserRepo.get_or_create(
        uid="legacy-placeholder-victim",
        email=_placeholder(target_uid),
        name="Victim profile",
    )

    with pytest.raises(IntegrityError):
        UserRepo.get_or_create_exact_uid(uid=target_uid, name="Attacker profile")

    assert UserRepo.get_by_id(target_uid) is None
    persisted_victim = UserRepo.get_by_id(victim.id)
    assert persisted_victim is not None
    assert persisted_victim.email == _placeholder(target_uid)
    assert persisted_victim.name == "Victim profile"
    assert [user.id for user in _all_users()] == ["legacy-placeholder-victim"]


@pytest.mark.parametrize(
    "uid",
    ["", " leading", "trailing ", "control\nuid", "x" * 129],
)
def test_invalid_exact_uid_fails_before_any_database_identity_is_created(uid: str) -> None:
    with pytest.raises(ValueError, match="Firebase UID"):
        UserRepo.get_or_create_exact_uid(uid=uid, name="Invalid")

    assert _all_users() == []


def test_concurrent_same_uid_calls_converge_to_one_exact_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "pipecat-identity-concurrency.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(identities, "get_session", lambda: Session(engine))
    workers = 8
    barrier = Barrier(workers)

    def provision(index: int) -> tuple[str, str, str | None]:
        barrier.wait()
        user = UserRepo.get_or_create_exact_uid(
            uid="firebase-concurrent",
            name=f"Caller {index}",
        )
        return user.id, user.email, user.name

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(provision, range(workers)))

        assert {result[0] for result in results} == {"firebase-concurrent"}
        assert {result[1] for result in results} == {_placeholder("firebase-concurrent")}
        with Session(engine) as session:
            users = list(session.exec(select(UserModel)).all())
        assert len(users) == 1
        assert users[0].id == "firebase-concurrent"
        assert users[0].email == _placeholder("firebase-concurrent")
    finally:
        engine.dispose()
