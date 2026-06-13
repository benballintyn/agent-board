"""Tests for the ``hook-*`` subcommands that Claude Code invokes."""

import io
import json

from agent_board import store
from agent_board.cli import build_stop_hook_output, main


def feed(mocker, payload):
    """Patch stdin with ``payload`` serialised as JSON."""
    mocker.patch("sys.stdin", io.StringIO(json.dumps(payload)))


def test_session_start_registers_and_writes_env_file(board, capsys, mocker, tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("")
    mocker.patch.dict("os.environ", {"CLAUDE_ENV_FILE": str(env_file)}, clear=False)
    feed(mocker, {"session_id": "sess-1", "cwd": "/Users/bb/personal/repos/myproj"})

    assert main(["hook-session-start"]) == 0
    agent = store.get_agent("sess-1")
    assert agent is not None and agent.name == "myproj"
    assert 'AGENT_BOARD_ID="sess-1"' in env_file.read_text()


def test_session_start_empty_payload_is_noop(board, capsys, mocker):
    mocker.patch("sys.stdin", io.StringIO(""))
    assert main(["hook-session-start"]) == 0
    assert store.list_agents() == []


def test_session_start_disambiguates_duplicate_name(board, mocker):
    # Two sessions in the same directory: first claims the basename, the
    # second is disambiguated with a short id suffix.
    feed(mocker, {"session_id": "sess-aaaa", "cwd": "/x/proj"})
    main(["hook-session-start"])
    feed(mocker, {"session_id": "sess-bbbb", "cwd": "/x/proj"})
    main(["hook-session-start"])
    names = sorted(a.name for a in store.list_agents())
    assert names == ["proj", "proj-sess"]


def test_session_start_without_env_file(board, mocker):
    mocker.patch.dict("os.environ", {}, clear=False)
    import os

    os.environ.pop("CLAUDE_ENV_FILE", None)
    feed(mocker, {"session_id": "sess-x", "cwd": ""})
    assert main(["hook-session-start"]) == 0
    assert store.get_agent("sess-x").name == "sess-x"


def test_stop_hook_blocks_when_unread(two_agents, capsys, mocker):
    # alice opens a channel -> bob has an invite waiting
    store.create_channel("alice", "bob", topic="sync")
    feed(mocker, {"session_id": "bob", "stop_hook_active": False})
    assert main(["hook-stop"]) == 0
    out = capsys.readouterr().out
    assert '"decision": "block"' in out and "sync" in out

    # second stop: cursor advanced, nothing to inject
    feed(mocker, {"session_id": "bob", "stop_hook_active": False})
    assert main(["hook-stop"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_stop_hook_respects_active_flag(two_agents, capsys, mocker):
    store.create_channel("alice", "bob")
    feed(mocker, {"session_id": "bob", "stop_hook_active": True})
    assert main(["hook-stop"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_stop_hook_no_session_id(board, capsys, mocker):
    feed(mocker, {"stop_hook_active": False})
    assert main(["hook-stop"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_build_stop_hook_output_none_when_idle(two_agents):
    assert build_stop_hook_output("alice") is None


def test_session_end_marks_offline(two_agents, mocker):
    feed(mocker, {"session_id": "alice"})
    assert main(["hook-session-end"]) == 0
    assert store.get_agent("alice").status == "offline"


def test_session_end_no_session_id(board, mocker):
    feed(mocker, {})
    assert main(["hook-session-end"]) == 0


def test_hook_payload_invalid_json(board, capsys, mocker):
    mocker.patch("sys.stdin", io.StringIO("{not json"))
    assert main(["hook-session-start"]) == 0  # tolerated, no crash
