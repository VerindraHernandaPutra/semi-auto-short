import os
from typing import Type, TypeVar
from pydantic import BaseModel
import anthropic
from .base import BaseLLMProvider

T = TypeVar("T", bound=BaseModel)

class AnthropicProvider(BaseLLMProvider):
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY tidak ditemukan di environment variables.")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def generate_structured(self, prompt: str, schema: Type[T], model_name: str) -> T:
        schema_json = schema.model_json_schema()
        
        # Menggunakan fitur tool calling untuk memaksa output JSON yang terstruktur
        tool = {
            "name": "generate_output",
            "description": "Menghasilkan output terstruktur sesuai skema",
            "input_schema": schema_json
        }
        
        response = self.client.messages.create(
            model=model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "generate_output"}
        )
        
        for content_block in response.content:
            if content_block.type == "tool_use" and content_block.name == "generate_output":
                return schema.model_validate(content_block.input)
        
        raise ValueError("Gagal mendapatkan output terstruktur dari Claude.")