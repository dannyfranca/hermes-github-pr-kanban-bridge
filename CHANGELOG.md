# Changelog

## Unreleased

- Resolve linked Kanban boards from `Kanban-Board: <board_slug>` PR body markers; PRs without the marker now target `default` exactly and never fuzzy-search other boards.
- Keep missing-task GitHub activity unacknowledged/unseen and preserve the PR for a later marker fix so the original activity is not swallowed by first-scan baselining.
- Scope pending unblock retries and delayed GitHub reaction acknowledgements by resolved board plus task id.

## 0.1.0 - 2026-06-08

- Initial versioned release of the local GitHub PR activity → Hermes Kanban unblock bridge.
- Poll allowlisted repositories for open `Hermes/` PRs linked with `Kanban-Task: t_xxxxxxxx`.
- Wake blocked Kanban cards on new human PR review/comment activity.
- Store compact dedupe state without raw GitHub comment/review bodies.
- Add bounded state retention/GC for `seen` and `baselined_prs`.
- Include fixture/dry-run mode, systemd timer templates, and pytest coverage.
