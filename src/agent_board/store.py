"""Filesystem operations that make up the board.

The board is just a directory tree -- there is no daemon. Concurrency safety
comes from a single rule: **every writer creates its own distinct file**, so
there is never append contention. The only shared counter (a channel's or
inbox's sequence number) is allocated with an ``O_EXCL`` create-and-retry loop,
which is atomic on a local filesystem.

Layout::

    <root>/
      agents/
        <agent-id>.json            # Agent record
        <agent-id>/
          inbox/<seq>.json         # invites + notifications addressed to me
          cursors.json             # {channel-id|"inbox": last_seen_seq}
      channels/
        <channel-id>/
          meta.json                # Channel record
          messages/<seq>.json      # one Event per file; seq == zero-padded name
          artifacts/<artifact-id>  # raw bytes of shared files
          close/<agent-id>         # presence of file == this member voted to close
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config
from .log import logger
from .models import Agent, Channel, Event

_SEQ_WIDTH = 6


class BoardError(Exception):
    """Raised for expected, user-facing failures (unknown channel, etc.)."""


def _check_segment(value: str, kind: str) -> str:
    """Validate that a caller-supplied id is safe as a single path segment.

    Ids are used directly as directory/file names; this rejects anything that
    could escape its parent (a defence-in-depth traversal guard). Ids the board
    generates -- ``secrets.token_hex`` channel ids, ``hex_name`` artifact ids,
    session ids -- always pass.

    Args:
        value: The id to validate.
        kind: Human label used in the error message ("channel", "artifact", ...).

    Returns:
        ``value`` unchanged when safe.

    Raises:
        BoardError: If the id is empty or contains a path separator, ``..``, or
            a NUL byte.
    """
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise BoardError(f"invalid {kind} id: {value!r}")
    return value


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string at second precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    """Load and parse a JSON object from ``path``."""
    return json.loads(path.read_text())


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a temp file + rename."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _seq_files(directory: Path) -> list[int]:
    """Return the sorted sequence numbers of ``NNNNNN.json`` files in a dir."""
    if not directory.exists():
        return []
    seqs = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
    return sorted(seqs)


def _max_seq(directory: Path) -> int:
    """Return the highest ``NNNNNN.json`` sequence number in a dir (0 if none)."""
    return max((int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()), default=0)


def _append_numbered(directory: Path, payload: dict[str, Any]) -> int:
    """Atomically write ``payload`` to the next free ``NNNNNN.json`` slot.

    The payload is written to a private temp file in full, then hard-linked into
    the next free numbered slot. ``os.link`` is atomic and exclusive -- it fails
    if the slot already exists, so concurrent writers just retry the next number,
    lock-free. Because a slot only ever appears as a link to an already-complete
    file, a crash mid-write can never leave a partial ``NNNNNN.json`` for readers
    to choke on (unlike a direct write into the slot).

    Args:
        directory: Target directory (created if missing).
        payload: JSON object to store; its ``seq`` key is set to the slot number.

    Returns:
        The allocated sequence number.
    """
    directory.mkdir(parents=True, exist_ok=True)
    n = _max_seq(directory) + 1
    tmp = directory / f".{secrets.token_hex(8)}.tmp"
    try:
        while True:
            payload["seq"] = n
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, indent=2)
            slot = directory / f"{n:0{_SEQ_WIDTH}d}.json"
            try:
                os.link(tmp, slot)
            except FileExistsError:
                n += 1
                continue
            return n
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Agents / directory
# --------------------------------------------------------------------------- #
def _agent_path(agent_id: str) -> Path:
    return config.agents_dir() / f"{agent_id}.json"


def _agent_home(agent_id: str) -> Path:
    return config.agents_dir() / agent_id


def register_agent(
    agent_id: str, name: str | None = None, blurb: str | None = None, cwd: str = ""
) -> Agent:
    """Create or refresh an agent's record and personal directory.

    Re-registering an existing agent updates ``last_seen`` and ``status`` and
    overwrites ``name``/``cwd``/``blurb`` only when a new value is supplied.

    Args:
        agent_id: Stable unique id for the agent.
        name: Human-friendly handle; defaults to ``agent_id`` on first registration.
        blurb: Optional self-description; preserved if ``None``.
        cwd: Working directory the agent registered from.

    Returns:
        The persisted :class:`~agent_board.models.Agent`.

    Raises:
        BoardError: If ``agent_id`` is not a safe path segment.
    """
    _check_segment(agent_id, "agent")
    config.ensure_layout()
    path = _agent_path(agent_id)
    now = _utc_now()
    if path.exists():
        data = _read_json(path)
        data["last_seen"] = now
        data["status"] = "online"
        if name:
            data["name"] = name
        if cwd:
            data["cwd"] = cwd
        if blurb is not None:
            data["blurb"] = blurb
        agent = Agent.from_dict(data)
    else:
        agent = Agent(
            id=agent_id,
            name=name or agent_id,
            blurb=blurb or "",
            cwd=cwd,
            started_at=now,
            last_seen=now,
            status="online",
        )
    _write_json_atomic(path, agent.to_dict())
    (_agent_home(agent_id) / "inbox").mkdir(parents=True, exist_ok=True)
    return agent


def get_agent(agent_id: str) -> Agent | None:
    """Return the agent with ``agent_id``, or ``None`` if not registered."""
    path = _agent_path(agent_id)
    if not path.exists():
        return None
    return Agent.from_dict(_read_json(path))


def set_bio(agent_id: str, blurb: str) -> Agent:
    """Update an agent's self-description.

    Raises:
        BoardError: If the agent is not registered.
    """
    agent = get_agent(agent_id)
    if agent is None:
        raise BoardError(f"agent '{agent_id}' is not registered")
    agent.blurb = blurb
    agent.last_seen = _utc_now()
    _write_json_atomic(_agent_path(agent_id), agent.to_dict())
    return agent


def set_status(agent_id: str, status: str) -> None:
    """Set an agent's ``status`` (e.g. ``"offline"``); no-op if unregistered."""
    agent = get_agent(agent_id)
    if agent is None:
        return
    agent.status = status
    agent.last_seen = _utc_now()
    _write_json_atomic(_agent_path(agent_id), agent.to_dict())


