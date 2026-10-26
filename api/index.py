"""Vercel entry point: exposes the FastAPI app as one Python function."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slidefinder.app import app  # noqa: E402,F401
