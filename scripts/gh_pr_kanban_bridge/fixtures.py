"""Local fixture support for safe bridge validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import Activity
from .json_io import load_json


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
