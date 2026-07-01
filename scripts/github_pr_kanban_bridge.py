#!/usr/bin/env python3
"""Compatibility entrypoint for the GitHub PR activity -> Hermes Kanban bridge."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from gh_pr_kanban_bridge import *  # noqa: F401,F403 - compatibility re-exports
from gh_pr_kanban_bridge import auth as _auth
from gh_pr_kanban_bridge import commands as _commands
from gh_pr_kanban_bridge import github_api as _github_api
from gh_pr_kanban_bridge import kanban_cli as _kanban_cli
from gh_pr_kanban_bridge import reactions as _reactions
from gh_pr_kanban_bridge import scanner as _scanner
from gh_pr_kanban_bridge.commands import ORIGINAL_RUN_CMD as _package_run_cmd
from gh_pr_kanban_bridge.github_api import (
    ORIGINAL_GH_JSON as _package_gh_json,
    collect_activities_from_api as _package_collect_activities_from_api,
    gh_ready as _package_gh_ready,
    list_closed_prs_from_api as _package_list_closed_prs_from_api,
    list_open_prs_from_api as _package_list_open_prs_from_api,
)
from gh_pr_kanban_bridge.kanban_cli import (
    kanban_comment as _package_kanban_comment,
    kanban_complete as _package_kanban_complete,
    kanban_unblock as _package_kanban_unblock,
    task_status as _package_task_status,
)
from gh_pr_kanban_bridge.reactions import (
    mark_reaction_acks_ready_for_task as _package_mark_reaction_acks_ready_for_task,
    process_ready_reaction_acks as _package_process_ready_reaction_acks,
    queue_reaction_ack as _package_queue_reaction_ack,
)
from gh_pr_kanban_bridge.scanner import scan as _package_scan


shutil = _auth.shutil


_DEFAULT_COMPAT_RUN_CMD = None
_DEFAULT_COMPAT_GH_JSON = None


def _effective_run_cmd():
    runner = globals().get("run_cmd", _package_run_cmd)
    return _package_run_cmd if runner is _DEFAULT_COMPAT_RUN_CMD else runner


def _sync_command_runner() -> None:
    runner = _effective_run_cmd()

    def compat_runner(args, *, check=True, env_overrides=None):
        try:
            return runner(args, check=check, env_overrides=env_overrides)
        except TypeError as e:
            if "env_overrides" not in str(e):
                raise
            return runner(args, check=check)

    _auth.run_cmd = compat_runner
    _commands.run_cmd = compat_runner
    _github_api.run_cmd = compat_runner
    _kanban_cli.run_cmd = compat_runner


def _sync_compat_overrides() -> None:
    """Propagate monkeypatches on this legacy module into split modules.

    Older tests and diagnostics import scripts/github_pr_kanban_bridge.py directly
    and patch functions such as task_status, run_cmd, or create_eyes_reaction on
    that module. The real implementation now lives in package modules, so keep
    that legacy patch surface working before every scan and wrapper call.
    """
    globals_ = globals()
    _sync_command_runner()
    _sync_github_json()
    if "gh_ready" in globals_:
        _auth.gh_ready = globals_["gh_ready"]
    if "shutil" in globals_:
        _auth.shutil = globals_["shutil"]
    for name in (
        "collect_activities_from_api",
        "gh_ready",
        "list_closed_prs_from_api",
        "list_open_prs_from_api",
        "kanban_comment",
        "kanban_complete",
        "kanban_unblock",
        "mark_reaction_acks_ready_for_task",
        "process_ready_reaction_acks",
        "queue_reaction_ack",
        "resolve_repo_auth",
        "task_status",
        "utc_now",
    ):
        if name in globals_:
            setattr(_scanner, name, globals_[name])
    for name in (
        "create_eyes_reaction",
        "reaction_endpoint_for_activity",
        "utc_now",
    ):
        if name in globals_:
            setattr(_reactions, name, globals_[name])
    if "create_eyes_reaction" in globals_:
        setattr(_github_api, "create_eyes_reaction", globals_["create_eyes_reaction"])


def run_cmd(args, *, check=True, env_overrides=None):
    return _package_run_cmd(args, check=check, env_overrides=env_overrides)


_DEFAULT_COMPAT_RUN_CMD = run_cmd


def _sync_github_json() -> None:
    helper = globals().get("gh_json", _package_gh_json)
    _github_api.gh_json = _package_gh_json if helper is _DEFAULT_COMPAT_GH_JSON else helper


def gh_json(args):
    _sync_command_runner()
    return _package_gh_json(args)


_DEFAULT_COMPAT_GH_JSON = gh_json


def gh_ready():
    _sync_command_runner()
    return _package_gh_ready()


def list_open_prs_from_api(repo):
    _sync_command_runner()
    _sync_github_json()
    return _package_list_open_prs_from_api(repo)


def list_closed_prs_from_api(repo, limit):
    _sync_command_runner()
    _sync_github_json()
    return _package_list_closed_prs_from_api(repo, limit)


def create_eyes_reaction(endpoint):
    args = [
        "gh",
        "api",
        "-X",
        "POST",
        endpoint,
        "-f",
        "content=eyes",
        "-H",
        "Accept: application/vnd.github+json",
    ]
    runner = _effective_run_cmd()
    try:
        proc = runner(args, check=False, env_overrides=getattr(_github_api, "_CURRENT_GH_ENV", None))
    except TypeError as e:
        if "env_overrides" not in str(e):
            raise
        proc = runner(args, check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]


def collect_activities_from_api(repo, pr_number):
    _sync_command_runner()
    _sync_github_json()
    return _package_collect_activities_from_api(repo, pr_number)


def task_status(task_id, board):
    _sync_command_runner()
    return _package_task_status(task_id, board)


def kanban_comment(task_id, board, author, body):
    _sync_command_runner()
    return _package_kanban_comment(task_id, board, author, body)


def kanban_unblock(task_id, board, reason):
    _sync_command_runner()
    return _package_kanban_unblock(task_id, board, reason)


def kanban_complete(task_id, board, summary, metadata):
    _sync_command_runner()
    return _package_kanban_complete(task_id, board, summary, metadata)


def _sync_reaction_helpers() -> None:
    globals_ = globals()
    for name in ("create_eyes_reaction", "reaction_endpoint_for_activity", "utc_now"):
        if name in globals_:
            setattr(_reactions, name, globals_[name])


def queue_reaction_ack(state, repo, task_id, activity, *, ready, board=None):
    _sync_reaction_helpers()
    return _package_queue_reaction_ack(state, repo, task_id, activity, ready=ready, board=board)


def mark_reaction_acks_ready_for_task(state, task_id, board=None):
    _sync_reaction_helpers()
    return _package_mark_reaction_acks_ready_for_task(state, task_id, board=board)


def process_ready_reaction_acks(state, *, task_id=None, repo=None, board=None):
    _sync_reaction_helpers()
    return _package_process_ready_reaction_acks(state, task_id=task_id, repo=repo, board=board)


def scan(args):
    _sync_compat_overrides()
    return _package_scan(args)


def main(argv=None):
    p = argparse.ArgumentParser(description="Poll allowlisted GitHub PRs and unblock linked Hermes Kanban cards on new review/comment activity.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to bridge config JSON")
    p.add_argument("--state", default=None, help="Override state JSON path")
    p.add_argument("--board", default=None, help="Kanban board slug; defaults to config/env")
    p.add_argument("--dry-run", action="store_true", help="Scan and print decisions without mutating Kanban or state")
    p.add_argument("--fixture", help="Use local JSON fixture instead of GitHub API; read-only by default")
    p.add_argument("--fixture-write", action="store_true", help="Allow --fixture runs to mutate Kanban/state (unsafe; for explicit operator tests only)")
    p.add_argument("--verbose", action="store_true", help="Print skip and candidate diagnostics")
    p.add_argument("--strict", action="store_true", help="Return non-zero on gh/auth/API/mutation problems")
    p.add_argument("--init-config", action="store_true", help="Create default config and exit")
    p.add_argument("--force", action="store_true", help="Overwrite config with --init-config")
    args = p.parse_args(argv)

    config_path = Path(args.config).expanduser()
    cfg = load_config(config_path)
    state_path = Path(args.state or cfg["state_path"]).expanduser()
    if args.init_config:
        init_config(config_path, state_path, args.board or cfg.get("board") or DEFAULT_BOARD, args.force)
        return 0
    return globals()["scan"](args)


if __name__ == "__main__":
    raise SystemExit(main())
