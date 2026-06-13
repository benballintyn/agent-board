"""Tests for the CLI verb layer in :mod:`agent_board.cli`."""

import os

from agent_board import store
from agent_board.cli import main


def run(capsys, *argv):
    """Invoke ``main`` and return ``(exit_code, stdout, stderr)``."""
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_list_agents_empty(board, capsys):
    code, out, _ = run(capsys, "list-agents")
    assert code == 0 and "no agents" in out


def test_register_then_list(board, capsys):
    run(capsys, "register", "--id", "a1", "--name", "Ada", "--blurb", "ml")
    code, out, _ = run(capsys, "list-agents")
    assert code == 0
    assert "Ada" in out and "ml" in out


def test_list_agents_json(two_agents, capsys):
    code, out, _ = run(capsys, "list-agents", "--json")
    assert code == 0 and out.strip().startswith("[")
    assert "alice" in out


def test_set_bio_via_cli(two_agents, capsys):
    code, out, _ = run(capsys, "set-bio", "--id", "alice", "refactoring")
    assert code == 0
    assert store.get_agent("alice").blurb == "refactoring"


def test_full_conversation_flow(two_agents, capsys):
    # alice opens a channel with bob (by name)
    run(capsys, "create", "--id", "alice", "--to", "bob", "--topic", "sync")
    channel = store.member_channels("alice")[0].id

    # bob sees the invite
    code, out, _ = run(capsys, "check-messages", "--id", "bob")
    assert code == 0 and "invite" in out and channel in out

    # bob accepts and replies; alice reads
    run(capsys, "accept", "--id", "bob", channel)
    run(capsys, "post", "--id", "bob", channel, "--text", "on it")
    code, out, _ = run(capsys, "check-messages", "--id", "alice")
    assert "on it" in out

    # read with --json exposes head + events
    code, out, _ = run(capsys, "read", channel, "--json")
    assert code == 0 and '"head"' in out and "on it" in out


def test_post_then_read_text_form(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    run(capsys, "post", "--id", "alice", channel, "--text", "ping")
    code, out, _ = run(capsys, "read", channel)
    assert "ping" in out and "head=" in out


def test_post_artifact_via_cli(two_agents, capsys, tmp_path):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    f = tmp_path / "note.md"
    f.write_text("hi")
    code, out, _ = run(capsys, "post", "--id", "alice", channel, "--artifact", str(f))
    assert code == 0 and "posted" in out


def test_close_flow(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    code, out, _ = run(capsys, "close", "--id", "alice", channel)
    assert "waiting" in out
    code, out, _ = run(capsys, "close", "--id", "bob", channel)
    assert "closed and deleted" in out


def test_gc_command(two_agents, capsys):
    code, out, _ = run(capsys, "gc", "--ttl", "100000")
    assert code == 0 and out.startswith("gc:")


def test_missing_agent_id_is_error(board, capsys, mocker):
    mocker.patch.dict(os.environ, {}, clear=False)
    os.environ.pop("AGENT_BOARD_ID", None)
    code, _, err = run(capsys, "register")  # no --id, no env
    assert code == 1 and "no agent id" in err


def test_agent_id_from_env(board, capsys, mocker):
    mocker.patch.dict(os.environ, {"AGENT_BOARD_ID": "envagent"}, clear=False)
    code, out, _ = run(capsys, "register", "--name", "FromEnv")
    assert code == 0
    assert store.get_agent("envagent").name == "FromEnv"


def test_post_without_content_is_error(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    code, _, err = run(capsys, "post", "--id", "alice", channel)
    assert code == 1 and "nothing to post" in err


def test_check_messages_nothing_new(two_agents, capsys):
    code, out, _ = run(capsys, "check-messages", "--id", "alice")
    assert code == 0 and "nothing new" in out


def test_check_messages_json(two_agents, capsys):
    code, out, _ = run(capsys, "check-messages", "--id", "bob", "--json")
    assert code == 0 and '"invites"' in out


def test_read_renders_artifact_event(two_agents, capsys, tmp_path):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    f = tmp_path / "out.bin"
    f.write_text("data")
    run(capsys, "post", "--id", "alice", channel, "--artifact", str(f), "--text", "see this")
    code, out, _ = run(capsys, "read", channel)
    assert "shared artifact 'out.bin'" in out and "see this" in out


def test_check_messages_renders_notice(two_agents, capsys):
    # Deliver a non-invite notice directly, then render it via check-messages.
    store.deliver("bob", {"type": "notice", "msg": "heads up"})
    code, out, _ = run(capsys, "check-messages", "--id", "bob")
    assert code == 0 and "[notice]" in out


def test_check_messages_renders_channel_without_topic(two_agents, capsys):
    store.create_channel("alice", "bob")  # no topic
    channel = store.member_channels("alice")[0].id
    run(capsys, "post", "--id", "alice", channel, "--text", "no-topic msg")
    code, out, _ = run(capsys, "check-messages", "--id", "bob")
    assert code == 0 and f"[channel {channel}]" in out and "no-topic msg" in out
