import os
import tempfile

import pytest

# Must run before shorts_engine is imported. Keep a developer's real .env and real data out of
# the tests: the module-level database engine and every job folder follow DATA_DIR.
os.environ["SHORTS_ENV_FILE"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="shorts-engine-tests-")


@pytest.fixture(autouse=True)
def reset_provider_overrides():
    from shorts_engine.providers.factory import reset_provider_overrides as reset

    reset()
    yield
    reset()
