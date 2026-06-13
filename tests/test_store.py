"""Tests for the filesystem core in :mod:`agent_board.store`."""

import time

import pytest

from agent_board import config, store
from agent_board.store import BoardError


# --- agents / directory --------------------------------------------------- #
def test_register_and_get(board):
    agent = store.register_agent("a1", name="Ada", blurb="ml", cwd="/work")
    assert agent.name == "Ada"
    assert agent.status == "online"
    fetched = store.get_agent("a1")
    assert fetched is not None and fetched.blurb == "ml"


def test_get_unknown_agent_returns_none(board):
    assert store.get_agent("nope") is None


def test_register_is_idempotent_and_preserves_blurb(board):
    store.register_agent("a1", name="Ada", blurb="ml")
    again = store.register_agent("a1", cwd="/new")  # no blurb/name supplied
    assert again.blurb == "ml"
    assert again.cwd == "/new"
    assert again.name == "Ada"


def test_set_bio(board):
    store.register_agent("a1", name="Ada")
    updated = store.set_bio("a1", "now doing infra")
    assert updated.blurb == "now doing infra"


def test_set_bio_unknown_raises(board):
    with pytest.raises(BoardError):
        store.set_bio("ghost", "x")


def test_set_status_noop_for_unknown(board):
    store.set_status("ghost", "offline")  # must not raise


def test_list_and_resolve(two_agents):
    names = [a.name for a in store.list_agents()]
    assert names == ["alice", "bob"]
    assert store.resolve_agent("bob") == "bob"
    assert store.resolve_agent("ALICE") == "alice"  # by name, case-insensitive
    assert store.resolve_agent("missing") is None


# --- channels ------------------------------------------------------------- #
def test_create_channel_adds_members_and_invite(two_agents):
    channel = store.create_channel("alice", "bob", topic="sync")
    assert set(channel.members) == {"alice", "bob"}
    result = store.check_messages("bob")
    assert result["invites"][0]["channel"] == channel.id
    assert result["invites"][0]["type"] == "invite"


def test_create_channel_to_self_has_single_member(board):
    store.register_agent("solo", name="solo")
    channel = store.create_channel("solo", "solo")
    assert channel.members == ["solo"]


def test_create_unknown_target_raises(two_agents):
    with pytest.raises(BoardError):
        store.create_channel("alice", "nobody")


def test_load_unknown_channel_raises(board):
    with pytest.raises(BoardError):
        store.load_channel("deadbeef")


def test_post_and_read_since(two_agents):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="hello")
    store.post(channel.id, "bob", text="hi back")
    events, head = store.read_events(channel.id)
    # system "opened" event + two messages
    assert [e.text for e in events][-2:] == ["hello", "hi back"]
    assert head == len(events)
    newer, _ = store.read_events(channel.id, since=head - 1)
    assert len(newer) == 1 and newer[0].text == "hi back"


def test_post_artifact_copies_bytes(two_agents, tmp_path):
    channel = store.create_channel("alice", "bob")
    src = tmp_path / "data.txt"
    src.write_text("payload")
    event = store.post(channel.id, "alice", text="file attached", artifact_path=str(src))
    assert event.type == "artifact"
    assert event.artifact["name"] == "data.txt"
    stored = store.artifact_path(channel.id, event.artifact["id"])
    assert stored.read_text() == "payload"


def test_post_requires_content(two_agents):
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.post(channel.id, "alice")


def test_post_nonmember_raises(two_agents):
    store.register_agent("carol", name="carol")
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.post(channel.id, "carol", text="intruding")


def test_post_missing_artifact_file_raises(two_agents):
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.post(channel.id, "alice", artifact_path="/no/such/file")


def test_artifact_path_missing_raises(two_agents):
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.artifact_path(channel.id, "ghost")


# --- path-traversal / id hardening ---------------------------------------- #
def test_artifact_path_rejects_traversal(two_agents):
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.artifact_path(channel.id, "../../../../etc/passwd")


def test_channel_id_traversal_rejected(board):
    with pytest.raises(BoardError):
        store.read_events("../../../../etc")


def test_register_rejects_unsafe_id(board):
    with pytest.raises(BoardError):
        store.register_agent("../evil", name="x")


def test_accept_posts_joined_event(two_agents):
    channel = store.create_channel("alice", "bob")
    store.accept_channel(channel.id, "bob")
    events, _ = store.read_events(channel.id)
    assert any("joined" in (e.text or "") for e in events)


def test_accept_nonmember_raises(two_agents):
    store.register_agent("carol", name="carol")
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.accept_channel(channel.id, "carol")


# --- cursors / check_messages -------------------------------------------- #
def test_check_messages_surfaces_others_not_self(two_agents):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="from alice")
    # Bob sees alice's message...
    result = store.check_messages("bob")
    texts = [e["text"] for e in result["channels"][channel.id]["events"]]
    assert "from alice" in texts
    # ...and a second check yields nothing (cursor advanced).
    assert store.has_updates(store.check_messages("bob")) is False
    # Alice is not woken by her own post.
    alice_result = store.check_messages("alice")
    assert channel.id not in alice_result["channels"]


def test_check_messages_no_advance_keeps_cursor(two_agents):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="ping")
    first = store.check_messages("bob", advance=False)
    second = store.check_messages("bob", advance=False)
    assert store.has_updates(first) and store.has_updates(second)


