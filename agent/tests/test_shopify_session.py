"""Keep CDP alive between products, rather than re-requesting Edge permission."""
import asyncio

from agent.ghost.browser.shopify_session import ShopifySession


def test_two_products_use_one_connection_and_live_background_loop():
    instances = []
    class Actor:
        def __init__(self, headless):
            self.headless = headless
            self.started = self.closed = self.heartbeats = 0
            instances.append(self)

        async def start(self):
            self.started += 1
            async def heartbeat():
                while True:
                    self.heartbeats += 1
                    await asyncio.sleep(.01)
            self.task = asyncio.create_task(heartbeat())

        async def execute_product_creation(self, product, click_save):
            return {"title": product["title"], "save": click_save, "heartbeats": self.heartbeats}

        async def close(self):
            self.closed += 1
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    session = ShopifySession(actor_factory=Actor)
    try:
        first = session.run({"title": "First"})
        # This schedules on the worker loop between calls, proving it is still running.
        asyncio.run_coroutine_threadsafe(asyncio.sleep(.04), session._loop).result(timeout=2)
        second = session.run({"title": "Second"})
        assert len(instances) == 1
        assert instances[0].started == 1 and instances[0].closed == 0
        assert second["heartbeats"] > first["heartbeats"]
        assert first["save"] is False and second["save"] is False
    finally:
        session.close()
    assert instances[0].closed == 1
    assert not session._thread.is_alive()
    session.close()  # Repeated shutdown is harmless.


def test_failed_connection_is_cleaned_up_and_can_be_explicitly_retried():
    instances = []
    class Actor:
        def __init__(self, headless):
            self.closed = False
            instances.append(self)
        async def start(self):
            if len(instances) == 1:
                raise RuntimeError("Connection declined")
        async def execute_product_creation(self, product, click_save):
            return {"ok": True}
        async def close(self):
            self.closed = True
    session = ShopifySession(actor_factory=Actor)
    try:
        import pytest
        with pytest.raises(RuntimeError, match="Connection declined"):
            session.run({"title": "First"})
        assert instances[0].closed
        assert session.run({"title": "Retry"})["ok"]
        assert len(instances) == 2
    finally:
        session.close()
