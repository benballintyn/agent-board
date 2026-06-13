"""Edge-case coverage for defensive branches in the store."""

import os

import pytest

from agent_board import config, store


@pytest.fixture
def fresh_home(mocker, tmp_path):
    """Point the board at a path whose layout has NOT been created yet."""
    root = tmp_path / "fresh"
    mocker.patch.dict(os.environ, {"AGENT_BOARD_HOME": str(root)}, clear=False)
    return root


def test_seq_files_missing_dir(tmp_path):
    assert store._seq_files(tmp_path / "nope") == []


def test_list_agents_no_directory(fresh_home):
    assert store.list_agents() == []


def test_member_channels_no_directory(fresh_home):
    assert store.member_channels("anyone") == []


def test_member_channels_skips_non_member(two_agents):
    store.register_agent("carol", name="carol")
    store.create_channel("alice", "bob")
    assert store.member_channels("carol") == []


def test_resolve_by_name_when_filename_differs(board):
    store.register_agent("x1", name="Solo")
    assert store.resolve_agent("solo") == "x1"  # matched by name, not file


def test_register_idempotent_updates_name_and_blurb(board):
    store.register_agent("a1", name="Old", blurb="old")
    updated = store.register_agent("a1", name="New", blurb="new", cwd="/c")
    assert (updated.name, updated.blurb, updated.cwd) == ("New", "new", "/c")


def test_register_idempotent_without_cwd_preserves_cwd(board):
    store.register_agent("a1", name="Old", cwd="/orig")
    updated = store.register_agent("a1")  # no cwd/name/blurb supplied
    assert updated.cwd == "/orig"


def test_gc_no_channels_directory(fresh_home):
    summary = store.gc()
    assert summary == {"reaped_channels": [], "offline_agents": []}


def test_gc_skips_entries_without_meta(two_agents):
    config.ensure_layout()
    (config.channels_dir() / "stray").mkdir()  # a dir with no meta.json
    summary = store.gc(ttl=100000)
    assert "stray" not in summary["reaped_channels"]


def test_gc_skips_already_offline_agent(board):
    store.register_agent("z", name="z")
    store.set_status("z", "offline")
    summary = store.gc()
    assert "z" not in summary["offline_agents"]