def list_agents() -> list[Agent]:
    """Return all registered agents, sorted by name."""
    adir = config.agents_dir()
    if not adir.exists():
        return []
    agents = [Agent.from_dict(_read_json(p)) for p in adir.glob("*.json")]
    return sorted(agents, key=lambda a: a.name.lower())


def resolve_agent(target: str) -> str | None:
    """Resolve a ``--to`` target (an id or a name) to an agent id.

    Args:
        target: An agent id or a (case-insensitive) name.

    Returns:
        The matching agent id, or ``None`` if nothing matches.
    """
    path = _agent_path(target)
    if path.exists():
        # Return the canonical id from the record, not the caller's spelling --
        # on case-insensitive filesystems ``target`` may differ in case.
        return Agent.from_dict(_read_json(path)).id
    for agent in list_agents():
        if agent.name.lower() == target.lower():
            return agent.id
    return None


def unique_agent_name(base: str, agent_id: str) -> str:
    """Return ``base`` if free, else a variant suffixed for this agent.

    Keeps a friendly name in the common case but disambiguates when another
    agent already uses ``base`` -- e.g. two sessions started in the same
    directory would otherwise both be named after its basename, making a
    ``--to <name>`` target ambiguous.

    Args:
        base: The desired (human-friendly) name.
        agent_id: The id of the agent being (re-)registered; an existing record
            with this id does not count as a collision.

    Returns:
        ``base`` if unused by any other agent, otherwise ``f"{base}-{agent_id[:4]}"``.
    """
    taken = {a.name for a in list_agents() if a.id != agent_id}
    if base not in taken:
        return base
    return f"{base}-{agent_id[:4]}"


_ADJECTIVES = (
    "amber",
    "brisk",
    "calm",
    "clever",
    "crimson",
    "dapper",
    "eager",
    "fleet",
    "gentle",
    "jolly",
    "keen",
    "lively",
    "mellow",
    "nimble",
    "plucky",
    "quiet",
    "rapid",
    "sage",
    "swift",
    "witty",
)
_ANIMALS = (
    "otter",
    "heron",
    "lynx",
    "falcon",
    "badger",
    "marten",
    "gecko",
    "raven",
    "ibex",
    "tapir",
    "koala",
    "civet",
    "quokka",
    "numbat",
    "fennec",
    "wombat",
    "osprey",
    "dingo",
    "sable",
    "oryx",
)


