import pytest

from steamlink.api import build_demo_locator
from steamlink.config import ConfigError, load_settings
from steamlink.gc import GameCoordinatorDemoLocator
from steamlink.valve import UnconfiguredDemoLocator


def test_no_bot_credentials_keeps_retrieval_disabled():
    settings = load_settings({})
    assert not settings.demo_bot_configured
    assert isinstance(build_demo_locator(settings), UnconfiguredDemoLocator)


def test_refresh_token_enables_gc_locator_without_connecting():
    settings = load_settings({"STEAM_BOT_REFRESH_TOKEN": "eyJ.fake.token"})
    assert settings.demo_bot_configured
    assert isinstance(build_demo_locator(settings), GameCoordinatorDemoLocator)  # login is lazy


def test_refresh_token_file(tmp_path):
    path = tmp_path / "rt"
    path.write_text("eyJ.from.file\n")
    assert load_settings({"STEAM_BOT_REFRESH_TOKEN_FILE": str(path)}).steam_bot_refresh_token == "eyJ.from.file"


def test_password_without_shared_secret_rejected():
    with pytest.raises(ConfigError):
        load_settings({"STEAM_BOT_USERNAME": "bot", "STEAM_BOT_PASSWORD": "pw"})


def test_password_with_shared_secret_ok():
    s = load_settings({"STEAM_BOT_USERNAME": "bot", "STEAM_BOT_PASSWORD": "pw", "STEAM_BOT_SHARED_SECRET": "c2VjcmV0"})
    assert s.demo_bot_configured
