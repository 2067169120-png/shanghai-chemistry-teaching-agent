"""Versioned Shanghai Chemistry business integration.

The native desktop application imports readers and managers from this package
without starting or even importing the legacy HTTP/loopback surface.  The
historical package-level gateway names remain available through lazy attribute
loading so existing maintenance scripts keep their public API.
"""

from importlib import import_module
from typing import Any

from .config import AppConfig, ConfigError, Principal

__all__ = [
    "AppConfig",
    "ConfigError",
    "LauncherError",
    "Principal",
    "create_server",
    "health_session",
    "start_session",
    "stop_session",
]

__version__ = "1.0.0"


_LAZY_EXPORTS = {
    "create_server": (".http_app", "create_server"),
    "LauncherError": (".launcher", "LauncherError"),
    "health_session": (".launcher", "health_session"),
    "start_session": (".launcher", "start_session"),
    "stop_session": (".launcher", "stop_session"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
