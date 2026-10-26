"""Supabase over plain HTTP: PostgREST for tables and functions, Storage for images.

Everything uses the service role key, so this module only runs on the server or the build
machine. The browser never sees it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from . import config

_HEADERS = {"apikey": config.SUPABASE_KEY, "Authorization": f"Bearer {config.SUPABASE_KEY}"}
_client = httpx.Client(base_url=config.SUPABASE_URL, headers=_HEADERS, timeout=60)


def _check(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"supabase {r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
    return r


# ---- tables ---------------------------------------------------------------

def upsert(table: str, rows: list[dict], on_conflict: str = "id", batch: int = 200) -> None:
    for i in range(0, len(rows), batch):
        _check(_client.post(
            f"/rest/v1/{table}", params={"on_conflict": on_conflict}, json=rows[i:i + batch],
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        ))


def insert(table: str, row: dict) -> dict:
    r = _check(_client.post(f"/rest/v1/{table}", json=row, headers={"Prefer": "return=representation"}))
    return r.json()[0]


def select(table: str, params: dict[str, Any] | None = None) -> list[dict]:
    return _check(_client.get(f"/rest/v1/{table}", params=params or {})).json()


def select_all(table: str, params: dict[str, Any] | None = None, page: int = 1000) -> list[dict]:
    """Every matching row. PostgREST caps one response at 1000 rows, so page with Range headers."""
    out: list[dict] = []
    while True:
        r = _check(_client.get(f"/rest/v1/{table}", params=params or {},
                               headers={"Range-Unit": "items", "Range": f"{len(out)}-{len(out) + page - 1}"}))
        rows = r.json()
        out.extend(rows)
        if len(rows) < page:
            return out


def delete(table: str, params: dict[str, Any]) -> None:
    _check(_client.delete(f"/rest/v1/{table}", params=params))


def rpc(name: str, args: dict) -> Any:
    r = _check(_client.post(f"/rest/v1/rpc/{name}", json=args))
    return r.json() if r.content.strip() else None


def exec_sql(sql: str) -> None:
    rpc("sf_exec", {"sql": sql})


def count(table: str) -> int:
    r = _check(_client.get(f"/rest/v1/{table}", params={"select": "*", "limit": 1},
                           headers={"Prefer": "count=exact"}))
    return int(r.headers["content-range"].split("/")[1])


# ---- storage --------------------------------------------------------------

def upload(path: str, data: bytes, content_type: str = "image/webp", tries: int = 4) -> None:
    for attempt in range(tries):
        r = _client.post(f"/storage/v1/object/{config.BUCKET}/{path}", content=data,
                         headers={"Content-Type": content_type, "x-upsert": "true"})
        if r.status_code < 500:
            _check(r)
            return
        time.sleep(1.5 * (attempt + 1))  # Storage returns the odd 502 under parallel load
    _check(r)


def signed_url(path: str, expires_s: int = 3600) -> str:
    r = _check(_client.post(f"/storage/v1/object/sign/{config.BUCKET}/{path}", json={"expiresIn": expires_s}))
    return f"{config.SUPABASE_URL}/storage/v1{r.json()['signedURL']}"


def signed_urls(paths: list[str], expires_s: int = 3600) -> dict[str, str]:
    if not paths:
        return {}
    r = _check(_client.post(f"/storage/v1/object/sign/{config.BUCKET}", json={"expiresIn": expires_s, "paths": paths}))
    return {d["path"]: f"{config.SUPABASE_URL}/storage/v1{d['signedURL']}" for d in r.json() if d.get("signedURL")}


def signed_upload_url(path: str) -> dict:
    """A one-time URL the browser can PUT a file to. Returns {"url", "token"}."""
    r = _check(_client.post(f"/storage/v1/object/upload/sign/{config.BUCKET}/{path}", json={}))
    d = r.json()
    return {"url": f"{config.SUPABASE_URL}/storage/v1{d['url']}", "token": d.get("token", "")}


def download(path: str) -> bytes:
    return _check(_client.get(f"/storage/v1/object/{config.BUCKET}/{path}")).content
