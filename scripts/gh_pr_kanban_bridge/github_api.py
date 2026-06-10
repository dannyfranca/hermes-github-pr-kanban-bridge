"""GitHub CLI/API access and activity normalization."""
from __future__ import annotations

import json
import re
import shutil
from typing import Any

from .commands import run_cmd
from .common import Activity, user_login_and_type


def gh_json(args: list[str]) -> Any:
    proc = run_cmd(["gh", *args])
    text = proc.stdout.strip()
    return json.loads(text) if text else None


ORIGINAL_GH_JSON = gh_json


def gh_ready() -> tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "gh CLI is not installed"
    proc = run_cmd(["gh", "auth", "status"], check=False)
    if proc.returncode != 0:
        return False, "gh CLI is not authenticated"
    return True, "ok"


def collect_activities_from_api(repo: str, pr_number: int) -> list[Activity]:
    activities: list[Activity] = []
    reviews = gh_json(["api", f"repos/{repo}/pulls/{pr_number}/reviews"])
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

    review_comments = gh_json(["api", f"repos/{repo}/pulls/{pr_number}/comments"])
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

    issue_comments = gh_json(["api", f"repos/{repo}/issues/{pr_number}/comments"])
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
    ])
    return prs or []


def list_closed_prs_from_api(repo: str, limit: int) -> list[dict[str, Any]]:
    prs = gh_json([
        "pr", "list", "--repo", repo, "--state", "closed",
        "--json", "number,title,url,headRefName,body,author,updatedAt,closedAt,mergedAt",
        "--limit", str(limit),
    ])
    return prs or []


def reaction_endpoint_for_activity(repo: str, activity: Activity) -> str | None:
    """Return the GitHub reactions endpoint for ackable PR feedback."""
    match = re.fullmatch(rf"{re.escape(repo)}#\d+:(issue-comment|review-comment):(\d+)", activity.key)
    if not match:
        return None
    kind, comment_id = match.groups()
    if kind == "issue-comment":
        return f"repos/{repo}/issues/comments/{comment_id}/reactions"
    if kind == "review-comment":
        return f"repos/{repo}/pulls/comments/{comment_id}/reactions"
    return None


def create_eyes_reaction(endpoint: str) -> tuple[bool, str]:
    proc = run_cmd([
        "gh",
        "api",
        "-X",
        "POST",
        endpoint,
        "-f",
        "content=eyes",
        "-H",
        "Accept: application/vnd.github+json",
    ], check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]
