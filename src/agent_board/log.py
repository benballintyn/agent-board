"""Centralised loguru configuration for the CLI.

The board is a quiet command-line tool, so by default only warnings and above
are emitted to stderr. Set ``AGENT_BOARD_DEBUG=1`` for verbose tracing.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

_configured = False


def configure() -> None:
    """Install a single stderr sink, idempotently.

    The level is ``DEBUG`` when ``$AGENT_BOARD_DEBUG`` is truthy, otherwise
    ``WARNING``. Calling this more than once is a no-op.
    """
    global _configured
    if _configured:
        return
    logger.remove()
    level = "DEBUG" if os.environ.get("AGENT_BOARD_DEBUG") else "WARNING"
    logger.add(sys.stderr, level=level, format="<level>{level}</level> | {message}")
    _configured = True


__all__ = ["configure", "logger"]
