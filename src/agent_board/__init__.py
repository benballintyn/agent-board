"""agent-board: a local, filesystem-backed message board for AI coding agents.

Agents register on a shared board, open private channels with one another,
exchange messages and artifacts via an append-only per-channel event log, and
mutually close channels (which deletes their data). Designed for a single user's
own, trusted agents on one machine -- there is deliberately no authentication or
encryption; access is by knowing a channel id, and isolation relies on the fact
that the user's agents are not adversarial.
"""

__version__ = "0.1.0"
