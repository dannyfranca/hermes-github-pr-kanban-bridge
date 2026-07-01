#!/usr/bin/env python3
"""Board marker resolution tests for the PR -> Kanban bridge."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_pr_kanban_bridge.py"
spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
assert spec is not None
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
        "board": "legacy-board-should-not-win",
        "state_path": str(state),
        "notify_existing_on_first_scan": True,
        "ignored_actors": [],
        "ignore_bot_actors": False,
        "author": "test-author",
        "repos": ["Owner/repo"],
    })


def write_open_pr_fixture(path: Path, body: str, task_id: str = "t_1a2ed8d4") -> None:
    write_json(path, {
        "repos": {
            "Owner/repo": [{
                "number": 7,
                "title": "Board-aware fixture PR",
                "url": "https://github.com/Owner/repo/pull/7",
                "headRefName": "Hermes/board-aware-test",
                "body": body,
                "activities": [{
                    "id": 101,
                    "event_type": "PR issue comment",
                    "action": "created",
                    "actor": "human-reviewer",
                    "actor_type": "User",
                    "url": "https://github.com/Owner/repo/pull/7#issuecomment-101",
                    "created_at": "2026-06-08T00:00:00Z",
                }],
            }],
        },
    })


def test_pr_without_kanban_task_keeps_existing_skip_behavior(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board: psp\n")

    monkeypatch.setattr(bridge, "task_status", lambda *args: (_ for _ in ()).throw(AssertionError("PRs without Kanban-Task must not read Kanban")))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: (_ for _ in ()).throw(AssertionError("PRs without Kanban-Task must not comment")))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: (_ for _ in ()).throw(AssertionError("PRs without Kanban-Task must not unblock")))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["seen"] == {}
    assert saved["pending_reaction_acks"] == {}
    assert saved["reaction_acks"] == {}


def test_reaction_ack_readiness_is_scoped_by_board():
    state = {
        "pending_reaction_acks": {
            "Owner/repo#7:issue-comment:101": {"task_id": "t_1a2ed8d4", "board": "psp", "ready": False},
            "Owner/repo#8:issue-comment:202": {"task_id": "t_1a2ed8d4", "board": "default", "ready": False},
            "Owner/repo#9:issue-comment:303": {"task_id": "t_1a2ed8d4", "ready": False},
        },
        "reaction_acks": {},
    }

    bridge.mark_reaction_acks_ready_for_task(state, "t_1a2ed8d4", board="psp")

    assert state["pending_reaction_acks"]["Owner/repo#7:issue-comment:101"]["ready"] is True
    assert state["pending_reaction_acks"]["Owner/repo#8:issue-comment:202"]["ready"] is False
    assert state["pending_reaction_acks"]["Owner/repo#9:issue-comment:303"]["ready"] is True


def test_open_pr_uses_explicit_kanban_board_marker(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n")

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "psp")]
    assert [name for name, _ in mutations] == ["comment", "unblock"]
    assert mutations[0][1][1] == "psp"
    assert mutations[1][1][1] == "psp"


def test_failed_unblock_retry_preserves_explicit_kanban_board(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n")

    operations = []
    unblock_results = [(False, "temporary failure"), (True, "")]
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: operations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: operations.append(("unblock", args)) or unblock_results.pop(0))

    first_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))
    after_failure = json.loads(state.read_text(encoding="utf-8"))
    second_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert first_rc == 1
    assert second_rc == 0
    assert after_failure["pending_unblocks"] == {
        "psp:t_1a2ed8d4": {
            "task_id": "t_1a2ed8d4",
            "board": "psp",
            "reason": "GitHub PR activity on Owner/repo#7; wake worker to address feedback",
        }
    }
    assert [name for name, _ in operations] == ["comment", "unblock", "unblock"]
    assert operations[0][1][1] == "psp"
    assert operations[1][1][1] == "psp"
    assert operations[2][1][1] == "psp"


def test_legacy_string_pending_unblock_retries_with_configured_board(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["board"] = "psp"
    write_json(config, cfg)
    write_json(state, {
        "version": 1,
        "seen": {},
        "pending_unblocks": {"t_1a2ed8d4": "legacy retry"},
        "last_scan_at": None,
    })
    write_json(fixture, {"repos": {"Owner/repo": []}})

    operations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: operations.append(("status", task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: operations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    assert operations == [
        ("status", "t_1a2ed8d4", "psp"),
        ("unblock", ("t_1a2ed8d4", "psp", "legacy retry")),
    ]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pending_unblocks"] == {}


def test_open_pr_uses_explicit_kanban_board_marker_with_crlf_body(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\r\nKanban-Board: psp\r\nKanban-Task: t_1a2ed8d4\r\n")

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "psp")]
    assert mutations[0][1][1] == "psp"
    assert mutations[1][1][1] == "psp"


def test_non_blocked_open_pr_comment_uses_explicit_kanban_board_marker(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n")

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "ready", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "psp")]
    assert [name for name, _ in mutations] == ["comment"]
    assert mutations[0][1][1] == "psp"


def test_open_pr_without_board_marker_uses_default_board_exactly(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Task: t_1a2ed8d4\n")

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True, board="psp"))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "default")]
    assert mutations[0][1][1] == "default"
    assert mutations[1][1][1] == "default"


def test_blank_board_marker_does_not_capture_next_line_as_board(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board:\nKanban-Task: t_1a2ed8d4\n")

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True, board="psp"))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "default")]
    assert mutations[0][1][1] == "default"
    assert mutations[1][1][1] == "default"


def test_missing_board_marker_does_not_search_psp_or_mark_seen_when_default_lacks_task(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Task: t_1a2ed8d4\n")

    status_calls = []
    mutations = []
    def fake_task_status(task_id: str, board: str):
        status_calls.append((task_id, board))
        if board == "psp":
            return True, "blocked", ""
        return False, "", "not found on default"

    monkeypatch.setattr(bridge, "task_status", fake_task_status)
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert rc == 1
    assert status_calls == [("t_1a2ed8d4", "default")]
    assert mutations == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["seen"] == {}
    assert saved.get("reaction_acks", {}) == {}
    assert "linked task t_1a2ed8d4 not found on board default" in capsys.readouterr().err


def test_missing_task_scan_preserves_activity_for_later_board_marker_fix(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["notify_existing_on_first_scan"] = False
    write_json(config, cfg)
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Task: t_1a2ed8d4\n")

    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (board == "psp", "blocked" if board == "psp" else "", "missing on default"))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    first_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))
    after_missing = json.loads(state.read_text(encoding="utf-8"))
    write_open_pr_fixture(fixture, "## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n")
    second_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert first_rc == 1
    assert after_missing["seen"] == {}
    assert after_missing["task_lookup_failed_prs"] == {"Owner/repo#7": after_missing["task_lookup_failed_prs"]["Owner/repo#7"]}
    assert second_rc == 0
    assert [name for name, _ in mutations] == ["comment", "unblock"]
    assert mutations[0][1][1] == "psp"
    assert mutations[1][1][1] == "psp"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["seen"] == {"Owner/repo#7:fixture:101": "2026-06-08T00:00:00Z"}
    assert saved["task_lookup_failed_prs"] == {}


def test_lookup_failure_then_marker_fix_with_no_activity_onboards_future_activity(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    fixture = tmp_path / "fixture.json"
    write_config(config, state)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["notify_existing_on_first_scan"] = False
    write_json(config, cfg)

    def write_fixture(body: str, activities: list[dict[str, object]]) -> None:
        write_json(fixture, {
            "repos": {
                "Owner/repo": [{
                    "number": 7,
                    "title": "Board marker fixed later",
                    "url": "https://github.com/Owner/repo/pull/7",
                    "headRefName": "Hermes/board-aware-test",
                    "body": body,
                    "activities": activities,
                }],
            },
        })

    mutations = []
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: (board == "psp", "blocked" if board == "psp" else "", "missing on default"))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_unblock", lambda *args: mutations.append(("unblock", args)) or (True, ""))

    write_fixture("## Kanban\nKanban-Task: t_1a2ed8d4\n", [])
    first_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))
    write_fixture("## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n", [])
    second_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))
    write_fixture("## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n", [{
        "id": 101,
        "event_type": "PR issue comment",
        "action": "created",
        "actor": "human-reviewer",
        "actor_type": "User",
        "url": "https://github.com/Owner/repo/pull/7#issuecomment-101",
        "created_at": "2026-06-08T00:00:00Z",
    }])
    third_rc = bridge.scan(base_args(config, state, fixture, fixture_write=True))

    assert first_rc == 1
    assert second_rc == 0
    assert third_rc == 0
    assert [name for name, _ in mutations] == ["comment", "unblock"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "Owner/repo#7" in saved["baselined_prs"]
    assert saved["seen"] == {"Owner/repo#7:fixture:101": "2026-06-08T00:00:00Z"}
    assert saved["task_lookup_failed_prs"] == {}


def test_live_missing_task_is_not_marked_seen_or_acknowledged(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    pr = {
        "number": 8,
        "title": "Live PR missing default-board task",
        "url": "https://github.com/Owner/repo/pull/8",
        "headRefName": "Hermes/live-board-test",
        "body": "## Kanban\nKanban-Task: t_1a2ed8d4\n",
    }

    status_calls = []
    monkeypatch.setattr(bridge, "resolve_repo_auth", lambda cfg, repo: (bridge.AuthContext(source="test"), "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [pr])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (False, "", "missing on default"))
    monkeypatch.setattr(bridge, "collect_activities_from_api", lambda repo, number: (_ for _ in ()).throw(AssertionError("missing-task PRs must not collect or acknowledge activity")))
    monkeypatch.setattr(bridge, "create_eyes_reaction", lambda endpoint: (_ for _ in ()).throw(AssertionError("missing-task PRs must not acknowledge activity")), raising=False)

    rc = bridge.scan(base_args(config, state, fixture=None, board="psp"))

    assert rc == 1
    assert status_calls == [("t_1a2ed8d4", "default")]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["seen"] == {}
    assert saved["pending_reaction_acks"] == {}
    assert saved["reaction_acks"] == {}


def test_merged_pr_completion_uses_explicit_kanban_board_marker(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    merged_pr = {
        "number": 9,
        "title": "Merged PR",
        "url": "https://github.com/Owner/repo/pull/9",
        "headRefName": "Hermes/merged-board-test",
        "body": "## Kanban\nKanban-Board: psp\nKanban-Task: t_1a2ed8d4\n",
        "mergedAt": "2026-06-09T16:13:12Z",
    }

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "resolve_repo_auth", lambda cfg, repo: (bridge.AuthContext(source="test"), "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [merged_pr])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_complete", lambda *args: mutations.append(("complete", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture=None))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "psp")]
    assert [name for name, _ in mutations] == ["comment", "complete"]
    assert mutations[0][1][1] == "psp"
    assert mutations[1][1][1] == "psp"


def test_merged_pr_without_board_marker_uses_default_board_exactly(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_config(config, state)

    merged_pr = {
        "number": 10,
        "title": "Merged default-board PR",
        "url": "https://github.com/Owner/repo/pull/10",
        "headRefName": "Hermes/merged-default-board-test",
        "body": "## Kanban\nKanban-Task: t_1a2ed8d4\n",
        "mergedAt": "2026-06-09T16:13:12Z",
    }

    status_calls = []
    mutations = []
    monkeypatch.setattr(bridge, "resolve_repo_auth", lambda cfg, repo: (bridge.AuthContext(source="test"), "ok"))
    monkeypatch.setattr(bridge, "list_closed_prs_from_api", lambda repo, limit: [merged_pr])
    monkeypatch.setattr(bridge, "list_open_prs_from_api", lambda repo: [])
    monkeypatch.setattr(bridge, "task_status", lambda task_id, board: status_calls.append((task_id, board)) or (True, "blocked", ""))
    monkeypatch.setattr(bridge, "kanban_comment", lambda *args: mutations.append(("comment", args)) or (True, ""))
    monkeypatch.setattr(bridge, "kanban_complete", lambda *args: mutations.append(("complete", args)) or (True, ""))

    rc = bridge.scan(base_args(config, state, fixture=None, board="psp"))

    assert rc == 0
    assert status_calls == [("t_1a2ed8d4", "default")]
    assert [name for name, _ in mutations] == ["comment", "complete"]
    assert mutations[0][1][1] == "default"
    assert mutations[1][1][1] == "default"
