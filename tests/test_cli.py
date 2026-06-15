"""Tests for the CLI verb layer in :mod:`agent_board.cli`."""

import json
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


# --- create/post --json --------------------------------------------------- #
def test_create_json(two_agents, capsys):
    code, out, _ = run(capsys, "create", "--id", "alice", "--to", "bob", "--topic", "t", "--json")
    data = json.loads(out)
    assert code == 0 and data["members"] == ["alice", "bob"] and data["topic"] == "t"


def test_post_json(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    code, out, _ = run(capsys, "post", "--id", "alice", channel, "--text", "hi", "--json")
    data = json.loads(out)
    assert code == 0 and data["channel"] == channel and data["type"] == "message"


# --- get-artifact --------------------------------------------------------- #
def _make_artifact(capsys, src):
    """Open a channel, post ``src`` as an artifact, return (channel, artifact_id)."""
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    _, out, _ = run(capsys, "post", "--id", "alice", channel, "--artifact", str(src), "--json")
    return channel, json.loads(out)["artifact"]["id"]


def test_get_artifact_with_out_file(two_agents, capsys, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("bytes!")
    channel, aid = _make_artifact(capsys, src)
    dest = tmp_path / "fetched.txt"
    code, out, _ = run(capsys, "get-artifact", channel, aid, "--out", str(dest))
    assert code == 0 and dest.read_text() == "bytes!" and "saved artifact" in out


def test_get_artifact_default_cwd(two_agents, capsys, tmp_path, mocker):
    src = tmp_path / "src" / "data.bin"
    src.parent.mkdir()
    src.write_text("xy")
    channel, aid = _make_artifact(capsys, src)
    mocker.patch("agent_board.cli.Path.cwd", return_value=tmp_path)
    code, _, _ = run(capsys, "get-artifact", channel, aid)
    assert code == 0 and (tmp_path / "data.bin").read_text() == "xy"


def test_get_artifact_out_is_directory(two_agents, capsys, tmp_path):
    src = tmp_path / "report.csv"
    src.write_text("a,b")
    channel, aid = _make_artifact(capsys, src)
    outdir = tmp_path / "downloads"
    outdir.mkdir()
    code, _, _ = run(capsys, "get-artifact", channel, aid, "--out", str(outdir))
    assert code == 0 and (outdir / "report.csv").read_text() == "a,b"


def test_get_artifact_missing_errors(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    code, _, err = run(capsys, "get-artifact", channel, "nope_x.txt")
    assert code == 1 and "not found" in err


def test_get_artifact_rejects_traversal(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    channel = store.member_channels("alice")[0].id
    code, _, err = run(capsys, "get-artifact", channel, "../../../../etc/passwd")
    assert code == 1 and "invalid artifact id" in err


def test_command_rejects_unsafe_agent_id(board, capsys):
    code, _, err = run(capsys, "whoami", "--id", "../evil")
    assert code == 1 and "invalid agent id" in err


# --- list-channels -------------------------------------------------------- #
def test_list_channels_empty(two_agents, capsys):
    code, out, _ = run(capsys, "list-channels", "--id", "alice")
    assert code == 0 and "no channels" in out


def test_list_channels_populated(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob", "--topic", "sync")
    channel = store.member_channels("alice")[0].id
    store.post(channel, "alice", text="hi")
    code, out, _ = run(capsys, "list-channels", "--id", "bob")
    # "channel opened" system event + the "hi" message, both from alice.
    assert code == 0 and channel in out and "sync" in out and "unread: 2" in out


def test_list_channels_json(two_agents, capsys):
    run(capsys, "create", "--id", "alice", "--to", "bob")
    code, out, _ = run(capsys, "list-channels", "--id", "alice", "--json")
    assert code == 0 and '"unread"' in out


# --- whoami --------------------------------------------------------------- #
def test_whoami_registered(two_agents, capsys):
    code, out, _ = run(capsys, "whoami", "--id", "alice")
    assert code == 0 and "alice" in out and "frontend" in out


def test_whoami_unregistered(board, capsys):
    code, out, _ = run(capsys, "whoami", "--id", "ghost")
    assert code == 0 and "not registered" in out


def test_whoami_json(two_agents, capsys):
    code, out, _ = run(capsys, "whoami", "--id", "bob", "--json")
    assert code == 0 and '"id": "bob"' in out


# --- opt-in / lazy registration ------------------------------------------- #
def test_check_messages_does_not_register(board, capsys):
    code, out, _ = run(capsys, "check-messages", "--id", "fresh-id")
    assert code == 0 and "nothing new" in out
    assert store.get_agent("fresh-id") is None  # passive: did not join the board


def test_whoami_does_not_register(board, capsys):
    code, out, _ = run(capsys, "whoami", "--id", "fresh-id")
    assert code == 0 and "not registered" in out
    assert store.get_agent("fresh-id") is None


def test_create_lazily_registers_creator(two_agents, capsys):
    assert store.get_agent("newcomer") is None
    run(capsys, "create", "--id", "newcomer", "--to", "alice")
    agent = store.get_agent("newcomer")
    assert agent is not None and "-" in agent.name  # auto-joined with a memorable name


# --- wait ----------------------------------------------------------------- #
def test_wait_returns_pending_message(two_agents, capsys):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="ping")
    code, out, _ = run(capsys, "wait", "--id", "bob", "--timeout", "0", "--interval", "0")
    assert code == 0 and "ping" in out


def test_wait_timeout_with_nothing(two_agents, capsys):
    # bob is registered but has no channels/invites -> polls (sleeps) then times out
    code, out, _ = run(capsys, "wait", "--id", "bob", "--timeout", "0.05", "--interval", "0.01")
    assert code == 3 and "no new messages" in out


def test_wait_follow_streams_then_exits(two_agents, capsys):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="ping")
    code, out, _ = run(
        capsys, "wait", "--id", "bob", "--follow", "--timeout", "0", "--interval", "0"
    )
    assert code == 0 and "ping" in out


def test_wait_not_registered(board, capsys):
    code, out, _ = run(capsys, "wait", "--id", "ghost-x", "--timeout", "0")
    assert code == 0 and "not on the board" in out


def test_wait_does_not_wake_on_join(two_agents, capsys):
    ch = store.create_channel("alice", "bob")
    store.accept_channel(ch.id, "bob")  # bob's accept posts a "joined" system event
    code, out, _ = run(capsys, "wait", "--id", "alice", "--timeout", "0", "--interval", "0")
    assert code == 3 and "no new messages" in out  # the join did NOT wake alice
    store.post(ch.id, "bob", text="hello")
    code, out, _ = run(capsys, "wait", "--id", "alice", "--timeout", "0", "--interval", "0")
    assert code == 0 and "hello" in out  # a real message does
