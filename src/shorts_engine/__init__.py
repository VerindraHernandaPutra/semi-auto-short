from shorts_engine.env import load_environment

# Load .env before any module reads configuration such as DATA_DIR or API keys.
load_environment()
