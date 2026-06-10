#!/usr/bin/env python3
"""Regression tests for the split bridge module boundaries."""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "github_pr_kanban_bridge.py"


def load_legacy_bridge():
    spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_split_modules_import_from_scripts_entrypoint_path() -> None:
    sys.path.insert(0, str(SCRIPTS))
    try:
        for name in (
            "gh_pr_kanban_bridge.commands",
            "gh_pr_kanban_bridge.common",
            "gh_pr_kanban_bridge.config",
            "gh_pr_kanban_bridge.fixtures",
            "gh_pr_kanban_bridge.github_api",
            "gh_pr_kanban_bridge.kanban_cli",
            "gh_pr_kanban_bridge.reactions",
            "gh_pr_kanban_bridge.scanner",
            "gh_pr_kanban_bridge.state",
        ):
            importlib.import_module(name)
    finally:
        sys.path.remove(str(SCRIPTS))


def test_legacy_script_preserves_public_patch_surface() -> None:
    bridge = load_legacy_bridge()
    activity = bridge.Activity(
        key="Owner/repo#1:issue-comment:2",
        event_type="PR issue comment",
        action="created",
        actor="reviewer",
        url="https://example.test/comment/2",
        created_at="2026-06-08T00:00:00Z",
    )

    assert bridge.reaction_endpoint_for_activity("Owner/repo", activity) == "repos/Owner/repo/issues/comments/2/reactions"
    assert bridge.extract_task_id("## Kanban\nKanban-Task: t_1234abcd\n") == "t_1234abcd"


def test_legacy_run_cmd_patch_reaches_split_command_helpers() -> None:
    bridge = load_legacy_bridge()
    calls: list[list[str]] = []

    def fake_run_cmd(args: list[str], *, check: bool = True):
        calls.append(args)
        if args[:2] == ["gh", "api"]:
            return SimpleNamespace(stdout='[{"number": 1}]', stderr="", returncode=0)
        if args[:4] == ["hermes", "kanban", "--board", "default"]:
            return SimpleNamespace(stdout=json.dumps({"task": {"status": "blocked"}}), stderr="", returncode=0)
        raise AssertionError(f"unexpected command: {args}")

    setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.gh_json(["api", "repos/Owner/repo/pulls/1/reviews"]) == [{"number": 1}]
    assert bridge.task_status("t_deadbeef", "default") == (True, "blocked", "")
    assert bridge.kanban_comment("t_deadbeef", "default", "author", "body") == (True, '{"task": {"status": "blocked"}}')
    assert bridge.kanban_unblock("t_deadbeef", "default", "reason") == (True, '{"task": {"status": "blocked"}}')
    assert bridge.kanban_complete("t_deadbeef", "default", "summary", {"ok": True}) == (True, '{"task": {"status": "blocked"}}')
    assert bridge.create_eyes_reaction("repos/Owner/repo/issues/comments/1/reactions") == (True, '[{"number": 1}]')
    assert calls == [
        ["gh", "api", "repos/Owner/repo/pulls/1/reviews"],
        ["hermes", "kanban", "--board", "default", "show", "--json", "t_deadbeef"],
        ["hermes", "kanban", "--board", "default", "comment", "--author", "author", "t_deadbeef", "body"],
        ["hermes", "kanban", "--board", "default", "unblock", "--reason", "reason", "t_deadbeef"],
        ["hermes", "kanban", "--board", "default", "complete", "--summary", "summary", "--metadata", '{"ok": true}', "t_deadbeef"],
        ["gh", "api", "-X", "POST", "repos/Owner/repo/issues/comments/1/reactions", "-f", "content=eyes", "-H", "Accept: application/vnd.github+json"],
    ]


