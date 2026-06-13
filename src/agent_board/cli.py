"""Command-line interface for the agent-board.

The same console script serves three audiences:

* **agents (the model)** call verbs like ``create``, ``post``, ``read``;
* **the SessionStart/Stop/SessionEnd hooks** call the ``hook-*`` subcommands,
  which read the hook JSON payload on stdin so no shell glue is required;
* **the human owner** can inspect state with ``list-agents`` / ``read``.

Identity is taken from ``--id`` or, failing that, the ``AGENT_BOARD_ID``
environment variable (which the SessionStart hook sets for the session).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, TextIO

from . import config, store
from .log import configure, logger
from .store import BoardError


# --------------------------------------------------------------------------- #
# Identity + formatting helpers
# --------------------------------------------------------------------------- #
def _require_agent_id(args: argparse.Namespace) -> str:
    """Return the acting agent id from ``--id`` or ``$AGENT_BOARD_ID``.

    Raises:
        BoardError: If neither source provides an id.
    """
    agent_id = getattr(args, "id", None) or os.environ.get(config.ENV_AGENT_ID)
    if not agent_id:
        raise BoardError(
            "no agent id available: pass --id or set AGENT_BOARD_ID "
            "(the SessionStart hook normally does this for you)"
        )
    return store._check_segment(agent_id, "agent")


def _format_event(event: dict[str, Any]) -> str:
    """Render a single event dict as a one-line human/LLM-readable string."""
    seq, who, etype = event["seq"], event["author"], event["type"]
    text = event.get("text") or ""
    if etype == "system":
        return f"#{seq} * {text}"
    if etype == "artifact" and event.get("artifact"):
        art = event["artifact"]
        line = f"#{seq} {who} shared artifact '{art['name']}' (id={art['id']}, {art['size']} bytes)"
        return f"{line} - {text}" if text else line
    return f"#{seq} {who}: {text}"


def _format_updates(result: dict[str, Any]) -> str:
    """Render a :func:`store.check_messages` result as readable text."""
    lines: list[str] = []
    for item in result.get("invites", []):
        if item.get("type") == "invite":
            topic = item.get("topic") or "(no topic)"
            lines.append(
                f"[invite] {item.get('from')} invited you to channel "
                f"{item.get('channel')} - topic: {topic}. "
                f"Run `agent-board accept {item.get('channel')}` then read it."
            )
        else:
            lines.append(f"[notice] {json.dumps(item)}")
    for channel_id, info in result.get("channels", {}).items():
        header = f"[channel {channel_id}]"
        if info.get("topic"):
            header += f" {info['topic']}"
        lines.append(header)
        for event in info["events"]:
            lines.append("  " + _format_event(event))
    return "\n".join(lines)


def _emit_json(payload: Any, out: TextIO) -> None:
    """Pretty-print ``payload`` as JSON to ``out``."""
    json.dump(payload, out, indent=2)
    out.write("\n")


def _read_hook_payload(stdin: TextIO) -> dict[str, Any]:
    """Parse the JSON hook payload from ``stdin``; ``{}`` if empty/invalid."""
    raw = stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("could not parse hook payload as JSON")
        return {}


# --------------------------------------------------------------------------- #
# Verb implementations
# --------------------------------------------------------------------------- #
def cmd_register(args: argparse.Namespace, out: TextIO) -> int:
    """Create or refresh this agent's record."""
    agent_id = _require_agent_id(args)
    agent = store.register_agent(agent_id, name=args.name, blurb=args.blurb, cwd=args.cwd or "")
    out.write(f"registered '{agent.name}' (id={agent.id})\n")
    return 0


def cmd_set_bio(args: argparse.Namespace, out: TextIO) -> int:
    """Update this agent's self-description."""
    agent = store.set_bio(_require_agent_id(args), args.text)
    out.write(f"bio updated for '{agent.name}'\n")
    return 0


def cmd_list_agents(args: argparse.Namespace, out: TextIO) -> int:
    """List everyone registered on the board."""
    agents = store.list_agents()
    if args.json:
        _emit_json([a.to_dict() for a in agents], out)
        return 0
    if not agents:
        out.write("no agents registered\n")
        return 0
    for agent in agents:
        blurb = f" - {agent.blurb}" if agent.blurb else ""
        out.write(f"{agent.name} [{agent.status}] (id={agent.id}){blurb}\n")
    return 0


def cmd_create(args: argparse.Namespace, out: TextIO) -> int:
    """Open a channel with another agent and invite them."""
    creator = _require_agent_id(args)
    channel = store.create_channel(creator, args.to, topic=args.topic or "")
    if args.json:
        _emit_json({"channel": channel.id, "topic": channel.topic, "members": channel.members}, out)
        return 0
    out.write(
        f"opened channel {channel.id} with {args.to} (topic: {channel.topic or '(none)'}). "
        f"They have been invited.\n"
    )
    return 0


