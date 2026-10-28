from shorts_engine.config import get_config

from .asr.base import BaseASRProvider
from .asr.mock import MockASRProvider
from .asr.whisper_provider import WhisperASRProvider
from .footage.base import BaseFootageProvider
from .footage.mock import MockFootageProvider
from .footage.pexels import PexelsProvider
from .footage.pixabay import PixabayProvider
from .llm.anthropic import AnthropicProvider
from .llm.base import BaseLLMProvider
from .llm.mock import MockLLMProvider
from .llm.ollama_provider import OllamaProvider
from .search.base import BaseSearchProvider
from .search.mock import MockSearchProvider
from .search.tavily_search import TavilyProvider

LLM_PROVIDERS = {"mock", "ollama", "anthropic"}
SEARCH_PROVIDERS = {"mock", "tavily"}
FOOTAGE_PROVIDERS = {"mock", "pixabay", "pexels"}
ASR_PROVIDERS = {"mock", "whisper"}
VALID_PROVIDERS = {
    "llm": LLM_PROVIDERS,
    "search": SEARCH_PROVIDERS,
    "footage": FOOTAGE_PROVIDERS,
    "asr": ASR_PROVIDERS,
}

# Runtime overrides (dry-run, API settings). Without one, config/settings.yaml decides.
_overrides: dict[str, str] = {}


def active_provider_names() -> dict[str, str]:
    """Provider names in effect: runtime overrides first, then ``providers`` in settings.yaml."""
    configured = get_config().settings.get("providers", {})
    names = {kind: _overrides.get(kind) or configured.get(kind) for kind in VALID_PROVIDERS}
    for kind, name in names.items():
        if name not in VALID_PROVIDERS[kind]:
            raise ValueError(f"Provider {kind} tidak dikenal atau belum diatur: {name}")
    return names


def set_active_providers(
    llm: str,
    search: str,
    footage: str | None = None,
    asr: str | None = None,
):
    chosen = {"llm": llm, "search": search, "footage": footage, "asr": asr}
    for kind, name in chosen.items():
        if name is not None and name not in VALID_PROVIDERS[kind]:
            raise ValueError(f"Provider {kind} tidak dikenal: {name}")
    _overrides.update({kind: name for kind, name in chosen.items() if name is not None})


def use_mock_providers() -> None:
    """Route every service to its local fake (used by --dry-run); costs nothing."""
    _overrides.update({kind: "mock" for kind in VALID_PROVIDERS})


def reset_provider_overrides() -> None:
    _overrides.clear()


def get_llm_provider() -> BaseLLMProvider:
    if active_provider_names()["llm"] == "anthropic":
        return AnthropicProvider()
    if active_provider_names()["llm"] == "ollama":
        return OllamaProvider()
    if active_provider_names()["llm"] == "mock":
        return MockLLMProvider()
    raise ValueError(f"Provider LLM tidak dikenal: {active_provider_names()['llm']}")


def get_search_provider() -> BaseSearchProvider:
    if active_provider_names()["search"] == "tavily":
        return TavilyProvider()
    if active_provider_names()["search"] == "mock":
        return MockSearchProvider()
    raise ValueError(f"Provider search tidak dikenal: {active_provider_names()['search']}")


def get_footage_provider() -> BaseFootageProvider:
    if active_provider_names()["footage"] == "pixabay":
        return PixabayProvider()
    if active_provider_names()["footage"] == "pexels":
        return PexelsProvider()
    if active_provider_names()["footage"] == "mock":
        return MockFootageProvider()
    raise ValueError(f"Provider footage tidak dikenal: {active_provider_names()['footage']}")


def get_asr_provider() -> BaseASRProvider:
    if active_provider_names()["asr"] == "whisper":
        return WhisperASRProvider()
    if active_provider_names()["asr"] == "mock":
        return MockASRProvider()
    raise ValueError(f"Provider ASR tidak dikenal: {active_provider_names()['asr']}")


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
