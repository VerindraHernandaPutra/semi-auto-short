from .llm.anthropic import AnthropicProvider
from .llm.base import BaseLLMProvider
from .llm.mock import MockLLMProvider
from .llm.ollama_provider import OllamaProvider
from .search.base import BaseSearchProvider
from .search.mock import MockSearchProvider
from .search.tavily_search import TavilyProvider
from .asr.base import BaseASRProvider
from .asr.mock import MockASRProvider
from .asr.whisper_provider import WhisperASRProvider
from .footage.base import BaseFootageProvider
from .footage.mock import MockFootageProvider
from .video.pexels import PexelsProvider

LLM_PROVIDERS = {"mock", "ollama", "anthropic"}
SEARCH_PROVIDERS = {"mock", "tavily"}
FOOTAGE_PROVIDERS = {"mock", "pexels"}
ASR_PROVIDERS = {"mock", "whisper"}

ACTIVE_LLM_PROVIDER = "mock"      # Opsi: mock, ollama, anthropic
ACTIVE_SEARCH_PROVIDER = "mock"   # Opsi: mock, tavily
ACTIVE_FOOTAGE_PROVIDER = "pexels"
ACTIVE_ASR_PROVIDER = "whisper"


def set_active_providers(
    llm: str,
    search: str,
    footage: str | None = None,
    asr: str | None = None,
):
    if llm not in LLM_PROVIDERS:
        raise ValueError(f"Provider LLM tidak dikenal: {llm}")
    if search not in SEARCH_PROVIDERS:
        raise ValueError(f"Provider search tidak dikenal: {search}")
    if footage is not None and footage not in FOOTAGE_PROVIDERS:
        raise ValueError(f"Provider footage tidak dikenal: {footage}")
    if asr is not None and asr not in ASR_PROVIDERS:
        raise ValueError(f"Provider ASR tidak dikenal: {asr}")

    global ACTIVE_LLM_PROVIDER, ACTIVE_SEARCH_PROVIDER
    global ACTIVE_FOOTAGE_PROVIDER, ACTIVE_ASR_PROVIDER
    ACTIVE_LLM_PROVIDER = llm
    ACTIVE_SEARCH_PROVIDER = search
    if footage is not None:
        ACTIVE_FOOTAGE_PROVIDER = footage
    if asr is not None:
        ACTIVE_ASR_PROVIDER = asr


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


def get_footage_provider() -> BaseFootageProvider:
    if ACTIVE_FOOTAGE_PROVIDER == "pexels":
        return PexelsProvider()
    if ACTIVE_FOOTAGE_PROVIDER == "mock":
        return MockFootageProvider()
    raise ValueError(f"Provider footage tidak dikenal: {ACTIVE_FOOTAGE_PROVIDER}")


def get_asr_provider() -> BaseASRProvider:
    if ACTIVE_ASR_PROVIDER == "whisper":
        return WhisperASRProvider()
    if ACTIVE_ASR_PROVIDER == "mock":
        return MockASRProvider()
    raise ValueError(f"Provider ASR tidak dikenal: {ACTIVE_ASR_PROVIDER}")


def is_mock_provider(provider: object) -> bool:
    return isinstance(
        provider,
        (
            MockLLMProvider,
            MockSearchProvider,
            MockFootageProvider,
            MockASRProvider,
        ),
    )