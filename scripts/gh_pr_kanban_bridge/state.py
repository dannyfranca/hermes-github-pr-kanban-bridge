"""State retention and garbage-collection helpers."""
from __future__ import annotations

import datetime as dt
from typing import Any

from .common import (
    DEFAULT_STATE_MAX_BASELINED_PRS,
    DEFAULT_STATE_MAX_SEEN_ENTRIES,
    DEFAULT_STATE_RETENTION_DAYS,
    parse_utc_timestamp,
    positive_int,
    pr_key_from_activity_key,
)


def prune_timestamped_mapping(
    entries: dict[str, str],
    *,
    now: dt.datetime,
    retention_days: int,
    max_entries: int,
    active_keys: set[str],
    key_to_active_key,
) -> tuple[dict[str, str], int]:
    cutoff = now - dt.timedelta(days=retention_days)
    retained: dict[str, str] = {}
    pruned = 0

    for key, timestamp in entries.items():
        active_key = key_to_active_key(key)
        if active_key in active_keys:
            retained[key] = timestamp
            continue
        parsed = parse_utc_timestamp(timestamp)
        # Keep malformed timestamps conservatively; they may be hand-edited or
        # produced by an older bridge version and should not be lost silently.
        if parsed is None or parsed >= cutoff:
            retained[key] = timestamp
        else:
            pruned += 1

    if max_entries > 0 and len(retained) > max_entries:
        active_or_unknown = {
            key: timestamp
            for key, timestamp in retained.items()
            if key_to_active_key(key) in active_keys or parse_utc_timestamp(timestamp) is None
        }
        candidates = [
            (parse_utc_timestamp(timestamp), key)
            for key, timestamp in retained.items()
            if key not in active_or_unknown
        ]
        candidates.sort(key=lambda item: (item[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), item[1]))
        removable = max(0, len(retained) - max_entries)
        remove_keys = {key for _, key in candidates[:removable]}
        if remove_keys:
            retained = {key: timestamp for key, timestamp in retained.items() if key not in remove_keys}
            pruned += len(remove_keys)

    return retained, pruned


def gc_state(state: dict[str, Any], cfg: dict[str, Any], active_pr_keys: set[str], now_text: str) -> dict[str, int]:
    now = parse_utc_timestamp(now_text) or dt.datetime.now(dt.timezone.utc)
    retention_days = positive_int(cfg.get("state_retention_days"), DEFAULT_STATE_RETENTION_DAYS)
    max_seen = positive_int(cfg.get("state_max_seen_entries"), DEFAULT_STATE_MAX_SEEN_ENTRIES)
    max_baselined = positive_int(cfg.get("state_max_baselined_prs"), DEFAULT_STATE_MAX_BASELINED_PRS)

    seen = state.get("seen")
    if not isinstance(seen, dict):
        seen = {}
    baselined_prs = state.get("baselined_prs")
    if not isinstance(baselined_prs, dict):
        baselined_prs = {}
    task_lookup_failed_prs = state.get("task_lookup_failed_prs")
    if not isinstance(task_lookup_failed_prs, dict):
        task_lookup_failed_prs = {}
    reaction_acks = state.get("reaction_acks")
    if not isinstance(reaction_acks, dict):
        reaction_acks = {}

    pruned_seen_map, pruned_seen = prune_timestamped_mapping(
        {str(k): str(v) for k, v in seen.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_seen,
        active_keys=active_pr_keys,
        key_to_active_key=pr_key_from_activity_key,
    )
    pruned_baseline_map, pruned_baselined = prune_timestamped_mapping(
        {str(k): str(v) for k, v in baselined_prs.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_baselined,
        active_keys=active_pr_keys,
        key_to_active_key=lambda key: key,
    )
    pruned_reaction_acks_map, pruned_reaction_acks = prune_timestamped_mapping(
        {str(k): str(v) for k, v in reaction_acks.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_seen,
        active_keys=active_pr_keys,
        key_to_active_key=pr_key_from_activity_key,
    )
    pruned_task_lookup_failed_map, pruned_task_lookup_failed = prune_timestamped_mapping(
        {str(k): str(v) for k, v in task_lookup_failed_prs.items()},
        now=now,
        retention_days=retention_days,
        max_entries=max_baselined,
        active_keys=active_pr_keys,
        key_to_active_key=lambda key: key,
    )
    state["seen"] = pruned_seen_map
    state["baselined_prs"] = pruned_baseline_map
    state["task_lookup_failed_prs"] = pruned_task_lookup_failed_map
    state["reaction_acks"] = pruned_reaction_acks_map
    state["last_gc_at"] = now_text
    state["last_gc"] = {
        "retention_days": retention_days,
        "max_seen_entries": max_seen,
        "max_baselined_prs": max_baselined,
        "active_prs": len(active_pr_keys),
        "pruned_seen": pruned_seen,
        "pruned_baselined_prs": pruned_baselined,
        "pruned_task_lookup_failed_prs": pruned_task_lookup_failed,
        "pruned_reaction_acks": pruned_reaction_acks,
    }
    return {
        "seen": pruned_seen,
        "baselined_prs": pruned_baselined,
        "task_lookup_failed_prs": pruned_task_lookup_failed,
        "reaction_acks": pruned_reaction_acks,
    }
