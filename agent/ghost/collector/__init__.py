"""Collector package for DigitalSoul desktop and extension telemetry."""

from agent.ghost.collector.window_tracker import (
    DesktopCollector,
    is_workstation_locked,
    get_foreground_window_info,
)

__all__ = [
    "DesktopCollector",
    "is_workstation_locked",
    "get_foreground_window_info",
]
