"""GitHub eyes-reaction acknowledgement state machine."""
from __future__ import annotations

from typing import Any

from .common import Activity, utc_now
from .github_api import create_eyes_reaction, reaction_endpoint_for_activity


def ensure_reaction_state(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    pending = state.get("pending_reaction_acks")
    if not isinstance(pending, dict):
        pending = {}
        state["pending_reaction_acks"] = pending
    acks = state.get("reaction_acks")
    if not isinstance(acks, dict):
        acks = {}
        state["reaction_acks"] = acks
    return pending, acks


def queue_reaction_ack(
    state: dict[str, Any],
    repo: str,
    task_id: str,
    activity: Activity,
    *,
    ready: bool,
    board: str | None = None,
) -> None:
    endpoint = reaction_endpoint_for_activity(repo, activity)
    if endpoint is None:
        return
    pending, acks = ensure_reaction_state(state)
    if activity.key in acks:
        pending.pop(activity.key, None)
        return
    existing = pending.get(activity.key)
    if not isinstance(existing, dict):
        existing = {}
    existing.update({
        "repo": repo,
        "task_id": task_id,
        "board": board,
        "endpoint": endpoint,
        "observed_at": activity.created_at or utc_now(),
        "ready": bool(existing.get("ready")) or ready,
    })
    pending[activity.key] = existing

def mark_reaction_acks_ready_for_task(state: dict[str, Any], task_id: str, board: str | None = None) -> None:
    pending, _acks = ensure_reaction_state(state)
    for data in pending.values():
        if isinstance(data, dict) and data.get("task_id") == task_id and (board is None or data.get("board") in {board, None}):
            data["ready"] = True


def process_ready_reaction_acks(
    state: dict[str, Any],
    *,
    task_id: str | None = None,
    repo: str | None = None,
    board: str | None = None,
) -> list[str]:
    pending, acks = ensure_reaction_state(state)
    errors: list[str] = []
    for key, data in list(pending.items()):
        if not isinstance(data, dict):
            errors.append(f"reaction ack {key} has invalid pending state")
            continue
        if task_id is not None and data.get("task_id") != task_id:
            continue
        if repo is not None and data.get("repo") != repo:
            continue
        if board is not None and data.get("board") not in {board, None}:
            continue
        if not data.get("ready"):
            continue
        if key in acks:
            pending.pop(key, None)
            continue
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            errors.append(f"reaction ack {key} missing endpoint")
            continue
        ok, msg = create_eyes_reaction(endpoint)
        if not ok:
            errors.append(f"reaction ack failed for {key}: {msg}")
            continue
        acks[key] = str(data.get("observed_at") or utc_now())
        pending.pop(key, None)
    return errors
