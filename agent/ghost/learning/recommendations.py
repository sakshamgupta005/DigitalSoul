"""Recommendation scoring and card generator."""

from __future__ import annotations

import statistics
from typing import List, Dict, Any, Optional
from agent.ghost.learning.models import Episode, WorkflowCandidate
from agent.ghost.learning.segmenter import parse_iso_ts


def compute_episode_duration_seconds(ep: Episode) -> float:
    """Calculate duration of an episode in seconds."""
    start = parse_iso_ts(ep.start_ts)
    end = parse_iso_ts(ep.end_ts)
    dur = (end - start).total_seconds()
    return max(dur, 1.0)


def generate_recommendation_card(
    candidate: WorkflowCandidate,
    evidence_episodes: List[Episode],
    manual_steps_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute recommendation metrics:
    frequency × median duration × manual steps
    """
    count = len(evidence_episodes) or candidate.support
    durations = [compute_episode_duration_seconds(ep) for ep in evidence_episodes] if evidence_episodes else [360.0]

    median_dur_sec = statistics.median(durations) if durations else 360.0
    median_dur_min = max(1, round(median_dur_sec / 60.0))

    weekly_seconds = count * median_dur_sec
    weekly_hours = round(weekly_seconds / 3600.0, 1)

    steps_count = manual_steps_count or len(candidate.steps)

    card_text = (
        f"You did this {count} times this week, ~{median_dur_min} min each "
        f"(~{weekly_hours}h weekly across {steps_count} manual steps); automate?"
    )

    return {
        "candidate_id": candidate.candidate_id,
        "workflow_name": candidate.name,
        "frequency_weekly": count,
        "median_duration_minutes": median_dur_min,
        "weekly_time_saved_hours": weekly_hours,
        "manual_steps_count": steps_count,
        "headline": card_text,
        "dismissed": candidate.dismissed,
    }
