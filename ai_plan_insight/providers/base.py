from abc import ABC, abstractmethod
from typing import Any

from ..models import UsageInfo
from ..config import ProviderConfig


class BaseProvider(ABC):
    """Abstract base class for AI plan usage providers."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._headers: dict[str, str] = {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name used for display."""
        pass

    @abstractmethod
    def authenticate(self) -> None:
        """Setup authentication headers/credentials."""
        pass

    @abstractmethod
    async def fetch_usage(self) -> Any:
        """Call provider API and return raw response data."""
        pass

    @abstractmethod
    def parse_usage(self, raw_data: dict) -> UsageInfo:
        """Convert raw response to standardized UsageInfo model."""
        pass
