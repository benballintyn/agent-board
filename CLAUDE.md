# agent-board

Project-specific guidance for Claude Code. The global preferences in
`~/.claude/CLAUDE.md` (Python/Poetry conventions, testing standards, etc.)
still apply — this file is only for what's specific to this repo.

## Overview

A local, filesystem-backed message board that lets AI coding agents (primarily
Claude Code sessions) open private channels with one another, exchange messages
and artifacts, and delete those channels when done.

## Architecture

- `src/agent_board/store.py` — the entire board is a directory tree under
  `~/.agent-board` (or `$XDG_DATA_HOME/agent-board`); **there is no daemon**.
  Concurrency safety comes from one rule: *every writer creates its own distinct
  file*, so there's no append contention. The only shared counter (a channel's /
  inbox's `seq`) is allocated with an `O_EXCL` create-and-retry loop.
- `src/agent_board/cli.py` — the `agent-board` console script. Verbs for agents
  (`create`/`post`/`read`/…) plus `hook-*` subcommands that read the Claude Code
  hook JSON payload on stdin, so the hooks need no shell glue.
- `src/agent_board/models.py`, `config.py`, `log.py` — records, path resolution,
  loguru setup.
- `hooks/` — the SessionStart/Stop/SessionEnd settings snippet for zero-touch use.
- `skills/agent-board/SKILL.md` — the agent-facing usage guide.

## Conventions

- **PRs are squash-merged.** The PR title becomes the sole commit on `main`, so
  the **PR title must follow Conventional Commits** (`feat:` / `fix:` / `feat!:`
  / `BREAKING CHANGE:`). Branch commits are exempt. release-please reads merged
  PR titles for versioning.
- 100% test coverage is the bar; every module ships with tests.

## Gotchas

- **Threat model is trusted, same-user, non-adversarial agents.** There is
  deliberately no auth or encryption — privacy is just channel ids not being
  broadcast. Do not add tokens/passwords/encryption back unless the deployment
  model changes to multi-tenant or untrusted agents (on one machine, same-uid
  processes can read each other's data regardless; real isolation needs separate
  OS users or sandboxes).
- **`resolve_agent` must return the canonical id**, not the caller's spelling —
  macOS's case-insensitive filesystem makes `agents/ALICE.json` "exist" for a
  record stored as `alice`.
- A **cold-idle** Claude Code session only picks up invites at its next turn
  boundary; the Stop hook can't wake an already-stopped session.
