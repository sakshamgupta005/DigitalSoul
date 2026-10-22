"""Normalizer: converts raw telemetry events into semantic normalized actions with locator bundles."""

from __future__ import annotations

import uuid
from typing import List, Optional
from contracts import RawEvent, StepLocator
from agent.ghost.learning.models import NormalizedAction


def build_locator_bundle(event: RawEvent) -> List[StepLocator]:
    """Build ranked locators in priority order:
    1. data-testid
    2. role + accessible name
    3. label
    4. text
    5. css
    6. xpath
    7. uia (native)
    """
    locators: List[StepLocator] = []
    t = event.target
    if not t:
        return locators

    # 1. testid
    if t.testid:
        locators.append(StepLocator(by="testid", value=t.testid))

    # 2. role + accessible name
    if t.role and t.name:
        locators.append(StepLocator(by="role", role=t.role, name=t.name))

    # 3. label
    if t.label:
        locators.append(StepLocator(by="label", value=t.label))

    # 4. text fallback
    if t.name and not any(loc.by == "role" for loc in locators):
        locators.append(StepLocator(by="text", value=t.name))

    # 5. css path
    if t.css:
        locators.append(StepLocator(by="css", value=t.css))

    # 6. xpath
    if t.xpath:
        locators.append(StepLocator(by="xpath", value=t.xpath))

    # 7. UIA automation id / name
    if t.uia and (t.uia.automation_id or t.uia.name):
        locators.append(
            StepLocator(
                by="uia",
                uia_id=t.uia.automation_id,
                name=t.uia.name,
            )
        )

    return locators


def derive_target_signature(event: RawEvent) -> str:
    """Generate a stable target signature string for sequence tokenization."""
    t = event.target
    if not t:
        return "none"
    if t.testid:
        return f"testid:{t.testid}"
    if t.role and t.name:
        return f"{t.role}:{t.name}"
    if t.label:
        return f"label:{t.label}"
    if t.name:
        return f"name:{t.name}"
    if t.css:
        return f"css:{t.css.split('>')[-1].strip()}"
    if t.uia and t.uia.automation_id:
        return f"uia:{t.uia.automation_id}"
    return "element"


def map_kind_to_verb(kind: str) -> str:
    mapping = {
        "navigate": "navigate",
        "click": "click",
        "input_change": "set_field",
        "submit": "submit",
        "focus": "focus",
        "window_switch": "window_switch",
        "clipboard_copy": "read_field",
    }
    return mapping.get(kind, "click")


def normalize_event(event: RawEvent) -> NormalizedAction:
    """Normalize a single RawEvent into an immutable, semantically rich NormalizedAction."""
    verb = map_kind_to_verb(event.kind.value if hasattr(event.kind, "value") else str(event.kind))
    target_sig = derive_target_signature(event)
    url_pattern = event.page.url_pattern
    app_id = event.app.id
    app_type = event.app.type.value if hasattr(event.app.type, "value") else str(event.app.type)

    token = f"{app_id}:{url_pattern}:{verb}:{target_sig}"
    locators = build_locator_bundle(event)

    return NormalizedAction(
        action_id=str(uuid.uuid4()),
        session_id=str(event.session_id),
        ts=str(event.ts),
        verb=verb,  # type: ignore[arg-type]
        app_id=app_id,
        app_type=app_type,  # type: ignore[arg-type]
        page_url_pattern=url_pattern,
        target_signature=target_sig,
        token=token,
        locators=locators,
        value=event.value,
        raw_event_ids=[str(event.event_id)],
        bbox=event.target.bbox if event.target else None,
    )


def normalize_batch(events: List[RawEvent]) -> List[NormalizedAction]:
    """Normalize a batch of RawEvents in chronological order."""
    sorted_events = sorted(events, key=lambda e: str(e.ts))
    return [normalize_event(e) for e in sorted_events]
