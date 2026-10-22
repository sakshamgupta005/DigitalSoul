"""Repetition & Loop Guard for DigitalSoul (adapted from NousResearch/hermes-agent).

Detects runaway text repetition loops in model outputs and repetitive action cycles
in desktop/browser automation (e.g., endless scrolling, repeated clicks without state change).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

# Minimum text length before running text repetition checks
MIN_FRAGMENT_LENGTH = 400
_REPEAT_WINDOW = 60
_MIN_REPEAT_COUNT = 5
_DOMINANCE_RATIO = 0.5

REPETITION_LOOP_INTERRUPTED = "[the action or reply degenerated into a repetition loop and was interrupted]"


def is_repetition_dominated(text: str) -> bool:
    """True when a contiguous run of at least five exact repetitions covers at least half
    of ``text`` — the signature of a model repetition loop (NousResearch/hermes-agent).
    """
    if not isinstance(text, str):
        return False
    n = len(text)
    if n < MIN_FRAGMENT_LENGTH:
        return False

    # 1. Fast line-repetition check
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        counts = Counter(lines)
        most_common_line, count = counts.most_common(1)[0]
        if count >= _MIN_REPEAT_COUNT and (count * len(most_common_line)) >= (n * _DOMINANCE_RATIO):
            return True

    # 2. Window count check
    window = _REPEAT_WINDOW
    needed = max(_MIN_REPEAT_COUNT, math.ceil(n * _DOMINANCE_RATIO / window))
    w_counts: Dict[str, int] = {}
    for i in range(n - window + 1):
        key = text[i : i + window]
        c = w_counts.get(key, 0) + 1
        if c >= needed:
            return True
        w_counts[key] = c

    # 3. Periodic run check
    max_start = n - window
    if max_start < 1:
        return False

    sample_step = max(1, (max_start + 30) // 31)
    sample_starts = list(range(0, max_start + 1, sample_step))
    if sample_starts[-1] != max_start:
        sample_starts.append(max_start)

    rejected: List[Tuple[int, int, int]] = []
    for start in sample_starts:
        anchor = text[start : start + window]
        search_from = start + 1
        for _ in range(8):
            match = text.find(anchor, search_from)
            if match < 0:
                break
            period = match - start
            search_from = match + 1
            if any(lo <= start < hi and period % p == 0 for lo, hi, p in rejected):
                continue
            
            # Expand run
            left = start
            while left > 0 and text[left - 1] == text[left - 1 + period]:
                left -= 1
            right = start + window
            while right < n and text[right] == text[right - period]:
                right += 1
            
            if right - left >= _MIN_REPEAT_COUNT * period and right - left >= n * _DOMINANCE_RATIO:
                return True
            rejected.append((left, right, period))

    return False


def detect_action_loop(actions: List[Dict[str, Any]], max_identical: int = 4) -> bool:
    """Detect repetitive action loops in desktop or browser automation.
    
    Catches:
    1. Identical consecutive actions (e.g. scrolling 5+ times or clicking same control repeatedly).
    2. Alternating cyclical patterns (e.g. A -> B -> A -> B -> A -> B).
    """
    if not actions or len(actions) < max_identical:
        return False

    # Extract normalized signature for each action
    sigs: List[str] = []
    for a in actions:
        kind = str(a.get("kind", ""))
        label = str(a.get("label", "") or a.get("target", {}).get("name", "")).strip().lower()
        val = str(a.get("value", "") or a.get("url", "")).strip()
        sigs.append(f"{kind}:{label}:{val}")

    # Check consecutive identical tail
    tail_sig = sigs[-1]
    consecutive = 0
    for s in reversed(sigs):
        if s == tail_sig:
            consecutive += 1
            if consecutive >= max_identical:
                return True
        else:
            break

    # Check 2-cycle alternating pattern (A B A B A B)
    if len(sigs) >= 6:
        last_6 = sigs[-6:]
        if last_6[0] == last_6[2] == last_6[4] and last_6[1] == last_6[3] == last_6[5]:
            return True

    # Check 3-cycle pattern (A B C A B C)
    if len(sigs) >= 9:
        last_9 = sigs[-9:]
        if (last_9[0] == last_9[3] == last_9[6] and
            last_9[1] == last_9[4] == last_9[7] and
            last_9[2] == last_9[5] == last_9[8]):
            return True

    return False
