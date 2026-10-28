import os

import pytest

# Must run before shorts_engine is imported: keep a developer's real .env out of the tests.
os.environ["SHORTS_ENV_FILE"] = ""


@pytest.fixture(autouse=True)
def reset_provider_overrides():
    from shorts_engine.providers.factory import reset_provider_overrides as reset

    reset()
    yield
    reset()
