from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, schema: type[T], model_name: str) -> T:
        """Menghasilkan output terstruktur yang divalidasi oleh skema Pydantic."""
        pass
