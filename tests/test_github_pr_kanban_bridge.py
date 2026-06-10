#!/usr/bin/env python3
"""Regression tests for github_pr_kanban_bridge.py."""
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
        "verbose": True,
        "strict": True,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def write_config(path: Path, state: Path) -> None:
    write_json(path, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "notify_existing_on_first_scan": True,
        "ignored_actors": [],
        "ignore_bot_actors": False,
        "author": "test-author",
        "repos": ["Owner/repo"],
    })


def write_fixture(path: Path) -> None:
    write_json(path, {
        "repos": {
            "Owner/repo": [{
                "number": 7,
                "title": "Fixture PR",
                "url": "https://github.com/Owner/repo/pull/7",
                "headRefName": "Hermes/fixture-test",
                "body": "Kanban-Task: t_deadbeef\n",
                "activities": [{
                    "id": 101,
                    "event_type": "PR issue comment",
                    "actor": "human-reviewer",
                    "created_at": "2026-06-08T00:00:00Z",
                }],
            }],
        },
    })


def test_fixture_scan_is_read_only_by_default(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_fixture(fixture)
    write_json(state, {
        "version": 1,
        "seen": {},
        "pending_unblocks": {"t_deadbeef": "retry me"},
        "last_scan_at": None,
    })
    original_state = state.read_text(encoding="utf-8")

    mutations = []
    kanban_reads = []
    monkeypatch.setattr(bridge, "task_status", lambda *args: kanban_reads.append(args) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture))

    assert rc == 0
    assert kanban_reads == []
    assert mutations == []
    assert state.read_text(encoding="utf-8") == original_state


def test_fixture_scan_does_not_create_state_by_default(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "missing-state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_fixture(fixture)

    monkeypatch.setattr(bridge, "task_status", lambda *args: (_ for _ in ()).throw(AssertionError("fixture read-only must not read Kanban")))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (_ for _ in ()).throw(AssertionError("fixture read-only must not comment")))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: (_ for _ in ()).throw(AssertionError("fixture read-only must not unblock")))

    rc = bridge.scan(base_args(config, state, fixture))

    assert rc == 0
    assert not state.exists()


