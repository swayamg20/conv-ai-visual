#!/usr/bin/env python3
"""Prompt for a Cosmo workspace key without putting it in shell history."""

from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from pathlib import Path


def configure(env_path: Path, key: str) -> None:
    """Atomically change only the Cosmo entries and preserve unrelated settings."""
    if not re.fullmatch(r"cosmo_[A-Za-z0-9_.-]+", key):
        raise ValueError("Expected a Cosmo workspace API key beginning with cosmo_.")
    if env_path.is_symlink():
        raise ValueError("Refusing to replace a linked .env file.")
    content = env_path.read_text() if env_path.exists() else ""
    for name, value in (("COSMO_API_KEY", key), ("MURMUR_COSMO_ENABLED", "true")):
        pattern = re.compile(rf"^(?:export\s+)?{name}\s*=.*$", re.MULTILINE)
        replacement = f"{name}={value}"
        if pattern.search(content):
            content = pattern.sub(lambda _match, line=replacement: line, content)
        else:
            content = content.rstrip("\n") + "\n" + replacement + "\n"

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=env_path.parent, prefix=".cosmo-env-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, env_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not sys.stdin.isatty():
        print("Run this script directly in an interactive terminal.", file=sys.stderr)
        return 1
    print("Use a Cosmo workspace API key with the user_tokens:mint scope.")
    try:
        key = getpass.getpass("Cosmo API key (input hidden): ").strip()
        configure(env_path, key)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; no changes saved.")
        return 1
    except (OSError, ValueError):
        print(
            "Could not save the key. Check that it begins with cosmo_ and the .env file is writable.",
            file=sys.stderr,
        )
        return 1
    print(f"Saved to {env_path} with owner-only permissions.")
    print("Cosmo is enabled. Restart the backend to load the key, then tell Codex you're ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
