from types import SimpleNamespace
import pytest
from agent.ghost.desktop import whatsapp_window as target

class Client:
    def __init__(self, windows): self.windows = windows
    def call(self, *args): return {'windows':self.windows}

def row(pid):
    return dict(app_name='msedgewebview2.exe', pid=pid, window_id=pid+100, is_on_screen=True)

def test_only_whatsapp_owned_renderer_is_selected(monkeypatch):
    monkeypatch.setattr(target, 'webview_host', lambda pid: pid)
    monkeypatch.setattr(target, 'executable', lambda pid: r'C:\Apps\WhatsApp.exe' if pid==1 else r'C:\Apps\Other.exe')
    assert target.content_window(r'C:\Apps\WhatsApp.exe', Client([row(2),row(1)]))['pid']==1

def test_ambiguous_renderers_stop(monkeypatch):
    monkeypatch.setattr(target, 'webview_host', lambda pid: pid)
    monkeypatch.setattr(target, 'executable', lambda pid: r'C:\Apps\WhatsApp.exe')
    with pytest.raises(ValueError, match='Multiple'):
        target.content_window(None, Client([row(1),row(2)]))

def test_generic_composer_does_not_prove_recipient():
    from agent.ghost.desktop.whatsapp_send import WhatsAppSend
    adapter=WhatsAppSend()
    adapter._edits=lambda recipe:[SimpleNamespace(element_info=SimpleNamespace(name='Type a message'))]
    assert adapter._composer({},'Saksham Gupta') is None

def test_nested_contact_rows_are_one_result_but_separate_contacts_are_ambiguous():
    from agent.ghost.desktop.whatsapp_send import WhatsAppSend, Refused
    class Row:
        def __init__(self, rid, name, parent=None):
            self.element_info=SimpleNamespace(runtime_id=[rid],name=name)
            self._parent=parent
        def parent(self): return self._parent
        def is_visible(self): return True
    outer=Row(1,'Saksham Gupta 4:37 am preview')
    inner=Row(2,'Saksham Gupta 4:37 am',outer)
    rows=[outer,inner]
    window=SimpleNamespace(descendants=lambda control_type: rows if control_type=='DataItem' else [])
    a=WhatsAppSend()
    a._window=lambda recipe: window
    assert a._result({},'Saksham Gupta') is outer
    rows.append(Row(3,'Saksham Gupta 5:00 am another contact'))
    with pytest.raises(Refused): a._result({},'Saksham Gupta')
