"""Comprehensive test suite for Developer 1 (M1 — Learning & Intelligence)."""

import json
import pytest
from fastapi.testclient import TestClient

from contracts import RawEvent, Workflow
from agent.ghost.learning.models import NormalizedAction, Episode, CandidateStep, WorkflowCandidate
from agent.ghost.learning.normalizer import normalize_event, build_locator_bundle
from agent.ghost.learning.segmenter import segment_into_episodes
from agent.ghost.learning.miner import needleman_wunsch_alignment, mine_workflow_candidates
from agent.ghost.learning.loop_folder import fold_loops, find_repeating_subsequence
from agent.ghost.learning.rule_evaluator import evaluate_decision_rule, SafeRuleEvaluator
from agent.ghost.learning.compiler import compile_workflow
from agent.ghost.learning.recommendations import generate_recommendation_card
from agent.ghost.app import app


client = TestClient(app)


# ============================================================================
# 1. Normalizer Tests
# ============================================================================

def test_normalizer_locator_bundle():
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        data = json.load(f)
    event = RawEvent.model_validate(data)

    action = normalize_event(event)
    assert action.verb == "click"
    assert action.app_id == "acme-crm"
    assert action.target_signature == "testid:tag-select"
    assert "acme-crm:/customers/:id:click:testid:tag-select" in action.token

    # Check locator bundle ranking
    loc_bys = [loc.by.value if hasattr(loc.by, "value") else str(loc.by) for loc in action.locators]
    assert loc_bys[0] == "testid"
    assert loc_bys[1] == "role"
    assert loc_bys[2] == "label"


# ============================================================================
# 2. Segmenter Tests
# ============================================================================

def test_segmenter_idle_gap():
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        e1 = RawEvent.model_validate(json.load(f))
    with open("contracts/fixtures/raw_event.valid.2.json") as f:
        e2 = RawEvent.model_validate(json.load(f))

    a1 = normalize_event(e1)
    # Modify timestamp of a2 to be 10 minutes later (600s gap)
    a2 = normalize_event(e2)
    a2 = a2.model_copy(update={"ts": "2026-10-22T09:24:03.120Z"})

    episodes = segment_into_episodes([a1, a2], idle_gap_seconds=300.0)
    assert len(episodes) == 2
    assert episodes[0].actions[0].action_id == a1.action_id
    assert episodes[1].actions[0].action_id == a2.action_id


# ============================================================================
# 3. Needleman-Wunsch Alignment Tests
# ============================================================================

def test_needleman_wunsch_exact_match():
    seq = ["A", "B", "C", "D"]
    score, pairs = needleman_wunsch_alignment(seq, seq)
    assert score == 1.0
    assert len(pairs) == 4


def test_needleman_wunsch_with_gaps():
    seq1 = ["A", "B", "C", "D"]
    seq2 = ["A", "B", "D"]
    score, pairs = needleman_wunsch_alignment(seq1, seq2)
    assert score == 0.75  # 3 matches / max_len 4


# ============================================================================
# 4. Loop Folder Tests
# ============================================================================

def test_loop_folder():
    # Build 3 repeating step blocks simulating per-customer steps
    step_template = [
        CandidateStep(step_id="temp1", kind="action", verb="navigate", app="acme-crm"),
        CandidateStep(step_id="temp2", kind="action", verb="set_field", app="acme-crm", risk="R1"),
    ]

    steps = [
        CandidateStep(step_id="s1", kind="action", verb="navigate", app="acme-portal"),
        CandidateStep(step_id="s2", kind="action", verb="read_field", app="acme-portal"),
        # Repeat customer 1
        CandidateStep(step_id="s3", kind="action", verb="navigate", app="acme-crm"),
        CandidateStep(step_id="s4", kind="action", verb="set_field", app="acme-crm", risk="R1"),
        # Repeat customer 2
        CandidateStep(step_id="s5", kind="action", verb="navigate", app="acme-crm"),
        CandidateStep(step_id="s6", kind="action", verb="set_field", app="acme-crm", risk="R1"),
        # Repeat customer 3
        CandidateStep(step_id="s7", kind="action", verb="navigate", app="acme-crm"),
        CandidateStep(step_id="s8", kind="action", verb="set_field", app="acme-crm", risk="R1"),
        # Suffix
        CandidateStep(step_id="s9", kind="action", verb="click", app="acme-portal", risk="R2"),
    ]

    folded = fold_loops(steps)
    assert len(folded) == 4  # prefix(2) + loop(1) + suffix(1)
    loop_step = folded[2]
    assert loop_step.kind == "loop"
    assert loop_step.risk == "R1"
    assert loop_step.loop_iterator["source_param"] == "account_ids"
    assert len(loop_step.body) == 2


# ============================================================================
# 5. Safe Rule Evaluator Tests
# ============================================================================

def test_rule_evaluator_valid_expressions():
    ctx = {"plan": "Pro", "last_login_days": 35, "tickets": 0}
    assert evaluate_decision_rule("plan == 'Pro' and last_login_days > 30", ctx) is True
    assert evaluate_decision_rule("plan == 'Basic' or last_login_days < 10", ctx) is False


def test_rule_evaluator_blocks_forbidden_nodes():
    ctx = {"x": 10}
    # Arithmetic is forbidden by contracts/REVIEW.md
    with pytest.raises(ValueError, match="Forbidden AST node"):
        evaluate_decision_rule("x + 5 == 15", ctx)

    # Function calls are forbidden
    with pytest.raises(ValueError, match="Forbidden AST node"):
        evaluate_decision_rule("len('test') == 4", ctx)


# ============================================================================
# 6. API Endpoints Tests
# ============================================================================

def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_batch_ingest_and_candidates():
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        e1_data = json.load(f)

    # Post batch
    resp = client.post("/api/v1/events/batch", json=[e1_data])
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 1
    assert resp.json()["duplicates"] == 0

    # Post duplicate
    resp2 = client.post("/api/v1/events/batch", json=[e1_data])
    assert resp2.status_code == 200
    assert resp2.json()["ingested"] == 0
    assert resp2.json()["duplicates"] == 1

    # Check observation settings
    settings = client.get("/api/v1/settings/observation").json()
    assert settings["observing"] is True
    assert "acme-portal" in settings["allowlist"]
