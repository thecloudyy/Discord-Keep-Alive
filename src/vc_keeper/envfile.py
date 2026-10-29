"""The local .env file: where it lives, how to read it and how to write it."""

from __future__ import annotations

import sys
from pathlib import Path

# Written in this order so the file stays readable.
KEYS = (
    "DISCORD_TOKEN",
    "DISCORD_GUILD_ID",
    "DISCORD_CHANNEL_ID",
    "LOG_LEVEL",
    "SELF_DEAF",
    "STATUS",
    "ACTIVITY_TYPE",
    "ACTIVITY_TEXT",
    "CUSTOM_STATUS",
    "PLATFORM",
    "REJOIN_COOLDOWN_MIN",
    "REJOIN_COOLDOWN_MAX",
)


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


def write(path: Path, values: dict[str, str]) -> None:
    ordered = [key for key in KEYS if key in values]
    ordered += [key for key in values if key not in KEYS]
    path.write_text("".join(f"{key}={values[key]}\n" for key in ordered), encoding="utf-8")
