import pytest

from ozon_tracker_bot.config import Settings


def test_settings_require_only_telegram_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")

    settings = Settings.from_env()

    assert settings.telegram_bot_token == "token-for-test"
    assert settings.database_url.endswith("data/ozon_tracker.db")


def test_settings_reject_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env()


def test_settings_use_runtime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("HTTP_TIMEOUT_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.poll_interval_seconds == 900
    assert settings.http_timeout_seconds == 20


def test_settings_parse_allowed_usernames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")
    monkeypatch.setenv("ALLOWED_USERNAMES", " @First_User , second user;;@ThirdUser,,")

    settings = Settings.from_env()

    assert settings.allowed_usernames == ("First_User", "second user", "ThirdUser")


def test_settings_allow_empty_allowed_usernames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")
    monkeypatch.delenv("ALLOWED_USERNAMES", raising=False)

    settings = Settings.from_env()

    assert settings.allowed_usernames == ()
