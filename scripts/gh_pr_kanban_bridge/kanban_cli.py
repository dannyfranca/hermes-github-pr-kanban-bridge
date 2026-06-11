"""Hermes Kanban CLI interactions used by the bridge service."""
from __future__ import annotations

import json
from typing import Any

from .commands import run_cmd


def task_status(task_id: str, board: str) -> tuple[bool, str | None, str]:
    args = ["hermes", "kanban", "--board", board, "show", "--json", task_id]
    proc = run_cmd(args, check=False)
    if proc.returncode != 0:
        return False, None, proc.stderr.strip()[:300]
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return False, None, f"invalid kanban JSON: {e}"
    task = data.get("task") if isinstance(data, dict) else None
    if not isinstance(task, dict):
        return False, None, "kanban JSON missing task"
    return True, task.get("status"), ""


def kanban_comment(task_id: str, board: str, author: str, body: str) -> tuple[bool, str]:
    proc = run_cmd(["hermes", "kanban", "--board", board, "comment", "--author", author, task_id, body], check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]


def kanban_unblock(task_id: str, board: str, reason: str) -> tuple[bool, str]:
    proc = run_cmd(["hermes", "kanban", "--board", board, "unblock", "--reason", reason, task_id], check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]


def kanban_complete(task_id: str, board: str, summary: str, metadata: dict[str, Any]) -> tuple[bool, str]:
    proc = run_cmd([
        "hermes", "kanban", "--board", board, "complete",
        "--summary", summary,
        "--metadata", json.dumps(metadata, sort_keys=True),
        task_id,
    ], check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]
