#!/usr/bin/env python3
"""Regression checks for GitHub PR bridge comment-body leakage."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_pr_kanban_bridge.py"
spec = importlib.util.spec_from_file_location("github_pr_kanban_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

SECRET_BODY = "token ghp_thisRawReviewBodyMustNotLeak and private reviewer details"


class NoBodyLeakTests(unittest.TestCase):
    def test_activity_comment_has_no_excerpt_field(self) -> None:
        activity = bridge.Activity(
            key="DannyFranca/example#42:issue-comment:1001",
            event_type="PR issue comment",
            action="created",
            actor="human-reviewer",
            url="https://github.com/DannyFranca/example/pull/42#issuecomment-1001",
            created_at="2026-06-08T21:20:00Z",
        )
        pr = {
            "number": 42,
            "title": "Example Hermes PR with linked Kanban card",
            "url": "https://github.com/DannyFranca/example/pull/42",
        }

        comment = bridge.activity_comment("DannyFranca/example", pr, "t_1f71d97b", activity)

        self.assertNotIn(SECRET_BODY, comment)
        self.assertNotIn("Excerpt:", comment)

    def test_fixture_activity_bodies_are_not_copied_into_activity_records(self) -> None:
        pr = {
            "number": 42,
            "url": "https://github.com/DannyFranca/example/pull/42",
            "activities": [{"id": 1001, "actor": "human-reviewer", "body": SECRET_BODY}],
        }

        activities = bridge.fixture_activities(pr, "DannyFranca/example")

        self.assertFalse(hasattr(activities[0], "body_excerpt"))

    def test_api_activity_bodies_are_not_copied_into_activity_records(self) -> None:
        payloads = [
            [{"id": 1, "state": "COMMENTED", "user": {"login": "alice", "type": "User"}, "body": SECRET_BODY}],
            [{"id": 2, "user": {"login": "bob", "type": "User"}, "body": SECRET_BODY}],
            [{"id": 3, "user": {"login": "carol", "type": "User"}, "body": SECRET_BODY}],
        ]

        with patch.object(bridge, "gh_json", side_effect=payloads):
            activities = bridge.collect_activities_from_api("DannyFranca/example", 42)

        self.assertTrue(all(not hasattr(a, "body_excerpt") for a in activities))

    def test_dry_run_output_does_not_print_fixture_body_text(self) -> None:
        fixture = {
            "repos": {
                "DannyFranca/example": [
                    {
                        "number": 42,
                        "title": "Example Hermes PR with linked Kanban card",
                        "url": "https://github.com/DannyFranca/example/pull/42",
                        "headRefName": "Hermes/example-bridge-test",
                        "body": "## Kanban\nKanban-Task: t_1f71d97b\nOpened-by: Hermes\n",
                        "activities": [
                            {
                                "id": 1001,
                                "event_type": "PR issue comment",
                                "action": "created",
                                "actor": "human-reviewer",
                                "url": "https://github.com/DannyFranca/example/pull/42#issuecomment-1001",
                                "created_at": "2026-06-08T21:20:00Z",
                                "body": SECRET_BODY,
                            }
                        ],
                    }
                ]
            }
        }
        config = {
            "version": 1,
            "enabled": True,
            "board": "default",
            "notify_existing_on_first_scan": True,
            "repos": ["DannyFranca/example"],
            "state_path": "unused.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            config_path = Path(tmp) / "config.json"
            state_path = Path(tmp) / "state.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            args = Namespace(
                config=str(config_path),
                state=str(state_path),
                board="default",
                fixture=str(fixture_path),
                dry_run=True,
                verbose=True,
                strict=True,
            )

            buf = io.StringIO()
            with patch.object(bridge, "task_status", return_value=(True, "blocked", "")):
                with redirect_stdout(buf):
                    rc = bridge.scan(args)

        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertNotIn(SECRET_BODY, output)
        self.assertNotIn("Excerpt:", output)


if __name__ == "__main__":
    unittest.main()
