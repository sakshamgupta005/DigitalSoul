import asyncio

import pytest

from agent.ghost.browser.shopify_native import ShopifyNativeActor
from agent.ghost.browser.shopify_session import ShopifySession
from agent.ghost.drivers.cua import CuaClient

URL = "https://admin.shopify.com/store/gzn0vr-qn/products/new"


class NativeClient:
    def __init__(self, wrong_url=False, duplicate=False):
        self.values = {"Title":"", "Shop location quantity available":"5"}
        self.generation = 0
        self.calls = []
        self.wrong_url, self.duplicate = wrong_url, duplicate

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        if tool == "list_windows":
            return {"windows":[{"app_name":"msedge.exe", "title":"Add product - Shopify", "pid":10, "window_id":20}]}
        if tool == "get_window_state":
            self.generation += 1
            elements = [{"label":"Address and search bar", "value": URL if not self.wrong_url else URL.replace('gzn0vr-qn','other-store')}]
            elements += [{"label":label, "role":"Edit", "value":value, "element_token":f"{self.generation}:{label}"} for label,value in self.values.items()]
            if self.duplicate:
                elements.append(dict(elements[1]))
            return {"elements":elements}
        if tool == "set_value":
            generation, label = args['element_token'].split(':',1)
            assert int(generation) == self.generation, "A stale snapshot was used"
            self.values[label] = args['value']
            return {"ok":True}
        raise AssertionError(tool)

    def close(self):
        pass


def test_native_fills_fresh_controls_and_independently_reads_back_zero():
    client = NativeClient()
    result = ShopifyNativeActor(client).execute({"store_url":URL, "title":"Test", "inventory_quantity":0})
    assert result['ok'] and result['engine'] == 'cua-driver'
    assert client.values == {'Title':'Test', 'Shop location quantity available':'0'}
    assert result['save'] == {}
    assert {name for name,_ in client.calls} == {'list_windows','get_window_state','set_value'}


def test_native_never_writes_to_another_store_or_ambiguous_field():
    for client in (NativeClient(wrong_url=True), NativeClient(duplicate=True)):
        actor = ShopifyNativeActor(client)
        if client.wrong_url:
            with pytest.raises(RuntimeError):
                actor.execute({'store_url':URL,'title':'Test'})
        else:
            assert not actor.execute({'store_url':URL,'title':'Test'})['ok']
        assert not any(name=='set_value' for name,_ in client.calls)


def test_native_save_and_arbitrary_driver_actions_are_unavailable():
    with pytest.raises(ValueError, match='Save'):
        ShopifyNativeActor(NativeClient()).execute({'store_url':URL,'title':'Test'}, True)
    with pytest.raises(ValueError):
        CuaClient().call('press_key', {'key':'Enter'})


def test_native_cancellation_stops_before_the_next_field():
    client = NativeClient()
    actor = ShopifyNativeActor(client)
    original = client.call
    def cancelling_call(tool, args=None):
        result = original(tool, args)
        if tool == 'set_value':
            actor.cancel()
        return result
    client.call = cancelling_call
    with pytest.raises(RuntimeError, match='stopped'):
        actor.execute({'store_url':URL,'title':'First field','inventory_quantity':0})
    assert len([c for c in client.calls if c[0]=='set_value']) == 1


def test_connection_failure_selects_native_once_and_reuses_it():
    attempts = []
    class Browser:
        def __init__(self, headless):
            pass
        async def start(self):
            attempts.append('connect')
            raise RuntimeError('CDP unavailable')
        async def close(self):
            pass
    native = ShopifyNativeActor(NativeClient())
    session = ShopifySession(actor_factory=Browser, native_factory=lambda:native)
    try:
        for title in ('First','Second'):
            assert session.run({'store_url':URL,'title':title})['ok']
        assert attempts == ['connect']
    finally:
        session.close()


def test_browser_write_failure_does_not_repeat_through_native():
    class Browser:
        def __init__(self, headless):
            pass
        async def start(self):
            pass
        async def execute_product_creation(self, product, click_save):
            raise RuntimeError('Write outcome unknown')
        async def close(self):
            pass
    native_calls = []
    session = ShopifySession(actor_factory=Browser, native_factory=lambda:native_calls.append(True))
    try:
        with pytest.raises(RuntimeError, match='unknown'):
            session.run({'store_url':URL,'title':'Test'})
        assert not native_calls
    finally:
        session.close()
