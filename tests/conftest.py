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


@pytest.fixture(autouse=True)
def small_claim_sets_are_fine_in_tests(monkeypatch):
    """The material gate drops topics with little claim text; test fixtures have a few short claims.

    ``get_config`` reads the YAML on every call, so the setting is changed where it is loaded.
    Tests of the gate itself set ``min_claim_words`` on their own config.
    """
    import shorts_engine.config as config_module

    real_load = config_module.load_yaml

    def load_for_tests(file_name):
        data = real_load(file_name)
        if file_name == "settings.yaml":
            data.setdefault("script", {})["min_claim_words"] = 0
        return data

    monkeypatch.setattr(config_module, "load_yaml", load_for_tests)
