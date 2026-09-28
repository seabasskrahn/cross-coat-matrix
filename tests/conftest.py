"""Runs before any test module is imported: force offline settings so no test ever reaches the
real database or an LLM, whatever .env says (config.py only fills in variables that are unset)."""
import os
import tempfile

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CHECKPOINT_DB_URL"] = ""
os.environ["MATRIX_API_KEY"] = ""
os.environ["OWNER_TELEGRAM_ID"] = ""
# Scout (Tavily web search) is off in tests; tests that need it switch it on with a fake search.
os.environ["TAVILY_API_KEY"] = ""
# The worker On/Off switch: never read or write the real logs/keeper_settings.json from tests.
os.environ["KEEPER_SETTINGS_FILE"] = os.path.join(tempfile.mkdtemp(prefix="keeper-tests-"), "keeper_settings.json")
