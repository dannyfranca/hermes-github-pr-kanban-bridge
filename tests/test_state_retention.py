#!/usr/bin/env python3
"""State retention/GC regression tests for github_pr_kanban_bridge.py."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_pr_kanban_bridge.py"
spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
sys.modules["github_pr_kanban_bridge"] = bridge
assert spec.loader is not None
spec.loader.exec_module(bridge)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def base_args(config: Path, state: Path, fixture: Path | None = None, **overrides: object) -> argparse.Namespace:
    data = {
        "config": str(config),
        "state": str(state),
        "board": None,
        "dry_run": False,
        "fixture": str(fixture) if fixture else None,
        "fixture_write": False,
        "verbose": False,
        "strict": True,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def write_config(path: Path, state: Path, **overrides: object) -> None:
    cfg = {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "notify_existing_on_first_scan": True,
        "ignored_actors": [],
        "ignore_bot_actors": False,
        "author": "test-author",
        "state_retention_days": 30,
        "state_max_seen_entries": 10,
        "state_max_baselined_prs": 10,
        "repos": ["Owner/repo"],
    }
    cfg.update(overrides)
    write_json(path, cfg)


def fixture_with_activities(path: Path, activities: list[dict[str, object]], pr_number: int = 7) -> None:
    write_json(path, {
        "repos": {
            "Owner/repo": [{
                "number": pr_number,
                "title": "Fixture PR",
                "url": f"https://github.com/Owner/repo/pull/{pr_number}",
                "headRefName": "Hermes/fixture-test",
                "body": "Kanban-Task: t_deadbeef\n",
                "activities": activities,
            }],
        },
    })


def test_stale_seen_entries_are_pruned_while_active_pr_entries_are_retained(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    fixture_with_activities(fixture, [{
        "id": 101,
        "key": "Owner/repo#7:issue-comment:active-old",
        "event_type": "PR issue comment",
        "actor": "human-reviewer",
        "created_at": "2026-04-01T00:00:00Z",
    }])
    write_json(state, {
        "version": 1,
        "seen": {
            "Other/repo#1:issue-comment:stale": "2026-01-01T00:00:00Z",
            "Owner/repo#7:issue-comment:active-old": "2026-01-01T00:00:00Z",
            "Owner/repo#7:issue-comment:recent": "2026-06-07T00:00:00Z",
        },
        "pending_unblocks": {},
        "baselined_prs": {},
        "last_scan_at": "2026-06-08T00:00:00Z",
    })

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Other/repo#1:issue-comment:stale" not in saved["seen"]
    assert "Owner/repo#7:issue-comment:active-old" in saved["seen"]
    assert "Owner/repo#7:issue-comment:recent" in saved["seen"]


def test_baselined_prs_retention_prunes_stale_inactive_entries(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    fixture_with_activities(fixture, [], pr_number=7)
    write_json(state, {
        "version": 1,
        "seen": {},
        "pending_unblocks": {},
        "baselined_prs": {
            "Other/repo#1": "2026-01-01T00:00:00Z",
            "Owner/repo#7": "2026-01-01T00:00:00Z",
            "Owner/repo#8": "2026-06-07T00:00:00Z",
        },
        "last_scan_at": "2026-06-08T00:00:00Z",
    })

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Other/repo#1" not in saved["baselined_prs"]
    assert "Owner/repo#7" in saved["baselined_prs"]
    assert "Owner/repo#8" in saved["baselined_prs"]


def test_pending_unblocks_are_not_pruned_by_state_gc(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    fixture_with_activities(fixture, [])
    write_json(state, {
        "version": 1,
        "seen": {"Other/repo#1:issue-comment:stale": "2026-01-01T00:00:00Z"},
        "pending_unblocks": {"t_deadbeef": "retry reason"},
        "baselined_prs": {},
        "last_scan_at": "2026-06-08T00:00:00Z",
    })

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda task_id, board, reason: (False, "transient failure"))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending_unblocks"] == {"t_deadbeef": "retry reason"}
    assert saved["seen"] == {}


def test_fixture_read_only_does_not_save_gc_changes(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    fixture_with_activities(fixture, [])
    original = {
        "version": 1,
        "seen": {"Other/repo#1:issue-comment:stale": "2026-01-01T00:00:00Z"},
        "pending_unblocks": {},
        "baselined_prs": {"Other/repo#1": "2026-01-01T00:00:00Z"},
        "last_scan_at": "2026-06-08T00:00:00Z",
    }
    write_json(state, original)

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda *args: (_ for _ in ()).throw(AssertionError("read-only fixture must not read Kanban")))

    rc = bridge.scan(base_args(config, state, fixture))

    assert rc == 0
    assert json.loads(state.read_text(encoding="utf-8")) == original


def test_dry_run_does_not_save_gc_changes(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)
    original = {
        "version": 1,
        "seen": {"Other/repo#1:issue-comment:stale": "2026-01-01T00:00:00Z"},
        "pending_unblocks": {},
        "baselined_prs": {"Other/repo#1": "2026-01-01T00:00:00Z"},
        "last_scan_at": "2026-06-08T00:00:00Z",
    }
    write_json(state, original)

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [])

    rc = bridge.scan(base_args(config, state, fixture=None, dry_run=True))

    assert rc == 0
    assert json.loads(state.read_text(encoding="utf-8")) == original


def test_max_entry_caps_prune_oldest_inactive_entries(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state, state_retention_days=365, state_max_seen_entries=2, state_max_baselined_prs=2)
    fixture_with_activities(fixture, [], pr_number=7)
    write_json(state, {
        "version": 1,
        "seen": {
            "Other/repo#1:issue-comment:oldest": "2026-01-01T00:00:00Z",
            "Other/repo#1:issue-comment:middle": "2026-02-01T00:00:00Z",
            "Other/repo#1:issue-comment:newest": "2026-03-01T00:00:00Z",
            "Owner/repo#7:issue-comment:active": "2026-01-01T00:00:00Z",
        },
        "pending_unblocks": {},
        "baselined_prs": {
            "Other/repo#1": "2026-01-01T00:00:00Z",
            "Other/repo#2": "2026-02-01T00:00:00Z",
            "Other/repo#3": "2026-03-01T00:00:00Z",
            "Owner/repo#7": "2026-01-01T00:00:00Z",
        },
        "last_scan_at": "2026-06-08T00:00:00Z",
    })

    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Owner/repo#7:issue-comment:active" in saved["seen"]
    assert "Other/repo#1:issue-comment:newest" in saved["seen"]
    assert "Other/repo#1:issue-comment:oldest" not in saved["seen"]
    assert "Other/repo#1:issue-comment:middle" not in saved["seen"]
    assert "Owner/repo#7" in saved["baselined_prs"]
    assert "Other/repo#3" in saved["baselined_prs"]
    assert "Other/repo#1" not in saved["baselined_prs"]
    assert "Other/repo#2" not in saved["baselined_prs"]


def test_multi_iteration_dedupe_still_wakes_once_after_gc(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    fixture_with_activities(fixture, [{
        "id": 101,
        "key": "Owner/repo#7:issue-comment:first",
        "event_type": "PR issue comment",
        "actor": "human-reviewer",
        "created_at": "2026-06-08T00:00:00Z",
    }])
    write_json(state, {
        "version": 1,
        "seen": {"Other/repo#1:issue-comment:stale": "2026-01-01T00:00:00Z"},
        "pending_unblocks": {},
        "baselined_prs": {"Owner/repo#7": "2026-06-01T00:00:00Z"},
        "last_scan_at": "2026-06-08T00:00:00Z",
    })

    unblocks: list[str] = []
    monkeypatch.setattr(bridge, "utc_now", lambda: "2026-06-08T00:00:00+00:00")
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda task_id, board, reason: unblocks.append(reason) or (True, ""))

    first_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))
    second_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert first_rc == 0
    assert second_rc == 0
    assert len(unblocks) == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Owner/repo#7:issue-comment:first" in saved["seen"]
    assert "Other/repo#1:issue-comment:stale" not in saved["seen"]
