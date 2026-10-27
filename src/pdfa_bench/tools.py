"""Running the external tools: one helper, so that every call has a time limit.

A tool that hangs on a file -- a pathological PDF can send a renderer or converter into a loop -- is
killed when its limit (config.py) runs out and raises ToolTimeout. The lane or check that called it
records that as a failure for that one file, and the run goes on instead of stalling.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMED_OUT = "timed out after"


class ToolTimeout(RuntimeError):
    def __init__(self, tool: str, seconds: float) -> None:
        super().__init__(f"{tool} {_TIMED_OUT} {seconds:g}s")


def is_timeout(note: str | None) -> bool:
    """Whether a lane's failure note is a ToolTimeout's message."""
    return bool(note) and _TIMED_OUT in note


def run_tool(command: list[str], timeout: float, *, text: bool = True) -> subprocess.CompletedProcess:
    """Run a tool, capture its output, never raise on its exit code; raise ToolTimeout past `timeout` seconds."""
    try:
        return subprocess.run(command, capture_output=True, text=text, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolTimeout(Path(command[0]).name, timeout) from None
