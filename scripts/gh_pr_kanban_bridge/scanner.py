"""Main scan orchestration for GitHub PR activity -> Kanban actions."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .common import (
    DEFAULT_BOARD,
    HERMES_BRANCH_PREFIX,
    actor_is_ignored,
    activity_comment,
    extract_task_id,
    normalize_repo,
    positive_int,
    utc_now,
)
from .auth import AuthContext, resolve_repo_auth
from .config import load_config
from .fixtures import fixture_activities, iter_fixture_prs, load_fixture
from .github_api import collect_activities_from_api, list_closed_prs_from_api, list_open_prs_from_api, set_current_gh_env
from .json_io import load_json, save_json_atomic
from .kanban_cli import kanban_comment, kanban_complete, kanban_unblock, task_status
from .reactions import ensure_reaction_state, mark_reaction_acks_ready_for_task, process_ready_reaction_acks, queue_reaction_ack
from .state import gc_state


def _process_ready_reaction_acks(
    state: dict[str, Any],
    *,
    task_id: str | None = None,
    repo: str | None = None,
) -> list[str]:
    """Call reaction processing while preserving legacy monkeypatch compatibility."""
    try:
        return process_ready_reaction_acks(state, task_id=task_id, repo=repo)
    except TypeError as e:
        if "repo" not in str(e):
            raise
        return process_ready_reaction_acks(state, task_id=task_id)


def scan(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    cfg = load_config(config_path)
    state_path = Path(args.state or cfg["state_path"]).expanduser()
    board = args.board or cfg.get("board") or DEFAULT_BOARD
    author = cfg.get("author") or "github-pr-kanban-bridge"
    state = load_json(state_path, {
        "version": 1,
        "seen": {},
        "pending_unblocks": {},
        "pending_reaction_acks": {},
        "reaction_acks": {},
        "last_scan_at": None,
        "baselined_prs": {},
        "completed_prs": {},
    })
    seen: dict[str, str] = state.setdefault("seen", {})
    pending_unblocks: dict[str, str] = state.setdefault("pending_unblocks", {})
    ensure_reaction_state(state)
    baselined_prs: dict[str, str] = state.setdefault("baselined_prs", {})
    completed_prs: dict[str, str] = state.setdefault("completed_prs", {})
    allow_existing = bool(cfg.get("notify_existing_on_first_scan"))
    ignored_actors = {str(a).lower() for a in cfg.get("ignored_actors", [])}
    ignore_bots = bool(cfg.get("ignore_bot_actors", True))
    complete_merged_prs = bool(cfg.get("complete_merged_prs", True))
    closed_pr_scan_limit = positive_int(cfg.get("closed_pr_scan_limit"), 30)
    fixture = load_fixture(Path(args.fixture).expanduser()) if args.fixture else None
    fixture_read_only = fixture is not None and not getattr(args, "fixture_write", False)
    read_only = args.dry_run or fixture_read_only
    read_only_label = "DRY-RUN" if args.dry_run else "FIXTURE-READONLY"
    acknowledge_reactions = fixture is None

    repos = [r for r in (normalize_repo(x) for x in cfg.get("repos", [])) if r]
    if not cfg.get("enabled", True):
        if args.verbose or args.dry_run:
            print("bridge disabled by config")
        return 0
    if not repos:
        if args.verbose or args.dry_run:
            print(f"no allowlisted repos in {config_path}")
        return 0

    repo_auth: dict[str, AuthContext] = {}
    auth_failures: list[str] = []
    if fixture is None:
        for repo in repos:
            auth_context, msg = resolve_repo_auth(cfg, repo)
            if auth_context is None:
                auth_failures.append(f"auth unavailable for {repo}: {msg}" if not msg.startswith("auth unavailable") else msg)
                continue
            repo_auth[repo] = auth_context
            if args.verbose or args.dry_run:
                print(f"auth for {repo}: {auth_context.source}")

    planned: list[str] = []
    errors: list[str] = list(auth_failures)
    auth_failed = bool(auth_failures)
    active_pr_keys: set[str] = set()
    gc_safe = not auth_failed

    if pending_unblocks:
        for task_id, reason in list(pending_unblocks.items()):
            if read_only:
                print(f"{read_only_label} would retry pending unblock for {task_id}: {reason}")
                continue
            exists, status, detail = task_status(task_id, board)
            if not exists:
                errors.append(f"pending unblock task {task_id} no longer exists: {detail}")
                pending_unblocks.pop(task_id, None)
                continue
            if status != "blocked":
                pending_unblocks.pop(task_id, None)
                continue
            ok, msg = kanban_unblock(task_id, board, reason)
            if ok:
                pending_unblocks.pop(task_id, None)
                if acknowledge_reactions:
                    mark_reaction_acks_ready_for_task(state, task_id)
                print(f"unblocked pending task: {task_id}")
            else:
                errors.append(f"pending unblock failed for {task_id}: {msg}")

    for repo in repos:
        if fixture is None:
            auth_context = repo_auth.get(repo)
            if auth_context is None:
                continue
            set_current_gh_env(auth_context.env)
            if acknowledge_reactions and not read_only:
                errors.extend(_process_ready_reaction_acks(state, repo=repo))
        if complete_merged_prs and fixture is None:
            try:
                closed_prs = list_closed_prs_from_api(repo, closed_pr_scan_limit)
            except Exception as e:
                msg = f"closed PR scan failed for allowlisted repo {repo}: {e.__class__.__name__}"
                if args.verbose or args.dry_run:
                    print(msg)
                errors.append(msg)
                gc_safe = False
                closed_prs = []
            for pr in closed_prs:
                number = pr.get("number")
                merged_at = pr.get("mergedAt") or pr.get("merged_at")
                if not number or not merged_at:
                    continue
                task_id = extract_task_id(pr.get("body"))
                if not task_id:
                    continue
                pr_key = f"{repo}#{number}"
                if pr_key in completed_prs:
                    continue
                exists, status, detail = task_status(task_id, board)
                if not exists:
                    if args.dry_run or args.verbose:
                        print(f"skip merged {repo}#{number}: linked task {task_id} not found ({detail})")
                    continue
                if status in {"done", "archived"}:
                    if not read_only:
                        completed_prs[pr_key] = merged_at
                    continue
                summary = f"GitHub PR merged: {repo}#{number} -> complete {task_id}"
                planned.append(summary)
                if read_only:
                    print(f"{read_only_label} would complete: " + summary)
                    continue
                comment = "\n".join([
                    "GitHub PR merge detected for linked Hermes PR.",
                    f"Repo: {repo}",
                    f"PR: #{number} {pr.get('title') or '(untitled)'}",
                    f"PR URL: {pr.get('url') or ''}",
                    f"Merged at: {merged_at}",
                    f"Linked Kanban task: {task_id}",
                ])
                ok, msg = kanban_comment(task_id, board, author, comment)
                if not ok:
                    errors.append(f"merge comment failed for {pr_key}: {msg}")
                    continue
                ok, msg = kanban_complete(
                    task_id,
                    board,
                    f"Completed by GitHub merge of {repo}#{number}: {pr.get('title') or '(untitled)'}",
                    {
                        "source": "github-pr-kanban-bridge",
                        "repo": repo,
                        "pr_number": number,
                        "pr_url": pr.get("url") or "",
                        "merged_at": merged_at,
                    },
                )
                if not ok:
                    errors.append(f"complete failed for merged {pr_key}: {msg}")
                    continue
                completed_prs[pr_key] = merged_at
                pending_unblocks.pop(task_id, None)
                print("completed: " + summary)

        try:
            prs = iter_fixture_prs(fixture, repo) if fixture is not None else list_open_prs_from_api(repo)
        except Exception as e:  # keep the cron poller quiet/non-spammy by default
            msg = f"scan failed for allowlisted repo {repo}: {e.__class__.__name__}"
            if args.verbose or args.dry_run:
                print(msg)
            errors.append(msg)
            gc_safe = False
            continue
        for pr in prs:
            number = pr.get("number")
            head = pr.get("headRefName") or pr.get("head_ref") or ""
            if not isinstance(head, str) or not head.startswith(HERMES_BRANCH_PREFIX):
                continue
            task_id = extract_task_id(pr.get("body"))
            if not task_id:
                continue

            pr_key = f"{repo}#{number}"
            active_pr_keys.add(pr_key)

            if fixture_read_only:
                exists, status, detail = True, "blocked", ""
            else:
                exists, status, detail = task_status(task_id, board)
            if not exists:
                if args.dry_run or args.verbose:
                    print(f"skip {repo}#{number}: linked task {task_id} not found ({detail})")
                continue

            activities = fixture_activities(pr, repo) if fixture is not None else collect_activities_from_api(repo, int(number))
            relevant = [a for a in activities if not actor_is_ignored(a, ignored_actors, ignore_bots)]
            if args.dry_run or args.verbose:
                ignored_count = len(activities) - len(relevant)
                print(f"candidate {repo}#{number}: branch={head} task={task_id} task_status={status} activities={len(activities)} relevant={len(relevant)} ignored={ignored_count}")

            seen_prefix = pr_key + ":"
            pr_previously_seen = pr_key in baselined_prs or any(key.startswith(seen_prefix) for key in seen)
            onboard_pr = allow_existing or pr_previously_seen

            unseen: list[Activity] = []
            baseline_seen: dict[str, str] = {}
            for activity in relevant:
                observed_at = activity.created_at or utc_now()
                if activity.key in seen:
                    continue
                if not onboard_pr:
                    baseline_seen[activity.key] = observed_at
                    if args.dry_run or args.verbose:
                        print(f"baseline-only {activity.key}: newly allowlisted PR will mark seen without unblocking")
                    continue
                unseen.append(activity)

            if not read_only and not onboard_pr:
                baselined_prs[pr_key] = utc_now()
                seen.update(baseline_seen)

            # Once a linked task exists, all observed relevant activity is marked
            # seen at the end of the scan even when the task is not blocked. This
            # prevents old comments from waking a card later if it becomes blocked.
            if not unseen:
                continue
            if status != "blocked":
                if args.dry_run or args.verbose:
                    print(f"skip {repo}#{number}: linked task {task_id} is {status}, not blocked; {len(unseen)} unseen activity item(s) will be marked seen")
                if not read_only:
                    for activity in unseen:
                        seen[activity.key] = activity.created_at or utc_now()
                continue

            first = unseen[0]
            summary = f"{repo}#{number} {len(unseen)} new PR activity item(s) -> unblock {task_id}"
            planned.append(summary)
            if read_only:
                print(f"{read_only_label} would comment+unblock: " + summary)
                continue

            comment = activity_comment(repo, pr, task_id, first)
            if len(unseen) > 1:
                extra = "\nAdditional unseen activity in this scan:\n" + "\n".join(
                    f"- {a.event_type} / {a.action} by {a.actor}: {a.url}" for a in unseen[1:]
                )
                comment += extra
            ok, msg = kanban_comment(task_id, board, author, comment)
            if not ok:
                errors.append(f"comment failed for {first.key}: {msg}")
                continue
            if acknowledge_reactions:
                for activity in unseen:
                    queue_reaction_ack(state, repo, task_id, activity, ready=False)
            reason = f"GitHub PR activity on {repo}#{number}; wake worker to address feedback"
            ok, msg = kanban_unblock(task_id, board, reason)
            for activity in unseen:
                seen[activity.key] = activity.created_at or utc_now()
            if not ok:
                pending_unblocks[task_id] = reason
                errors.append(f"unblock failed for {first.key}; queued pending retry: {msg}")
                continue
            pending_unblocks.pop(task_id, None)
            if acknowledge_reactions:
                mark_reaction_acks_ready_for_task(state, task_id)
                errors.extend(_process_ready_reaction_acks(state, task_id=task_id, repo=repo))
            print("unblocked: " + summary)

    if fixture is None:
        set_current_gh_env(None)

    if not read_only:
        now_text = utc_now()
        state["last_scan_at"] = now_text
        state["config_path"] = str(config_path)
        state["board"] = board
        if gc_safe:
            gc_state(state, cfg, active_pr_keys, now_text)
        else:
            state["last_gc_skipped_at"] = now_text
            state["last_gc_skip_reason"] = "one or more repos failed to scan; active PR set may be incomplete"
        save_json_atomic(state_path, state)

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 2 if auth_failed else (1 if args.strict else 0)
    if args.dry_run and not planned:
        print("dry-run complete: no Kanban mutations would be made")
    return 0
