import httpx
import pytest

from shorts_engine import env as env_module
from shorts_engine.providers.footage.base import FootageProviderError
from shorts_engine.providers.footage.pexels import PexelsProvider
from shorts_engine.providers.footage.pixabay import PixabayProvider

SECRET = "super-secret-key-123"

# Shape follows the official Pixabay videos API documentation.
PIXABAY_PAYLOAD = {
    "total": 2,
    "totalHits": 2,
    "hits": [
        {
            "id": 111,
            "pageURL": "https://pixabay.com/videos/octopus-111/",
            "tags": "octopus, sea, animal",
            "duration": 14,
            "user": "diver",
            "user_id": 9,
            "videos": {
                "large": {"url": "", "width": 0, "height": 0, "size": 0},
                "medium": {
                    "url": "https://cdn.example/111_medium.mp4",
                    "width": 1920,
                    "height": 1080,
                    "size": 5_000_000,
                    "thumbnail": "https://cdn.example/111.jpg",
                },
                "small": {"url": "https://cdn.example/111_small.mp4", "width": 1280, "height": 720},
            },
        },
        {
            "id": 222,
            "pageURL": "https://pixabay.com/videos/reef-222/",
            "tags": "reef",
            "duration": 8,
            "user": "snorkel",
            "videos": {
                "large": {
                    "url": "https://cdn.example/222_large.mp4",
                    "width": 3840,
                    "height": 2160,
                    "size": 40_000_000,
                },
                "medium": {
                    "url": "https://cdn.example/222_medium.mp4",
                    "width": 1920,
                    "height": 1080,
                },
            },
        },
        {"id": 333, "pageURL": "https://pixabay.com/videos/broken-333/", "videos": {}},
    ],
}


def make_provider(handler, tmp_path, **kwargs):
    return PixabayProvider(
        api_key=kwargs.pop("api_key", SECRET),
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_pixabay_normalizes_hits_and_prefers_largest_available_rendition(tmp_path):
    provider = make_provider(lambda request: httpx.Response(200, json=PIXABAY_PAYLOAD), tmp_path)

    candidates = provider.search_videos("octopus", per_page=5)

    assert [c.asset_id for c in candidates] == ["111", "222"]  # malformed hit skipped
    assert candidates[0].download_url.endswith("111_medium.mp4")  # empty large -> medium
    assert candidates[1].download_url.endswith("222_large.mp4")
    assert candidates[0].orientation == "landscape"
    assert candidates[0].attribution == "Video by diver from Pixabay"
    assert candidates[0].tags == ["octopus", "sea", "animal"]
    assert candidates[0].contributor_url is None  # never invented


def test_pixabay_drops_ai_generated_clips(tmp_path):
    payload = {
        "hits": [
            {**PIXABAY_PAYLOAD["hits"][0], "id": 1, "tags": "octopus, AI Generated, sea"},
            {**PIXABAY_PAYLOAD["hits"][0], "id": 2, "tags": "octopus, sea"},
        ]
    }
    provider = make_provider(lambda request: httpx.Response(200, json=payload), tmp_path)

    candidates = provider.search_videos("octopus")

    assert [c.asset_id for c in candidates] == ["2"]


def test_pixabay_clamps_per_page_to_documented_minimum(tmp_path):
    seen = {}

    def handler(request):
        seen["per_page"] = request.url.params["per_page"]
        return httpx.Response(200, json={"hits": []})

    make_provider(handler, tmp_path).search_videos("octopus", per_page=1)

    assert seen["per_page"] == "3"


def test_pixabay_caches_responses_for_24_hours(tmp_path):
    calls = []
    now = [1000.0]

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=PIXABAY_PAYLOAD)

    provider = make_provider(handler, tmp_path, clock=lambda: now[0])

    provider.search_videos("octopus")
    provider.search_videos("octopus")
    assert len(calls) == 1

    now[0] += 24 * 60 * 60 + 1
    provider.search_videos("octopus")
    assert len(calls) == 2


