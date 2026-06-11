"""Command-line entrypoint for the GitHub PR Kanban bridge."""
from __future__ import annotations

import argparse
from pathlib import Path

from .common import DEFAULT_BOARD, DEFAULT_CONFIG
from .config import init_config, load_config
from .scanner import scan


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
