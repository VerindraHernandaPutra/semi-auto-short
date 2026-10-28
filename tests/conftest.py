import os

# Must run before shorts_engine is imported: keep a developer's real .env out of the tests.
os.environ["SHORTS_ENV_FILE"] = ""
