from pathlib import Path
import os


def pytest_configure(config):
    (Path(__file__).resolve().parents[2] / ".ghost").mkdir(exist_ok=True)
    if os.environ.get("DIGITALSOUL_TEST_NO_IO") == "1":
        import asyncio
        from agent.tests.no_io_loop import NoIOPolicy
        asyncio.set_event_loop_policy(NoIOPolicy())
