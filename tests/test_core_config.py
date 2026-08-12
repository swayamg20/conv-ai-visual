import os
import subprocess
import sys
from pathlib import Path

from murmur.core.config import default_env_path


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
