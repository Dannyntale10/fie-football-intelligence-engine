from abc import ABC, abstractmethod
from typing import Any

class BookmakerConnector(ABC):
    """Interface for an authorized bookmaker/odds-feed integration.

    Implementations must use official APIs, licensed feeds, or otherwise
    permitted access methods. Do not add credential bypasses or prohibited scraping.
    """
    bookmaker_name: str

    @abstractmethod
    def fetch_fixtures(self) -> list[dict[str, Any]]:
        raise NotImplementedError
