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
5. Linked Kanban task exists and is currently `blocked`.
6. New activity is from a human, not an ignored bot/actor.

Merged PRs are also scanned when `complete_merged_prs` is enabled. A recently closed PR with `mergedAt` and a `Kanban-Task` marker completes the linked Kanban card once, tracked in state as `completed_prs`.

Activity types watched:

- PR reviews
- PR review comments
- PR issue comments

The bridge never copies raw review/comment bodies into Kanban comments or dry-run output.

## State and retention

State stores compact IDs, not comment bodies:

- `seen`: processed GitHub activity keys like `Owner/repo#42:review-comment:123456`.
- `baselined_prs`: PRs whose historical activity was marked seen on first encounter.
- `pending_unblocks`: retry queue if Kanban unblock failed.
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
  --config ~/.hermes/profiles/coder/github-pr-kanban-bridge/config.json
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
```
