"""Tests for path/tunable resolution in :mod:`agent_board.config`."""

import os
from pathlib import Path

from agent_board import config


def test_board_root_prefers_explicit_home(mocker, tmp_path):
    mocker.patch.dict(os.environ, {"AGENT_BOARD_HOME": str(tmp_path)}, clear=True)
    assert config.board_root() == tmp_path


def test_board_root_uses_xdg_when_no_home(mocker, tmp_path):
    mocker.patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}, clear=True)
    assert config.board_root() == tmp_path / "agent-board"


def test_board_root_falls_back_to_home(mocker):
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch.object(Path, "home", return_value=Path("/home/someone"))
    assert config.board_root() == Path("/home/someone/.agent-board")


def test_ttl_default_when_unset(mocker):
    mocker.patch.dict(os.environ, {}, clear=True)
    assert config.ttl_seconds() == config.DEFAULT_TTL_SECONDS


def test_ttl_parses_valid_value(mocker):
    mocker.patch.dict(os.environ, {config.ENV_TTL: "60"}, clear=True)
    assert config.ttl_seconds() == 60


def test_ttl_rejects_non_integer(mocker):
    mocker.patch.dict(os.environ, {config.ENV_TTL: "soon"}, clear=True)
    assert config.ttl_seconds() == config.DEFAULT_TTL_SECONDS


def test_ttl_rejects_non_positive(mocker):
    mocker.patch.dict(os.environ, {config.ENV_TTL: "0"}, clear=True)
    assert config.ttl_seconds() == config.DEFAULT_TTL_SECONDS
