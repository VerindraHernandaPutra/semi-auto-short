from types import SimpleNamespace

import anthropic
import httpx
import pytest

from shorts_engine.domain.schemas import ClaimSupportCheck
from shorts_engine.providers.llm.anthropic import AnthropicProvider
from shorts_engine.providers.llm.base import LLMProviderError

SECRET = "sk-ant-test-secret-value"


class FakeMessages:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def make_provider(result=None, error=None):
    messages = FakeMessages(result, error)
    provider = AnthropicProvider(api_key=SECRET, client=SimpleNamespace(messages=messages))
    return provider, messages


def response(parsed, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)


def test_returns_validated_output_and_uses_structured_outputs_not_forced_tools():
    check = ClaimSupportCheck(status="supports", reason="explicit")
    provider, messages = make_provider(response(check))

    result = provider.generate_structured("prompt", ClaimSupportCheck, "some-model", effort="low")

    assert result is check
    call = messages.calls[0]
    assert call["output_format"] is ClaimSupportCheck
    assert call["output_config"] == {"effort": "low"}
    assert call["model"] == "some-model"
    assert "tool_choice" not in call  # forced tool use is rejected by newer models


def test_effort_defaults_to_medium_and_rejects_unknown_values():
    provider, messages = make_provider(response(ClaimSupportCheck(status="no", reason="x")))

    provider.generate_structured("prompt", ClaimSupportCheck, "m")
    assert messages.calls[0]["output_config"] == {"effort": "medium"}

    with pytest.raises(ValueError, match="Unknown effort"):
        provider.generate_structured("prompt", ClaimSupportCheck, "m", effort="turbo")


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_stop_reasons_are_errors_not_silent_output(stop_reason):
    provider, _ = make_provider(response(None, stop_reason))

    with pytest.raises(LLMProviderError, match=stop_reason):
        provider.generate_structured("prompt", ClaimSupportCheck, "m")


def test_missing_parsed_output_is_an_error():
    provider, _ = make_provider(response(None))

    with pytest.raises(LLMProviderError, match="no parsed output"):
        provider.generate_structured("prompt", ClaimSupportCheck, "m")


def test_http_errors_become_provider_errors_without_leaking_the_key():
    api_response = httpx.Response(400, request=httpx.Request("POST", "https://api.example/v1"))
    error = anthropic.BadRequestError("bad schema", response=api_response, body=None)
    provider, _ = make_provider(error=error)

    with pytest.raises(LLMProviderError) as raised:
        provider.generate_structured("prompt", ClaimSupportCheck, "m")

    assert "HTTP 400" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_missing_api_key_is_an_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_schema_descriptions_are_english_so_content_follows_content_language():
    import re

    from shorts_engine.domain import schemas

    descriptions = [
        field.description or ""
        for model in vars(schemas).values()
        if isinstance(model, type) and issubclass(model, schemas.BaseModel)
        for field in model.model_fields.values()
    ]
    indonesian = re.compile(
        r"\b(apakah|alasan|kutipan|daftar|nama|teks|untuk|yang|dengan|jika)\b", re.IGNORECASE
    )

    assert descriptions
    assert [d for d in descriptions if indonesian.search(d)] == []


def test_tavily_uses_configured_depth_and_returns_page_text(monkeypatch):
    from shorts_engine.providers.search import tavily_search

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    provider = tavily_search.TavilyProvider()
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return {"results": [{"url": "https://www.nasa.gov/x", "title": "T", "raw_content": "body"}]}

    monkeypatch.setattr(provider.client, "search", fake_search)

    results = provider.search_and_extract(["octopus hearts"])

    assert calls[0]["search_depth"] == "basic"  # 1 credit; "advanced" costs 2
    assert calls[0]["include_raw_content"] is True
    assert results[0]["content"] == "body"
    assert results[0]["domain"] == "nasa.gov"
