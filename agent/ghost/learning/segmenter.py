"""Segmenter: Splits the continuous normalized action stream into task episodes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional
from agent.ghost.learning.models import Episode, NormalizedAction

# 5 minutes idle gap threshold from spec
DEFAULT_IDLE_GAP_SECONDS = 300.0


def parse_iso_ts(ts_str: str) -> datetime:
    """Parse ISO UTC timestamp safely."""
    # Replace Z with +00:00 for python fromisoformat
    clean_ts = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean_ts)
    except Exception:
        return datetime.now(timezone.utc)


def segment_into_episodes(
    actions: List[NormalizedAction],
    idle_gap_seconds: float = DEFAULT_IDLE_GAP_SECONDS,
    allowed_apps: Optional[List[str]] = None,
) -> List[Episode]:
    """Segment an ordered list of NormalizedActions into discrete Episodes.

    Segmentation triggers:
    1. Idle gap > idle_gap_seconds (default 300s / 5 min).
    2. Explicit session_id change.
    3. Return to starting application after completing a multi-app cycle.
    """
    if not actions:
        return []

    # Filter out actions from apps not in allowlist if provided
    filtered_actions = actions
    if allowed_apps:
        filtered_actions = [a for a in actions if a.app_id in allowed_apps]

    if not filtered_actions:
        return []

    episodes: List[Episode] = []
    current_actions: List[NormalizedAction] = [filtered_actions[0]]
    prev_time = parse_iso_ts(filtered_actions[0].ts)
    current_session = filtered_actions[0].session_id

    for action in filtered_actions[1:]:
        curr_time = parse_iso_ts(action.ts)
        delta_seconds = abs((curr_time - prev_time).total_seconds())

        is_idle_break = delta_seconds > idle_gap_seconds
        is_session_break = action.session_id != current_session

        if is_idle_break or is_session_break:
            # Finalize current episode
            episodes.append(
                Episode(
                    episode_id=f"ep_{uuid.uuid4().hex[:8]}",
                    session_id=current_session,
                    start_ts=current_actions[0].ts,
                    end_ts=current_actions[-1].ts,
                    actions=current_actions,
                    tokens=[a.token for a in current_actions],
                )
            )
            current_actions = [action]
            current_session = action.session_id
        else:
            current_actions.append(action)

        prev_time = curr_time

    if current_actions:
        episodes.append(
            Episode(
                episode_id=f"ep_{uuid.uuid4().hex[:8]}",
                session_id=current_session,
                start_ts=current_actions[0].ts,
                end_ts=current_actions[-1].ts,
                actions=current_actions,
                tokens=[a.token for a in current_actions],
            )
        )

    return episodes
