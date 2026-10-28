import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

from .base import BaseLLMProvider

T = TypeVar("T", bound=BaseModel)


class OllamaProvider(BaseLLMProvider):
    def __init__(self, host: str = "http://localhost:11434", default_model: str = "llama3"):
        self.host = host
        self.default_model = default_model

    def generate_structured(self, prompt: str, schema: type[T], model_name: str) -> T:
        # Abaikan nama model dari config jika itu Claude, paksakan pakai Llama 3
        if "claude" in model_name:
            model_name = self.default_model

        # Minta schema JSON Pydantic ke Ollama
        schema_json = schema.model_json_schema()
        system_prompt = (
            f"You are a strict data assistant. You MUST output ONLY valid JSON that exactly matches this schema:\n"
            f"{json.dumps(schema_json)}\n"
            f"Do not include any markdown formatting, explanations, or extra text. Just the JSON."
        )

        payload = {
            "model": model_name,
            "prompt": prompt,
            "system": system_prompt,
            "format": "json",  # Paksa Ollama mengeluarkan JSON
            "stream": False,
        }

        try:
            with httpx.Client(timeout=180.0) as client:
                response = client.post(f"{self.host}/api/generate", json=payload)
                response.raise_for_status()
                result = response.json()

            # Validasi JSON dari Ollama menggunakan Pydantic
            return schema.model_validate_json(result["response"])
        except Exception as e:
            print(f"Ollama Error: {e}")
            print(f"Raw Response: {result.get('response', '') if 'result' in locals() else 'None'}")
            raise ValueError(f"Gagal mendapatkan output terstruktur dari Ollama: {e}") from e
