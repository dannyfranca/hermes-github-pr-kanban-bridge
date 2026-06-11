"""Bridge configuration loading and initialization."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    DEFAULT_BOARD,
    DEFAULT_GITHUB_APP_CONFIG,
    DEFAULT_GITHUB_APP_HELPER,
    DEFAULT_STATE,
    DEFAULT_STATE_MAX_BASELINED_PRS,
    DEFAULT_STATE_MAX_SEEN_ENTRIES,
    DEFAULT_STATE_RETENTION_DAYS,
)
from .json_io import load_json, save_json_atomic


def load_config(path: Path) -> dict[str, Any]:
    cfg = load_json(path, {})
    cfg.setdefault("version", 1)
    cfg.setdefault("enabled", True)
    cfg.setdefault("board", DEFAULT_BOARD)
    cfg.setdefault("state_path", str(DEFAULT_STATE))
    cfg.setdefault("repos", [])
    cfg.setdefault("notify_existing_on_first_scan", False)
    cfg.setdefault("author", "github-pr-kanban-bridge")
    cfg.setdefault("ignored_actors", ["github-actions[bot]", "dependabot[bot]"])
    cfg.setdefault("ignore_bot_actors", True)
    cfg.setdefault("state_retention_days", DEFAULT_STATE_RETENTION_DAYS)
    cfg.setdefault("state_max_seen_entries", DEFAULT_STATE_MAX_SEEN_ENTRIES)
    cfg.setdefault("state_max_baselined_prs", DEFAULT_STATE_MAX_BASELINED_PRS)
    cfg.setdefault("complete_merged_prs", True)
    cfg.setdefault("closed_pr_scan_limit", 30)
    cfg.setdefault("auth", {"mode": "auto"})
    return cfg


def init_config(path: Path, state_path: Path, board: str, force: bool = False) -> None:
    if path.exists() and not force:
        print(f"config exists: {path}")
        return
    sample = {
        "version": 1,
        "enabled": True,
        "board": board,
        "state_path": str(state_path),
        "notify_existing_on_first_scan": False,
        "author": "github-pr-kanban-bridge",
        "ignored_actors": ["github-actions[bot]", "dependabot[bot]"],
        "ignore_bot_actors": True,
        "state_retention_days": DEFAULT_STATE_RETENTION_DAYS,
        "state_max_seen_entries": DEFAULT_STATE_MAX_SEEN_ENTRIES,
        "state_max_baselined_prs": DEFAULT_STATE_MAX_BASELINED_PRS,
        "complete_merged_prs": True,
        "closed_pr_scan_limit": 30,
        "auth": {
            "mode": "auto",
            "github_app": {
                "helper": DEFAULT_GITHUB_APP_HELPER,
                "config": DEFAULT_GITHUB_APP_CONFIG,
            },
        },
        "repos": [],
        "notes": [
            "Add explicit repo allowlist entries as owner/name strings, e.g. \"DannyFranca/example\".",
            "Only open PRs with head branches starting Hermes/ and body marker Kanban-Task: t_xxxxxxxx are considered.",
            "State GC keeps active open Hermes PR entries, then prunes inactive seen/baselined_prs entries older than state_retention_days and caps inactive entries by state_max_* limits.",
            "Merged PRs with a Kanban-Task marker are completed once, tracked by completed_prs in state.",
            "After successful comment+unblock handling, reaction_acks/pending_reaction_acks dedupe GitHub eyes acknowledgements for issue and review comments.",
            "auth.mode auto prefers GH_TOKEN/GITHUB_TOKEN, then gh auth, then repo-scoped GitHub App tokens via hermes-gh-app.",
        ],
    }
    save_json_atomic(path, sample)
    print(f"wrote config: {path}")
