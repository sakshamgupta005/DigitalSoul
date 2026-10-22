"""Test-only coroutine scheduler for sandboxes without socketpair support.

There is no network/subprocess implementation. These tests exercise state logic,
not operating-system IO. Never use this event loop to run the agent itself.
"""
import asyncio
import time


class ClockSelector:
    def select(self, timeout=None):
        time.sleep(min(timeout or 0, 0.005))
        return []


class NoIOLoop(asyncio.BaseEventLoop):
    def __init__(self):
        super().__init__()
        self._selector = ClockSelector()

    def _process_events(self, events):
        pass

    def _write_to_self(self):
        # Thread notifications are observed on the next <=5ms scheduler tick.
        pass


class NoIOPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        return NoIOLoop()
