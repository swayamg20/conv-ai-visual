import os
import subprocess
import sys
from pathlib import Path

import pytest
from murmur.core.config import Config, default_env_path, normalize_azure_openai_endpoint


def test_default_env_path_is_the_documented_repository_file() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    assert default_env_path() == repository_root / ".env"


def test_dotenv_can_be_disabled_before_config_import(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=must-not-load\n", encoding="utf-8")
    command = (
        "import os; "
        "from pathlib import Path; "
        "import dotenv; "
        "dotenv.load_dotenv = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('loaded')); "
        "os.environ['PYTHON_DOTENV_DISABLED'] = '1'; "
        "import murmur.core.config"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend"),
            "PYTHON_DOTENV_DISABLED": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (
            " https://murmur-resource.openai.azure.com ",
            "https://murmur-resource.openai.azure.com/openai/v1/",
        ),
        (
            "https://MURMUR-RESOURCE.openai.azure.com/openai/v1/",
            "https://murmur-resource.openai.azure.com/openai/v1/",
        ),
        (
            "https://murmur-resource.services.ai.azure.com/",
            "https://murmur-resource.services.ai.azure.com/openai/v1/",
        ),
    ],
)
def test_normalize_azure_openai_endpoint(endpoint: str, expected: str) -> None:
    assert normalize_azure_openai_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://murmur-resource.openai.azure.com",
        "https://example.com",
        "https://evilopenai.azure.com",
        "https://@murmur-resource.openai.azure.com",
        "https://user@murmur-resource.openai.azure.com",
        "https://murmur-resource.openai.azure.com:443",
        "https://murmur-resource.openai.azure.com/openai/deployments/example",
        "https://murmur-resource.openai.azure.com?api-version=preview",
        "https://murmur-resource.openai.azure.com/#fragment",
    ],
)
def test_normalize_azure_openai_endpoint_rejects_unsafe_values(endpoint: str) -> None:
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        normalize_azure_openai_endpoint(endpoint)


def test_azure_scene_config_inherits_deployment_name_as_model() -> None:
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("MURMUR_SCENE_LLM_") or name.startswith("AZURE_OPENAI_"):
            env.pop(name)
    env.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "LLM_PROVIDER": "groq",
            "MURMUR_SCENE_LLM_PROVIDER": "azure_openai",
            "AZURE_OPENAI_API_KEY": "server-only-test-key",
            "AZURE_OPENAI_ENDPOINT": "https://murmur-resource.openai.azure.com",
            "AZURE_OPENAI_DEPLOYMENT": "murmur-gpt-oss-120b",
        }
    )
    script = """
import json
from murmur.core.config import config
print(json.dumps({
    'provider': config.MURMUR_SCENE_LLM_PROVIDER,
    'model': config.MURMUR_SCENE_LLM_MODEL,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.stdout.strip() == (
        '{"provider": "azure_openai", "model": "murmur-gpt-oss-120b"}'
    )


@pytest.mark.parametrize(
    ("primary_provider", "scene_provider", "missing_key", "primary_key"),
    [
        ("groq", "openai", "OPENAI_API_KEY", "GROQ_API_KEY"),
        ("openai", "groq", "GROQ_API_KEY", "OPENAI_API_KEY"),
        ("openai", "gemini", "GEMINI_API_KEY", "OPENAI_API_KEY"),
    ],
)
def test_enabled_scene_provider_requires_its_own_api_key(
    monkeypatch,
    primary_provider: str,
    scene_provider: str,
    missing_key: str,
    primary_key: str,
) -> None:
    monkeypatch.setattr(Config, "LLM_PROVIDER", primary_provider)
    monkeypatch.setattr(Config, primary_key, "primary-provider-key")
    monkeypatch.setattr(Config, "MURMUR_SCENE_ENABLED", True)
    monkeypatch.setattr(Config, "MURMUR_SCENE_LLM_PROVIDER", scene_provider)
    monkeypatch.setattr(Config, missing_key, None)
    monkeypatch.setattr(Config, "TTS_PROVIDER", "kokoro")

    with pytest.raises(
        ValueError,
        match=rf"{missing_key}.*MURMUR_SCENE_LLM_PROVIDER={scene_provider}",
    ):
        Config.validate()
