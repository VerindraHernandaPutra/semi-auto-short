from abc import ABC, abstractmethod
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, schema: Type[T], model_name: str) -> T:
        """Menghasilkan output terstruktur yang divalidasi oleh skema Pydantic."""
        pass