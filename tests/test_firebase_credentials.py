"""Provider-free tests for secret-safe shared Firebase credential loading."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from murmur.api import authentication, firebase_credentials, pipecat_application


def test_inline_service_account_json_takes_precedence_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "private-key-must-not-escape"
    certificate = Mock(return_value=object())
    monkeypatch.setattr(
        firebase_credentials.config,
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        '{"type":"service_account","private_key":"' + secret + '"}',
    )
    monkeypatch.setattr(
        firebase_credentials.config,
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        "/must/not/be/read.json",
    )
    monkeypatch.setattr(firebase_credentials.credentials, "Certificate", certificate)

    credential = firebase_credentials.create_firebase_credential()

    assert credential is certificate.return_value
    certificate.assert_called_once_with({"type": "service_account", "private_key": secret})
    assert secret not in caplog.text


@pytest.mark.parametrize("value", ["not-json-secret", "[]", "null"])
def test_invalid_inline_service_account_json_has_a_fixed_secret_free_failure(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(firebase_credentials.config, "FIREBASE_SERVICE_ACCOUNT_JSON", value)
    monkeypatch.setattr(firebase_credentials.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)

    with pytest.raises(
        firebase_credentials.FirebaseCredentialConfigurationError,
        match=r"^Firebase service account configuration is invalid$",
    ) as captured:
        firebase_credentials.create_firebase_credential()

    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert value not in str(captured.value)


def test_service_account_path_and_application_default_credentials_remain_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = Mock(return_value=object())
    monkeypatch.setattr(firebase_credentials.config, "FIREBASE_SERVICE_ACCOUNT_JSON", None)
    monkeypatch.setattr(
        firebase_credentials.config,
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        "/secure/firebase.json",
    )
    monkeypatch.setattr(firebase_credentials.credentials, "Certificate", certificate)

    assert firebase_credentials.create_firebase_credential() is certificate.return_value
    certificate.assert_called_once_with("/secure/firebase.json")

    monkeypatch.setattr(firebase_credentials.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    certificate.reset_mock()
    assert firebase_credentials.create_firebase_credential() is None
    certificate.assert_not_called()


def test_certificate_validation_failure_drops_secret_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "certificate-secret-must-not-escape"
    monkeypatch.setattr(
        firebase_credentials.config,
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        '{"type":"service_account","private_key":"configured-secret"}',
    )
    monkeypatch.setattr(firebase_credentials.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    monkeypatch.setattr(
        firebase_credentials.credentials,
        "Certificate",
        Mock(side_effect=ValueError(secret)),
    )

    with pytest.raises(
        firebase_credentials.FirebaseCredentialConfigurationError,
        match=r"^Firebase service account configuration is invalid$",
    ) as captured:
        firebase_credentials.create_firebase_credential()

    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert secret not in str(captured.value)


def test_normal_and_pipecat_authentication_share_the_credential_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = object()
    normal_app = object()
    pipecat_app = object()

    monkeypatch.setattr(authentication, "_firebase_app", None)
    monkeypatch.setattr(authentication, "_firebase_init_attempted", False)
    monkeypatch.setattr(authentication.config, "FIREBASE_PROJECT_ID", "firebase-project")
    normal_loader = Mock(return_value=credential)
    normal_initializer = Mock(return_value=normal_app)
    monkeypatch.setattr(authentication, "create_firebase_credential", normal_loader)
    monkeypatch.setattr(authentication.firebase_admin, "initialize_app", normal_initializer)

    assert authentication._ensure_firebase() is normal_app
    normal_loader.assert_called_once_with()
    normal_initializer.assert_called_once_with(
        credential,
        options={"httpTimeout": 2.0, "projectId": "firebase-project"},
    )

    monkeypatch.setattr(pipecat_application, "_firebase_app", None)
    monkeypatch.setattr(
        pipecat_application.firebase_admin,
        "get_app",
        Mock(side_effect=ValueError("missing")),
    )
    pipecat_loader = Mock(return_value=credential)
    pipecat_initializer = Mock(return_value=pipecat_app)
    monkeypatch.setattr(pipecat_application, "create_firebase_credential", pipecat_loader)
    monkeypatch.setattr(pipecat_application.firebase_admin, "initialize_app", pipecat_initializer)

    assert pipecat_application._get_pipecat_firebase_app() is pipecat_app
    pipecat_loader.assert_called_once_with()
    pipecat_initializer.assert_called_once_with(
        credential,
        options={"projectId": "firebase-project"},
        name="murmur-pipecat",
    )
