"""Runs before any test module is imported: force offline settings so no test ever reaches the
real database or an LLM, whatever .env says (config.py only fills in variables that are unset)."""
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CHECKPOINT_DB_URL"] = ""
os.environ["MATRIX_API_KEY"] = ""
os.environ["OWNER_TELEGRAM_ID"] = ""
