"""Unit tests for SQLite durable storage layer (Developer 1 / M1)."""

import json
import uuid
import pytest
from pathlib import Path
from contracts import RawEvent, Workflow
from agent.ghost.db.database import Database
from agent.ghost.learning.models import Episode, WorkflowCandidate, CandidateStep


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_ghost.db"
    return Database(db_file)


def test_insert_raw_event_and_deduplication(test_db: Database):
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        data = json.load(f)

    event = RawEvent.model_validate(data)

    # First insert -> True (new)
    assert test_db.insert_raw_event(event) is True

    # Duplicate identical insert -> False (no-op)
    assert test_db.insert_raw_event(event) is False

    # Retrieve event
    retrieved = test_db.get_raw_event(event.event_id)
    assert retrieved is not None
    assert retrieved.event_id == event.event_id


def test_conflicting_raw_event_raises(test_db: Database):
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        data = json.load(f)

    event1 = RawEvent.model_validate(data)
    test_db.insert_raw_event(event1)

    # Modify payload with same event_id
    data2 = dict(data)
    data2["session_id"] = "11112222-3333-4444-5555-666677778888"
    event2 = RawEvent.model_validate(data2)

    with pytest.raises(ValueError, match="Conflicting content"):
        test_db.insert_raw_event(event2)


def test_episodes_persistence(test_db: Database):
    ep = Episode(
        episode_id="ep_test_001",
        session_id="session_001",
        start_ts="2026-10-22T09:00:00Z",
        end_ts="2026-10-22T09:05:00Z",
        actions=[],
        tokens=["acme-portal:report:click:btn-filter", "acme-crm:customer:set:tag"],
    )

    test_db.save_episodes([ep])
    all_episodes = test_db.get_all_episodes()
    assert len(all_episodes) == 1
    assert all_episodes[0].episode_id == "ep_test_001"
    assert all_episodes[0].tokens == ["acme-portal:report:click:btn-filter", "acme-crm:customer:set:tag"]


def test_candidate_persistence_and_dismissal(test_db: Database):
    cand = WorkflowCandidate(
        candidate_id="cand_test_001",
        name="Test Workflow Candidate",
        description="Testing candidate persistence",
        support=4,
        alignment=0.95,
        steps=[],
    )

    test_db.save_candidate(cand)
    candidates = test_db.get_all_candidates()
    assert len(candidates) == 1
    assert candidates[0].candidate_id == "cand_test_001"
    assert candidates[0].dismissed is False

    # Dismiss
    success = test_db.dismiss_candidate("cand_test_001")
    assert success is True

    updated = test_db.get_all_candidates()
    assert updated[0].dismissed is True


def test_workflow_persistence(test_db: Database):
    with open("contracts/fixtures/workflow.valid.json") as f:
        wf_data = json.load(f)

    wf = Workflow.model_validate(wf_data)
    test_db.save_workflow(wf)

    retrieved = test_db.get_workflow(wf.workflow_id)
    assert retrieved is not None
    assert retrieved.workflow_id == wf.workflow_id
    assert retrieved.version == wf.version

    all_wfs = test_db.get_all_workflows()
    assert len(all_wfs) == 1


def test_settings_storage(test_db: Database):
    default = test_db.get_setting("non_existent_key", default={"val": 42})
    assert default == {"val": 42}

    test_db.set_setting("observation_config", {"enabled": True, "apps": ["acme-crm"]})
    retrieved = test_db.get_setting("observation_config")
    assert retrieved == {"enabled": True, "apps": ["acme-crm"]}
