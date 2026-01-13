from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HandlerFn = Callable[[dict[str, Any]], None]


@dataclass
class Handler:
    event_type: str
    fn: HandlerFn


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HandlerFn]] = {}

    def register(self, event_type: str, fn: HandlerFn) -> None:
        self._handlers.setdefault(event_type, []).append(fn)

    def handlers_for(self, event_type: str) -> list[HandlerFn]:
        return self._handlers.get(event_type, [])