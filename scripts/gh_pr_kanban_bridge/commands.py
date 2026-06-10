"""Subprocess command execution boundary shared by GitHub and Kanban clients."""
from __future__ import annotations

import os
import subprocess


def run_cmd(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Never print env or command lines that might include secrets. We only pass
    # static args and rely on gh's credential store/env internally.
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, env=env)


ORIGINAL_RUN_CMD = run_cmd
