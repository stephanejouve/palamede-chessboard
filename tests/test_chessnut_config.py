"""Per-user settings of the bridge and the app : a short command line (#90)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from palamede_chessnut import config
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.token_light import fingerprint

TOKEN = "secret-token-never-shown"
GAME = ["--game", "g1", "--color", "white"]


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def token_file(tmp_path) -> Path:
    path = write(tmp_path / "token", TOKEN)
    path.chmod(0o600)
    return path


@pytest.fixture
def use(monkeypatch, tmp_path):
    """Write ``config.toml`` and make it the user's file."""

    def written(text: str) -> Path:
        path = write(tmp_path / "config.toml", text)
        monkeypatch.setenv("PALAMEDE_CONFIG", str(path))
        return path

    return written


# ---- where the file is ----------------------------------------------------


def test_palamede_config_names_the_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PALAMEDE_CONFIG", str(tmp_path / "mine.toml"))
    assert config.config_path() == tmp_path / "mine.toml"


def test_default_place_follows_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PALAMEDE_CONFIG", raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "palamede" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config.config_path() == Path.home() / ".config" / "palamede" / "config.toml"


def test_default_place_on_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PALAMEDE_CONFIG", raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config.config_path() == tmp_path / "palamede" / "config.toml"


# ---- what it may hold -----------------------------------------------------


def test_no_file_no_settings(tmp_path) -> None:
    assert config.load(tmp_path / "absent.toml") == {}


def test_settings_are_read(tmp_path) -> None:
    path = write(
        tmp_path / "config.toml",
        'viewer = "https://chess.example"\ntoken_file = "/keys/t"\ntransport = "usb"\n',
    )
    assert config.load(path) == {
        "viewer": "https://chess.example",
        "token_file": Path("/keys/t"),
        "transport": "usb",
    }


def test_token_file_home_and_relative(tmp_path) -> None:
    path = write(tmp_path / "config.toml", 'token_file = "~/t"\n')
    assert config.load(path)["token_file"] == Path.home() / "t"
    write(path, 'token_file = "keys/t"\n')
    assert config.load(path)["token_file"] == tmp_path / "keys" / "t"


def test_the_token_itself_is_refused(tmp_path) -> None:
    path = write(tmp_path / "config.toml", f'token = "{TOKEN}"\n')
    with pytest.raises(SystemExit) as refused:
        config.load(path)
    assert "token_file" in str(refused.value)
    assert TOKEN not in str(refused.value)


@pytest.mark.parametrize(
    "text",
    [
        "viewer = 3\n",
        'viewer = ""\n',
        "token_file = [1]\n",
        'transport = "wifi"\n',
        "viewer = \n",
        "[viewer]\nurl = 1\n",
    ],
)
def test_malformed_settings_stop_with_the_file_named(tmp_path, text: str) -> None:
    path = write(tmp_path / "config.toml", text)
    with pytest.raises(SystemExit) as refused:
        config.load(path)
    assert str(path) in str(refused.value)


def test_unknown_settings_are_only_said(tmp_path, caplog) -> None:
    path = write(tmp_path / "config.toml", 'viewer = "https://v.test"\ncolour = "white"\n')
    with caplog.at_level(logging.WARNING):
        assert config.load(path) == {"viewer": "https://v.test"}
    assert "colour" in caplog.text


def test_an_unreadable_file_stops(tmp_path) -> None:
    with pytest.raises(SystemExit):
        config.load(tmp_path)  # a directory


# ---- the command line -----------------------------------------------------


def test_the_file_shortens_the_command_line(use, token_file) -> None:
    use(f'viewer = "https://chess.example"\ntoken_file = "{token_file}"\ntransport = "usb"\n')
    bridge = bridge_from_args(GAME, "t")
    assert (bridge.viewer, bridge.board_transport) == ("https://chess.example", "usb")
    assert bridge.token_print == fingerprint(TOKEN)


def test_the_command_line_wins(use, token_file) -> None:
    use('viewer = "https://old.example"\ntransport = "usb"\n')
    argv = [*GAME, "--viewer", "https://new.example", "--transport", "ble"]
    bridge = bridge_from_args([*argv, "--token-file", str(token_file)], "t")
    assert (bridge.viewer, bridge.board_transport) == ("https://new.example", "ble")


