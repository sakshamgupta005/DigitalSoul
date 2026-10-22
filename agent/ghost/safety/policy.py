from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from ..execution.errors import GhostError
from .risk import effective_risk


@dataclass(frozen=True)
class AppPolicy:
    origin: str
    paths: tuple[str, ...]
    # Explicit adapter registrations: (verb, stable testid) -> minimum risk.
    actions: dict = field(default_factory=dict)


class Policy:
    def __init__(self, apps: dict[str, AppPolicy]):
        self.apps = apps
        self.generation = 0

    def url(self, app, value):
        policy = self.app(app)
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GhostError("POLICY_BLOCKED", "URL credentials, queries and fragments are disabled", 403)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme not in ("http", "https") or origin != policy.origin:
            raise GhostError("POLICY_BLOCKED", "Destination origin is not registered", 403)
        # Path families are deliberately explicit; normalized record IDs cannot escape them.
        if not any(parsed.path == p or (p.endswith("/") and parsed.path.startswith(p)) for p in policy.paths):
            raise GhostError("POLICY_BLOCKED", "Destination path is not registered", 403)
        decoded = unquote(parsed.path)
        if "\\" in decoded or "\x00" in decoded or any(part in (".", "..") for part in decoded.split("/")):
            raise GhostError("POLICY_BLOCKED", "Destination contains path traversal", 403)
        for prefix in policy.paths:
            if prefix.endswith("/") and parsed.path.startswith(prefix):
                tail = unquote(parsed.path[len(prefix):])
                if not tail or "/" in tail or "%" in tail:
                    raise GhostError("POLICY_BLOCKED", "Record path must contain one encoded ID", 403)
        return value

    def app(self, app):
        if app not in self.apps:
            raise GhostError("POLICY_BLOCKED", "App is not registered", 403)
        return self.apps[app]

    def classify(self, step):
        registered = self.app(step["app"])
        verb = step.get("verb")
        if step["kind"] in ("loop", "decision", "assert"):
            return effective_risk(step, "R0")
        if verb == "navigate":
            return effective_risk(step, "R0")
        # Every actionable target must have a server-side adapter registration.
        ids = [v["value"] for v in step["locators"] if v["by"] == "testid"]
        risks = [registered.actions[(verb, tid)] for tid in ids if (verb, tid) in registered.actions]
        if not risks:
            raise GhostError("POLICY_BLOCKED", "Action has no registered capability", 403)
        return effective_risk(step, max(risks, key=lambda r: int(r[1])))


def acme_policy(portal="http://portal.acme.local", crm="http://crm.acme.local"):
    return Policy({
        "acme-portal": AppPolicy(portal, ("/reports/overdue", "/team/notes", "/api/team/notes"), {
            ("read_field", "overdue-account-list"): "R0",
            ("set_field", "team-note-body"): "R1",
            ("click", "post-team-note-btn"): "R2",
        }),
        "acme-crm": AppPolicy(crm, ("/customers/", "/api/customers/"), {
            ("read_field", "customer-plan"): "R0", ("read_field", "last-login-days"): "R0",
            ("select_option", "tag-select"): "R1", ("set_field", "tag-select"): "R1",
            ("click", "save-btn"): "R2",
        }),
    })
