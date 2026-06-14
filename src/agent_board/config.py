"""Filesystem paths and tunables for the board.

All paths are resolved lazily from the environment on every call so that tests
(and concurrent agents with different settings) can point the board at different
roots without restarting anything.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Environment variable overriding the board root directory.
ENV_HOME = "AGENT_BOARD_HOME"
#: Environment variable carrying the calling agent's id (set by the SessionStart hook).
ENV_AGENT_ID = "AGENT_BOARD_ID"
#: Environment variable overriding the channel idle time-to-live, in seconds.
ENV_TTL = "AGENT_BOARD_TTL"

#: Default channel idle time-to-live: channels untouched for this long are reaped.
DEFAULT_TTL_SECONDS = 24 * 3600
#: An agent whose ``last_seen`` is older than this is reported as offline by ``gc``.
#: Activity (any check-messages from the Stop hook) refreshes last_seen, so a live
#: session stays online; a dead one drops offline within this window.
DEFAULT_AGENT_STALE_SECONDS = 2 * 3600
#: An agent whose ``last_seen`` is older than this is pruned (record + home) by ``gc``.
DEFAULT_AGENT_PRUNE_SECONDS = 7 * 24 * 3600


def board_root() -> Path:
    """Return the board's root directory.

    Resolution order: ``$AGENT_BOARD_HOME``, then ``$XDG_DATA_HOME/agent-board``,
    then ``~/.agent-board``. The directory is not created here.

    Returns:
        The resolved root path.
    """
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "agent-board"
    return Path.home() / ".agent-board"


def agents_dir() -> Path:
    """Return the directory holding agent records (``<root>/agents``)."""
    return board_root() / "agents"


def channels_dir() -> Path:
    """Return the directory holding channels (``<root>/channels``)."""
    return board_root() / "channels"


def ttl_seconds() -> int:
    """Return the configured channel idle TTL in seconds.

    Returns:
        The value of ``$AGENT_BOARD_TTL`` if set and a positive integer, else
        :data:`DEFAULT_TTL_SECONDS`.
    """
    raw = os.environ.get(ENV_TTL)
    if raw is None:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def ensure_layout() -> None:
    """Create the top-level ``agents`` and ``channels`` directories if missing."""
    agents_dir().mkdir(parents=True, exist_ok=True)
    channels_dir().mkdir(parents=True, exist_ok=True)