def test_config_option_names_another_file(tmp_path, token_file) -> None:
    other = write(
        tmp_path / "other.toml", f'viewer = "https://other.example"\ntoken_file = "{token_file}"\n'
    )
    bridge = bridge_from_args([*GAME, "--config", str(other)], "t")
    assert bridge.viewer == "https://other.example"


def test_without_file_the_viewer_is_still_required(monkeypatch) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    with pytest.raises(SystemExit):
        bridge_from_args(GAME, "t")


def test_the_file_viewer_is_checked_like_the_option(use, monkeypatch) -> None:
    """The token is sent there : http off the loopback is refused, whoever named it."""
    use('viewer = "http://chess.example"\n')
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(GAME, "t")
    assert "https required" in str(refused.value)


def test_the_file_token_file_is_checked_like_the_option(use, token_file) -> None:
    token_file.chmod(0o644)
    use(f'viewer = "https://v.test"\ntoken_file = "{token_file}"\n')
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(GAME, "t")
    assert "chmod 600" in str(refused.value)


# ---- who may change it (review Admin #124) --------------------------------


@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
def test_a_file_others_may_change_is_refused(tmp_path, mode: int) -> None:
    """It chooses where the token goes : another account must not be able to write it."""
    path = write(tmp_path / "config.toml", 'viewer = "https://v.test"\n')
    path.chmod(mode)
    with pytest.raises(SystemExit) as refused:
        config.load(path)
    assert "chmod go-w" in str(refused.value)


def test_a_directory_others_may_change_is_refused(tmp_path) -> None:
    """Whoever writes the directory can put another file in its place."""
    folder = tmp_path / "palamede"
    folder.mkdir()
    path = write(folder / "config.toml", 'viewer = "https://v.test"\n')
    folder.chmod(0o777)
    try:
        with pytest.raises(SystemExit) as refused:
            config.load(path)
    finally:
        folder.chmod(0o700)
    assert str(folder) in str(refused.value)


def test_a_file_of_another_account_is_refused(monkeypatch, tmp_path) -> None:
    """644 is not enough : its owner could still change it (review Junior #124, seen on hera)."""
    path = write(tmp_path / "config.toml", 'viewer = "https://v.test"\n')
    path.chmod(0o644)
    monkeypatch.setattr(config.os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(SystemExit) as refused:
        config.load(path)
    assert "owned by another account" in str(refused.value)


def test_a_link_from_a_directory_others_may_change_is_refused(tmp_path) -> None:
    """The link itself can be replaced by whoever writes its directory (review Admin #124)."""
    target = write(tmp_path / "config.toml", 'viewer = "https://v.test"\n')
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "config.toml").symlink_to(target)
    shared.chmod(0o777)
    try:
        with pytest.raises(SystemExit) as refused:
            config.load(shared / "config.toml")
    finally:
        shared.chmod(0o700)
    assert str(shared) in str(refused.value)


def test_a_readable_file_is_fine(tmp_path) -> None:
    """Nothing secret in it : others may read it."""
    path = write(tmp_path / "config.toml", 'viewer = "https://v.test"\n')
    path.chmod(0o644)
    assert config.load(path) == {"viewer": "https://v.test"}


def test_windows_wants_the_file_under_the_profile(monkeypatch, tmp_path) -> None:
    path = write(tmp_path / "config.toml", 'viewer = "https://v.test"\n')
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda _cls: tmp_path / "profile"))
    with pytest.raises(SystemExit) as refused:
        config.load(path)
    assert "user profile" in str(refused.value)
    monkeypatch.setattr(config.Path, "home", classmethod(lambda _cls: tmp_path))
    assert config.load(path) == {"viewer": "https://v.test"}


# ---- a named file, and the token from the environment ---------------------


def test_a_named_file_must_exist(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    argv = [*GAME, "--viewer", "https://v.test", "--config", str(tmp_path / "absent.toml")]
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(argv, "t")
    assert "no such settings file" in str(refused.value)
    monkeypatch.setenv("PALAMEDE_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(SystemExit) as refused:
        bridge_from_args([*GAME, "--viewer", "https://v.test"], "t")
    assert "no such settings file" in str(refused.value)


def test_the_environment_token_wins_over_the_file(use, monkeypatch, tmp_path) -> None:
    """Set for this launch, PALAMEDE_PLAYER_TOKEN is not hidden by the file's token_file."""
    use(f'viewer = "https://v.test"\ntoken_file = "{tmp_path / "absent"}"\n')
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    assert bridge_from_args(GAME, "t").token_print == fingerprint(TOKEN)
