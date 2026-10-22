"""Loop Folder: Identifies repeated consecutive action patterns and folds them into bounded loops."""

from __future__ import annotations

from typing import List, Tuple, Optional
from agent.ghost.learning.models import CandidateStep


def find_repeating_subsequence(steps: List[CandidateStep], min_repetitions: int = 2) -> Optional[Tuple[int, int, int]]:
    """Detect the most prominent contiguous repeating sub-sequence of steps.

    Returns:
        (start_index, block_length, repetition_count) or None
    """
    n = len(steps)
    if n < min_repetitions * 2:
        return None

    # Compare step equivalence based on (app, verb, locators[0] signature)
    def step_sig(s: CandidateStep) -> str:
        loc_sig = ""
        if s.locators and len(s.locators) > 0:
            loc_sig = f"{s.locators[0].by}:{s.locators[0].value or s.locators[0].name}"
        return f"{s.app}:{s.verb}:{loc_sig}"

    sigs = [step_sig(s) for s in steps]

    best_match: Optional[Tuple[int, int, int]] = None
    max_coverage = 0

    # Try all possible block lengths (from 2 up to n // min_repetitions)
    for block_len in range(2, (n // min_repetitions) + 1):
        for start_idx in range(n - block_len * min_repetitions + 1):
            pattern = sigs[start_idx : start_idx + block_len]
            reps = 1

            curr = start_idx + block_len
            while curr + block_len <= n and sigs[curr : curr + block_len] == pattern:
                reps += 1
                curr += block_len

            if reps >= min_repetitions:
                coverage = reps * block_len
                if coverage > max_coverage:
                    max_coverage = coverage
                    best_match = (start_idx, block_len, reps)

    return best_match


def fold_loops(steps: List[CandidateStep], source_param: str = "account_ids") -> List[CandidateStep]:
    """Fold repeating contiguous step sequences into a loop step."""
    match = find_repeating_subsequence(steps)
    if not match:
        return steps

    start_idx, block_len, reps = match
    prefix = steps[:start_idx]
    loop_body_template = steps[start_idx : start_idx + block_len]
    suffix = steps[start_idx + (block_len * reps) :]

    # Rename loop body steps to have scoped IDs, e.g. s3.1, s3.2
    scoped_body: List[CandidateStep] = []
    loop_step_id = f"s{len(prefix) + 1}"

    for b_idx, b_step in enumerate(loop_body_template):
        scoped_body.append(
            b_step.model_copy(
                update={
                    "step_id": f"{loop_step_id}_{b_idx + 1}",
                    "origin": "inferred",
                }
            )
        )

    # Determine loop risk as max risk of its children
    risk_rank = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
    body_max_risk = max((b.risk for b in scoped_body), key=lambda r: risk_rank.get(r, 0), default="R0")

    loop_app = scoped_body[0].app if scoped_body and scoped_body[0].app else "acme-crm"

    loop_step = CandidateStep(
        step_id=loop_step_id,
        kind="loop",
        app=loop_app,
        locators=[],
        risk=body_max_risk,
        required=True,
        origin="inferred",
        loop_iterator={
            "source_param": source_param,
            "item_name": "item",
            "max_iterations": 100,
        },
        body=scoped_body,
    )

    # Re-index suffix steps
    reindexed_suffix: List[CandidateStep] = []
    base_idx = len(prefix) + 2
    for s_idx, s_step in enumerate(suffix):
        reindexed_suffix.append(
            s_step.model_copy(
                update={"step_id": f"s{base_idx + s_idx}"}
            )
        )

    return prefix + [loop_step] + reindexed_suffix
