"""The local .env file: where it lives and how to read it."""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Next to the executable when frozen, otherwise the repository root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def env_path() -> Path | None:
    """The .env in use, preferring the working directory."""
    for candidate in (Path.cwd() / ".env", app_dir() / ".env"):
        if candidate.is_file():
            return candidate
    return None


def read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values