def test_get_cursors_empty(board):
    assert store.get_cursors("nobody") == {}


# --- closing -------------------------------------------------------------- #
def test_vote_close_requires_all_members(two_agents):
    channel = store.create_channel("alice", "bob")
    assert store.vote_close(channel.id, "alice") is False
    assert store.load_channel(channel.id).status == "closing"
    assert store.vote_close(channel.id, "bob") is True
    with pytest.raises(BoardError):
        store.load_channel(channel.id)  # deleted


def test_vote_close_nonmember_raises(two_agents):
    store.register_agent("carol", name="carol")
    channel = store.create_channel("alice", "bob")
    with pytest.raises(BoardError):
        store.vote_close(channel.id, "carol")


def test_propose_close_notifies_peer(two_agents):
    channel = store.create_channel("alice", "bob")
    store.vote_close(channel.id, "alice")
    # Bob learns about the proposal via his normal check-messages delta.
    result = store.check_messages("bob")
    texts = [e["text"] for e in result["channels"][channel.id]["events"]]
    assert any("proposed closing" in t for t in texts)


def test_propose_close_does_not_double_notify(two_agents):
    channel = store.create_channel("alice", "bob")
    store.vote_close(channel.id, "alice")
    store.vote_close(channel.id, "alice")  # repeat vote
    events, _ = store.read_events(channel.id)
    proposals = [e for e in events if "proposed closing" in (e.text or "")]
    assert len(proposals) == 1


# --- channel overviews / unique names ------------------------------------- #
def test_channel_overviews_counts_unread_from_others(two_agents):
    channel = store.create_channel("alice", "bob")
    store.post(channel.id, "alice", text="one")
    store.post(channel.id, "alice", text="two")
    [overview] = store.channel_overviews("bob")
    assert overview["channel"].id == channel.id
    # "channel opened" system event + two messages, all authored by alice.
    assert overview["unread"] == 3
    # Alice's own posts are not unread for her (only the system "opened" event).
    alice_overview = store.channel_overviews("alice")[0]
    assert alice_overview["unread"] == 0


def test_channel_overviews_empty(board):
    store.register_agent("lonely", name="lonely")
    assert store.channel_overviews("lonely") == []


def test_unique_agent_name_free(two_agents):
    assert store.unique_agent_name("brand-new", "alice") == "brand-new"


def test_unique_agent_name_self_is_not_collision(two_agents):
    # "alice" is taken by agent id "alice" -- re-registering self keeps it.
    assert store.unique_agent_name("alice", "alice") == "alice"


def test_unique_agent_name_collision_gets_suffix(two_agents):
    # A different agent wants the name "alice" -> disambiguated.
    assert store.unique_agent_name("alice", "zz99") == "alice-zz99"


# --- gc ------------------------------------------------------------------- #
def test_gc_reaps_idle_channel(two_agents):
    channel = store.create_channel("alice", "bob")
    future = time.time() + 10_000
    summary = store.gc(ttl=1, now=future)
    assert channel.id in summary["reaped_channels"]
    with pytest.raises(BoardError):
        store.load_channel(channel.id)


def test_gc_keeps_fresh_channel(two_agents):
    channel = store.create_channel("alice", "bob")
    summary = store.gc(ttl=10_000)
    assert channel.id not in summary["reaped_channels"]
    assert store.load_channel(channel.id).status == "open"


def test_gc_marks_stale_agent_offline(board):
    import json

    store.register_agent("old", name="old")
    path = config.agents_dir() / "old.json"
    raw = json.loads(path.read_text())
    raw["last_seen"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw))
    summary = store.gc()
    assert "old" in summary["offline_agents"]
    assert store.get_agent("old").status == "offline"


def test_gc_skips_unparseable_last_seen(board):
    store.register_agent("weird", name="weird")
    import json

    path = config.agents_dir() / "weird.json"
    raw = json.loads(path.read_text())
    raw["last_seen"] = "not-a-timestamp"
    path.write_text(json.dumps(raw))
    summary = store.gc()  # must not raise
    assert "weird" not in summary["offline_agents"]


def test_gc_prunes_long_offline_agent(board):
    import json

    store.register_agent("ancient", name="ancient")
    store.set_status("ancient", "offline")
    path = config.agents_dir() / "ancient.json"
    raw = json.loads(path.read_text())
    raw["last_seen"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw))
    summary = store.gc()
    assert "ancient" in summary["pruned_agents"]
    assert store.get_agent("ancient") is None
    assert not (config.agents_dir() / "ancient").exists()


def test_seq_allocation_skips_existing_gap(two_agents):
    channel = store.create_channel("alice", "bob")
    mdir = config.channels_dir() / channel.id / "messages"
    # Pre-create a high-numbered slot; the next post must jump past it.
    (mdir / "000050.json").write_text('{"seq": 50}')
    event = store.post(channel.id, "alice", text="after gap")
    assert event.seq == 51


def test_append_numbered_retries_on_collision(board, mocker, tmp_path):
    # Force the link-collision branch: pretend the dir is empty (n starts at 1)
    # while slot 000001.json already exists, so the first os.link must retry.
    target = tmp_path / "seqdir"
    target.mkdir()
    (target / "000001.json").write_text("{}")
    mocker.patch.object(store, "_max_seq", return_value=0)
    seq = store._append_numbered(target, {"hello": "world"})
    assert seq == 2  # 1 collided, retried to 2
    assert (target / "000002.json").exists()
