from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class Publisher(ABC):
    @abstractmethod
    def publish(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError


class ConsumerTransport(ABC):
    @abstractmethod
    def ensure_consumer_group(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read(self, count: int, block_ms: int) -> list[tuple[str, dict[str, str]]]:
        """
        Returns: list of (message_id, fields)
        """
        raise NotImplementedError

    @abstractmethod
    def ack(self, message_id: str) -> None:
        raise NotImplementedError