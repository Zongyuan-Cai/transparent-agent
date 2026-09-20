"""Transparent Agent: a small terminal coding agent.

The agent core (agent.py, tools/, models.py, events.py) is independent from any
interface: it emits lifecycle events to an optional sink and never depends on
one being present. The TUI is only one possible consumer of those events.
"""

__version__ = "0.1.0"
