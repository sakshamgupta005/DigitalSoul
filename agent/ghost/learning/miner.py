"""Needleman-Wunsch pairwise sequence alignment and frequent-sequence mining."""

from __future__ import annotations

import uuid
from typing import List, Tuple, Dict, Any, Optional
from agent.ghost.learning.models import Episode, NormalizedAction, WorkflowCandidate, CandidateStep


def needleman_wunsch_alignment(
    seq1: List[str],
    seq2: List[str],
    match_score: float = 2.0,
    mismatch_score: float = -1.0,
    gap_penalty: float = -1.0,
) -> Tuple[float, List[Tuple[Optional[str], Optional[str]]]]:
    """Compute Needleman-Wunsch global sequence alignment between two token sequences.

    Returns:
        (normalized_similarity_score [0.0 - 1.0], aligned_pairs)
    """
    n, m = len(seq1), len(seq2)
    if n == 0 or m == 0:
        return 0.0, []

    # Initialize DP matrix
    score = [[0.0] * (m + 1) for _ in range(n + 1)]

    for i in range(n + 1):
        score[i][0] = i * gap_penalty
    for j in range(m + 1):
        score[0][j] = j * gap_penalty

    # Fill DP matrix
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            is_match = seq1[i - 1] == seq2[j - 1]
            match_diag = score[i - 1][j - 1] + (match_score if is_match else mismatch_score)
            delete_gap = score[i - 1][j] + gap_penalty
            insert_gap = score[i][j - 1] + gap_penalty
            score[i][j] = max(match_diag, delete_gap, insert_gap)

    # Traceback
    aligned_pairs: List[Tuple[Optional[str], Optional[str]]] = []
    i, j = n, m
    matches = 0

    while i > 0 or j > 0:
        if i > 0 and j > 0 and (
            (seq1[i - 1] == seq2[j - 1] and abs(score[i][j] - (score[i - 1][j - 1] + match_score)) < 1e-6) or
            (seq1[i - 1] != seq2[j - 1] and abs(score[i][j] - (score[i - 1][j - 1] + mismatch_score)) < 1e-6)
        ):
            if seq1[i - 1] == seq2[j - 1]:
                matches += 1
            aligned_pairs.append((seq1[i - 1], seq2[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and abs(score[i][j] - (score[i - 1][j] + gap_penalty)) < 1e-6:
            aligned_pairs.append((seq1[i - 1], None))
            i -= 1
        else:
            aligned_pairs.append((None, seq2[j - 1]))
            j -= 1

    aligned_pairs.reverse()
    max_len = max(n, m)
    normalized_score = matches / max_len if max_len > 0 else 0.0
    return normalized_score, aligned_pairs


def calculate_average_alignment(episodes: List[Episode]) -> float:
    """Calculate average pairwise Needleman-Wunsch alignment score across all episode pairs."""
    if len(episodes) < 2:
        return 1.0

    total_score = 0.0
    pair_count = 0

    for i in range(len(episodes)):
        for j in range(i + 1, len(episodes)):
            sim, _ = needleman_wunsch_alignment(episodes[i].tokens, episodes[j].tokens)
            total_score += sim
            pair_count += 1

    return total_score / pair_count if pair_count > 0 else 0.0


def extract_consensus_actions(episodes: List[Episode]) -> List[NormalizedAction]:
    """Extract representative actions for the consensus sequence from the highest-match episode."""
    if not episodes:
        return []
    # Pick the episode closest to the median length
    sorted_by_len = sorted(episodes, key=lambda ep: len(ep.actions))
    median_ep = sorted_by_len[len(sorted_by_len) // 2]
    return median_ep.actions


def mine_workflow_candidates(
    episodes: List[Episode],
    min_support: int = 3,
    min_alignment: float = 0.75,
) -> List[WorkflowCandidate]:
    """Find recurring sub-sequences across episodes that satisfy support >= 3 and alignment >= 0.75."""
    if len(episodes) < min_support:
        return []

    # Cluster episodes that share similar token sequences
    clusters: List[List[Episode]] = []

    for ep in episodes:
        placed = False
        for cluster in clusters:
            # Check similarity with representative of cluster
            rep = cluster[0]
            sim, _ = needleman_wunsch_alignment(ep.tokens, rep.tokens)
            if sim >= min_alignment:
                cluster.append(ep)
                placed = True
                break
        if not placed:
            clusters.append([ep])

    candidates: List[WorkflowCandidate] = []

    for cluster in clusters:
        if len(cluster) >= min_support:
            avg_alignment = calculate_average_alignment(cluster)
            if avg_alignment >= min_alignment:
                evidence_ids = [ep.episode_id for ep in cluster]
                consensus_actions = extract_consensus_actions(cluster)

                # Convert consensus actions to initial CandidateSteps
                candidate_steps: List[CandidateStep] = []
                for idx, action in enumerate(consensus_actions):
                    c_step = CandidateStep(
                        step_id=f"s{idx + 1}",
                        kind="action",
                        verb=action.verb,
                        app=action.app_id,
                        locators=action.locators,
                        risk="R0" if action.verb in ("navigate", "read_field", "focus") else "R1",
                        required=True,
                        origin="observed",
                    )
                    candidate_steps.append(c_step)

                candidates.append(
                    WorkflowCandidate(
                        candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                        name="Discovered Recurring Workflow",
                        description=f"Automated pattern observed across {len(cluster)} episodes with {avg_alignment:.0%} alignment.",
                        support=len(cluster),
                        alignment=round(avg_alignment, 3),
                        evidence_episode_ids=evidence_ids,
                        steps=candidate_steps,
                    )
                )

    return candidates