def test_fixture_write_flag_allows_explicit_test_mutations(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_fixture(fixture)

    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: (_ for _ in ()).throw(AssertionError("fixture-write must not call GitHub reactions")))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    assert [name for name, _ in mutations] == ["comment", "unblock"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Owner/repo#7:fixture:101" in saved["seen"]


def test_live_non_dry_scan_still_mutates_and_saves_state(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activity = bridge.Activity(
        key="Owner/repo#8:issue-comment:202",
        event_type="PR issue comment",
        action="created",
        actor="human-reviewer",
        actor_type="User",
        url="https://github.com/Owner/repo/pull/8#issuecomment-202",
        created_at="2026-06-08T00:00:00Z",
    )

    mutations = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: [activity])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: (True, "created"))

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 0
    assert [name for name, _ in mutations] == ["comment", "unblock"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Owner/repo#8:issue-comment:202" in saved["seen"]


def test_successful_unblock_acknowledges_issue_and_review_comments_after_kanban_mutations(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activities = [
        bridge.Activity(
            key="Owner/repo#8:issue-comment:202",
            event_type="PR issue comment",
            action="created",
            actor="human-reviewer",
            actor_type="User",
            url="https://github.com/Owner/repo/pull/8#issuecomment-202",
            created_at="2026-06-08T00:00:00Z",
        ),
        bridge.Activity(
            key="Owner/repo#8:review-comment:303",
            event_type="PR review comment",
            action="created",
            actor="human-reviewer",
            actor_type="User",
            url="https://github.com/Owner/repo/pull/8#discussion_r303",
            created_at="2026-06-08T00:01:00Z",
        ),
    ]

    operations = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: activities)
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: operations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: operations.append(("unblock", args)) or (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: operations.append(("reaction", endpoint)) or (True, "created"), raising=False)

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 0
    assert [op[0] for op in operations] == ["comment", "unblock", "reaction", "reaction"]
    assert operations[2][1] == "repos/Owner/repo/issues/comments/202/reactions"
    assert operations[3][1] == "repos/Owner/repo/pulls/comments/303/reactions"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["reaction_acks"] == {
        "Owner/repo#8:issue-comment:202": "2026-06-08T00:00:00Z",
        "Owner/repo#8:review-comment:303": "2026-06-08T00:01:00Z",
    }
    assert saved["pending_reaction_acks"] == {}


def test_existing_eyes_reaction_is_recorded_once_and_not_retried(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activity = bridge.Activity(
        key="Owner/repo#8:issue-comment:202",
        event_type="PR issue comment",
        action="created",
        actor="human-reviewer",
        actor_type="User",
        url="https://github.com/Owner/repo/pull/8#issuecomment-202",
        created_at="2026-06-08T00:00:00Z",
    )

    reaction_calls = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: [activity])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: reaction_calls.append(endpoint) or (True, "already exists"), raising=False)

    first_rc = bridge.scan(base_args(config, state, fixture=None))
    second_rc = bridge.scan(base_args(config, state, fixture=None))

    assert first_rc == 0
    assert second_rc == 0
    assert reaction_calls == ["repos/Owner/repo/issues/comments/202/reactions"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["reaction_acks"] == {"Owner/repo#8:issue-comment:202": "2026-06-08T00:00:00Z"}


def test_reaction_failure_is_queued_without_repeating_kanban_unblock(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activity = bridge.Activity(
        key="Owner/repo#8:issue-comment:202",
        event_type="PR issue comment",
        action="created",
        actor="human-reviewer",
        actor_type="User",
        url="https://github.com/Owner/repo/pull/8#issuecomment-202",
        created_at="2026-06-08T00:00:00Z",
    )

    kanban_ops = []
    reaction_results = [(False, "api down"), (True, "created")]
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: [activity])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: kanban_ops.append("comment") or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: kanban_ops.append("unblock") or (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: reaction_results.pop(0), raising=False)

    first_rc = bridge.scan(base_args(config, state, fixture=None))
    after_failure = json.loads(state.read_text(encoding="utf-8"))
    second_rc = bridge.scan(base_args(config, state, fixture=None))

    assert first_rc == 1
    assert after_failure["seen"] == {"Owner/repo#8:issue-comment:202": "2026-06-08T00:00:00Z"}
    assert after_failure["pending_reaction_acks"]["Owner/repo#8:issue-comment:202"]["ready"] is True
    assert second_rc == 0
    assert kanban_ops == ["comment", "unblock"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending_reaction_acks"] == {}
    assert saved["reaction_acks"] == {"Owner/repo#8:issue-comment:202": "2026-06-08T00:00:00Z"}


def test_reaction_is_not_attempted_when_kanban_comment_or_unblock_fails(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activity = bridge.Activity(
        key="Owner/repo#8:issue-comment:202",
        event_type="PR issue comment",
        action="created",
        actor="human-reviewer",
        actor_type="User",
        url="https://github.com/Owner/repo/pull/8#issuecomment-202",
        created_at="2026-06-08T00:00:00Z",
    )

    reaction_calls = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: [activity])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: (False, "blocked by test"))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: reaction_calls.append(endpoint) or (True, "created"), raising=False)

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 1
    assert reaction_calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending_reaction_acks"]["Owner/repo#8:issue-comment:202"]["ready"] is False


def test_reaction_is_not_queued_or_attempted_when_kanban_comment_fails(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-test",
        "body": "Kanban-Task: t_deadbeef\n",
    }
    activity = bridge.Activity(
        key="Owner/repo#8:issue-comment:202",
        event_type="PR issue comment",
        action="created",
        actor="human-reviewer",
        actor_type="User",
        url="https://github.com/Owner/repo/pull/8#issuecomment-202",
        created_at="2026-06-08T00:00:00Z",
    )

    reaction_calls = []
    unblock_calls = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: [activity])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (False, "comment failed by test"))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: unblock_calls.append(args) or (True, ""))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: reaction_calls.append(endpoint) or (True, "created"), raising=False)

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 1
    assert unblock_calls == []
    assert reaction_calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending_reaction_acks"] == {}
    assert saved["reaction_acks"] == {}


def test_merged_pr_completes_linked_task_once(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    merged_pr = {
        "number": 9,
        "title": "Merged PR",
        "url": "https://github.com/Owner/repo/pull/9",
        "headRefName": "chore/merged-test",
        "body": "Kanban-Task: t_deadbeef\n",
        "mergedAt": "2026-06-09T16:13:12Z",
    }

    mutations = []
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [merged_pr])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_complete", lambda *args: mutations.append(("complete", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 0
    assert [name for name, _ in mutations] == ["comment", "complete"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["completed_prs"] == {"Owner/repo#9": "2026-06-09T16:13:12Z"}

    mutations.clear()
    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 0
    assert mutations == []
