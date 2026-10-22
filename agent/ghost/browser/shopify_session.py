"""One browser-use connection and live event loop per desktop bridge lifetime."""
import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeout

from .shopify_actor import ShopifyBrowserActor


class ShopifySession:
    def __init__(self, actor_factory=ShopifyBrowserActor, native_factory=None):
        if actor_factory is ShopifyBrowserActor:
            # browser-use eagerly imports its optional recording watchdog at
            # startup, even when recording is off. On Windows, first importing
            # NumPy's native extension on this worker thread can deadlock. Load
            # that dependency on the caller thread before starting the loop.
            import browser_use.browser.video_recorder  # noqa: F401
        self._factory = actor_factory
        self._native_factory = native_factory
        self._native_enabled = native_factory is not None or actor_factory is ShopifyBrowserActor
        self._native_actor = None
        self._actor = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._serve, daemon=True, name="shopify-browser-use")
        self._thread.start()
        self._ready.wait(timeout=5)
        if not self._ready.is_set():
            raise RuntimeError("The browser-use worker did not start")

    def _serve(self):
        asyncio.set_event_loop(self._loop)
        self._lock = asyncio.Lock()
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.close()

    async def _run(self, product, click_save, headless):
        async with self._lock:
            target_url = str(product.get("store_url", ""))
            is_local = "127.0.0.1" in target_url or "localhost" in target_url
            if headless or is_local:
                actor = self._factory(headless=True)
                try:
                    return await actor.execute_product_creation(product, click_save=click_save)
                finally:
                    await actor.close()
            if self._native_actor is not None:
                return await self._run_native(product, click_save)
            if self._actor is None:
                actor = self._factory(headless=False)
                try:
                    await actor.start()
                except asyncio.CancelledError:
                    await actor.close()
                    raise
                except Exception:
                    await actor.close()
                    if not self._native_enabled:
                        raise
                    if self._native_factory is None:
                        from .shopify_native import ShopifyNativeActor
                        self._native_factory = ShopifyNativeActor
                    self._native_actor = self._native_factory()
                    # Only connection failure selects native controls. Never
                    # repeat an attempted browser write through another engine.
                    return await self._run_native(product, click_save)
                self._actor = actor
            # Keep both the Actor and its event loop alive between products.
            # keep_alive=True alone only preserves the Edge process, not CDP.
            return await self._actor.execute_product_creation(product, click_save=click_save)

    async def _run_native(self, product, click_save):
        if hasattr(self._native_actor, "prepare"):
            self._native_actor.prepare()
        task = asyncio.create_task(asyncio.to_thread(self._native_actor.execute, product, click_save))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if hasattr(self._native_actor, "cancel"):
                self._native_actor.cancel()
            # Keep the execution lock until the native boundary has returned.
            await asyncio.gather(task, return_exceptions=True)
            raise

    def submit(self, product, click_save=False, headless=False):
        if self._closed:
            raise RuntimeError("The browser-use session is closed")
        return asyncio.run_coroutine_threadsafe(self._run(product, click_save, headless), self._loop)

    def run(self, product, click_save=False, headless=False):
        future = self.submit(product, click_save, headless)
        try:
            return future.result(timeout=170)
        except FutureTimeout:
            future.cancel()
            raise RuntimeError("Browser-use timed out; inspect Shopify before retrying") from None

    async def _disconnect(self):
        async with self._lock:
            if self._native_actor is not None:
                actor, self._native_actor = self._native_actor, None
                await asyncio.to_thread(actor.close)
            if self._actor is not None:
                actor, self._actor = self._actor, None
                await actor.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop).result(timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