def _generate_name(agent_id: str) -> str:
    """Derive a memorable ``adjective-animal`` handle deterministically from an id.

    Deterministic so a session keeps the same name across resume/compact
    re-fires. Collisions are possible (400 combinations) and resolved by
    :func:`unique_agent_name`.
    """
    digest = int(hashlib.sha256(agent_id.encode()).hexdigest(), 16)
    adjective = _ADJECTIVES[digest % len(_ADJECTIVES)]
    animal = _ANIMALS[(digest // len(_ADJECTIVES)) % len(_ANIMALS)]
    return f"{adjective}-{animal}"


def ensure_registered(
    agent_id: str, name: str | None = None, blurb: str | None = None, cwd: str = ""
) -> Agent:
    """Register an agent on first participation, or refresh it if already known.

    This is the **opt-in** entry point: the active verbs (create/accept/post/
    set-bio/register) call it so a session only joins the board once it actually
    engages. Passive checks (check-messages, list-agents, read, whoami) never
    call it, so idle sessions never clutter the directory.

    Args:
        agent_id: The acting agent.
        name: Explicit display name; defaults to a unique memorable handle.
        blurb: Explicit self-description; on first registration defaults to the
            working directory. ``None`` leaves an existing blurb untouched.
        cwd: Working directory to record (and seed the default blurb from).

    Returns:
        The persisted :class:`~agent_board.models.Agent`.
    """
    existing = get_agent(agent_id)
    if existing is None:
        final_name = name or unique_agent_name(_generate_name(agent_id), agent_id)
        final_blurb = blurb if blurb is not None else (f"working in {cwd}" if cwd else "")
        return register_agent(agent_id, name=final_name, blurb=final_blurb, cwd=cwd)

    if name:
        existing.name = name
    if blurb is not None:
        existing.blurb = blurb
    if cwd:
        existing.cwd = cwd
    existing.status = "online"
    existing.last_seen = _utc_now()
    _write_json_atomic(_agent_path(agent_id), existing.to_dict())
    return existing


# --------------------------------------------------------------------------- #
# Inbox (invites + notifications)
# --------------------------------------------------------------------------- #
def deliver(agent_id: str, item: dict[str, Any]) -> int:
    """Append a notification ``item`` to an agent's inbox.

    Returns:
        The inbox sequence number assigned to the item.
    """
    payload = {"ts": _utc_now(), **item}
    return _append_numbered(_agent_home(agent_id) / "inbox", payload)


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #
def _channel_dir(channel_id: str) -> Path:
    return config.channels_dir() / _check_segment(channel_id, "channel")


def _meta_path(channel_id: str) -> Path:
    return _channel_dir(channel_id) / "meta.json"


def load_channel(channel_id: str) -> Channel:
    """Load a channel's metadata.

    Raises:
        BoardError: If the channel does not exist.
    """
    path = _meta_path(channel_id)
    if not path.exists():
        raise BoardError(f"channel '{channel_id}' not found (it may be closed)")
    return Channel.from_dict(_read_json(path))


def create_channel(creator_id: str, target: str, topic: str = "") -> Channel:
    """Open a channel between the creator and a target agent.

    Both agents are added as members immediately and an invite notification is
    delivered to the target's inbox.

    Args:
        creator_id: The agent opening the channel.
        target: The other agent, given as an id or a name.
        topic: Optional free-text subject.

    Returns:
        The newly created :class:`~agent_board.models.Channel`.

    Raises:
        BoardError: If ``target`` cannot be resolved to a registered agent.
    """
    config.ensure_layout()
    target_id = resolve_agent(target)
    if target_id is None:
        raise BoardError(f"no registered agent matches '{target}'")

    channel_id = secrets.token_hex(4)
    cdir = _channel_dir(channel_id)
    (cdir / "messages").mkdir(parents=True)
    (cdir / "artifacts").mkdir()
    (cdir / "close").mkdir()

    members = [creator_id] if creator_id == target_id else [creator_id, target_id]
    channel = Channel(
        id=channel_id, topic=topic, members=members, created=_utc_now(), status="open"
    )
    _write_json_atomic(_meta_path(channel_id), channel.to_dict())
    _append_event(channel_id, creator_id, "system", text=f"channel opened by {creator_id}")
    deliver(
        target_id,
        {"type": "invite", "channel": channel_id, "from": creator_id, "topic": topic},
    )
    logger.debug("created channel {} ({} -> {})", channel_id, creator_id, target_id)
    return channel


def accept_channel(channel_id: str, agent_id: str) -> None:
    """Mark an agent as having joined a channel by posting a ``joined`` event.

    Raises:
        BoardError: If the channel does not exist or the agent is not a member.
    """
    channel = load_channel(channel_id)
    if agent_id not in channel.members:
        raise BoardError(f"agent '{agent_id}' is not a member of channel '{channel_id}'")
    _append_event(channel_id, agent_id, "system", text=f"{agent_id} joined")


def _append_event(
    channel_id: str,
    author: str,
    etype: str,
    text: str | None = None,
    artifact: dict[str, Any] | None = None,
) -> Event:
    """Append an event to a channel's log and return it."""
    payload: dict[str, Any] = {
        "ts": _utc_now(),
        "author": author,
        "type": etype,
        "text": text,
        "artifact": artifact,
    }
    seq = _append_numbered(_channel_dir(channel_id) / "messages", payload)
    payload["seq"] = seq
    return Event.from_dict(payload)


def post(
    channel_id: str, author: str, text: str | None = None, artifact_path: str | None = None
) -> Event:
    """Post a message and/or share an artifact to a channel.

    Args:
        channel_id: Target channel.
        author: Posting agent id.
        text: Optional message body.
        artifact_path: Optional path to a file to copy into the channel.

    Returns:
        The appended :class:`~agent_board.models.Event`.

    Raises:
        BoardError: If the channel is missing, the author is not a member, or
            neither ``text`` nor ``artifact_path`` is provided.
    """
    channel = load_channel(channel_id)
    if author not in channel.members:
        raise BoardError(f"agent '{author}' is not a member of channel '{channel_id}'")
    if text is None and artifact_path is None:
        raise BoardError("nothing to post: provide --text and/or --artifact")

    artifact: dict[str, Any] | None = None
    if artifact_path is not None:
        src = Path(artifact_path)
        if not src.is_file():
            raise BoardError(f"artifact '{artifact_path}' is not a readable file")
        artifact_id = f"{secrets.token_hex(4)}_{src.name}"
        dest = _channel_dir(channel_id) / "artifacts" / artifact_id
        shutil.copyfile(src, dest)
        artifact = {"id": artifact_id, "name": src.name, "size": dest.stat().st_size}

    etype = "artifact" if artifact is not None else "message"
    return _append_event(channel_id, author, etype, text=text, artifact=artifact)


def read_events(channel_id: str, since: int = 0) -> tuple[list[Event], int]:
    """Return events with ``seq > since`` plus the channel's current head.

    Args:
        channel_id: Target channel.
        since: Cursor; only events strictly newer than this are returned.

    Returns:
        A ``(events, head)`` tuple where ``head`` is the maximum sequence number
        present (0 if the channel is empty).

    Raises:
        BoardError: If the channel does not exist.
    """
    load_channel(channel_id)  # existence check
    mdir = _channel_dir(channel_id) / "messages"
    seqs = _seq_files(mdir)
    events = [
        Event.from_dict(_read_json(mdir / f"{s:0{_SEQ_WIDTH}d}.json")) for s in seqs if s > since
    ]
    head = seqs[-1] if seqs else 0
    return events, head


def artifact_path(channel_id: str, artifact_id: str) -> Path:
    """Return the on-disk path of a shared artifact.

    Raises:
        BoardError: If the artifact does not exist in the channel.
    """
    path = _channel_dir(channel_id) / "artifacts" / _check_segment(artifact_id, "artifact")
    if not path.exists():
        raise BoardError(f"artifact '{artifact_id}' not found in channel '{channel_id}'")
    return path


def member_channels(agent_id: str) -> list[Channel]:
    """Return all open channels that ``agent_id`` is a member of, sorted by id."""
    cdir = config.channels_dir()
    if not cdir.exists():
        return []
    result: list[Channel] = []
    for meta in cdir.glob("*/meta.json"):
        channel = Channel.from_dict(_read_json(meta))
        if agent_id in channel.members:
            result.append(channel)
    return sorted(result, key=lambda c: c.id)


def vote_close(channel_id: str, agent_id: str) -> bool:
    """Record an agent's vote to close a channel; delete it once all have voted.

    Args:
        channel_id: Target channel.
        agent_id: Voting member.

    Returns:
        ``True`` if this vote completed the set and the channel was deleted,
        ``False`` if more members still need to vote.

    Raises:
        BoardError: If the channel is missing or the agent is not a member.
    """
    channel = load_channel(channel_id)
    if agent_id not in channel.members:
        raise BoardError(f"agent '{agent_id}' is not a member of channel '{channel_id}'")

    close_dir = _channel_dir(channel_id) / "close"
    close_dir.mkdir(exist_ok=True)
    vote_file = close_dir / agent_id
    is_new_vote = not vote_file.exists()
    vote_file.write_text(_utc_now())

    voted = {p.name for p in close_dir.iterdir()}
    if set(channel.members).issubset(voted):
        shutil.rmtree(_channel_dir(channel_id))
        logger.debug("closed and deleted channel {}", channel_id)
        return True

    # Notify the other members via the event log so a peer's check-messages
    # surfaces the proposal and they know to close too (otherwise the channel
    # would just linger until the TTL gc).
    if is_new_vote:
        _append_event(
            channel_id,
            agent_id,
            "system",
            text=(
                f"{agent_id} proposed closing this channel; "
                f"close it too (agent-board close {channel_id}) to delete it"
            ),
        )
    channel.status = "closing"
    _write_json_atomic(_meta_path(channel_id), channel.to_dict())
    return False


# --------------------------------------------------------------------------- #
# Cursors + aggregated checks
# --------------------------------------------------------------------------- #
def _cursors_path(agent_id: str) -> Path:
    return _agent_home(agent_id) / "cursors.json"


def get_cursors(agent_id: str) -> dict[str, int]:
    """Return the agent's per-channel (and ``inbox``) cursor map."""
    path = _cursors_path(agent_id)
    if not path.exists():
        return {}
    return {k: int(v) for k, v in _read_json(path).items()}


def _write_cursors(agent_id: str, cursors: dict[str, int]) -> None:
    _agent_home(agent_id).mkdir(parents=True, exist_ok=True)
    _write_json_atomic(_cursors_path(agent_id), cursors)


def check_messages(agent_id: str, advance: bool = True) -> dict[str, Any]:
    """Collect everything new for an agent since its last cursors.

    Scans the agent's inbox and every open channel it belongs to, returning only
    items newer than the stored cursor. When ``advance`` is true the cursors are
    moved forward so the same items are not reported twice.

    Args:
        agent_id: The agent to check on behalf of.
        advance: Whether to persist advanced cursors.

    Returns:
        A mapping ``{"invites": [...], "channels": {cid: {"topic", "events"}}}``.
        Empty (with no side effects) if the agent has not opted in / registered.
    """
    # Passive check: never registers an unregistered session (opt-in model), but
    # does refresh presence for one that has joined the board.
    if get_agent(agent_id) is None:
        return {"invites": [], "channels": {}}
    set_status(agent_id, "online")
    cursors = get_cursors(agent_id)

    inbox = _agent_home(agent_id) / "inbox"
    inbox_seqs = _seq_files(inbox)
    inbox_last = cursors.get("inbox", 0)
    invites = [_read_json(inbox / f"{s:0{_SEQ_WIDTH}d}.json") for s in inbox_seqs if s > inbox_last]
    if inbox_seqs and advance:
        cursors["inbox"] = inbox_seqs[-1]

    channels: dict[str, Any] = {}
    for channel in member_channels(agent_id):
        last = cursors.get(channel.id, 0)
        events, head = read_events(channel.id, last)
        # Surface only others' events (an agent should not be woken by its own
        # posts), but still advance the cursor past everything that was read.
        foreign = [e for e in events if e.author != agent_id]
        if foreign:
            channels[channel.id] = {
                "topic": channel.topic,
                "events": [e.to_dict() for e in foreign],
            }
        if events and advance:
            cursors[channel.id] = head

    if advance:
        _write_cursors(agent_id, cursors)
    return {"invites": invites, "channels": channels}


def has_updates(result: dict[str, Any]) -> bool:
    """Return ``True`` if a :func:`check_messages` result contains anything new."""
    return bool(result.get("invites")) or bool(result.get("channels"))


def channel_overviews(agent_id: str) -> list[dict[str, Any]]:
    """List the agent's open channels with its unread count for each.

    Unlike :func:`check_messages` (which returns only new deltas and advances
    cursors), this is a non-mutating snapshot an agent can use to see and resume
    its active conversations.

    Args:
        agent_id: The agent whose channels to list.

    Returns:
        One ``{"channel": Channel, "unread": int, "head": int}`` per channel,
        sorted by channel id. ``unread`` counts only events authored by others
        since the agent's cursor.
    """
    cursors = get_cursors(agent_id)
    overviews: list[dict[str, Any]] = []
    for channel in member_channels(agent_id):
        events, head = read_events(channel.id, cursors.get(channel.id, 0))
        unread = sum(1 for e in events if e.author != agent_id)
        overviews.append({"channel": channel, "unread": unread, "head": head})
    return overviews


def poll_updates(
    agent_id: str, baseline: dict[str, int], channel: str | None = None
) -> tuple[dict[str, Any], dict[str, int]]:
    """Non-mutating check for activity newer than ``baseline``.

    Unlike :func:`check_messages`, this never touches the agent's stored cursors --
    it backs the blocking ``wait`` command, which only *notifies* of new activity
    and leaves the authoritative read (and cursor advance) to the agent's own
    check-messages/read once it's woken.

    Args:
        agent_id: The waiting agent.
        baseline: Per-source last-seen seq (``"inbox"`` plus channel ids). Seed it
            from :func:`get_cursors` to also surface anything already unread.
        channel: If set, watch only this channel and ignore the inbox.

    Returns:
        ``(result, advanced_baseline)`` -- ``result`` has the same shape as
        :func:`check_messages` (others' events only); ``advanced_baseline`` is
        ``baseline`` moved past everything seen this poll (so a follow loop won't
        re-report it).
    """
    result: dict[str, Any] = {"invites": [], "channels": {}}
    advanced = dict(baseline)

    if channel is None:
        inbox = _agent_home(agent_id) / "inbox"
        inbox_seqs = _seq_files(inbox)
        last = baseline.get("inbox", 0)
        invites = [_read_json(inbox / f"{s:0{_SEQ_WIDTH}d}.json") for s in inbox_seqs if s > last]
        if invites:
            result["invites"] = invites
        if inbox_seqs:
            advanced["inbox"] = inbox_seqs[-1]

    channels = member_channels(agent_id)
    if channel is not None:
        channels = [c for c in channels if c.id == channel]
    for ch in channels:
        try:
            events, head = read_events(ch.id, baseline.get(ch.id, 0))
        except BoardError:
            continue  # channel closed/deleted between listing and read
        foreign = [e for e in events if e.author != agent_id]
        if foreign:
            result["channels"][ch.id] = {
                "topic": ch.topic,
                "events": [e.to_dict() for e in foreign],
            }
        if events:
            advanced[ch.id] = head
    return result, advanced


# --------------------------------------------------------------------------- #
# Garbage collection
# --------------------------------------------------------------------------- #
def _channel_last_activity(channel_id: str) -> float:
    """Return the mtime of a channel's most recently touched file."""
    cdir = _channel_dir(channel_id)
    mtimes = [p.stat().st_mtime for p in cdir.rglob("*") if p.is_file()]
    mtimes.append(cdir.stat().st_mtime)
    return max(mtimes)


def gc(ttl: int | None = None, now: float | None = None) -> dict[str, list[str]]:
    """Reap idle channels and mark stale agents offline.

    Args:
        ttl: Channel idle TTL in seconds; defaults to :func:`config.ttl_seconds`.
        now: Override for the current epoch time (used in tests).

    Returns:
        A mapping ``{"reaped_channels": [...], "offline_agents": [...],
        "pruned_agents": [...]}``. Agents idle past
        :data:`config.DEFAULT_AGENT_PRUNE_SECONDS` have their record and home
        directory deleted (a fresh registration recreates them).
    """
    ttl = config.ttl_seconds() if ttl is None else ttl
    now = time.time() if now is None else now

    reaped: list[str] = []
    cdir = config.channels_dir()
    if cdir.exists():
        for child in cdir.iterdir():
            if not (child / "meta.json").exists():
                continue
            if now - _channel_last_activity(child.name) > ttl:
                shutil.rmtree(child)
                reaped.append(child.name)
                logger.debug("gc reaped idle channel {}", child.name)

    offline: list[str] = []
    for agent in list_agents():
        if agent.status == "online" and agent.last_seen:
            try:
                seen = datetime.fromisoformat(agent.last_seen).timestamp()
            except ValueError:
                continue
            if now - seen > config.DEFAULT_AGENT_STALE_SECONDS:
                set_status(agent.id, "offline")
                offline.append(agent.id)

    pruned: list[str] = []
    for agent in list_agents():
        try:
            seen = datetime.fromisoformat(agent.last_seen).timestamp()
        except ValueError:
            continue  # empty/garbage timestamp -- leave it alone
        if now - seen > config.DEFAULT_AGENT_PRUNE_SECONDS:
            _agent_path(agent.id).unlink(missing_ok=True)
            shutil.rmtree(_agent_home(agent.id), ignore_errors=True)
            pruned.append(agent.id)
            logger.debug("gc pruned long-offline agent {}", agent.id)

    return {"reaped_channels": reaped, "offline_agents": offline, "pruned_agents": pruned}
