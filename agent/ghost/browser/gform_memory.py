"""Persistent profile memory and fuzzy field matcher for Google Forms automation."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_PROFILE = {
    "name": "Eshan Sharma",
    "email": "eshansharma@example.com",
    "gender": "Male",
    "category": "Developer/Working Professional",
    "phone": "",
    "organization": "GDG",
    "referral_code": "GDG2026",
    "google_skills_email": "eshansharma@example.com",
    "google_skills_profile_url": "https://www.cloudskillsboost.google/public_profiles/eshansharma",
    "google_developer_profile_url": "https://g.dev/eshansharma",
}

# Semantic aliases for common Google Form questions
FIELD_ALIASES = {
    "name": [
        r"\b(full\s*name|your\s*name|first\s*name|name)\b",
        r"\bname\b",
    ],
    "email": [
        r"\b(email|email\s*address|mail|confirm\s*your\s*email)\b",
    ],
    "gender": [
        r"\b(gender|sex)\b",
    ],
    "category": [
        r"\b(learner\s*category|category|role|profession|status|occupation|are\s*you\s*a\s*student)\b",
    ],
    "phone": [
        r"\b(phone|mobile|contact|phone\s*number|whatsapp\s*number)\b",
    ],
    "organization": [
        r"\b(organi[sz]er|organi[sz]ation|college|university|company|institution)\b",
    ],
    "referral_code": [
        r"\b(referral|referral\s*code|invite\s*code|coupon|code)\b",
    ],
    "google_skills_email": [
        r"\b(email.*skills|skills.*email)\b",
    ],
    "google_skills_profile_url": [
        r"\b(google\s*skills.*profile|public\s*profile\s*url|skills.*profile.*url)\b",
    ],
    "google_developer_profile_url": [
        r"\b(developer\s*profile.*url|google\s*developer.*profile|g\.dev)\b",
    ],
}


class GFormMemory:
    """Manages remembered personal fields and semantic mapping for Google Forms."""

    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            base_dir = Path(os.environ.get("DIGITALSOUL_DATA_DIR", ".ghost"))
            storage_path = base_dir / "gform_profile.json"
        self.storage_path = Path(storage_path)
        self.profile: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.storage_path.is_file():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Merge defaults with saved values
                    merged = dict(DEFAULT_PROFILE)
                    merged.update(data)
                    return merged
            except Exception:
                pass
        return dict(DEFAULT_PROFILE)

    def save(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update profile memory with new field values and persist to disk."""
        cleaned = {k: str(v).strip() for k, v in updates.items() if v is not None and str(v).strip()}
        self.profile.update(cleaned)
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps(self.profile, indent=2), encoding="utf-8")
        except Exception:
            pass
        return dict(self.profile)

    def match_question(self, question_text: str, options: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str], float]:
        """Match question text to a remembered key and choose the best matching value.

        Returns (profile_key, value_to_fill, confidence).
        """
        q_norm = question_text.strip().lower()

        # Check positive boolean / agreement choices first (e.g. Terms, Acknowledge, Verify)
        if options and len(options) >= 1:
            # Positive accept patterns
            positive_patterns = [
                r"\b(yes|accept|agree|acknowledge|verify|i\s*accept|i\s*agree|i\s*acknowledge|i\s*verify)\b"
            ]
            for opt in options:
                opt_lower = opt.strip().lower()
                if any(re.search(pat, opt_lower) for pat in positive_patterns) and not re.search(r"\b(decline|no|not|disagree)\b", opt_lower):
                    if any(term in q_norm for term in ("terms", "agree", "accept", "acknowledge", "verify", "declare", "conditions")):
                        return ("agreement", opt, 0.95)

        # Match specific question text against profile aliases
        best_key = None
        best_score = 0.0

        for key, patterns in FIELD_ALIASES.items():
            for pat in patterns:
                if re.search(pat, q_norm, re.IGNORECASE):
                    score = 0.9 if key in q_norm else 0.75
                    if score > best_score:
                        best_score = score
                        best_key = key

        if best_key and best_key in self.profile and self.profile[best_key]:
            val = self.profile[best_key]
            # If options are present, match the best option
            if options:
                matched_opt = self._match_option(val, options)
                if matched_opt:
                    return (best_key, matched_opt, best_score)
            else:
                return (best_key, val, best_score)

        # Check exact key matching if key exists directly in profile
        for key, val in self.profile.items():
            k_slug = key.replace("_", " ").lower()
            if k_slug in q_norm:
                if options:
                    matched_opt = self._match_option(val, options)
                    if matched_opt:
                        return (key, matched_opt, 0.7)
                else:
                    return (key, val, 0.7)

        return (None, None, 0.0)

    @staticmethod
    def _match_option(user_val: str, options: List[str]) -> Optional[str]:
        val_norm = user_val.strip().lower()
        # Exact match
        for opt in options:
            if opt.strip().lower() == val_norm:
                return opt
        # Partial match
        for opt in options:
            opt_lower = opt.strip().lower()
            if val_norm in opt_lower or opt_lower in val_norm:
                return opt
        return None
