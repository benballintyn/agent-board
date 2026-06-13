"""Shared pytest fixtures."""

import os

import pytest

from agent_board import config, store


@pytest.fixture
def board(tmp_path, mocker):
    """Point the board at an isolated temp root and return that path."""
    mocker.patch.dict(os.environ, {"AGENT_BOARD_HOME": str(tmp_path)}, clear=False)
    config.ensure_layout()
    return tmp_path


@pytest.fixture
def two_agents(board):
    """Register two agents (``alice``, ``bob``) and return their ids."""
    store.register_agent("alice", name="alice", blurb="frontend work")
    store.register_agent("bob", name="bob", blurb="backend work")
    return "alice", "bob"
