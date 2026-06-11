#!/usr/bin/env python3
"""Authentication mode regression tests for github_pr_kanban_bridge.py."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_pr_kanban_bridge.py"
spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
assert spec is not None and spec.loader is not None
bridge = importlib.util.module_from_spec(spec)
sys.modules["github_pr_kanban_bridge"] = bridge
spec.loader.exec_module(bridge)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def base_args(config: Path, state: Path, **overrides: object) -> argparse.Namespace:
    data = {
        "config": str(config),
        "state": str(state),
        "board": None,
        "dry_run": True,
        "fixture": None,
        "fixture_write": False,
        "verbose": True,
        "strict": True,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def test_auto_mode_mints_repo_scoped_app_tokens_for_each_allowlisted_repo(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "auto",
            "github_app": {
                "helper": str(helper),
                "config": "/opt/github-apps.json",
            },
        },
        "repos": ["Owner/one", "Other/two"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(bridge, "gh_ready", lambda: (False, "gh CLI is not authenticated"))

    helper_calls: list[tuple[str, dict[str, str] | None]] = []
    gh_calls: list[tuple[list[str], str | None]] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            repo = args[2]
            helper_calls.append((repo, env_overrides))
            return subprocess.CompletedProcess(args, 0, stdout=f"token-for-{repo}\n", stderr="")
        if args[:2] == ["gh", "auth"]:
            raise AssertionError("auto mode uses patched gh_ready, not gh auth status directly")
        if args[:2] == ["gh", "pr"]:
            gh_calls.append((args, (env_overrides or {}).get("GH_TOKEN")))
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    rc = bridge.scan(base_args(config, state))

    assert rc == 0
    assert helper_calls == [
        ("Owner/one", {"HERMES_GITHUB_APP_CONFIG": "/opt/github-apps.json"}),
        ("Other/two", {"HERMES_GITHUB_APP_CONFIG": "/opt/github-apps.json"}),
    ]
    assert [token for _, token in gh_calls] == [
        "token-for-Owner/one",
        "token-for-Owner/one",
        "token-for-Other/two",
        "token-for-Other/two",
    ]


def test_auto_auth_prefers_ambient_token_without_calling_github_app_helper(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "auto",
            "github_app": {"helper": "/opt/hermes-gh-app"},
        },
        "repos": ["Owner/repo"],
    })
    monkeypatch.setenv("GH_TOKEN", "ambient-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[0] == "/opt/hermes-gh-app" or args[:2] == ["gh", "auth"]:
            raise AssertionError("auto mode should prefer ambient GH_TOKEN")
        if args[:2] == ["gh", "pr"]:
            assert env_overrides is None
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.scan(base_args(config, state)) == 0


def test_missing_auth_is_loud_and_nonzero_even_without_strict(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {"mode": "auto"},
        "repos": ["Owner/repo"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_GITHUB_APP_CONFIG", raising=False)
    monkeypatch.delenv("HERMES_GITHUB_APP_HELPER", raising=False)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(bridge, "gh_ready", lambda: (False, "gh CLI is not authenticated"))

    rc = bridge.scan(base_args(config, state, strict=False, verbose=False))

    captured = capsys.readouterr()
    assert rc == 2
    assert "auth unavailable for Owner/repo" in captured.err
    assert "configure auth.mode='github_app'" in captured.err


def test_auto_mode_uses_service_env_github_app_config_when_config_lacks_app_block(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {"mode": "auto"},
        "repos": ["Owner/repo"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("HERMES_GITHUB_APP_HELPER", str(helper))
    monkeypatch.setenv("HERMES_GITHUB_APP_CONFIG", "/service/github-apps.json")
    monkeypatch.setattr(bridge, "gh_ready", lambda: (False, "gh CLI is not authenticated"))

    helper_envs: list[dict[str, str] | None] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            helper_envs.append(env_overrides)
            return subprocess.CompletedProcess(args, 0, stdout="service-token\n", stderr="")
        if args[:2] == ["gh", "pr"]:
            assert (env_overrides or {}).get("GH_TOKEN") == "service-token"
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.scan(base_args(config, state, strict=True, verbose=False)) == 0
    assert helper_envs == [{"HERMES_GITHUB_APP_CONFIG": "/service/github-apps.json"}]


def test_auth_failure_for_one_repo_stays_nonzero_but_scans_resolved_repos(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "github_app",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Good/repo", "Bad/repo"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    scanned_repos: list[str] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            repo = args[2]
            if repo == "Bad/repo":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="installation missing")
            return subprocess.CompletedProcess(args, 0, stdout=f"token-for-{repo}\n", stderr="")
        if args[:2] == ["gh", "pr"]:
            scanned_repos.append(args[args.index("--repo") + 1])
            assert (env_overrides or {}).get("GH_TOKEN") == "token-for-Good/repo"
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    rc = bridge.scan(base_args(config, state, strict=False, verbose=False))

    captured = capsys.readouterr()
    assert rc == 2
    assert scanned_repos == ["Good/repo", "Good/repo"]
    assert "auth unavailable for Bad/repo" in captured.err
    assert "installation missing" in captured.err


def test_non_executable_github_app_helper_reports_actionable_auth_error(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o644)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "github_app",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Owner/repo"],
    })
    monkeypatch.setattr(bridge, "run_cmd", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("helper must not be executed")))

    rc = bridge.scan(base_args(config, state, strict=False, verbose=False))

    captured = capsys.readouterr()
    assert rc == 2
    assert "GitHub App helper is not executable" in captured.err


def test_auto_mode_prefers_existing_gh_auth_over_configured_github_app(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "auto",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Owner/repo"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))

    gh_envs: list[dict[str, str] | None] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            raise AssertionError("auto mode should prefer existing gh auth before GitHub App")
        if args[:2] == ["gh", "pr"]:
            gh_envs.append(env_overrides)
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.scan(base_args(config, state, strict=True, verbose=False)) == 0
    assert gh_envs == [None, None]


def test_gh_auth_mode_preserves_existing_gh_auth_without_token_env(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {"mode": "gh"},
        "repos": ["Owner/repo"],
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(bridge, "gh_ready", lambda: (True, "ok"))

    gh_envs: list[dict[str, str] | None] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == ["gh", "pr"]:
            gh_envs.append(env_overrides)
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.scan(base_args(config, state)) == 0
    assert gh_envs == [None, None]


def test_github_app_token_reaches_legacy_reaction_ack_path(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "complete_merged_prs": False,
        "auth": {
            "mode": "github_app",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Owner/repo"],
    })
    write_json(state, {
        "version": 1,
        "seen": {},
        "pending_unblocks": {},
        "pending_reaction_acks": {
            "Owner/repo#1:issue-comment:2": {
                "repo": "Owner/repo",
                "task_id": "t_1234abcd",
                "endpoint": "repos/Owner/repo/issues/comments/2/reactions",
                "observed_at": "2026-06-11T00:00:00+00:00",
                "ready": True,
            },
        },
        "reaction_acks": {},
    })
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    reaction_tokens: list[str | None] = []

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            return subprocess.CompletedProcess(args, 0, stdout="token-for-Owner/repo\n", stderr="")
        if args[:2] == ["gh", "api"]:
            reaction_tokens.append((env_overrides or {}).get("GH_TOKEN"))
            return subprocess.CompletedProcess(args, 0, stdout="{}\n", stderr="")
        if args[:2] == ["gh", "pr"]:
            assert (env_overrides or {}).get("GH_TOKEN") == "token-for-Owner/repo"
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    assert bridge.scan(base_args(config, state, dry_run=False, strict=True, verbose=False)) == 0
    assert reaction_tokens == ["token-for-Owner/repo"]


def test_github_app_mode_requires_gh_cli_for_actionable_service_error(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "github_app",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Owner/repo"],
    })
    monkeypatch.setattr(bridge.shutil, "which", lambda name: None if name == "gh" else None)
    monkeypatch.setattr(bridge, "run_cmd", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("helper must not be called without gh")))

    rc = bridge.scan(base_args(config, state, strict=False, verbose=False))

    captured = capsys.readouterr()
    assert rc == 2
    assert "gh CLI is not installed" in captured.err


def test_failed_github_app_helper_does_not_echo_secret_stdout(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    helper = tmp_path / "hermes-gh-app"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    write_json(config, {
        "version": 1,
        "enabled": True,
        "board": "default",
        "state_path": str(state),
        "auth": {
            "mode": "github_app",
            "github_app": {"helper": str(helper)},
        },
        "repos": ["Owner/repo"],
    })
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run_cmd(args, *, check=True, env_overrides=None):
        if args[:2] == [str(helper), "token"]:
            return subprocess.CompletedProcess(args, 1, stdout="secret-token-fragment\n", stderr="installation missing")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bridge, "run_cmd", fake_run_cmd)

    rc = bridge.scan(base_args(config, state, strict=False, verbose=False))

    captured = capsys.readouterr()
    assert rc == 2
    assert "installation missing" in captured.err
    assert "secret-token-fragment" not in captured.err