def test_legacy_gh_json_patch_reaches_pr_list_helpers_and_scan(tmp_path) -> None:
    bridge = load_legacy_bridge()
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({
        "version": 1,
        "enabled": True,
        "repos": ["Owner/repo"],
        "state_path": str(state),
        "complete_merged_prs": True,
    }), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_gh_json(args: list[str]):
        calls.append(args)
        if args[:4] == ["pr", "list", "--repo", "Owner/repo"]:
            return []
        raise AssertionError(f"unexpected gh_json args: {args}")

    setattr(bridge, "gh_json", fake_gh_json)
    setattr(bridge, "gh_ready", lambda: (True, "ok"))

    assert bridge.list_open_prs_from_api("Owner/repo") == []
    assert bridge.list_closed_prs_from_api("Owner/repo", 30) == []
    assert bridge.scan(bridge.argparse.Namespace(
        config=str(config),
        state=str(state),
        board=None,
        dry_run=True,
        fixture=None,
        fixture_write=False,
        verbose=False,
        strict=True,
    )) == 0
    open_args = ["pr", "list", "--repo", "Owner/repo", "--state", "open", "--json", "number,title,url,headRefName,body,author,updatedAt", "--limit", "100"]
    closed_args = ["pr", "list", "--repo", "Owner/repo", "--state", "closed", "--json", "number,title,url,headRefName,body,author,updatedAt,closedAt,mergedAt", "--limit", "30"]
    assert calls == [open_args, closed_args, closed_args, open_args]


def test_legacy_reaction_helper_honors_patched_create_reaction() -> None:
    bridge = load_legacy_bridge()
    state = {
        "pending_reaction_acks": {
            "Owner/repo#1:issue-comment:2": {
                "task_id": "t_deadbeef",
                "endpoint": "repos/Owner/repo/issues/comments/2/reactions",
                "observed_at": "2026-06-08T00:00:00Z",
                "ready": True,
            }
        },
        "reaction_acks": {},
    }
    calls: list[str] = []

    setattr(bridge, "create_eyes_reaction", lambda endpoint: calls.append(endpoint) or (True, "created"))

    assert bridge.process_ready_reaction_acks(state, task_id="t_deadbeef") == []
    assert calls == ["repos/Owner/repo/issues/comments/2/reactions"]
    assert state["reaction_acks"] == {"Owner/repo#1:issue-comment:2": "2026-06-08T00:00:00Z"}


def test_legacy_scan_honors_patched_reaction_processing(tmp_path) -> None:
    bridge = load_legacy_bridge()
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({
        "version": 1,
        "enabled": True,
        "repos": ["Owner/repo"],
        "state_path": str(state),
        "complete_merged_prs": False,
    }), encoding="utf-8")
    state.write_text(json.dumps({
        "version": 1,
        "seen": {},
        "pending_unblocks": {},
        "pending_reaction_acks": {"Owner/repo#1:issue-comment:2": {"ready": True}},
        "reaction_acks": {},
    }), encoding="utf-8")
    calls: list[object] = []

    setattr(bridge, "gh_ready", lambda: (True, "ok"))
    setattr(bridge, "list_open_prs_from_api", lambda repo: [])
    setattr(bridge, "process_ready_reaction_acks", lambda state_arg, *, task_id=None: calls.append((state_arg, task_id)) or [])

    assert bridge.scan(bridge.argparse.Namespace(
        config=str(config),
        state=str(state),
        board=None,
        dry_run=False,
        fixture=None,
        fixture_write=False,
        verbose=False,
        strict=True,
    )) == 0
    assert len(calls) == 1


def test_legacy_main_uses_patchable_scan_wrapper(tmp_path) -> None:
    bridge = load_legacy_bridge()
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"version": 1, "enabled": True, "repos": [], "state_path": str(state)}), encoding="utf-8")
    seen_args = []

    setattr(bridge, "scan", lambda args: seen_args.append(args) or 0)

    assert bridge.main(["--config", str(config)]) == 0
    assert seen_args and seen_args[0].config == str(config)


def test_legacy_script_executable_entrypoint_runs_fixture_dry_run() -> None:
    proc = subprocess.run(
        [
            str(SCRIPT),
            "--config",
            str(ROOT / "fixtures" / "test-config.json"),
            "--fixture",
            str(ROOT / "fixtures" / "fixture.json"),
            "--dry-run",
            "--verbose",
            "--strict",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "DRY-RUN would comment+unblock" in proc.stdout
