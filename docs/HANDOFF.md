# agent-board — Handoff

A living snapshot for resuming this project cold. Describes **now**, not a journal.

## Current state

- **Date:** 2026-06-16
- **Branch:** `main` (this doc is being added on `docs/handoff`)
- **Version:** `0.3.1` — published to PyPI as **`agent-postbox`**, tagged on GitHub, and installed locally.
- **Tests:** `123 passed`, **100% coverage** (statements + branches) via `poetry run pytest`.
- **Lint/type:** `ruff check` clean, `ruff format --check` clean, `mypy src` clean.
- **CI:** green on `main` (run_tests across Python 3.11–3.14, release-please).
- **Open PRs:** only Dependabot [#2](https://github.com/benballintyn/agent-board/pull/2) (pytest 8→9 dev-dep bump), deferred.

One glance: **healthy and shipped.** No red state.

## What this is

A local, **filesystem-backed** message board that lets AI coding agents (mainly Claude Code
sessions) discover each other and hold private, deletable channels for messages + file
artifacts. **No daemon, no network, no auth, no encryption.** The entire board is a directory
tree under `~/.agent-board` (resolves `$AGENT_BOARD_HOME` → `$XDG_DATA_HOME/agent-board` →
`~/.agent-board`). Zero-touch via Claude Code SessionStart/Stop/SessionEnd hooks. See
[CLAUDE.md](../CLAUDE.md) and [README.md](../README.md) for architecture; [docs/one-pager.html](one-pager.html)
/ [docs/agent-board-one-pager.pdf](agent-board-one-pager.pdf) for the shareable explainer.

**Name split (intentional, mirrors vox/vox-llm):** repo = `agent-board`, CLI command =
`agent-board`, Python import = `agent_board`, **PyPI dist = `agent-postbox`** (bare
`agent-board` was rejected by PyPI as too similar to the existing `agentboard`).

## What shipped (the build arc, 0.0.1 → 0.3.1)

Merged PRs, newest first:

- [#18](https://github.com/benballintyn/agent-board/pull/18) chore: gitignore + untrack `.DS_Store`
- [#17](https://github.com/benballintyn/agent-board/pull/17) release 0.3.1
- [#16](https://github.com/benballintyn/agent-board/pull/16) **fix: wake only on content**, not structural system events (the false-wake fix)
- [#15](https://github.com/benballintyn/agent-board/pull/15) release 0.3.0
- [#14](https://github.com/benballintyn/agent-board/pull/14) **feat: `wait` command** for event-driven delivery
- [#13](https://github.com/benballintyn/agent-board/pull/13) release 0.2.0
- [#12](https://github.com/benballintyn/agent-board/pull/12) **feat: opt-in (lazy) registration + memorable agent names**
- [#11](https://github.com/benballintyn/agent-board/pull/11) fix: atomic event/inbox writes (temp + hardlink)
- [#10](https://github.com/benballintyn/agent-board/pull/10) release 0.1.1
- [#9](https://github.com/benballintyn/agent-board/pull/9) fix: harden id handling, prune stale agents, drop deprecated license classifier
- [#8](https://github.com/benballintyn/agent-board/pull/8) **feat: artifact download, channel listing, close notifications, Python 3.14**
- [#7](https://github.com/benballintyn/agent-board/pull/7) release 0.1.0
- [#6](https://github.com/benballintyn/agent-board/pull/6) docs: README badges
- [#5](https://github.com/benballintyn/agent-board/pull/5) / [#4](https://github.com/benballintyn/agent-board/pull/4) / [#3](https://github.com/benballintyn/agent-board/pull/3) release 0.0.1 / publish as `agent-postbox` / PyPI trusted publishing

## Decisions & why (don't re-litigate)

1. **Trusted, same-user, non-adversarial threat model → no auth, no encryption.** On a
   single-user machine all agents share one uid, so **no userland mechanism (token / 0600
   file / env var / daemon) can isolate one agent from another** — a same-uid process can
   always read another's secret and replay it. Real isolation needs an OS boundary (separate
   UNIX users or sandboxes/containers), which is overkill for self-use. Privacy = channel ids
   simply not being broadcast. Do **not** add passwords/tokens/encryption back unless the
   deployment model changes to multi-tenant or untrusted agents.
2. **Filesystem-core, no daemon.** Concurrency safety comes from one rule: *every writer
   creates its own distinct file*. The only shared counter (a channel/inbox `seq`) is
   allocated with an atomic temp-file + `os.link` (hardlink) create-and-retry loop — lock-free.
3. **Registration is opt-in / lazy.** SessionStart only exports `AGENT_BOARD_ID` and runs
   `gc`; it does **not** create an agent record. A session joins on first *active* use
   (`create`/`accept`/`post`/`set-bio`/`register` via `store.ensure_registered`); passive
   verbs never register. **Why:** the Claude *desktop* app's Cowork/agent-mode sessions each
   fire SessionStart from the home dir with no transcript, which flooded the board with 42 `bb`
   agents under auto-register. **Don't move registration back into SessionStart.**
4. **Wake only on content (`store.has_wakeworthy()`).** `wait` and the Stop hook interrupt
   only on a message/artifact/invite — never on structural system events (a peer's
   accept/join/open/close). Those still show on read; they just don't poke. **Why:** a peer's
   `accept` posts a "… joined" event that previously false-woke a waiter twice (via the wait
   *and* the Stop hook), so the agent gave up arming `wait`.
5. **`wait` as a harness-managed background task, not self-rewake.** `agent-board wait`
   blocks on a light non-mutating internal poll (`store.poll_updates`) until wakeworthy
   activity. Run it via Bash `run_in_background` (one-shot "ping me on reply") or the `Monitor`
   tool with `--follow` (standing listener). **Why:** the `asyncRewake` experiment proved it
   *can* wake an idle stream-json session but loops/orphans (re-armed ~50×, runaway risk) —
   rejected. With `wait`, the harness owns the task lifecycle, so no orphans.
6. **`resolve_agent` returns the canonical id**, not the caller's spelling — macOS's
   case-insensitive FS makes `agents/ALICE.json` "exist" for a record stored as `alice`.
7. **Release flow mirrors vox.** Squash-merge **Conventional-Commit PR titles** (the title is
   the sole commit on `main`); release-please opens/maintains the release PR; merging it tags
   and triggers `pypi-publish.yml` via OIDC Trusted Publishing into the `pypi` GitHub env.

## Known issues / open items

- **Shared-host file permissions (offered, NOT yet accepted).** Board root is `0755`,
  metadata/artifacts `0644`, messages `0600`. On a multi-user host other OS users can read the
  board. A proposed **0.3.2 hardening** would set board root `0700` and all files `0600` to
  close same-host exposure. **Awaiting user go-ahead** — not started.
- **Dependabot [#2](https://github.com/benballintyn/agent-board/pull/2)** (pytest 8→9 dev-dep) open and deferred.
- **Agent-to-agent prompt-injection surface.** By design agents read each other's messages;
  a malicious/compromised peer's content is untrusted input. SKILL.md carries trust-boundary
  guardrails; there is no programmatic sanitization (out of scope for the trusted model).
- **Cold-idle sessions** only pick up invites at their next turn boundary; the Stop hook
  can't wake an already-stopped session. `wait`+background-task is the workaround.
- **Identity rotation gotcha:** a project's agent identity rotates as each session fills its
  context and retires (e.g. dcbdg: jolly-fennec → mellow-raven → calm-otter). A peer can go
  silent permanently mid-thread. Treat earlier names as dead; re-convey context to the current
  agent and close retired channels.
- **Tooling landmines:** changing `requires-python` invalidates `poetry.lock` (CI
  `poetry install` fails until you `poetry lock`). `pipx upgrade` lags the PyPI index after
  publish — deploy immediately with `pipx install --force --pip-args=--no-cache-dir agent-postbox==<ver>`.
  Chrome headless `--print-to-pdf` writes a valid PDF but hangs on exit — wrap in `timeout`.

## Next phase plan

No committed next phase. Candidate work, each with acceptance criteria:

- **0.3.2 permissions hardening** *(if user approves)*: board root `0700`, all files `0600`.
  Done when: new tests assert the modes on create, full suite + 100% coverage stays green, a
  pre-existing looser board is tightened on next write.
- **Merge Dependabot #2**: bump pytest to 9.x. Done when: CI green on the bump branch and the
  suite passes locally on pytest 9.
- **Multi-tenant / hosted pivot** *(only if threat model changes)*: this is the server-core
  + E2E path (daemon, tokens, AES escrow) deliberately deferred — see decision #1 and the
  `project-tomte-commercial-intent` memory.

## How to resume

```bash
cd /Users/bb/personal/repos/agent-board
git checkout main && git pull --ff-only
poetry install                              # if deps changed
poetry run pytest                           # 123 passed, 100% coverage
poetry run ruff check . && poetry run ruff format --check . && poetry run mypy src
gh run list --branch main --limit 5         # confirm CI green
```

Release: merge a Conventional-Commit-titled PR, then squash-merge the standing release-please
PR — that tags and publishes to PyPI automatically. Deploy locally with
`pipx install --force --pip-args=--no-cache-dir agent-postbox==<ver>` and re-sync the skill to
`~/.claude/skills/agent-board/SKILL.md`.
