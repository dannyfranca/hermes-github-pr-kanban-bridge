#!/usr/bin/env python3
"""Regression tests for github_pr_kanban_bridge.py."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_pr_kanban_bridge.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BaselineSemanticsTest(unittest.TestCase):
    def test_newly_allowlisted_pr_is_baselined_even_when_global_seen_state_exists(self) -> None:
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            config_path = root / "config.json"
            fixture_path = root / "fixture.json"

            state_path.write_text(json.dumps({
                "version": 1,
                "seen": {
                    "existing/repo#1:issue-comment:old": "2026-06-01T00:00:00Z",
                },
                "pending_unblocks": {},
                "last_scan_at": "2026-06-01T00:00:00Z",
            }), encoding="utf-8")
            config_path.write_text(json.dumps({
                "version": 1,
                "enabled": True,
                "board": "test-board",
                "state_path": str(state_path),
                "notify_existing_on_first_scan": False,
                "repos": ["new/repo"],
            }), encoding="utf-8")
            fixture_path.write_text(json.dumps({
                "repos": {
                    "new/repo": [{
                        "number": 42,
                        "title": "historical feedback",
                        "url": "https://github.example/new/repo/pull/42",
                        "headRefName": "Hermes/t_12345678-fix",
                        "body": "Kanban-Task: t_12345678",
                        "activities": [{
                            "key": "new/repo#42:issue-comment:99",
                            "event_type": "PR issue comment",
                            "action": "created",
                            "actor": "human-reviewer",
                            "actor_type": "User",
                            "url": "https://github.example/new/repo/pull/42#issuecomment-99",
                            "created_at": "2026-06-02T00:00:00Z",
                            "body": "old comment from before allowlisting",
                        }],
                    }],
                },
            }), encoding="utf-8")

            comments: list[str] = []
            unblocks: list[str] = []
            bridge.task_status = lambda task_id, board: (True, "blocked", "")
            bridge.kanban_comment = lambda task_id, board, author, body: (comments.append(body) or (True, ""))
            bridge.kanban_unblock = lambda task_id, board, reason: (unblocks.append(reason) or (True, ""))

            rc = bridge.scan(argparse.Namespace(
                config=str(config_path),
                state=None,
                board=None,
                fixture=str(fixture_path),
                fixture_write=True,
                dry_run=False,
                verbose=False,
                strict=True,
            ))

            self.assertEqual(rc, 0)
            self.assertEqual(comments, [])
            self.assertEqual(unblocks, [])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("new/repo#42", saved["baselined_prs"])
            self.assertIn("new/repo#42:issue-comment:99", saved["seen"])


if __name__ == "__main__":
    unittest.main()
