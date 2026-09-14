from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"Environment variable {name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    database_url: str
    poll_interval_seconds: int
    http_timeout_seconds: int
    log_level: str
    allowed_usernames: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite+aiosqlite:///./data/ozon_tracker.db"
            ).strip(),
            poll_interval_seconds=_positive_int("POLL_INTERVAL_SECONDS", 900),
            http_timeout_seconds=_positive_int("HTTP_TIMEOUT_SECONDS", 20),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip() or "INFO",
            allowed_usernames=_parse_usernames(
                os.getenv("ALLOWED_USERNAMES", "")
            ),
        )


def _parse_usernames(raw: str) -> tuple[str, ...]:
    usernames: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        username = chunk.strip().lstrip("@")
        if username:
            usernames.append(username)
    return tuple(usernames)
