"""Unit tests for the live Mistake Guard Feed (Developer 1 / M1)."""

import json
from contracts import RawEvent
from agent.ghost.learning.guard_feed import MistakeGuardFeed


def test_guard_detects_wrong_record_mismatch():
    feed = MistakeGuardFeed()
    feed.update_copied_context("1042")

    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        data = json.load(f)

    # raw_event.valid.1.json is on acme-crm with id: 1042 -> matches copied context
    event_matching = RawEvent.model_validate(data)
    warning = feed.inspect_event(event_matching)
    assert warning is None

    # Now simulate user navigating to wrong customer 9999
    data_wrong = dict(data)
    data_wrong["page"] = {
        "url_pattern": "/customers/:id",
        "url_params": {"id": "9999"},
    }
    event_wrong = RawEvent.model_validate(data_wrong)
    warning = feed.inspect_event(event_wrong)

    assert warning is not None
    assert warning.check_type == "wrong_record"
    assert warning.severity == "warning"
    assert warning.expected_value == "1042"
    assert warning.actual_value == "9999"


def test_guard_blocks_destructive_actions():
    feed = MistakeGuardFeed()

    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        data = json.load(f)

    data_delete = dict(data)
    data_delete["target"] = {
        "role": "button",
        "name": "Delete Customer Account",
        "testid": "btn-purge-record",
    }
    event_delete = RawEvent.model_validate(data_delete)

    warning = feed.inspect_event(event_delete)
    assert warning is not None
    assert warning.check_type == "destructive_action"
    assert warning.severity == "block"
    assert warning.confidence == 1.0


def test_guard_passes_benign_event():
    feed = MistakeGuardFeed()

    with open("contracts/fixtures/raw_event.valid.2.json") as f:
        data = json.load(f)

    event = RawEvent.model_validate(data)
    warning = feed.inspect_event(event)
    assert warning is None
