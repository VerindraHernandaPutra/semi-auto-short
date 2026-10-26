from .llm.anthropic import AnthropicProvider
from .llm.base import BaseLLMProvider
from .llm.mock import MockLLMProvider
from .llm.ollama_provider import OllamaProvider
from .search.base import BaseSearchProvider
from .search.mock import MockSearchProvider
from .search.tavily_search import TavilyProvider

LLM_PROVIDERS = {"mock", "ollama", "anthropic"}
SEARCH_PROVIDERS = {"mock", "tavily"}

ACTIVE_LLM_PROVIDER = "mock"      # Opsi: mock, ollama, anthropic
ACTIVE_SEARCH_PROVIDER = "mock"   # Opsi: mock, tavily


def set_active_providers(llm: str, search: str):
    if llm not in LLM_PROVIDERS:
        raise ValueError(f"Provider LLM tidak dikenal: {llm}")
    if search not in SEARCH_PROVIDERS:
        raise ValueError(f"Provider search tidak dikenal: {search}")

    global ACTIVE_LLM_PROVIDER, ACTIVE_SEARCH_PROVIDER
    ACTIVE_LLM_PROVIDER = llm
    ACTIVE_SEARCH_PROVIDER = search


def get_llm_provider() -> BaseLLMProvider:
    if ACTIVE_LLM_PROVIDER == "anthropic":
        return AnthropicProvider()
    if ACTIVE_LLM_PROVIDER == "ollama":
        return OllamaProvider()
    if ACTIVE_LLM_PROVIDER == "mock":
        return MockLLMProvider()
    raise ValueError(f"Provider LLM tidak dikenal: {ACTIVE_LLM_PROVIDER}")


def get_search_provider() -> BaseSearchProvider:
    if ACTIVE_SEARCH_PROVIDER == "tavily":
        return TavilyProvider()
    if ACTIVE_SEARCH_PROVIDER == "mock":
        return MockSearchProvider()
    raise ValueError(f"Provider search tidak dikenal: {ACTIVE_SEARCH_PROVIDER}")


def is_mock_provider(provider: object) -> bool:
    return isinstance(provider, (MockLLMProvider, MockSearchProvider))