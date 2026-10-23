"""Local Markdown memory and fuzzy field matcher for Google Forms automation.

The memory file is intentionally human-readable and local-only.  It starts
empty: DigitalSoul must never invent a person's name, email address, profile
URL, or other personal details.  Values are added only after the person enters
them into a form (or explicitly saves them through the existing memory path).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Kept as a public compatibility constant for callers/tests.  It must remain
# empty so a fresh install cannot manufacture identity data.
DEFAULT_PROFILE: Dict[str, Any] = {}

MEMORY_HEADER = """# DigitalSoul local memory

This file is private to this machine. DigitalSoul updates it from values you
enter and successfully use in forms. Review or edit it directly when needed.

## Remembered details
"""

# Semantic aliases for common Google Form questions
FIELD_ALIASES = {
    "name": [
        r"\b(full\s*name|your\s*name|first\s*name|name|user\s*name)\b",
        r"\bname\b",
    ],
    "user_name": [
        r"\b(full\s*name|your\s*name|first\s*name|name|user\s*name)\b",
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
        r"\b(organi[sz]er|organi[sz]ation|organiser|organizer|college|university|company|institution)\b",
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
            storage_path = Path(os.environ.get("DIGITALSOUL_MEMORY_FILE", base_dir / "user_memory.md"))
        self.storage_path = Path(storage_path)
        self.profile: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.storage_path.is_file():
            try:
                return self._parse_markdown(self.storage_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return dict(DEFAULT_PROFILE)

    @staticmethod
    def _parse_markdown(text: str) -> Dict[str, str]:
        """Read ``- `key`: value`` entries from the local memory Markdown."""
        result: Dict[str, str] = {}
        pattern = re.compile(r"^\s*-\s+`?([A-Za-z0-9][A-Za-z0-9 _-]*)`?\s*:\s*(.*?)\s*$")
        for line in text.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            key = re.sub(r"[^a-zA-Z0-9]+", "_", match.group(1)).strip("_").lower()
            value = match.group(2).strip()
            if key and value:
                result[key] = json.loads(value[6:]) if value.startswith("@json:") else value
        return result

    def _write_markdown(self) -> None:
        lines = [MEMORY_HEADER.rstrip(), ""]
        for key in sorted(self.profile):
            value = ("@json:" + json.dumps(self.profile[key], ensure_ascii=False) if isinstance(self.profile[key], (list, dict)) else str(self.profile[key])).replace("\r", " ").replace("\n", " ").strip()
            if value:
                lines.append(f"- `{key}`: {value}")
        lines.append("")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text("\n".join(lines), encoding="utf-8")

    def save(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update profile memory with new field values and persist to disk."""
        for raw_key, value in updates.items():
            key = re.sub(r"[^a-zA-Z0-9]+", "_", str(raw_key)).strip("_").lower()
            if not key or value is None:
                continue
            cleaned = value if isinstance(value, (list, dict)) else str(value).strip()
            if cleaned:
                self.profile[key] = cleaned
            else:
                self.profile.pop(key, None)
        try:
            self._write_markdown()
        except Exception:
            pass
        return dict(self.profile)

    def match_question(self, question_text: str, options: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str], float]:
        """Match question text to a remembered key and choose the best matching value.

        Returns (profile_key, value_to_fill, confidence).
        """
        q_norm = question_text.strip().lower()
        exact_key = re.sub(r"[^a-z0-9]+", "_", q_norm).strip("_")
        if exact_key in self.profile:
            return exact_key, self.profile[exact_key], 1.0
        if re.search(r"\b(father|mother|guardian|emergency|alternate)\b", q_norm):
            return None, None, 0.0

        # Match specific question text against profile aliases
        best_key = None
        best_score = 0.0

        for key, patterns in FIELD_ALIASES.items():
            for pat in patterns:
                m = re.search(pat, q_norm, re.IGNORECASE)
                if m:
                    # If question asks for college/organization name, don't match person name
                    if key == "name" and re.search(r"\b(organi[sz]er|organi[sz]ation|college|university|company|institution)\b", q_norm):
                        continue
                    score = 0.75 + min(0.2, len(m.group(0)) * 0.02)
                    if score > best_score:
                        best_score = score
                        best_key = key

        if best_key:
            val = self.profile.get(best_key)
            if not val and best_key == "name":
                val = self.profile.get("user_name") or self.profile.get("full_name") or self.profile.get("candidate_name")
            elif not val and best_key == "email":
                val = self.profile.get("user_email") or self.profile.get("google_skills_email")
            elif not val and best_key == "organization":
                val = self.profile.get("org") or self.profile.get("college") or self.profile.get("university") or self.profile.get("company")
            elif not val and best_key == "phone":
                val = self.profile.get("phone_number") or self.profile.get("mobile") or self.profile.get("contact")
            if val:
                if options:
                    matched_opt = self._match_option(val, options)
                    if matched_opt:
                        return (best_key, matched_opt, best_score)
                else:
                    return (best_key, val, best_score)

        # Check exact key matching if key exists directly in profile or question contains key words
        for key, val in self.profile.items():
            if not val:
                continue
            k_slug = key.replace("_", " ").lower()
            words = [w for w in k_slug.split() if len(w) >= 3]
            if k_slug in q_norm or q_norm in k_slug or (words and any(w in q_norm for w in words)):
                if options:
                    matched_opt = self._match_option(val, options)
                    if matched_opt:
                        return (key, matched_opt, 0.7)
                else:
                    return (key, val, 0.7)

        return (None, None, 0.0)

    @staticmethod
    def _match_option(user_val: str, options: List[str]) -> Optional[str]:
        if not isinstance(user_val, str):
            return None
        val_norm = user_val.strip().lower()
        # Exact match
        for opt in options:
            if opt.strip().lower() == val_norm:
                return opt
        return None
