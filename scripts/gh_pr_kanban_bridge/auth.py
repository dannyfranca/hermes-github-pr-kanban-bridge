"""Authentication resolution for GitHub API access."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import run_cmd
from .common import DEFAULT_GITHUB_APP_CONFIG, DEFAULT_GITHUB_APP_HELPER
from .github_api import gh_ready


@dataclass(frozen=True)
class AuthContext:
    source: str
    env: dict[str, str] | None = None


def ambient_token_present() -> bool:
    return bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))


def gh_cli_available() -> tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "gh CLI is not installed"
    return True, "ok"


def auth_settings(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw = cfg.get("auth")
    auth = raw if isinstance(raw, dict) else {}
    mode = str(auth.get("mode") or cfg.get("auth_mode") or "auto").strip().lower()
    aliases = {"pat": "gh", "token": "gh", "github-app": "github_app", "app": "github_app"}
    return aliases.get(mode, mode), auth


def github_app_settings(auth: dict[str, Any]) -> dict[str, str]:
    raw = auth.get("github_app")
    github_app = raw if isinstance(raw, dict) else {}
    helper = str(
        github_app.get("helper")
        or os.environ.get("HERMES_GITHUB_APP_HELPER")
        or DEFAULT_GITHUB_APP_HELPER
    )
    config = github_app.get("config") or os.environ.get("HERMES_GITHUB_APP_CONFIG") or DEFAULT_GITHUB_APP_CONFIG
    return {"helper": helper, "config": str(config)}


def github_app_configured(auth: dict[str, Any]) -> bool:
    raw = auth.get("github_app")
    return bool(
        (isinstance(raw, dict) and raw)
        or os.environ.get("HERMES_GITHUB_APP_CONFIG")
        or os.environ.get("HERMES_GITHUB_APP_HELPER")
    )


def mint_github_app_token(repo: str, auth: dict[str, Any]) -> tuple[AuthContext | None, str]:
    settings = github_app_settings(auth)
    helper = settings["helper"]
    helper_path = shutil.which(helper) if os.path.sep not in helper else helper
    if not helper_path or (os.path.sep in helper and not Path(helper).exists()):
        return None, f"GitHub App helper not found: {helper}"
    if not os.access(helper_path, os.X_OK):
        return None, f"GitHub App helper is not executable: {helper_path}"

    env_overrides = {"HERMES_GITHUB_APP_CONFIG": settings["config"]}
    try:
        proc = run_cmd([helper_path, "token", repo], check=False, env_overrides=env_overrides)
    except OSError as e:
        return None, f"GitHub App token mint failed for {repo}: {e.__class__.__name__}: {e}"
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:500]
        return None, f"GitHub App token mint failed for {repo}: {detail or 'helper returned non-zero'}"
    token = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if not token:
        return None, f"GitHub App token mint failed for {repo}: helper returned an empty token"
    return AuthContext(source="github_app", env={"GH_TOKEN": token, "GITHUB_TOKEN": token}), "ok"


def resolve_repo_auth(cfg: dict[str, Any], repo: str) -> tuple[AuthContext | None, str]:
    mode, auth = auth_settings(cfg)
    if mode not in {"auto", "gh", "github_app"}:
        return None, f"unsupported auth.mode={mode!r}; use 'auto', 'gh', or 'github_app'"

    gh_available, gh_available_msg = gh_cli_available()
    if not gh_available:
        return None, gh_available_msg

    if mode == "github_app":
        return mint_github_app_token(repo, auth)

    if ambient_token_present():
        return AuthContext(source="ambient-token"), "ok"

    if mode in {"auto", "gh"}:
        ok, msg = gh_ready()
        if ok:
            return AuthContext(source="gh-auth"), "ok"
        if mode == "gh":
            return None, f"gh/PAT auth unavailable for {repo}: {msg}"
        if not github_app_configured(auth):
            app_msg = "GitHub App auth is not configured"
        else:
            app_context, app_msg = mint_github_app_token(repo, auth)
            if app_context is not None:
                return app_context, app_msg
        return None, (
            f"auth unavailable for {repo}: {msg}; {app_msg}. "
            "Set GH_TOKEN/GITHUB_TOKEN, run 'gh auth login', or configure auth.mode='github_app' "
            "with auth.github_app.helper and HERMES_GITHUB_APP_CONFIG/auth.github_app.config."
        )

    return None, f"auth unavailable for {repo}"
