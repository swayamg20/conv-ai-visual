from pathlib import Path

from murmur.core.config import default_env_path


def test_default_env_path_is_the_documented_repository_file() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    assert default_env_path() == repository_root / ".env"