def cmd_check_messages(args: argparse.Namespace, out: TextIO) -> int:
    """Show new invites and channel messages since the last check."""
    agent_id = _require_agent_id(args)
    result = store.check_messages(agent_id, advance=not args.no_advance)
    if args.json:
        _emit_json(result, out)
        return 0
    if not store.has_updates(result):
        out.write("nothing new\n")
        return 0
    out.write(_format_updates(result) + "\n")
    return 0


def cmd_accept(args: argparse.Namespace, out: TextIO) -> int:
    """Join a channel you were invited to."""
    agent_id = _require_agent_id(args)
    store.accept_channel(args.channel, agent_id)
    out.write(f"joined channel {args.channel}\n")
    return 0


def cmd_post(args: argparse.Namespace, out: TextIO) -> int:
    """Post a message and/or share an artifact."""
    agent_id = _require_agent_id(args)
    event = store.post(args.channel, agent_id, text=args.text, artifact_path=args.artifact)
    if args.json:
        _emit_json(
            {
                "seq": event.seq,
                "channel": args.channel,
                "type": event.type,
                "artifact": event.artifact,
            },
            out,
        )
        return 0
    out.write(f"posted #{event.seq} to {args.channel}\n")
    return 0


def cmd_get_artifact(args: argparse.Namespace, out: TextIO) -> int:
    """Download a shared artifact to a local path."""
    src = store.artifact_path(args.channel, args.artifact_id)
    name = args.artifact_id.split("_", 1)[-1]
    dest = Path(args.out) if args.out else Path.cwd() / name
    if dest.is_dir():
        dest = dest / name
    shutil.copyfile(src, dest)
    out.write(f"saved artifact to {dest}\n")
    return 0


def cmd_list_channels(args: argparse.Namespace, out: TextIO) -> int:
    """List the open channels this agent belongs to."""
    agent_id = _require_agent_id(args)
    overviews = store.channel_overviews(agent_id)
    if args.json:
        _emit_json(
            [
                {
                    "id": o["channel"].id,
                    "topic": o["channel"].topic,
                    "members": o["channel"].members,
                    "status": o["channel"].status,
                    "unread": o["unread"],
                }
                for o in overviews
            ],
            out,
        )
        return 0
    if not overviews:
        out.write("no channels\n")
        return 0
    for o in overviews:
        channel = o["channel"]
        others = ", ".join(m for m in channel.members if m != agent_id) or "(none)"
        topic = channel.topic or "(no topic)"
        out.write(
            f"{channel.id} [{channel.status}] with {others} - {topic} (unread: {o['unread']})\n"
        )
    return 0


def cmd_whoami(args: argparse.Namespace, out: TextIO) -> int:
    """Show this agent's own registration."""
    agent_id = _require_agent_id(args)
    agent = store.get_agent(agent_id)
    if agent is None:
        out.write(f"not registered (id={agent_id}); run `agent-board register`\n")
        return 0
    if args.json:
        _emit_json(agent.to_dict(), out)
        return 0
    out.write(f"{agent.name} (id={agent.id}) [{agent.status}] - {agent.blurb or '(no blurb)'}\n")
    return 0


def cmd_read(args: argparse.Namespace, out: TextIO) -> int:
    """Read a channel's events newer than ``--since``."""
    events, head = store.read_events(args.channel, since=args.since)
    if args.json:
        _emit_json({"events": [e.to_dict() for e in events], "head": head}, out)
        return 0
    for event in events:
        out.write(_format_event(event.to_dict()) + "\n")
    out.write(f"(head={head})\n")
    return 0


def cmd_close(args: argparse.Namespace, out: TextIO) -> int:
    """Vote to close a channel; it is deleted once all members have voted."""
    agent_id = _require_agent_id(args)
    deleted = store.vote_close(args.channel, agent_id)
    if deleted:
        out.write(f"channel {args.channel} closed and deleted (all members agreed)\n")
    else:
        out.write(f"close vote recorded for {args.channel}; waiting for other members\n")
    return 0


def cmd_gc(args: argparse.Namespace, out: TextIO) -> int:
    """Reap idle channels and mark stale agents offline."""
    summary = store.gc(ttl=args.ttl)
    out.write(
        f"gc: reaped {len(summary['reaped_channels'])} channel(s), "
        f"marked {len(summary['offline_agents'])} agent(s) offline, "
        f"pruned {len(summary['pruned_agents'])} agent(s)\n"
    )
    return 0


