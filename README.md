# Hermes GitHub PR → Kanban bridge

Local poller for Danny's Hermes VM workflow. It scans explicitly allowlisted GitHub repositories and wakes blocked Kanban cards when a human adds review activity to a Hermes-created PR.

## Runtime model

- Source lives in this repo: `~/git/hermes-github-pr-kanban-bridge`.
- Runtime config/state live outside git under the coder profile:
  - Config: `~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json`
  - State: `~/.hermes/profiles/coder/github-pr-kanban-bridge/state.json`
- User systemd timer runs every 1 minute.

## PR gates

A PR can wake a blocked Kanban card on human review activity only when all gates pass:

1. Repo is listed in config `repos`.
2. PR is open.
3. PR head branch starts with `Hermes/`.
4. PR body contains `Kanban-Task: t_xxxxxxxx`.
5. The bridge resolves the Kanban board from `Kanban-Board: <board_slug>` in the PR body, or exactly `default` when the marker is absent. It does not search other boards.
6. Linked Kanban task exists on the resolved board and is currently `blocked`.
7. New activity is from a human, not an ignored bot/actor.

Merged PRs are also scanned when `complete_merged_prs` is enabled. A recently closed PR with `mergedAt` and a `Kanban-Task` marker completes the linked Kanban card once on the resolved board, tracked in state as `completed_prs`.

PR bodies for non-default boards should include the board marker:

```md
## Kanban
Kanban-Board: psp
Kanban-Task: t_xxxxxxxx
```

If `Kanban-Board` is absent, `default` is assumed exactly. A task that only exists on another board is skipped, logged as not found on `default`, and its GitHub activity is not marked seen.

Activity types watched:

- PR reviews
- PR review comments
- PR issue comments

The bridge never copies raw review/comment bodies into Kanban comments or dry-run output.

## State and retention

State stores compact IDs, not comment bodies:

- `seen`: processed GitHub activity keys like `Owner/repo#42:review-comment:123456`.
- `baselined_prs`: PRs whose historical activity was marked seen on first encounter.
- `pending_unblocks`: retry queue if Kanban unblock failed, keyed by resolved board and task for new entries.
- `task_lookup_failed_prs`: linked PRs whose task was not found on the resolved board; this keeps later marker fixes from baselining and swallowing the original activity.
- `completed_prs`: merged PRs already used to complete linked Kanban cards.

Retention defaults:

```json
{
  "state_retention_days": 90,
  "state_max_seen_entries": 5000,
  "state_max_baselined_prs": 1000
}
```

Active open linked Hermes PRs are exempt from GC, so old processed activity remains deduped during review iterations. GC is skipped if a repo scan fails, because active PR state may be incomplete. `pending_unblocks` are not pruned by GC.

## Configure

Copy and edit the sample:

```bash
mkdir -p ~/.hermes/profiles/coder/github-pr-kanban-bridge
cp config.example.json ~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json
$EDITOR ~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json
```

Add exact owner/repo strings:

```json
"repos": ["dannyfranca/hermes-github-pr-kanban-bridge"]
```

Configure authentication explicitly or leave `auth.mode` as `auto`:

```json
"auth": {
  "mode": "auto",
  "github_app": {
    "helper": "/home/agent/bin/hermes-gh-app",
    "config": "/home/agent/.hermes/github-apps.json"
  }
}
```

Supported modes:

- `auto`: prefer an ambient `GH_TOKEN`/`GITHUB_TOKEN`, then existing `gh auth`, then mint a GitHub App token per allowlisted repo.
- `gh` (aliases: `pat`, `token`): use an ambient token or existing `gh auth` only.
- `github_app`: mint a token for each repo with the configured `hermes-gh-app` helper.

GitHub App mode is service-safe: the bridge passes `HERMES_GITHUB_APP_CONFIG` to the helper and sets a repo-scoped token only for the `gh` calls for that repo. This supports allowlisted repos that span owners/installations. If auth cannot be resolved, the scan prints an actionable error to stderr and exits non-zero instead of succeeding silently.

## Project layout

- `scripts/github_pr_kanban_bridge.py` remains the stable CLI/systemd entrypoint and legacy import surface.
- `scripts/gh_pr_kanban_bridge/` contains the implementation split by concern: config, JSON state IO, GitHub API access, Kanban CLI access, fixtures, reaction acknowledgements, state retention, and scan orchestration.

## Commands

Run tests:

```bash
python3 -m pytest -q
```

Fixture dry-run, no Kanban writes and no state mutation:

```bash
scripts/github_pr_kanban_bridge.py \
  --config fixtures/test-config.json \
  --fixture fixtures/fixture.json \
  --verbose
```

Live dry-run:

```bash
scripts/github_pr_kanban_bridge.py \
  --config ~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json \
  --dry-run --verbose
```

Live scheduled behavior:

```bash
scripts/github_pr_kanban_bridge.py \
  --config ~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json \
  --strict
```

## systemd user timer

Installed on Danny's VM as:

```text
~/.config/systemd/user/hermes-github-pr-kanban-bridge.service
~/.config/systemd/user/hermes-github-pr-kanban-bridge.timer
```

Useful status commands:

```bash
systemctl --user status hermes-github-pr-kanban-bridge.timer --no-pager
systemctl --user list-timers hermes-github-pr-kanban-bridge.timer --no-pager --all
systemctl --user status hermes-github-pr-kanban-bridge.service --no-pager --lines=30
journalctl --user -u hermes-github-pr-kanban-bridge.service --no-pager --since '15 minutes ago'
```

The packaged service sets `HERMES_GITHUB_APP_CONFIG=/home/agent/.hermes/github-apps.json` and runs the bridge with `--strict`, so auth/API failures are visible in `systemctl status`/`journalctl` and the timer run is marked failed instead of silently no-oping.
