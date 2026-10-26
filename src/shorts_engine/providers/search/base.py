from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseSearchProvider(ABC):
    @abstractmethod
    def search_and_extract(self, queries: List[str]) -> List[Dict[str, Any]]:
        """Melakukan pencarian dan mengembalikan teks hasil ekstraksi halaman."""
        pass