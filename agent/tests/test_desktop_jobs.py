"""State-machine and adapter guard tests, never access or send to WhatsApp."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent.ghost.desktop.jobs import Jobs
from agent.ghost.desktop.whatsapp import WhatsApp, resolve, validate_recipe


NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)


def recipe():
    return {"format": "digitalsoul.whatsapp-teaching.v1", "recipient": "Test Contact",
            "identity_verified": True, "executable": r"C:\Apps\WhatsApp.exe",
            "controls": {
                "header": {"control_type": "Text", "automation_id": "chat-header", "name": "Test Contact"},
                "composer": {"control_type": "Edit", "automation_id": "composer", "name": ""},
                "send": {"control_type": "Button", "automation_id": "send", "name": "Send"}}}


class Adapter:
    def __init__(self, prepare=None, send=None):
        self.preparations, self.sends = [], []
        self.on_prepare, self.on_send = prepare, send

    def prepare(self, plan, message):
        self.preparations.append((plan, message))
        if self.on_prepare:
            self.on_prepare()

    def send(self, plan, message):
        self.sends.append((plan, message))
        if self.on_send:
            self.on_send()


@pytest.fixture
def store(tmp_path):
    jobs = Jobs(tmp_path / "jobs.sqlite3", clock=lambda: NOW)
    yield jobs
    jobs.close()


def schedule(jobs, **kwargs):
    return jobs.schedule(recipe(), "happy birthday", (NOW + timedelta(seconds=10)).isoformat(),
                         kwargs.get("recipient", "Test Contact"), now=NOW)


def test_due_message_is_attempted_once(store):
    job_id = schedule(store)
    adapter = Adapter()
    assert store.tick(adapter, now=NOW) is None
    assert store.tick(adapter, now=NOW + timedelta(seconds=10)) == job_id
    store.tick(adapter, now=NOW + timedelta(seconds=11))
    assert len(adapter.sends) == 1
    assert adapter.sends[0] == (recipe(), "happy birthday")
    assert store.list()[0]["state"] == "attempted_unverified"
    assert "payload" not in store.list()[0]


@pytest.mark.parametrize("at", ["2026-12-01T00:00:00", "2020-01-01T00:00:00+05:30"])
def test_naive_or_past_time_rejected(store, at):
    with pytest.raises(ValueError):
        store.schedule(recipe(), "happy birthday", at, "Test Contact", now=NOW)


@pytest.mark.parametrize("message", ["", "\nhello", "x" * 4097])
def test_invalid_message_rejected(store, message):
    with pytest.raises(ValueError):
        store.schedule(recipe(), message, "2026-12-01T00:00:00+05:30", "Test Contact", now=NOW)


def test_offset_is_converted_to_utc(store):
    store.schedule(recipe(), "test", "2026-10-22T16:00:00+05:30", "Test Contact", now=NOW)
    assert store.list()[0]["due"] == (NOW + timedelta(minutes=30)).timestamp()


def test_wrong_recipient_approval_rejected(store):
    with pytest.raises(ValueError):
        schedule(store, recipient="Other Contact")


def test_cancel_pending_and_during_prepare(store):
    first = schedule(store)
    assert store.cancel(first)
    adapter = Adapter()
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert not adapter.preparations
    second = schedule(store)
    adapter = Adapter(prepare=lambda: store.cancel(second))
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert not adapter.sends


def test_precondition_failure_never_dispatches(store):
    schedule(store)

    def fail():
        raise ValueError("private contact details")

    adapter = Adapter(prepare=fail)
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert not adapter.sends
    assert store.list()[0]["state"] == "blocked"
    assert "private" not in store.list()[0]["detail"]


def test_missed_job_is_not_sent_late(store):
    schedule(store)
    adapter = Adapter()
    store.tick(adapter, now=NOW + timedelta(minutes=2))
    assert store.list()[0]["state"] == "missed"
    assert not adapter.preparations


def test_slow_preparation_does_not_send_late(store):
    schedule(store)
    store.clock = lambda: NOW + timedelta(minutes=2)
    adapter = Adapter()
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert store.list()[0]["state"] == "missed"
    assert not adapter.sends


def test_send_error_and_restart_do_not_duplicate(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = Jobs(path, clock=lambda: NOW)
    job_id = schedule(store)

    def fail():
        raise OSError("unknown external outcome")

    adapter = Adapter(send=fail)
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert store.list()[0]["state"] == "uncertain"
    assert not store.cancel(job_id)
    store.close()
    reopened = Jobs(path, clock=lambda: NOW)
    try:
        reopened.tick(adapter, now=NOW + timedelta(seconds=20))
        assert len(adapter.sends) == 1
    finally:
        reopened.close()


def test_dispatching_crash_is_never_replayed(store):
    job_id = schedule(store)
    assert store._transition(job_id, "pending", "dispatching")
    adapter = Adapter()
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert not adapter.preparations


def test_two_workers_only_one_claim(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first, second = Jobs(path, clock=lambda: NOW), Jobs(path, clock=lambda: NOW)
    try:
        schedule(first)
        other = Adapter()
        adapter = Adapter(prepare=lambda: second.tick(other, now=NOW + timedelta(seconds=10)))
        first.tick(adapter, now=NOW + timedelta(seconds=10))
        assert len(adapter.sends) == 1
        assert not other.preparations
    finally:
        first.close()
        second.close()


def test_job_snapshots_recipe_instead_of_future_edits(store):
    plan = recipe()
    store.schedule(plan, "approved", "2026-10-22T10:00:10+00:00", "Test Contact", now=NOW)
    plan["recipient"] = "Other Contact"
    adapter = Adapter()
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert adapter.sends[0][0]["recipient"] == "Test Contact"


def test_recipe_guards():
    for change in (lambda r: r.update(identity_verified=False),
                   lambda r: r.update(executable=r"C:\Apps\Other.exe"),
                   lambda r: r["controls"]["header"].update(name="Other Contact"),
                   lambda r: r["controls"]["composer"].update(automation_id="")):
        plan = copy.deepcopy(recipe())
        change(plan)
        with pytest.raises(ValueError):
            validate_recipe(plan)


def test_ambiguous_selector_fails():
    visible = SimpleNamespace(is_visible=lambda: True, is_enabled=lambda: True)
    window = SimpleNamespace(descendants=lambda **kwargs: [visible, visible])
    with pytest.raises(ValueError):
        resolve(window, recipe()["controls"]["header"])


def test_send_rechecks_exact_composer(monkeypatch):
    adapter = WhatsApp()
    monkeypatch.setattr(adapter, "window", lambda plan: object())
    monkeypatch.setattr("agent.ghost.desktop.whatsapp.resolve", lambda w, spec: SimpleNamespace(get_value=lambda: "changed draft"))
    with pytest.raises(ValueError, match="approved message"):
        adapter.send(recipe(), "happy birthday")


def test_stop_during_preparation_cancels_and_latches(store):
    schedule(store)
    adapter = Adapter(prepare=store.stop)
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    assert not adapter.sends
    assert store.list()[0]["state"] == "cancelled"
    with pytest.raises(ValueError, match="latched"):
        schedule(store)
    store.resume()
    schedule(store)
    assert store.list()[0]["state"] == "cancelled"


def test_stop_persists_across_workers(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first = Jobs(path, clock=lambda: NOW)
    first.stop()
    first.close()
    second = Jobs(path, clock=lambda: NOW)
    try:
        with pytest.raises(ValueError, match="latched"):
            schedule(second)
    finally:
        second.close()


def test_check_ignores_empty_or_newline_draft(monkeypatch):
    adapter = WhatsApp()
    monkeypatch.setattr(adapter, "window", lambda plan: object())
    for empty_val in ["", "\n", "   \r\n"]:
        monkeypatch.setattr("agent.ghost.desktop.whatsapp.resolve", lambda w, spec: SimpleNamespace(get_value=lambda: empty_val))
        assert adapter.check(recipe()) is True
    monkeypatch.setattr("agent.ghost.desktop.whatsapp.resolve", lambda w, spec: SimpleNamespace(get_value=lambda: "real draft"))
    with pytest.raises(ValueError, match="existing draft"):
        adapter.check(recipe())


def test_send_allows_matching_stripped_composer(monkeypatch):
    adapter = WhatsApp()
    monkeypatch.setattr(adapter, "window", lambda plan: object())
    invoked = False
    def fake_resolve(w, spec):
        if spec.get("control_type") == "Button":
            return SimpleNamespace(invoke=lambda: True)
        return SimpleNamespace(get_value=lambda: "happy birthday\n")
    monkeypatch.setattr("agent.ghost.desktop.whatsapp.resolve", fake_resolve)
    # Should not raise "Composer differs from the approved message"
    adapter.send(recipe(), "happy birthday")


@pytest.mark.parametrize('code', ['open', 'window', 'focus', 'search', 'recipient', 'composer', 'draft', 'type', 'verify', 'ambiguous'])
def test_preparation_reason_is_persisted_without_sending(store, code):
    from agent.ghost.desktop.preparation import PreparationError
    schedule(store)
    def fail():
        raise PreparationError(code)
    adapter = Adapter(prepare=fail)
    store.tick(adapter, now=NOW + timedelta(seconds=10))
    store.tick(adapter, now=NOW + timedelta(seconds=11))
    detail = store.list()[0]['detail']
    assert f'[{code}]' in detail
    assert 'Send was not attempted' in detail
    assert not adapter.sends
    assert len(adapter.preparations) == 1


def test_real_send_adapter_wraps_provider_error_at_open_stage(monkeypatch):
    from agent.ghost.desktop.whatsapp_send import WhatsAppSend, FORMAT
    from agent.ghost.desktop.preparation import PreparationError, failure_detail
    def fail(_):
        raise OSError('PRIVATE contact and message text')
    native = SimpleNamespace(open=fail, park=lambda: None)
    adapter = WhatsAppSend(native=native)
    plan = dict(format=FORMAT, recipient='Private Contact', executable=r'C:\Apps\WhatsApp.exe')
    with pytest.raises(PreparationError) as caught:
        adapter.prepare(plan, 'Private Message')
    assert caught.value.code == 'open'
    assert 'PRIVATE' not in failure_detail(caught.value)
    assert 'Private' not in failure_detail(caught.value)


def test_stage_wrapper_hides_unknown_exception_and_rejects_unknown_codes():
    from agent.ghost.desktop.preparation import diagnosed_prepare, PreparationError, failure_detail
    class Broken:
        @diagnosed_prepare
        def prepare(self):
            self.preparation_stage = 'composer'
            raise RuntimeError('PRIVATE UI CONTENT')
    with pytest.raises(PreparationError) as caught:
        Broken().prepare()
    assert caught.value.code == 'composer'
    assert 'PRIVATE' not in failure_detail(caught.value)
    assert '[unknown]' in failure_detail(PreparationError('PRIVATE UI CONTENT'))


def test_known_flow_failure_does_not_start_another_adapter(monkeypatch):
    from agent.ghost.desktop import flow, whatsapp_send
    from agent.ghost.desktop.preparation import PreparationError
    class BrokenFlow:
        def __init__(self, **kwargs):
            pass
        def prepare(self, *args):
            raise PreparationError('draft')
    monkeypatch.setattr(flow, 'FlowAdapter', BrokenFlow)
    monkeypatch.setattr(whatsapp_send, 'WhatsAppSend', lambda **kwargs: pytest.fail('must not retry preparation'))
    with pytest.raises(PreparationError) as caught:
        WhatsApp().prepare({'format': 'digitalsoul.whatsapp-flow.v1'}, 'message')
    assert caught.value.code == 'draft'