def test_pixabay_cache_key_does_not_depend_on_api_key(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=PIXABAY_PAYLOAD)

    make_provider(handler, tmp_path, api_key="key-one").search_videos("octopus")
    make_provider(handler, tmp_path, api_key="key-two").search_videos("octopus")

    assert len(calls) == 1
    assert not any("key-one" in p.read_text() for p in (tmp_path / "cache").iterdir())


def test_pixabay_invalid_key_fails_without_retry_and_without_leaking_secret(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="[ERROR 400] Invalid API key")

    provider = make_provider(handler, tmp_path)

    with pytest.raises(FootageProviderError) as error:
        provider.search_videos("octopus")

    assert len(calls) == 1
    assert error.value.status_code == 400
    assert SECRET not in str(error.value)


def test_pixabay_retries_rate_limit_and_honors_retry_after(tmp_path):
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json=PIXABAY_PAYLOAD),
        ]
    )
    waits = []
    provider = PixabayProvider(
        api_key=SECRET,
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(lambda request: next(responses)),
        sleep=waits.append,
    )

    assert len(provider.search_videos("octopus")) == 2
    assert waits == [7.0]


def test_pixabay_gives_up_after_bounded_attempts(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    provider = make_provider(handler, tmp_path, max_attempts=3)

    with pytest.raises(FootageProviderError, match="gave up after 3 attempts"):
        provider.search_videos("octopus")
    assert len(calls) == 3


def test_pixabay_network_error_message_hides_request_url(tmp_path):
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    provider = make_provider(handler, tmp_path, max_attempts=1)

    with pytest.raises(FootageProviderError) as error:
        provider.search_videos("octopus")

    assert SECRET not in str(error.value)


def test_pixabay_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    provider = PixabayProvider(cache_dir=tmp_path / "cache")

    with pytest.raises(FootageProviderError, match="PIXABAY_API_KEY"):
        provider.search_videos("octopus")


def test_pexels_uses_current_endpoint_and_normalizes(tmp_path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "videos": [
                    {
                        "id": 5,
                        "url": "https://www.pexels.com/video/5/",
                        "image": "https://cdn.example/5.jpg",
                        "duration": 12,
                        "user": {"name": "Ana", "url": "https://www.pexels.com/@ana"},
                        "video_files": [
                            {
                                "quality": "sd",
                                "width": 640,
                                "height": 1138,
                                "link": "https://cdn.example/sd.mp4",
                            },
                            {
                                "quality": "hd",
                                "width": 1080,
                                "height": 1920,
                                "link": "https://cdn.example/hd.mp4",
                            },
                        ],
                    }
                ]
            },
        )

    provider = PexelsProvider(api_key=SECRET, transport=httpx.MockTransport(handler))

    [candidate] = provider.search_videos("octopus")

    assert seen["path"] == "/v1/videos/search"
    assert candidate.download_url.endswith("hd.mp4")
    assert candidate.orientation == "portrait"
    assert candidate.contributor_url == "https://www.pexels.com/@ana"


def test_load_environment_reads_file_without_overriding_real_variables(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("SHORTS_TEST_FROM_FILE=file\nSHORTS_TEST_EXISTING=file\n", encoding="utf-8")
    monkeypatch.setenv("SHORTS_ENV_FILE", str(env_file))
    monkeypatch.setenv("SHORTS_TEST_EXISTING", "real")
    monkeypatch.delenv("SHORTS_TEST_FROM_FILE", raising=False)

    loaded = env_module.load_environment()

    assert loaded == env_file
    assert env_module.os.environ["SHORTS_TEST_FROM_FILE"] == "file"
    assert env_module.os.environ["SHORTS_TEST_EXISTING"] == "real"
    monkeypatch.delenv("SHORTS_TEST_FROM_FILE", raising=False)


def test_load_environment_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SHORTS_ENV_FILE", "")

    assert env_module.load_environment() is None
