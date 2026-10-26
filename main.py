"""Vercel entry point. Vercel finds the FastAPI `app` here and routes every request to it;
files in public/ are served statically before the function is reached."""

from slidefinder.app import app  # noqa: F401
