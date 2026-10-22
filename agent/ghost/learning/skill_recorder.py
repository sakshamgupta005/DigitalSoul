"""Demonstration Engineering & Skill Recorder for DigitalSoul (inspired by microsoft/skill-recorder).

Translates recorded human desktop demonstrations (actions, active windows,
screen OCR, and optional audio/voice narration) into reusable, generalized
Agent Skills formatted as standard SKILL.md documents and DigitalSoul WorkflowCandidates.
Includes on-device sensitive data redaction to scrub secrets and PII.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from contracts import DecisionRule, PrePostCondition, Recovery, StepLocator, Value
from agent.ghost.learning.models import CandidateStep, WorkflowCandidate



# ============================================================================
# 1. On-Device Sensitive Data Filter (Redaction)
# ============================================================================

class SensitiveDataFilter:
    """Detects and redacts sensitive credentials, tokens, and PII from telemetry before AI analysis or storage."""

    # Common credential & token patterns
    PATTERNS: List[Tuple[str, re.Pattern, str]] = [
        # API Keys & Secrets
        ("openai_key", re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
        ("anthropic_key", re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,}\b"), "[REDACTED_ANTHROPIC_KEY]"),
        ("github_token", re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),
        ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
        ("jwt_token", re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\.[A-Za-z0-9._-]{10,}\b"), "[REDACTED_JWT]"),
        ("bearer_token", re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.]{15,}\b", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
        
        # PII & Financial
        ("credit_card", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[REDACTED_CREDIT_CARD]"),
        ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
        ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"), "[REDACTED_EMAIL]"),
        ("phone", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    ]

    # Sensitive field / query parameter names
    SENSITIVE_FIELD_NAMES = {
        "password", "passwd", "pwd", "secret", "token", "auth", "authorization",
        "api_key", "apikey", "pin", "cvv", "cvc", "ssn", "credit_card", "access_token"
    }

    @classmethod
    def redact_text(cls, text: Optional[str]) -> Tuple[str, int]:
        """Redact known sensitive regex patterns from plain text. Returns (sanitized_text, count)."""
        if not text:
            return text or "", 0

        sanitized = text
        redacted_count = 0
        for _, pattern, replacement in cls.PATTERNS:
            matches = len(pattern.findall(sanitized))
            if matches > 0:
                sanitized = pattern.sub(replacement, sanitized)
                redacted_count += matches

        return sanitized, redacted_count

    @classmethod
    def redact_step(cls, step: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Deeply scrub sensitive values from a desktop step dictionary."""
        cleaned = dict(step)
        redacted_count = 0

        # 1. Scrub text entry value if target is a password/secret field
        target = cleaned.get("target") or {}
        target_name = (target.get("name") or cleaned.get("label") or "").strip().lower()
        is_password_field = any(s in target_name for s in cls.SENSITIVE_FIELD_NAMES)

        if cleaned.get("kind") == "set_text" and is_password_field:
            if cleaned.get("value"):
                cleaned["value"] = "[REDACTED_PASSWORD]"
                redacted_count += 1
        elif cleaned.get("value") and isinstance(cleaned["value"], str):
            val, count = cls.redact_text(cleaned["value"])
            cleaned["value"] = val
            redacted_count += count

        # 2. Scrub URLs (e.g. query tokens)
        if cleaned.get("url") and isinstance(cleaned["url"], str):
            url_val, count = cls.redact_url(cleaned["url"])
            cleaned["url"] = url_val
            redacted_count += count

        # 3. Scrub labels / titles
        for key in ("label", "title"):
            if cleaned.get(key) and isinstance(cleaned[key], str):
                txt, count = cls.redact_text(cleaned[key])
                cleaned[key] = txt
                redacted_count += count

        return cleaned, redacted_count

    @classmethod
    def redact_url(cls, url: str) -> Tuple[str, int]:
        """Remove access tokens, keys, and credentials embedded inside URLs."""
        try:
            parts = urlsplit(url)
            if not parts.query:
                txt, count = cls.redact_text(url)
                return txt, count

            queries = parts.query.split("&")
            cleaned_queries = []
            count = 0
            for q in queries:
                if "=" in q:
                    k, v = q.split("=", 1)
                    if k.lower() in cls.SENSITIVE_FIELD_NAMES:
                        cleaned_queries.append(f"{k}=[REDACTED]")
                        count += 1
                        continue
                cleaned_queries.append(q)

            sanitized_query = "&".join(cleaned_queries)
            sanitized_url = parts._replace(query=sanitized_query).geturl()
            txt, more_count = cls.redact_text(sanitized_url)
            return txt, count + more_count
        except Exception:
            return cls.redact_text(url)


