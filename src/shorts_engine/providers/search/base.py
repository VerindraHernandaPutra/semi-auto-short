from abc import ABC, abstractmethod
from typing import Any


class BaseSearchProvider(ABC):
    @abstractmethod
    def search_and_extract(self, queries: list[str]) -> list[dict[str, Any]]:
        """Melakukan pencarian dan mengembalikan teks hasil ekstraksi halaman."""
        pass
