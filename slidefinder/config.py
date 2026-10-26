"""Settings from .env (never committed) or the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# SF_OPEN=1 turns the passcode gate off without discarding the passcode (remove SF_OPEN to turn it back on).
PASSCODE = "" if os.environ.get("SF_OPEN") else os.environ.get("SF_PASSCODE", "")

BUCKET = "slide-finder"
EMBED_MODEL = "voyage-3.5"
EMBED_DIMS = 1024

LECTURE_ARCHIVE = Path(os.environ.get("LECTURE_ARCHIVE", "~/Lecture Archive")).expanduser()
FT_BUILD = LECTURE_ARCHIVE / "_build"

# Phase 1 courses (Faculty Twin's processed build). Keys are the folder names in _build.
COURSES = {
    "70445": {"course": "70-445", "name": "AI for Business Leaders", "term": "2026 Fall"},
    "45884": {"course": "45-884", "name": "AI Methods for Social and Visual Data", "term": "2026 Fall"},
}
# Sessions whose material stays out of the index entirely.
SKIP_SESSIONS = {("45884", 11)}  # Tesla vs Waymo case

SAID_CHUNK_WORDS = 120
