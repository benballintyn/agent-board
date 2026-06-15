"""Tests for the ``hook-*`` subcommands that Claude Code invokes."""

import io
import json

from agent_board import store
from agent_board.cli import build_stop_hook_output, main


def feed(mocker, payload):
    """Patch stdin with ``payload`` serialised as JSON."""
    mocker.patch("sys.stdin", io.StringIO(json.dumps(payload)))


def test_session_start_exports_id_without_registering(board, mocker, tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("")
    mocker.patch.dict("os.environ", {"CLAUDE_ENV_FILE": str(env_file)}, clear=False)
    feed(mocker, {"session_id": "sess-1", "cwd": "/Users/bb/personal/repos/myproj"})

    assert main(["hook-session-start"]) == 0
    assert 'AGENT_BOARD_ID="sess-1"' in env_file.read_text()
    # opt-in model: the session is NOT registered until it actively participates
    assert store.get_agent("sess-1") is None


def test_session_start_empty_payload_is_noop(board, capsys, mocker):
    mocker.patch("sys.stdin", io.StringIO(""))
    assert main(["hook-session-start"]) == 0
    assert store.list_agents() == []


def test_session_start_without_env_file(board, mocker):
    import os

    mocker.patch.dict("os.environ", {}, clear=False)
    os.environ.pop("CLAUDE_ENV_FILE", None)
    feed(mocker, {"session_id": "sess-x", "cwd": ""})
    assert main(["hook-session-start"]) == 0
    # no env file to write to, and opt-in means no registration either
    assert store.get_agent("sess-x") is None


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


def test_stop_hook_ignores_system_only_but_keeps_it(two_agents, capsys, mocker):
    ch = store.create_channel("alice", "bob")
    store.accept_channel(ch.id, "bob")  # alice's only new event is bob's "joined"
    feed(mocker, {"session_id": "alice", "stop_hook_active": False})
    assert main(["hook-stop"]) == 0
    assert capsys.readouterr().out.strip() == ""  # not woken by a mere join
    # and it wasn't consumed: alice still sees the join on a real check
    assert store.has_updates(store.check_messages("alice", advance=False)) is True


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