# ============================================================================
# 2. Demonstration Engineering & Skill Synthesizer
# ============================================================================

class SkillSynthesizer:
    """Reconstructs intent, parameters, and structured procedures from demonstrations."""

    def __init__(self, omniroute_base_url: Optional[str] = None):
        self.base_url = (omniroute_base_url or os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1")).rstrip("/")

    def synthesize(
        self,
        steps: List[Dict[str, Any]],
        voice_notes: Optional[str] = None,
        screen_context: Optional[str] = None,
        app_name: Optional[str] = None,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        """Synthesize a complete Skill definition from raw demonstrations and narration."""
        # 1. On-device privacy filtering
        scrubbed_steps = []
        total_redacted = 0
        for s in steps:
            clean_s, r_count = SensitiveDataFilter.redact_step(s)
            scrubbed_steps.append(clean_s)
            total_redacted += r_count

        scrubbed_voice, v_count = SensitiveDataFilter.redact_text(voice_notes)
        total_redacted += v_count

        # 2. Try LLM intent reconstruction if requested and available
        candidate = None
        if use_llm:
            candidate = self._synthesize_with_omniroute(
                steps=scrubbed_steps,
                voice_notes=scrubbed_voice,
                screen_context=screen_context,
                app_name=app_name,
            )

        # 3. Deterministic heuristic synthesis fallback (always reliable offline)
        if not candidate:
            candidate = self._synthesize_heuristic(
                steps=scrubbed_steps,
                voice_notes=scrubbed_voice,
                app_name=app_name,
            )

        # 4. Format into standard SKILL.md representation
        skill_markdown = export_skill_markdown(candidate)

        return {
            "candidate": candidate,
            "skill_markdown": skill_markdown,
            "redacted_count": total_redacted,
            "voice_notes_used": bool(scrubbed_voice),
        }

    def _synthesize_heuristic(
        self,
        steps: List[Dict[str, Any]],
        voice_notes: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> WorkflowCandidate:
        """Rule-based demonstration engineering without external model calls."""
        candidate_steps: List[CandidateStep] = []
        parameters: List[Dict[str, Any]] = []
        apps_touched = set()

        for idx, s in enumerate(steps):
            kind = s.get("kind") or "action"
            raw_verb = "click" if kind == "click" else ("set_field" if kind == "set_text" else ("navigate" if kind == "open_url" else "action"))
            app = (s.get("app") or app_name or "application").replace(".exe", "").lower()
            apps_touched.add(app)

            label = s.get("label") or (s.get("target") or {}).get("name") or f"Element {idx + 1}"
            value = s.get("value")

            # Extract variable parameter if text entry
            input_val = None
            if kind == "set_text" and value:
                param_name = re.sub(r"[^a-zA-Z0-9_]", "_", label.lower().strip())
                if not param_name or param_name in ("input", "field", "text"):
                    param_name = f"input_val_{idx + 1}"
                parameters.append({
                    "name": param_name,
                    "type": "string",
                    "label": label,
                    "default_value": value,
                    "source": "demonstrated_input",
                    "origin": "inferred",
                    "confirmed": True,
                })
                input_val = f"{{{{{param_name}}}}}"

            locators: List[StepLocator] = []
            if s.get("target"):
                target = s["target"]
                if target.get("automation_id"):
                    locators.append(StepLocator(by="uia", uia_id=str(target["automation_id"])))
                if target.get("name"):
                    locators.append(StepLocator(by="text", value=str(target["name"])))
            if not locators:
                locators.append(StepLocator(by="label", value=str(label)))

            candidate_steps.append(
                CandidateStep(
                    step_id=f"step_{idx + 1}",
                    kind="action",
                    verb=raw_verb,
                    app=app,
                    locators=locators,
                    input=input_val or value,
                    pre=[PrePostCondition(type="element_visible", locator_ref=0)],
                    post=[PrePostCondition(type="element_visible", locator_ref=0)],
                    risk="R1" if raw_verb in ("click", "set_field") else "R0",
                    required=True,
                    origin="observed",
                    recovery=Recovery(retries=2, on_fail="pause"),
                )
            )


        # Infer intent title & description
        main_app = next(iter(apps_touched)) if apps_touched else "System"
        name = f"Automated {main_app.capitalize()} Procedure"
        description = f"Executes {len(candidate_steps)} demonstrated actions across {', '.join(apps_touched) or 'desktop'}."

        if voice_notes:
            # First non-empty sentence or first 120 chars as intent
            first_sentence = voice_notes.split(".")[0].strip()
            if first_sentence:
                name = first_sentence[:60].capitalize()
                description = f"{voice_notes.strip()} Demonstrated on {main_app}."

        return WorkflowCandidate(
            candidate_id=f"cand_rec_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description,
            support=1,
            alignment=1.0,
            evidence_episode_ids=[f"demo_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"],
            steps=candidate_steps,
            decision_rules=[],
            parameters=parameters,
            outcome_checks=[],
            dismissed=False,
        )

    def _synthesize_with_omniroute(
        self,
        steps: List[Dict[str, Any]],
        voice_notes: Optional[str] = None,
        screen_context: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> Optional[WorkflowCandidate]:
        """Analyze demonstration using local OmniRoute gateway (http://127.0.0.1:20128/v1)."""
        model = os.environ.get("OMNIROUTE_MODEL") or "default"
        try:
            import httpx
            with httpx.Client(timeout=4.0) as client:
                # Fast probe to check if OmniRoute is running
                probe = client.get(f"{self.base_url}/models")
                if probe.status_code != 200:
                    return None

                system_prompt = (
                    "You are the DigitalSoul Skill Recorder Analyst. Your job is Demonstration Engineering: "
                    "transforming raw recorded human actions and optional voice commentary into a structured, reusable skill.\n"
                    "Respond with ONLY valid JSON with fields: 'name', 'description', 'parameters' (list of {name, type, label, default_value}), "
                    "and 'steps' (list of {step_id, verb, app, label, input, risk})."
                )

                prompt_content = {
                    "demonstration_steps": steps[:25],
                    "voice_narration": voice_notes or "",
                    "active_application": app_name or "",
                    "screen_context": screen_context[:300] if screen_context else "",
                }

                req_body = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Analyze recorded demonstration:\n{json.dumps(prompt_content, indent=2)}"}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                }

                res = client.post(f"{self.base_url}/chat/completions", json=req_body, timeout=12.0)
                if res.status_code == 200:
                    data = res.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    s_idx, e_idx = content.find("{"), content.rfind("}") + 1
                    if s_idx != -1 and e_idx > s_idx:
                        parsed = json.loads(content[s_idx:e_idx])
                        # Build candidate from parsed LLM analysis
                        return self._candidate_from_parsed_analysis(parsed, steps)
        except Exception:
            pass
        return None

    def _candidate_from_parsed_analysis(
        self,
        parsed: Dict[str, Any],
        raw_steps: List[Dict[str, Any]],
    ) -> WorkflowCandidate:
        """Assemble WorkflowCandidate using LLM analysis combined with raw locators."""
        name = parsed.get("name") or "Learned Demonstrated Skill"
        description = parsed.get("description") or "Demonstrated workflow compiled from human execution."
        parameters = parsed.get("parameters") or []

        steps: List[CandidateStep] = []
        analyzed_steps = parsed.get("steps") or []

        for idx, raw_s in enumerate(raw_steps):
            step_spec = analyzed_steps[idx] if idx < len(analyzed_steps) else {}
            verb = step_spec.get("verb") or raw_s.get("kind") or "action"
            app = step_spec.get("app") or raw_s.get("app") or "desktop"
            risk = step_spec.get("risk") or ("R1" if verb in ("click", "set_field") else "R0")

            label = raw_s.get("label") or (raw_s.get("target") or {}).get("name") or f"Element {idx + 1}"
            locators = [StepLocator(by="label", value=str(label))]

            steps.append(
                CandidateStep(
                    step_id=f"step_{idx + 1}",
                    kind="action",
                    verb=verb,
                    app=str(app).replace(".exe", "").lower(),
                    locators=locators,
                    input=step_spec.get("input") or raw_s.get("value"),
                    risk=risk,
                    required=True,
                    origin="inferred",
                    recovery=Recovery(retries=2, on_fail="pause"),
                )
            )

        return WorkflowCandidate(
            candidate_id=f"cand_rec_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description,
            support=1,
            alignment=1.0,
            evidence_episode_ids=[f"demo_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"],
            steps=steps,
            decision_rules=[],
            parameters=parameters,
            outcome_checks=[],
            dismissed=False,
        )


# ============================================================================
# 3. Standard SKILL.md Export & Parsing (Microsoft skill-recorder format)
# ============================================================================

def export_skill_markdown(candidate_or_pattern: Union[WorkflowCandidate, Dict[str, Any]]) -> str:
    """Format a workflow candidate or learned pattern into the standard Microsoft SKILL.md format.

    Uses YAML frontmatter and standard sections:
    ---
    name: ...
    description: ...
    compatibility: ...
    metadata: ...
    ---
    ## Overview
    ## Prerequisites / Native Tools
    ## Parameters
    ## Instructions
    ## Guardrails & Edge Cases
    """
    if isinstance(candidate_or_pattern, WorkflowCandidate):
        name = candidate_or_pattern.name
        description = candidate_or_pattern.description
        steps = candidate_or_pattern.steps
        params = candidate_or_pattern.parameters
        rules = candidate_or_pattern.decision_rules
        support = candidate_or_pattern.support
    else:
        name = candidate_or_pattern.get("name") or "Learned Routine"
        description = candidate_or_pattern.get("intent") or candidate_or_pattern.get("description") or "Automated desktop workflow."
        steps = candidate_or_pattern.get("skeleton") or candidate_or_pattern.get("steps") or []
        params = candidate_or_pattern.get("parameters") or []
        rules = candidate_or_pattern.get("decision_rules") or []
        support = candidate_or_pattern.get("support", 1)

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "learned-skill"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    apps_involved = set()
    for s in steps:
        app = getattr(s, "app", None) or (s.get("app") if isinstance(s, dict) else None)
        if app:
            apps_involved.add(str(app))

    apps_str = ", ".join(sorted(apps_involved)) if apps_involved else "Desktop Application"

    frontmatter = f"""---
name: {slug}
description: >-
  {description.replace(chr(10), ' ')}
  Use when triggered to execute {name}.
compatibility: DigitalSoul Desktop, Windows 10/11 ({apps_str})
metadata:
  author: DigitalSoul Skill Recorder
  version: "1.0"
  support_demonstrations: {support}
  recorded_at: "{now_iso}"
---
"""

    body_lines = [
        frontmatter,
        "## Overview",
        f"{description}\n",
        "## Prerequisites / Native Tools",
        f"- Target Applications: {apps_str}",
        "- Runtime: DigitalSoul deterministic desktop runner (browser-use / Windows UI Automation)",
        "- Permissions: Local desktop session access with user confirmation\n",
    ]

    # Parameters section
    body_lines.append("## Parameters")
    if params:
        for p in params:
            p_name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "param")
            p_type = p.get("type", "string") if isinstance(p, dict) else getattr(p, "type", "string")
            p_def = p.get("default_value") or p.get("default") or ""
            body_lines.append(f"- **`{p_name}`** (`{p_type}`): Default `{p_def}`. Variable parameter slot.")
    else:
        body_lines.append("- *No variable parameters; execution is fixed.*")
    body_lines.append("")

    # Instructions section
    body_lines.append("## Instructions")
    for idx, s in enumerate(steps, 1):
        if isinstance(s, CandidateStep):
            verb = s.verb or "action"
            app = s.app or "app"
            val_clause = f" with value `{s.input}`" if s.input else ""
            target_loc = (s.locators[0].value or s.locators[0].name or s.locators[0].uia_id or "") if s.locators else ""
            target_clause = f" on `{target_loc}`" if target_loc else ""
            body_lines.append(f"{idx}. **{verb.upper()}** ({app}){target_clause}{val_clause}.")

        elif isinstance(s, dict):
            kind = s.get("kind") or "action"
            label = s.get("label") or (s.get("target") or {}).get("name") or s.get("title") or "target element"
            val = s.get("value")
            val_clause = f" to `{val}`" if val else ""
            body_lines.append(f"{idx}. {kind.capitalize()} on '{label}'{val_clause}.")
    body_lines.append("")

    # Guardrails & Edge Cases
    body_lines.append("## Guardrails & Edge Cases")
    body_lines.append("- **Verification**: Ensure pre-condition elements are visible before dispatching clicks or keypresses.")
    body_lines.append("- **On Failure**: Pause execution immediately and notify user before retrying.")
    if rules:
        body_lines.append("- **Decision Rules**:")
        for r in rules:
            expr = getattr(r, "expr", None) or (r.get("expr") if isinstance(r, dict) else "")
            then = getattr(r, "then", None) or (r.get("then") if isinstance(r, dict) else "")
            body_lines.append(f"  - When `{expr}` -> `{then}`")

    return "\n".join(body_lines)


def parse_skill_markdown(markdown_text: str) -> WorkflowCandidate:
    """Parse a Microsoft SKILL.md file into a DigitalSoul WorkflowCandidate."""
    if not markdown_text or not markdown_text.strip():
        raise ValueError("Empty SKILL.md document")

    # 1. Parse YAML frontmatter
    frontmatter_match = re.search(r"^---\s*\n(.*?)\n---\s*\n", markdown_text, re.DOTALL)
    name = "Imported Skill"
    description = "Workflow imported from SKILL.md"

    if frontmatter_match:
        fm_content = frontmatter_match.group(1)
        for line in fm_content.splitlines():
            line_str = line.strip()
            if line_str.startswith("name:"):
                name = line_str.split(":", 1)[1].strip()
            elif line_str.startswith("description:"):
                description = line_str.split(":", 1)[1].strip().lstrip(">-").strip()

    # 2. Extract sections
    instructions: List[CandidateStep] = []
    parameters: List[Dict[str, Any]] = []

    # Look for ## Instructions
    inst_match = re.search(r"## Instructions\s*\n(.*?)(?=\n## |\Z)", markdown_text, re.DOTALL)
    if inst_match:
        lines = inst_match.group(1).strip().splitlines()
        for idx, line in enumerate(lines, 1):
            m = re.match(r"^\d+\.\s*(.+)$", line.strip())
            if m:
                instruction_text = m.group(1).strip()
                # Parse verb and target if present
                verb = "action"
                for v in ("CLICK", "SET_FIELD", "NAVIGATE", "SUBMIT", "FOCUS", "click", "set_text", "open_url"):
                    if v in instruction_text:
                        verb = v.lower()
                        break
                
                instructions.append(
                    CandidateStep(
                        step_id=f"step_{idx}",
                        kind="action",
                        verb=verb,
                        app="imported",
                        locators=[StepLocator(by="label", value=str(instruction_text[:80]))],
                        risk="R1" if verb in ("click", "set_field") else "R0",

                        required=True,
                        origin="user_defined",
                        recovery=Recovery(retries=2, on_fail="pause"),
                    )
                )

    # Look for ## Parameters
    param_match = re.search(r"## Parameters\s*\n(.*?)(?=\n## |\Z)", markdown_text, re.DOTALL)
    if param_match:
        p_lines = param_match.group(1).strip().splitlines()
        for pline in p_lines:
            pm = re.search(r"-\s*\*\*`([^`]+)`\*\*\s*\(([^)]+)\):\s*Default\s*`([^`]*)`", pline)
            if pm:
                parameters.append({
                    "name": pm.group(1),
                    "type": pm.group(2),
                    "default_value": pm.group(3),
                    "source": "imported_skill",
                    "origin": "user_defined",
                    "confirmed": True,
                })

    return WorkflowCandidate(
        candidate_id=f"cand_imp_{uuid.uuid4().hex[:8]}",
        name=name.replace("-", " ").title(),
        description=description,
        support=1,
        alignment=1.0,
        evidence_episode_ids=[f"import_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"],
        steps=instructions,
        decision_rules=[],
        parameters=parameters,
        outcome_checks=[],
        dismissed=False,
    )


# ============================================================================
# 4. Demonstration Compactor & Noise Reducer (ActionCompactor)
# ============================================================================

class ActionCompactor:
    """Compacts and cleans noisy interactive human demonstrations.
    
    Inspired by OpenAdapt action segmentation and Microsoft skill-recorder:
    - Merges consecutive typing interactions into atomic settled inputs.
    - Elides redundant focus clicks on fields immediately prior to text entry.
    - Deduplicates accidental rapid duplicate clicks.
    """
    
    @classmethod
    def compact(cls, raw_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not raw_steps:
            return []

        cleaned: List[Dict[str, Any]] = []
        for step in raw_steps:
            s = dict(step)
            kind = s.get("kind")
            label = (s.get("label") or (s.get("target") or {}).get("name") or "").strip()
            val = s.get("value")

            if not kind:
                continue

            if cleaned:
                prev = cleaned[-1]
                prev_kind = prev.get("kind")
                prev_label = (prev.get("label") or (prev.get("target") or {}).get("name") or "").strip()

                # If prev was click to focus, and current is set_text on same field, replace click with set_text
                if prev_kind == "click" and kind == "set_text" and (prev_label == label or not prev_label or not label):
                    cleaned[-1] = s
                    continue

                # If prev was set_text on same field, update settled value
                if prev_kind == "set_text" and kind == "set_text" and prev_label == label:
                    prev["value"] = val
                    continue

                # If duplicate click on same element within rapid sequence
                if prev_kind == "click" and kind == "click" and prev_label == label and prev_label != "":
                    continue

            cleaned.append(s)

        return cleaned


# ============================================================================
# 5. Skill Quality Evaluator (Inspired by microsoft/skill-recorder/evals)
# ============================================================================

class SkillQualityReport(BaseModel):
    score: int = Field(..., ge=0, le=100)
    grade: str
    dimension_scores: Dict[str, int]
    strengths: List[str]
    improvement_tips: List[str]
    ready_for_execution: bool


class SkillQualityEvaluator:
    """Automated multi-dimensional skill evaluation harness.
    
    Scores candidate skills across:
    1. Locator Resilience (0-25 pts)
    2. Guardrails & Assertions (0-25 pts)
    3. Parameterization & Generality (0-25 pts)
    4. Privacy & Sensitive Content Scrubbing (0-25 pts)
    """

    @classmethod
    def evaluate(cls, candidate: WorkflowCandidate) -> SkillQualityReport:
        locator_score = 0
        guardrail_score = 0
        param_score = 0
        privacy_score = 25
        strengths = []
        tips = []

        steps = candidate.steps
        total_steps = len(steps)

        if total_steps == 0:
            return SkillQualityReport(
                score=0,
                grade="F",
                dimension_scores={"locators": 0, "guardrails": 0, "parameters": 0, "privacy": 25},
                strengths=[],
                improvement_tips=["Skill contains zero executable steps."],
                ready_for_execution=False,
            )

        # 1. Locator Resilience (up to 25 pts)
        multi_loc_count = sum(1 for s in steps if s.locators and len(s.locators) >= 2)
        has_loc_count = sum(1 for s in steps if s.locators and len(s.locators) >= 1)
        if multi_loc_count == total_steps:
            locator_score = 25
            strengths.append("High resilience: All steps contain fallback locator strategies.")
        elif has_loc_count == total_steps:
            locator_score = 18
            tips.append("Consider adding secondary fallback locators (UIA + role + accessible name).")
        else:
            locator_score = int((has_loc_count / total_steps) * 15)
            tips.append(f"{total_steps - has_loc_count} step(s) lack valid locators.")

        # 2. Guardrails & Assertions (up to 25 pts)
        has_pre = sum(1 for s in steps if s.pre and len(s.pre) > 0)
        has_post = sum(1 for s in steps if s.post and len(s.post) > 0)
        pre_ratio = has_pre / total_steps
        post_ratio = has_post / total_steps

        guardrail_score = int((pre_ratio * 12) + (post_ratio * 13))
        if pre_ratio >= 0.8 and post_ratio >= 0.8:
            strengths.append("Comprehensive right-record pre-conditions and post-assertions.")
        else:
            tips.append("Add verification assertions (element visibility, field state) to ensure state read-back.")

        # 3. Parameterization & Generality (up to 25 pts)
        params = candidate.parameters or []
        input_steps = [s for s in steps if s.verb in ("set_field", "set_text") or (s.input and str(s.input).startswith("{{"))]
        if params and len(params) > 0:
            param_score = min(25, 15 + (len(params) * 5))
            strengths.append(f"Generalized with {len(params)} variable parameter slot(s).")
        elif input_steps:
            param_score = 10
            tips.append("Input steps detected without parameter extraction. Consider slotting variable values.")
        else:
            param_score = 20
            strengths.append("Fixed routine with no variable inputs needed.")

        # 4. Privacy & Scrubbing (up to 25 pts)
        all_text = " ".join([
            candidate.name,
            candidate.description,
            " ".join(str(s.input) for s in steps if s.input),
            " ".join(str(loc.value or loc.name or "") for s in steps for loc in (s.locators or [])),
        ])
        _, leaked_count = SensitiveDataFilter.redact_text(all_text)
        if leaked_count > 0:
            privacy_score = max(0, 25 - (leaked_count * 10))
            tips.append(f"Warning: Found {leaked_count} potential unredacted sensitive token(s). Run /learning/redact.")
        else:
            strengths.append("Clean privacy posture: No exposed keys, credentials, or personal numbers found.")

        total_score = min(100, locator_score + guardrail_score + param_score + privacy_score)
        grade = "A+" if total_score >= 95 else ("A" if total_score >= 85 else ("B" if total_score >= 70 else "C"))
        ready = total_score >= 60 and privacy_score >= 15

        return SkillQualityReport(
            score=total_score,
            grade=grade,
            dimension_scores={
                "locators": locator_score,
                "guardrails": guardrail_score,
                "parameters": param_score,
                "privacy": privacy_score,
            },
            strengths=strengths,
            improvement_tips=tips,
            ready_for_execution=ready,
        )


# ============================================================================
# 6. Skill Optimizer
# ============================================================================

class SkillOptimizer:
    """Runs end-to-end compaction, scrubbing, parameter inference, and quality verification."""

    @classmethod
    def optimize(
        cls,
        steps: List[Dict[str, Any]],
        voice_notes: Optional[str] = None,
        screen_context: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1. Compact noisy human actions
        compacted = ActionCompactor.compact(steps)

        # 2. Synthesize with multi-strategy locators and guardrails
        synth = SkillSynthesizer()
        result = synth.synthesize(
            steps=compacted,
            voice_notes=voice_notes,
            screen_context=screen_context,
            app_name=app_name,
            use_llm=False,
        )
        candidate = result["candidate"]

        # 3. Enhance locators with multiple strategies for maximum resilience
        enhanced_steps: List[CandidateStep] = []
        for s in candidate.steps:
            locs = list(s.locators or [])
            if len(locs) == 1:
                first = locs[0]
                val = first.value or first.name or first.uia_id or "element"
                locs.append(StepLocator(by="text", value=str(val)[:50]))
            
            verb_str = str(s.verb or "").lower()
            risk_val = "R0"
            if verb_str in ("click", "set_field"):
                target_str = str(s.locators[0].value or "").lower() if s.locators else ""
                if any(k in target_str for k in ("delete", "remove", "drop", "purge", "clear", "cancel")):
                    risk_val = "R2"
                elif any(k in target_str for k in ("pay", "checkout", "transfer", "charge", "refund")):
                    risk_val = "R3"
                else:
                    risk_val = "R1"

            enhanced_steps.append(
                s.model_copy(
                    update={
                        "locators": locs,
                        "risk": risk_val,
                        "pre": [PrePostCondition(type="element_visible", locator_ref=0)],
                        "post": [PrePostCondition(type="element_visible", locator_ref=0)],
                    }
                )
            )

        optimized_candidate = candidate.model_copy(update={"steps": enhanced_steps})
        report = SkillQualityEvaluator.evaluate(optimized_candidate)
        skill_md = export_skill_markdown(optimized_candidate)

        return {
            "candidate": optimized_candidate,
            "skill_markdown": skill_md,
            "quality_report": report.model_dump(),
            "compacted_steps_count": len(compacted),
            "original_steps_count": len(steps),
        }

