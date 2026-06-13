"""Typed records persisted as JSON on the board.

Each model is a thin dataclass with explicit ``to_dict``/``from_dict`` helpers so
that the on-disk JSON schema is decoupled from the in-memory representation and
unknown future fields are ignored gracefully on read.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Return only the keys of ``data`` that are declared fields of ``cls``."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


@dataclass
class Agent:
    """A participant registered on the board.

    Attributes:
        id: Stable unique id (for Claude Code sessions, the session id).
        name: Human-friendly handle used for discovery and as a ``--to`` target.
        blurb: Optional self-description of who the agent is / what it works on.
        cwd: Working directory the agent registered from.
        started_at: ISO-8601 UTC timestamp of first registration.
        last_seen: ISO-8601 UTC timestamp of most recent activity.
        status: Either ``"online"`` or ``"offline"``.
    """

    id: str
    name: str
    blurb: str = ""
    cwd: str = ""
    started_at: str = ""
    last_seen: str = ""
    status: str = "online"

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable mapping for this agent."""
        return {
            "id": self.id,
            "name": self.name,
            "blurb": self.blurb,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "last_seen": self.last_seen,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        """Build an :class:`Agent` from on-disk JSON, ignoring unknown keys."""
        return cls(**_filter_known(cls, data))


@dataclass
class Channel:
    """A private conversation between two (or more) agents.

    Attributes:
        id: Random channel id; the handle passed between agents.
        topic: Free-text subject set by the creator.
        members: Agent ids permitted to participate.
        created: ISO-8601 UTC timestamp.
        status: Either ``"open"`` or ``"closing"``.
    """

    id: str
    topic: str = ""
    members: list[str] = field(default_factory=list)
    created: str = ""
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable mapping for this channel."""
        return {
            "id": self.id,
            "topic": self.topic,
            "members": list(self.members),
            "created": self.created,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        """Build a :class:`Channel` from on-disk JSON, ignoring unknown keys."""
        return cls(**_filter_known(cls, data))


@dataclass
class Event:
    """A single entry in a channel's append-only log.

    Attributes:
        seq: Monotonic per-channel sequence number (also the file name).
        ts: ISO-8601 UTC timestamp.
        author: Agent id that produced the event.
        type: One of ``"message"``, ``"artifact"``, or ``"system"``.
        text: Optional message body.
        artifact: Optional ``{"id", "name", "size"}`` mapping for shared files.
    """

    seq: int
    ts: str
    author: str
    type: str
    text: str | None = None
    artifact: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable mapping for this event."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "author": self.author,
            "type": self.type,
            "text": self.text,
            "artifact": self.artifact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Build an :class:`Event` from on-disk JSON, ignoring unknown keys."""
        return cls(**_filter_known(cls, data))
