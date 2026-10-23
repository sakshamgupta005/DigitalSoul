"""Deterministic, page-scoped Google Forms controls. No generated executable code."""
import json
from pathlib import Path

SCRIPT = Path(__file__).with_name("gform_fields.js").read_text(encoding="utf-8")

async def operate(page, operation, **arguments):
    result = await page.evaluate(SCRIPT, {"operation": operation, **arguments})
    return json.loads(result) if isinstance(result, str) else result
