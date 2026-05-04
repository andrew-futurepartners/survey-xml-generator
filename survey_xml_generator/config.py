"""Configuration and environment setup."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_MINI = os.getenv("OPENAI_MODEL_MINI", "gpt-4o-mini")

# --- Pipeline settings ---
# Max paragraphs per chunk when sending to the AI for segmentation.
# Overlap prevents cutting a question block at the boundary.
SEGMENTATION_CHUNK_SIZE = 150
SEGMENTATION_CHUNK_OVERLAP = 25

# Temperature for AI calls (low = more deterministic)
AI_TEMPERATURE = 0.1

# When a select (dropdown) question has no [DROPDOWN] indicator in the
# source and its explicit option count is at or below this threshold,
# the classifier guard converts it to radio (single-select buttons).
SELECT_TO_RADIO_MAX_OPTIONS = int(os.getenv("SELECT_TO_RADIO_MAX_OPTIONS", "10"))

# --- Forsta XML defaults ---
SURVEY_NAMESPACES = {
    "xmlns:builder": "http://decipherinc.com/builder",
    "xmlns:ss": "http://decipherinc.com/ss",
    "xmlns:html": "http://decipherinc.com/html",
}

# Default attributes for the <survey> root element
SURVEY_ROOT_DEFAULTS = {
    "autosave": "0",
    "builderCompatible": "1",
    "compat": "153",
    "delphi": "1",
    "fir": "on",
    "html:showNumber": "0",
    "mobile": "compat",
    "mobileDevices": "smartphone,tablet,desktop",
    "name": "Survey",
    "secure": "1",
    "setup": "term,decLang,quota,time",
    "ss:disableBackButton": "1",
    "ss:enableNavigation": "1",
    "ss:hideProgressBar": "0",
    "state": "testing",
}
