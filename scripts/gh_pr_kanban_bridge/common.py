"""Shared models, constants, and formatting helpers for the bridge."""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TASK_RE = re.compile(r"\bKanban-Task:\s*(t_[0-9a-fA-F]{8,32})\b")
HERMES_BRANCH_PREFIX = "Hermes/"
# This poller is installed as a coder-profile operator utility. Keep the
# default config/state path stable even when a human runs diagnostics from a
# reviewer/default shell with a different HERMES_HOME. Operators can still
# intentionally target another install with --config/--state.
DEFAULT_PROFILE_HOME = Path("/home/agent/.hermes/profiles/coder")
DEFAULT_BASE = DEFAULT_PROFILE_HOME / "github-pr-kanban-bridge"
DEFAULT_CONFIG = DEFAULT_BASE / "config.json"
DEFAULT_STATE = DEFAULT_BASE / "state.json"
DEFAULT_BOARD = os.environ.get("HERMES_KANBAN_BOARD", "default")
DEFAULT_STATE_RETENTION_DAYS = 90
DEFAULT_STATE_MAX_SEEN_ENTRIES = 5000
DEFAULT_STATE_MAX_BASELINED_PRS = 1000
DEFAULT_GITHUB_APP_HELPER = "/home/agent/bin/hermes-gh-app"
DEFAULT_GITHUB_APP_CONFIG = "/home/agent/.hermes/github-apps.json"


@dataclass(frozen=True)
class Activity:
    key: str
    event_type: str
    action: str
    actor: str
    url: str
    created_at: str
    actor_type: str = ""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_utc_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def pr_key_from_activity_key(key: str) -> str:
    return key.split(":", 1)[0]


def normalize_repo(entry: Any) -> str | None:
    repo = entry.get("repo") if isinstance(entry, dict) else entry
    if not isinstance(repo, str):
        return None
    repo = repo.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return None
    return repo


def extract_task_id(body: str | None) -> str | None:
    if not body:
        return None
    m = TASK_RE.search(body)
    return m.group(1) if m else None


def actor_is_ignored(activity: Activity, ignored_actors: set[str], ignore_bots: bool) -> bool:
    actor = activity.actor.lower()
    if actor in ignored_actors:
        return True
    if ignore_bots and (activity.actor_type.lower() == "bot" or actor.endswith("[bot]") or actor in {"github-actions", "github-actions[bot]"}):
        return True
    return False


def user_login_and_type(obj: dict[str, Any]) -> tuple[str, str]:
    user = obj.get("user") or {}
    return (user.get("login") or "unknown", user.get("type") or "")


def _github_reference(url: str) -> str:
    """Return a human-usable GitHub reference without a raw PR URL.

    Hermes Kanban's dispatcher has an ``active_pr`` respawn guard that scans
    recent task comments for raw ``https://github.com/.../pull/<n>`` URLs. The
    bridge writes the wake-up comment immediately before unblocking a card; if
    we include raw PR URLs here, the dispatcher can unblock the card and then
    refuse to spawn the worker because its own bridge comment looks like an
    active-PR handoff. Keep PR/event references copyable enough for humans and
    workers while avoiding that guard pattern.
    """
    if not url:
        return ""
    text = str(url).strip()
    text = re.sub(r"^https?://github\.com/", "github:", text, flags=re.IGNORECASE)
    return text


def activity_comment(repo: str, pr: dict[str, Any], task_id: str, activity: Activity) -> str:
    title = pr.get("title") or "(untitled)"
    number = pr.get("number")
    pr_ref = _github_reference(pr.get("url") or "")
    event_ref = _github_reference(activity.url)
    lines = [
        "GitHub PR activity detected for linked Hermes PR.",
        f"Repo: {repo}",
        f"PR: #{number} {title}",
        f"PR ref: {pr_ref}",
        f"Actor: {activity.actor}",
        f"Event: {activity.event_type} / {activity.action}",
        f"Event ref: {event_ref}",
        "Instruction: address the feedback on the same PR/branch; do not open a replacement PR unless the human asks.",
        "Collaboration requirement: first post a short acknowledgement on the PR/review thread so the reviewer knows Hermes saw the feedback, then post a final PR comment/reply summarizing what changed, what tests ran, and any remaining question. Do not rely on Kanban/session context as the only communication surface.",
        f"Linked Kanban task: {task_id}",
    ]
    return "\n".join(lines)
