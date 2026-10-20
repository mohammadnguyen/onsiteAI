"""Exit codes and output, shared by every subcommand.

Exit codes are the contract the skill branches on:

    0  proceed — the step did what it says
    1  blocked — a limit, a stale verdict, a failing gate, an unusable review,
       an unresolved blocking finding
    2  misuse  — bad arguments, missing or invalid brief, unreadable state

"blocked" is never an error to route around. It is the workflow stopping on
purpose, and the reason is printed and recorded.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_MISUSE = 2


def emit(message: str) -> None:
    print(message, flush=True)
