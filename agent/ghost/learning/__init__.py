"""DigitalSoul Learning & Intelligence Engine (M1)."""

from agent.ghost.learning.models import (
    NormalizedAction,
    Episode,
    CandidateStep,
    WorkflowCandidate,
)
from agent.ghost.learning.normalizer import (
    normalize_event,
    normalize_batch,
    build_locator_bundle,
    derive_target_signature,
)
from agent.ghost.learning.segmenter import (
    segment_into_episodes,
)
from agent.ghost.learning.miner import (
    needleman_wunsch_alignment,
    calculate_average_alignment,
    mine_workflow_candidates,
)
from agent.ghost.learning.loop_folder import (
    find_repeating_subsequence,
    fold_loops,
)
from agent.ghost.learning.rule_evaluator import (
    SafeRuleEvaluator,
    evaluate_decision_rule,
    test_rule_against_observations,
)
from agent.ghost.learning.compiler import (
    compile_workflow,
    compile_candidate_step,
)
from agent.ghost.learning.recommendations import (
    generate_recommendation_card,
    compute_episode_duration_seconds,
)
from agent.ghost.learning.hypothesis import (
    generate_heuristic_hypotheses,
    enhance_candidate_with_llm,
)

__all__ = [
    "NormalizedAction",
    "Episode",
    "CandidateStep",
    "WorkflowCandidate",
    "normalize_event",
    "normalize_batch",
    "build_locator_bundle",
    "derive_target_signature",
    "segment_into_episodes",
    "needleman_wunsch_alignment",
    "calculate_average_alignment",
    "mine_workflow_candidates",
    "find_repeating_subsequence",
    "fold_loops",
    "SafeRuleEvaluator",
    "evaluate_decision_rule",
    "test_rule_against_observations",
    "compile_workflow",
    "compile_candidate_step",
    "generate_recommendation_card",
    "compute_episode_duration_seconds",
    "generate_heuristic_hypotheses",
    "enhance_candidate_with_llm",
]