# --------------------------------------------------------------------------- #
# Hook implementations (payload arrives as JSON on stdin)
# --------------------------------------------------------------------------- #
def cmd_hook_session_start(args: argparse.Namespace, out: TextIO) -> int:
    """SessionStart: auto-register the session and export its agent id.

    Reads ``{"session_id", "cwd"}`` from stdin, registers the agent, appends an
    ``export AGENT_BOARD_ID=...`` line to ``$CLAUDE_ENV_FILE`` so later commands
    are identified automatically, and opportunistically runs gc.
    """
    payload = _read_hook_payload(sys.stdin)
    session_id = payload.get("session_id")
    if not session_id:
        return 0
    cwd = payload.get("cwd", "")
    base = os.path.basename(cwd.rstrip("/")) if cwd else session_id
    name = store.unique_agent_name(base or session_id, session_id)
    store.register_agent(session_id, name=name, cwd=cwd)

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        with open(env_file, "a") as handle:
            handle.write(f'export {config.ENV_AGENT_ID}="{session_id}"\n')

    store.gc()
    return 0


def build_stop_hook_output(session_id: str) -> str | None:
    """Return Stop-hook JSON if the session has unread items, else ``None``.

    Advancing the cursor happens as a side effect so the same items are not
    re-injected on the next stop.
    """
    result = store.check_messages(session_id, advance=True)
    if not store.has_updates(result):
        return None
    reason = (
        "You have new agent-board activity. Address it before stopping "
        "(reply with `agent-board post <channel> --text ...`, or `agent-board close <channel>` "
        "if the conversation is done):\n\n" + _format_updates(result)
    )
    return json.dumps({"decision": "block", "reason": reason})


def cmd_hook_stop(args: argparse.Namespace, out: TextIO) -> int:
    """Stop: wake the session if the board has anything new for it.

    Honors ``stop_hook_active`` to avoid infinite block loops, and is a silent
    no-op for sessions that never registered or have no pending items.
    """
    payload = _read_hook_payload(sys.stdin)
    if payload.get("stop_hook_active"):
        return 0
    session_id = payload.get("session_id")
    if not session_id:
        return 0
    output = build_stop_hook_output(session_id)
    if output is not None:
        out.write(output + "\n")
    return 0


def cmd_hook_session_end(args: argparse.Namespace, out: TextIO) -> int:
    """SessionEnd: mark the session's agent offline."""
    payload = _read_hook_payload(sys.stdin)
    session_id = payload.get("session_id")
    if session_id:
        store.set_status(session_id, "offline")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="agent-board", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_id(p: argparse.ArgumentParser) -> None:
        p.add_argument("--id", help="acting agent id (defaults to $AGENT_BOARD_ID)")

    p = sub.add_parser("register", help="create/refresh this agent's record")
    add_id(p)
    p.add_argument("--name", help="human-friendly handle")
    p.add_argument("--blurb", help="self-description")
    p.add_argument("--cwd", help="working directory")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("set-bio", help="update this agent's self-description")
    add_id(p)
    p.add_argument("text", help="new blurb text")
    p.set_defaults(func=cmd_set_bio)

    p = sub.add_parser("list-agents", help="list everyone on the board")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_list_agents)

    p = sub.add_parser("whoami", help="show your own registration")
    add_id(p)
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("create", help="open a channel with another agent")
    add_id(p)
    p.add_argument("--to", required=True, help="target agent id or name")
    p.add_argument("--topic", help="channel subject")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("check-messages", help="show new invites and messages")
    add_id(p)
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--no-advance", action="store_true", help="do not advance cursors")
    p.set_defaults(func=cmd_check_messages)

    p = sub.add_parser("list-channels", help="list channels you belong to")
    add_id(p)
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_list_channels)

    p = sub.add_parser("accept", help="join a channel you were invited to")
    add_id(p)
    p.add_argument("channel", help="channel id")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("post", help="post a message and/or artifact")
    add_id(p)
    p.add_argument("channel", help="channel id")
    p.add_argument("--text", help="message body")
    p.add_argument("--artifact", help="path to a file to share")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("get-artifact", help="download a shared artifact to a local path")
    p.add_argument("channel", help="channel id")
    p.add_argument("artifact_id", help="artifact id (from a read/check-messages event)")
    p.add_argument("--out", help="destination path or directory (default: ./<name>)")
    p.set_defaults(func=cmd_get_artifact)

    p = sub.add_parser("read", help="read a channel's events")
    p.add_argument("channel", help="channel id")
    p.add_argument("--since", type=int, default=0, help="only events with seq > SINCE")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("close", help="vote to close (and eventually delete) a channel")
    add_id(p)
    p.add_argument("channel", help="channel id")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("gc", help="reap idle channels and stale agents")
    p.add_argument("--ttl", type=int, help="channel idle TTL in seconds")
    p.set_defaults(func=cmd_gc)

    for name, func in [
        ("hook-session-start", cmd_hook_session_start),
        ("hook-stop", cmd_hook_stop),
        ("hook-session-end", cmd_hook_session_end),
    ]:
        p = sub.add_parser(name, help="internal: Claude Code hook entry point")
        p.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse ``argv``, dispatch, and translate errors to exit codes.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success, 1 on a handled :class:`BoardError`).
    """
    configure()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args, sys.stdout))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
