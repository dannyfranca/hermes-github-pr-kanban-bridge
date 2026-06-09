# Changelog

## 0.1.0 - 2026-06-08

- Initial versioned release of the local GitHub PR activity → Hermes Kanban unblock bridge.
- Poll allowlisted repositories for open `Hermes/` PRs linked with `Kanban-Task: t_xxxxxxxx`.
- Wake blocked Kanban cards on new human PR review/comment activity.
- Store compact dedupe state without raw GitHub comment/review bodies.
- Add bounded state retention/GC for `seen` and `baselined_prs`.
- Include fixture/dry-run mode, systemd timer templates, and pytest coverage.
