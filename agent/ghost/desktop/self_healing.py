"""DigitalSoul Self-Healing Execution Engine.

Provides bounded recovery for recoverable runtime obstacles:
- Cookie consent banners
- Newsletter / promotional modals
- Non-consequential dialog overlays
- Changed or transient DOM elements
- Navigation / rendering delays

STRICT SAFEGUARDS:
- Never automatically dismiss authentication, CAPTCHA, payment confirmations,
  UAC elevation, or consequential commitments.
- Bounded retry budget (default: 2 retries).
- Loop prevention tracking obstacle signatures.
- Distinguishes attempted, completed, and verified actions.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class ObstacleCategory(str, Enum):
    COOKIE_BANNER = "cookie_banner"
    NEWSLETTER_POPUP = "newsletter_popup"
    MODAL_OVERLAY = "modal_overlay"
    ELEMENT_MISSING = "element_missing"
    TRANSIENT_NAVIGATION = "transient_navigation"
    SECURITY_BOUNDARY = "security_boundary"


class RecoveryStatus(str, Enum):
    RESOLVED = "resolved"
    BLOCKED_SECURITY = "blocked_security"
    BLOCKED_LOOP = "blocked_loop"
    RETRY_LIMIT_EXCEEDED = "retry_limit_exceeded"
    UNRECOVERABLE = "unrecoverable"
    NOT_NEEDED = "not_needed"


# Patterns that indicate a security boundary or consequential commitment.
# AUTOMATED DISMISSAL IS STRICTLY FORBIDDEN FOR THESE.
SECURITY_PATTERNS = [
    re.compile(r"\bpassword\b", re.I),
    re.compile(r"\bsign\s*in\b", re.I),
    re.compile(r"\blog\s*in\b", re.I),
    re.compile(r"\btwo[- ]?factor\b", re.I),
    re.compile(r"\b2fa\b", re.I),
    re.compile(r"\botp\b", re.I),
    re.compile(r"\bcaptcha\b", re.I),
    re.compile(r"\bturnstile\b", re.I),
    re.compile(r"\brecaptcha\b", re.I),
    re.compile(r"\bhcaptcha\b", re.I),
    re.compile(r"\buser\s*account\s*control\b", re.I),
    re.compile(r"\buac\b", re.I),
    re.compile(r"\badministrator\b", re.I),
    re.compile(r"\bcredit\s*card\b", re.I),
    re.compile(r"\bauthorize\s*payment\b", re.I),
    re.compile(r"\bcheckout\b", re.I),
    re.compile(r"\bconfirm\s*purchase\b", re.I),
    re.compile(r"\bpay\s*[\$€£0-9]", re.I),
    re.compile(r"\bdelete\s*account\b", re.I),
    re.compile(r"\btransfer\s*funds\b", re.I),
]

SAFE_COOKIE_ACCEPT_WORDS = [
    "accept all", "accept cookies", "accept", "i accept", "allow all", "agree", "got it", "ok"
]

SAFE_PROMO_DISMISS_WORDS = [
    "dismiss", "close", "no thanks", "maybe later", "skip", "not now", "×", "cancel"
]


@dataclass
class ObstacleDescriptor:
    category: ObstacleCategory
    label: str
    element_selector: str
    dismiss_selector: Optional[str] = None
    dismiss_text: Optional[str] = None
    is_safe: bool = False
    reason_if_blocked: Optional[str] = None
    rect: Optional[Dict[str, float]] = None
    signature: str = ""

    def to_event_payload(self) -> Dict[str, Any]:
        return {
            "event": "hud_obstacle",
            "kind": self.category.value,
            "label": self.label,
            "recoverable": self.is_safe,
            "reason": self.reason_if_blocked,
            "rect": self.rect,
            "details": f"Selector: {self.element_selector} | Action: {self.dismiss_text or 'None'}"
        }


@dataclass
class RecoveryState:
    max_retries: int = 2
    attempt_count: int = 0
    seen_signatures: Set[str] = field(default_factory=set)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def can_retry(self, signature: str) -> Tuple[bool, Optional[str]]:
        if signature in self.seen_signatures:
            return False, "Loop prevented: identical obstacle recurred after dismissal"
        if self.attempt_count >= self.max_retries:
            return False, f"Retry limit exceeded ({self.attempt_count}/{self.max_retries})"
        return True, None

    def record_attempt(self, descriptor: ObstacleDescriptor, status: RecoveryStatus):
        self.attempt_count += 1
        if descriptor.signature:
            self.seen_signatures.add(descriptor.signature)
        self.history.append({
            "attempt": self.attempt_count,
            "category": descriptor.category.value,
            "label": descriptor.label,
            "status": status.value,
            "timestamp": time.monotonic()
        })


def is_security_boundary(text: str) -> Tuple[bool, Optional[str]]:
    """Strictly checks if text contains authentication, payment, or security boundary words."""
    if not text:
        return False, None
    for pattern in SECURITY_PATTERNS:
        match = pattern.search(text)
        if match:
            return True, f"Security boundary matched: '{match.group(0)}'"
    return False, None


def compute_signature(category: str, raw_text: str) -> str:
    """Produces a deterministic hash to prevent recovery loops."""
    norm = re.sub(r"\s+", " ", (raw_text or "").strip().lower())[:120]
    return f"{category}:{hashlib.sha256(norm.encode('utf-8')).hexdigest()[:12]}"


# Client-side JavaScript evaluated inside browser-use page
BROWSER_OBSTACLE_SCAN_JS = """() => {
    function queryAll(sel, root = document) {
        const list = Array.from(root.querySelectorAll(sel));
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) list.push(...queryAll(sel, el.shadowRoot));
        }
        return list;
    }

    function isVisible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const r = el.getBoundingClientRect();
        return r.width > 20 && r.height > 20;
    }

    function getRect(el) {
        const r = el.getBoundingClientRect();
        return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
    }

    // 1. Check for security or payment modals first
    const securityWords = [
        'password', 'sign in', 'log in', 'two-factor', '2fa', 'otp', 'captcha', 'turnstile',
        'recaptcha', 'credit card', 'authorize payment', 'checkout', 'pay $'
    ];
    const candidateDialogs = queryAll('dialog[open], [role="dialog"], .modal.show, .obstacle-overlay, #security-auth-overlay, #cookie-consent-overlay, #newsletter-modal-overlay')
        .filter(isVisible);

    for (const d of candidateDialogs) {
        const text = (d.innerText || '').toLowerCase();
        for (const word of securityWords) {
            if (text.includes(word)) {
                return {
                    detected: true,
                    category: 'security_boundary',
                    label: 'Security or Payment Boundary',
                    selector: d.id ? '#' + d.id : (d.className ? '.' + d.className.split(' ')[0] : 'dialog'),
                    safe: false,
                    reason: 'Requires user authorization: ' + word,
                    rect: getRect(d),
                    raw_text: text.slice(0, 80)
                };
            }
        }
    }

    // 2. Cookie consent banner
    const cookieCandidates = queryAll('#cookie-consent-overlay, [id*="cookie" i], [class*="cookie" i], [aria-label*="cookie" i], .consent-banner')
        .filter(isVisible);
    if (cookieCandidates.length > 0) {
        const banner = cookieCandidates[0];
        const buttons = queryAll('button, a[role="button"], input[type="button"]', banner).filter(isVisible);
        const acceptBtn = buttons.find(b => {
            const t = (b.innerText || b.value || b.getAttribute('aria-label') || '').toLowerCase().trim();
            return ['accept', 'accept all', 'accept all cookies', 'agree', 'allow all', 'got it', 'ok'].some(w => t === w || t.includes(w));
        });
        const rect = getRect(banner);
        return {
            detected: true,
            category: 'cookie_banner',
            label: 'Cookie Consent Banner',
            selector: banner.id ? '#' + banner.id : 'div[class*="cookie"]',
            dismiss_selector: acceptBtn ? (acceptBtn.id ? '#' + acceptBtn.id : 'button') : null,
            dismiss_text: acceptBtn ? (acceptBtn.innerText || acceptBtn.value || 'Accept').trim() : null,
            safe: !!acceptBtn,
            reason: acceptBtn ? null : 'No safe accept button found on cookie banner',
            rect: rect,
            raw_text: (banner.innerText || '').slice(0, 80)
        };
    }

    // 3. Newsletter / Promotional popup
    const promoCandidates = queryAll('#newsletter-modal-overlay, [id*="newsletter" i], [class*="newsletter" i], [id*="promo" i], .modal-overlay')
        .filter(isVisible);
    if (promoCandidates.length > 0) {
        const modal = promoCandidates[0];
        const closeBtns = queryAll('button, [aria-label="Close"], .close, .close-btn', modal).filter(isVisible);
        const dismissBtn = closeBtns.find(b => {
            const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase().trim();
            return ['close', 'dismiss', 'no thanks', 'skip', 'maybe later', '×'].some(w => t === w || t.includes(w));
        });
        const rect = getRect(modal);
        return {
            detected: true,
            category: 'newsletter_popup',
            label: 'Promotional Newsletter Popup',
            selector: modal.id ? '#' + modal.id : 'div.modal',
            dismiss_selector: dismissBtn ? (dismissBtn.id ? '#' + dismissBtn.id : 'button[aria-label="Close"]') : null,
            dismiss_text: dismissBtn ? (dismissBtn.innerText || dismissBtn.getAttribute('aria-label') || 'Dismiss').trim() : null,
            safe: !!dismissBtn,
            reason: dismissBtn ? null : 'No safe dismiss button found on promo modal',
            rect: rect,
            raw_text: (modal.innerText || '').slice(0, 80)
        };
    }

    return { detected: false };
}"""


async def scan_browser_obstacles(page: Any) -> Optional[ObstacleDescriptor]:
    """Inspects the browser page DOM for active obstacles."""
    try:
        raw = await page.evaluate(BROWSER_OBSTACLE_SCAN_JS)
        info = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not info.get("detected"):
            return None

        category = ObstacleCategory(info["category"])
        raw_text = info.get("raw_text", "")

        # Server-side safety verification
        is_sec, sec_reason = is_security_boundary(raw_text)
        if is_sec:
            category = ObstacleCategory.SECURITY_BOUNDARY
            is_safe = False
            reason = sec_reason
        else:
            is_safe = bool(info.get("safe"))
            reason = info.get("reason")

        sig = compute_signature(category.value, raw_text or info.get("selector", ""))

        return ObstacleDescriptor(
            category=category,
            label=info.get("label", "Obstacle Detected"),
            element_selector=info.get("selector", ""),
            dismiss_selector=info.get("dismiss_selector"),
            dismiss_text=info.get("dismiss_text"),
            is_safe=is_safe,
            reason_if_blocked=reason,
            rect=info.get("rect"),
            signature=sig
        )
    except Exception as exc:
        return None


async def heal_browser_page(
    page: Any,
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    state: Optional[RecoveryState] = None
) -> Tuple[RecoveryStatus, Optional[str]]:
    """Performs bounded self-healing recovery on an active browser page."""
    if state is None:
        state = RecoveryState()

    obstacle = await scan_browser_obstacles(page)
    if not obstacle:
        return RecoveryStatus.NOT_NEEDED, None

    # Emit Obstacle Detected to HUD
    if event_sink:
        event_sink(obstacle.to_event_payload())

    # STRICT SAFEGUARD: Do not touch security boundaries
    if not obstacle.is_safe or obstacle.category == ObstacleCategory.SECURITY_BOUNDARY:
        if event_sink:
            event_sink({
                "event": "hud_paused_for_help",
                "category": obstacle.category.value,
                "reason": obstacle.reason_if_blocked or "Security boundary requires user intervention"
            })
        state.record_attempt(obstacle, RecoveryStatus.BLOCKED_SECURITY)
        return RecoveryStatus.BLOCKED_SECURITY, obstacle.reason_if_blocked

    # Loop prevention & retry budget
    can_retry, loop_err = state.can_retry(obstacle.signature)
    if not can_retry:
        status = RecoveryStatus.BLOCKED_LOOP if "Loop" in (loop_err or "") else RecoveryStatus.RETRY_LIMIT_EXCEEDED
        if event_sink:
            event_sink({
                "event": "hud_paused_for_help",
                "category": obstacle.category.value,
                "reason": loop_err
            })
        state.record_attempt(obstacle, status)
        return status, loop_err

    # Emit Recovering event
    attempt_num = state.attempt_count + 1
    if event_sink:
        event_sink({
            "event": "hud_recovering",
            "strategy": f"dismiss_{obstacle.category.value}",
            "status": f"Recovering: Dismissing {obstacle.label} (attempt {attempt_num}/{state.max_retries})",
            "rect": obstacle.rect,
            "label": f"Dismissing: {obstacle.dismiss_text or 'Obstacle'}"
        })

    # ATTEMPT: Execute the safe dismiss action
    dismiss_js = f"""() => {{
        const btn = document.querySelector('{obstacle.dismiss_selector}');
        if (btn) {{
            btn.click();
            return true;
        }}
        return false;
    }}"""
    try:
        clicked = await page.evaluate(dismiss_js)
    except Exception as exc:
        state.record_attempt(obstacle, RecoveryStatus.UNRECOVERABLE)
        return RecoveryStatus.UNRECOVERABLE, f"Failed to execute dismiss action: {exc}"

    # VERIFICATION: Verify the obstacle is truly resolved
    if event_sink:
        event_sink({
            "event": "hud_retrying_verification",
            "status": f"Retrying verification: Confirming {obstacle.label} resolved...",
            "attempt": attempt_num,
            "max_attempts": state.max_retries
        })

    # Short delay for DOM mutation
    time.sleep(0.35)

    post_obstacle = await scan_browser_obstacles(page)
    if post_obstacle and post_obstacle.signature == obstacle.signature:
        # Obstacle persisted!
        state.record_attempt(obstacle, RecoveryStatus.UNRECOVERABLE)
        if event_sink:
            event_sink({
                "event": "hud_paused_for_help",
                "reason": "Dismissal action did not clear obstacle. Pausing for user."
            })
        return RecoveryStatus.UNRECOVERABLE, "Obstacle remained after attempted dismissal"

    # Successfully verified resolution!
    state.record_attempt(obstacle, RecoveryStatus.RESOLVED)
    if event_sink:
        event_sink({
            "event": "hud_acting",
            "status": "Obstacle resolved • Resuming workflow",
            "action": "resume",
            "label": "Resumed"
        })

    return RecoveryStatus.RESOLVED, f"Successfully dismissed {obstacle.label}"
