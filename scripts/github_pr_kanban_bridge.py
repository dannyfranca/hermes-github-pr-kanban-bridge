#!/usr/bin/env python3
"""Local GitHub PR activity -> Hermes Kanban unblock bridge.

Polls allowlisted GitHub repositories for human activity on Hermes-authored PRs
and unblocks the linked Kanban task when new review/comment activity appears.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
GITHUB_APP_HELPER = Path(os.environ.get("HERMES_GITHUB_APP_HELPER", "/home/agent/bin/hermes-gh-app"))
GITHUB_APP_CONFIG = Path(os.environ.get("HERMES_GITHUB_APP_CONFIG", "/home/agent/.hermes/github-apps.json"))
_GH_TOKEN_CACHE: dict[str, str] = {}


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


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def app_helper_configured() -> bool:
    return GITHUB_APP_HELPER.exists() and GITHUB_APP_CONFIG.exists()


def github_app_env(repo: str | None) -> dict[str, str] | None:
    if not repo or not app_helper_configured():
        return None
    token = _GH_TOKEN_CACHE.get(repo)
    if not token:
        helper_env = os.environ.copy()
        helper_env["HERMES_GITHUB_APP_CONFIG"] = str(GITHUB_APP_CONFIG)
        proc = subprocess.run(
            [str(GITHUB_APP_HELPER), "token", repo],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=helper_env,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"GitHub App token mint failed for {repo}")
        token = proc.stdout.strip()
        if not token:
            raise RuntimeError(f"GitHub App token helper returned empty token for {repo}")
        _GH_TOKEN_CACHE[repo] = token
    return {"GH_TOKEN": token, "GITHUB_TOKEN": token}


def run_cmd(args: list[str], *, check: bool = True, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    # Never print env or command lines that might include secrets. We only pass
    # static args and rely on gh's credential store/env internally.
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, env=env)


def gh_json(args: list[str], *, repo: str | None = None) -> Any:
    proc = run_cmd(["gh", *args], env_overrides=github_app_env(repo))
    text = proc.stdout.strip()
    return json.loads(text) if text else None


def gh_ready() -> tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "gh CLI is not installed"
    proc = run_cmd(["gh", "auth", "status"], check=False)
    if proc.returncode != 0:
        if app_helper_configured():
            return True, "ok (GitHub App helper)"
        return False, "gh CLI is not authenticated"
    return True, "ok"


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
    return cfg


def prune_timestamped_mapping(
    entries: dict[str, str],
    *,
    now: dt.datetime,
    retention_days: int,
    max_entries: int,
    active_keys: set[str],
    key_to_active_key,
) -> tuple[dict[str, str], int]:
    cutoff = now - dt.timedelta(days=retention_days)
    retained: dict[str, str] = {}
    pruned = 0

    for key, timestamp in entries.items():
        active_key = key_to_active_key(key)
        if active_key in active_keys:
            retained[key] = timestamp
            continue
        parsed = parse_utc_timestamp(timestamp)
        # Keep malformed timestamps conservatively; they may be hand-edited or
        # produced by an older bridge version and should not be lost silently.
        if parsed is None or parsed >= cutoff:
            retained[key] = timestamp
        else:
            pruned += 1

    if max_entries > 0 and len(retained) > max_entries:
        active_or_unknown = {
            key: timestamp
            for key, timestamp in retained.items()
            if key_to_active_key(key) in active_keys or parse_utc_timestamp(timestamp) is None
        }
        candidates = [
            (parse_utc_timestamp(timestamp), key)
            for key, timestamp in retained.items()
            if key not in active_or_unknown
        ]
        candidates.sort(key=lambda item: (item[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), item[1]))
        removable = max(0, len(retained) - max_entries)
        remove_keys = {key for _, key in candidates[:removable]}
        if remove_keys:
            retained = {key: timestamp for key, timestamp in retained.items() if key not in remove_keys}
            pruned += len(remove_keys)

    return retained, pruned


def gc_state(state: dict[str, Any], cfg: dict[str, Any], active_pr_keys: set[str], now_text: str) -> dict[str, int]:
    now = parse_utc_timestamp(now_text) or dt.datetime.now(dt.timezone.utc)
    retention_days = positive_int(cfg.get("state_retention_days"), DEFAULT_STATE_RETENTION_DAYS)
    max_seen = positive_int(cfg.get("state_max_seen_entries"), DEFAULT_STATE_MAX_SEEN_ENTRIES)
    max_baselined = positive_int(cfg.get("state_max_baselined_prs"), DEFAULT_STATE_MAX_BASELINED_PRS)

    seen = state.get("seen")
    if not isinstance(seen, dict):
        seen = {}
    baselined_prs = state.get("baselined_prs")
    if not isinstance(baselined_prs, dict):
        baselined_prs = {}

    pruned_seen_map, pruned_seen = prune_timestamped_mapping(
        {str(k): str(v) for k, v in seen.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_seen,
        active_keys=active_pr_keys,
        key_to_active_key=pr_key_from_activity_key,
    )
    pruned_baseline_map, pruned_baselined = prune_timestamped_mapping(
        {str(k): str(v) for k, v in baselined_prs.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_baselined,
        active_keys=active_pr_keys,
        key_to_active_key=lambda key: key,
    )
    state["seen"] = pruned_seen_map
    state["baselined_prs"] = pruned_baseline_map
    state["last_gc_at"] = now_text
    state["last_gc"] = {
        "retention_days": retention_days,
        "max_seen_entries": max_seen,
        "max_baselined_prs": max_baselined,
        "active_prs": len(active_pr_keys),
        "pruned_seen": pruned_seen,
        "pruned_baselined_prs": pruned_baselined,
    }
    return {"seen": pruned_seen, "baselined_prs": pruned_baselined}


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


def collect_activities_from_api(repo: str, pr_number: int) -> list[Activity]:
    activities: list[Activity] = []
    reviews = gh_json(["api", f"repos/{repo}/pulls/{pr_number}/reviews"], repo=repo)
    for r in reviews or []:
        rid = r.get("id")
        if rid is None:
            continue
        actor, actor_type = user_login_and_type(r)
        activities.append(Activity(
            key=f"{repo}#{pr_number}:review:{rid}",
            event_type="PR review",
            action=str(r.get("state") or "submitted").lower(),
            actor=actor,
            actor_type=actor_type,
            url=r.get("html_url") or "",
            created_at=r.get("submitted_at") or r.get("submittedAt") or "",
        ))

    review_comments = gh_json(["api", f"repos/{repo}/pulls/{pr_number}/comments"], repo=repo)
    for c in review_comments or []:
        cid = c.get("id")
        if cid is None:
            continue
        actor, actor_type = user_login_and_type(c)
        activities.append(Activity(
            key=f"{repo}#{pr_number}:review-comment:{cid}",
            event_type="PR review comment",
            action="created",
            actor=actor,
            actor_type=actor_type,
            url=c.get("html_url") or "",
            created_at=c.get("created_at") or "",
        ))

    issue_comments = gh_json(["api", f"repos/{repo}/issues/{pr_number}/comments"], repo=repo)
    for c in issue_comments or []:
        cid = c.get("id")
        if cid is None:
            continue
        actor, actor_type = user_login_and_type(c)
        activities.append(Activity(
            key=f"{repo}#{pr_number}:issue-comment:{cid}",
            event_type="PR issue comment",
            action="created",
            actor=actor,
            actor_type=actor_type,
            url=c.get("html_url") or "",
            created_at=c.get("created_at") or "",
        ))
    return sorted(activities, key=lambda a: (a.created_at or "", a.key))


def list_open_prs_from_api(repo: str) -> list[dict[str, Any]]:
    prs = gh_json([
        "pr", "list", "--repo", repo, "--state", "open",
        "--json", "number,title,url,headRefName,body,author,updatedAt",
        "--limit", "100",
    ], repo=repo)
    return prs or []


def list_closed_prs_from_api(repo: str, limit: int) -> list[dict[str, Any]]:
    prs = gh_json([
        "pr", "list", "--repo", repo, "--state", "closed",
        "--json", "number,title,url,headRefName,body,author,updatedAt,closedAt,mergedAt",
        "--limit", str(limit),
    ], repo=repo)
    return prs or []


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


def activity_comment(repo: str, pr: dict[str, Any], task_id: str, activity: Activity) -> str:
    title = pr.get("title") or "(untitled)"
    number = pr.get("number")
    pr_url = pr.get("url") or ""
    lines = [
        "GitHub PR activity detected for linked Hermes PR.",
        f"Repo: {repo}",
        f"PR: #{number} {title}",
        f"PR URL: {pr_url}",
        f"Actor: {activity.actor}",
        f"Event: {activity.event_type} / {activity.action}",
        f"Event URL: {activity.url}",
        "Instruction: address the feedback on the same PR/branch; do not open a replacement PR unless the human asks.",
        f"Linked Kanban task: {task_id}",
    ]
    return "\n".join(lines)


def load_fixture(path: Path) -> dict[str, Any]:
    data = load_json(path, None)
    if not isinstance(data, dict):
        raise SystemExit(f"fixture must be a JSON object: {path}")
    return data


def iter_fixture_prs(fixture: dict[str, Any], repo: str) -> list[dict[str, Any]]:
    repos = fixture.get("repos", {})
    if isinstance(repos, dict):
        prs = repos.get(repo, [])
    else:
        prs = []
    return prs if isinstance(prs, list) else []


def fixture_activities(pr: dict[str, Any], repo: str) -> list[Activity]:
    out = []
    for raw in pr.get("activities", []) or []:
        if not isinstance(raw, dict):
            continue
        key = raw.get("key") or f"{repo}#{pr.get('number')}:fixture:{raw.get('id', len(out))}"
        out.append(Activity(
            key=str(key),
            event_type=str(raw.get("event_type") or "PR issue comment"),
            action=str(raw.get("action") or "created"),
            actor=str(raw.get("actor") or "unknown"),
            actor_type=str(raw.get("actor_type") or raw.get("user_type") or "User"),
            url=str(raw.get("url") or pr.get("url") or ""),
            created_at=str(raw.get("created_at") or ""),
        ))
    return out


def scan(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    cfg = load_config(config_path)
    state_path = Path(args.state or cfg["state_path"]).expanduser()
    board = args.board or cfg.get("board") or DEFAULT_BOARD
    author = cfg.get("author") or "github-pr-kanban-bridge"
    state = load_json(state_path, {"version": 1, "seen": {}, "pending_unblocks": {}, "last_scan_at": None, "baselined_prs": {}, "completed_prs": {}})
    seen: dict[str, str] = state.setdefault("seen", {})
    pending_unblocks: dict[str, str] = state.setdefault("pending_unblocks", {})
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

    repos = [r for r in (normalize_repo(x) for x in cfg.get("repos", [])) if r]
    if not cfg.get("enabled", True):
        if args.verbose or args.dry_run:
            print("bridge disabled by config")
        return 0
    if not repos:
        if args.verbose or args.dry_run:
            print(f"no allowlisted repos in {config_path}")
        return 0

    if fixture is None:
        ok, msg = gh_ready()
        if not ok:
            if args.verbose or args.dry_run:
                print(msg)
            return 2 if args.strict else 0

    planned: list[str] = []
    errors: list[str] = []
    active_pr_keys: set[str] = set()
    gc_safe = True

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
                print(f"unblocked pending task: {task_id}")
            else:
                errors.append(f"pending unblock failed for {task_id}: {msg}")

    for repo in repos:
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
            reason = f"GitHub PR activity on {repo}#{number}; wake worker to address feedback"
            ok, msg = kanban_unblock(task_id, board, reason)
            for activity in unseen:
                seen[activity.key] = activity.created_at or utc_now()
            if not ok:
                pending_unblocks[task_id] = reason
                errors.append(f"unblock failed for {first.key}; queued pending retry: {msg}")
                continue
            pending_unblocks.pop(task_id, None)
            print("unblocked: " + summary)

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
        return 1 if args.strict else 0
    if args.dry_run and not planned:
        print("dry-run complete: no Kanban mutations would be made")
    return 0


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
        "repos": [],
        "notes": [
            "Add explicit repo allowlist entries as owner/name strings, e.g. \"DannyFranca/example\".",
            "Only open PRs with head branches starting Hermes/ and body marker Kanban-Task: t_xxxxxxxx are considered.",
            "State GC keeps active open Hermes PR entries, then prunes inactive seen/baselined_prs entries older than state_retention_days and caps inactive entries by state_max_* limits.",
            "Merged PRs with a Kanban-Task marker are completed once, tracked by completed_prs in state.",
        ],
    }
    save_json_atomic(path, sample)
    print(f"wrote config: {path}")


def main(argv: list[str] | None = None) -> int:
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
    return scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
