"""The web app: a static page plus a small JSON API. Runs on Vercel or with uvicorn.

Routes (behind the passcode cookie when SF_PASSCODE is set and SF_OPEN is not):
    GET  /api/me                          whether a passcode is needed and whether this browser has it
    GET  /api/search?q=...&course=...&mode=hybrid|vector|keyword&n=30
    GET  /api/slide/{slide_id}            slide, image, narration per term, items, notes, versions in other courses
    GET  /api/courses                     course codes and names for the filter
    POST /api/notes {slide_id, text}      add a note; searchable immediately
    GET  /api/picks  POST /api/picks {slide_id}  DELETE /api/picks/{position}
    GET  /api/export.pdf                  picked slides as one PDF
    POST /api/decks/begin {name, pages}   signed upload URLs for a new deck's page images
    POST /api/decks/finish {...}          text + metadata for the uploaded deck; embeds and inserts
    POST /api/login {passcode}
"""

from __future__ import annotations

import hmac
import io
import re
import secrets
import time
import unicodedata

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, embed, search
from .text import card_text

app = FastAPI(title="Slide Finder", docs_url=None, redoc_url=None)
PUBLIC = config.ROOT / "public"
COOKIE = "sf_session"


# ---- passcode -------------------------------------------------------------

def _token() -> str:
    return hmac.new(config.PASSCODE.encode(), b"slide-finder", "sha256").hexdigest()


def require_login(request: Request) -> None:
    if not config.PASSCODE:
        return
    if not hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
        raise HTTPException(401, "passcode required")


class Login(BaseModel):
    passcode: str = Field(max_length=200)


_attempts: dict[str, list[float]] = {}


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        raise HTTPException(429, "too many tries; wait ten minutes")
    _attempts[ip] = recent + [now]
    if not config.PASSCODE or not hmac.compare_digest(body.passcode.strip(), config.PASSCODE):
        raise HTTPException(401, "wrong passcode")
    response.set_cookie(COOKIE, _token(), httponly=True, secure=True, samesite="lax", max_age=90 * 86400)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    needs = bool(config.PASSCODE)
    ok = not needs or hmac.compare_digest(request.cookies.get(COOKIE, ""), _token())
    return {"needs_passcode": needs, "logged_in": ok}


# ---- search and slides ----------------------------------------------------

@app.get("/api/search", dependencies=[Depends(require_login)])
def api_search(q: str, course: str | None = None, mode: str = "hybrid", n: int = 30):
    q = q.strip()[:500]
    if not q:
        raise HTTPException(400, "empty question")
    if mode not in ("hybrid", "vector", "keyword"):
        raise HTTPException(400, "bad mode")
    return search.search(q, course or None, mode, n)


@app.get("/api/slide/{slide_id}", dependencies=[Depends(require_login)])
def api_slide(slide_id: str):
    d = search.slide_detail(slide_id)
    if not d:
        raise HTTPException(404, "no such slide")
    return d


@app.get("/api/courses", dependencies=[Depends(require_login)])
def api_courses():
    rows = db.select_all("sf_slides", {"select": "course,course_name,term", "order": "course"})
    seen = {}
    for r in rows:
        seen.setdefault(r["course"], {"course": r["course"], "course_name": r["course_name"], "terms": set()})
        seen[r["course"]]["terms"].add(r["term"])
    return [{**v, "terms": sorted(v["terms"])} for v in seen.values()]


# ---- notes (runtime add) --------------------------------------------------

class Note(BaseModel):
    slide_id: str = Field(max_length=80)
    text: str = Field(min_length=2, max_length=2000)


@app.post("/api/notes", dependencies=[Depends(require_login)])
def add_note(body: Note):
    rows = db.select("sf_slides", {"slide_id": f"eq.{body.slide_id}", "select": "slide_id,title,term"})
    if not rows:
        raise HTTPException(404, "no such slide")
    text = " ".join(body.text.split())
    item = {
        "id": f"{body.slide_id}-note-{secrets.token_hex(4)}", "slide_id": body.slide_id, "kind": "note",
        "term": rows[0]["term"], "text": f"[{rows[0]['title']}] {text}" if rows[0]["title"] else text,
        "embedding": embed.embed_documents([text])[0],
    }
    db.upsert("sf_items", [item])
    return {"ok": True, "id": item["id"]}


# ---- picks and export -----------------------------------------------------

class Pick(BaseModel):
    slide_id: str = Field(max_length=80)


@app.get("/api/picks", dependencies=[Depends(require_login)])
def get_picks():
    picks = db.select("sf_picks", {"order": "position"})
    ids = [p["slide_id"] for p in picks]
    rows = {r["slide_id"]: r for r in db.select("sf_slides", {"slide_id": f"in.({','.join(ids)})"})} if ids else {}
    thumbs = db.signed_urls([rows[i]["thumb_path"] for i in ids if i in rows])
    return [{**p, "slide": rows.get(p["slide_id"]), "thumb": thumbs.get(rows.get(p["slide_id"], {}).get("thumb_path", ""))}
            for p in picks if p["slide_id"] in rows]


@app.post("/api/picks", dependencies=[Depends(require_login)])
def add_pick(body: Pick):
    if db.select("sf_picks", {"slide_id": f"eq.{body.slide_id}"}):
        return {"ok": True, "already": True}
    db.insert("sf_picks", {"slide_id": body.slide_id})
    return {"ok": True}


@app.delete("/api/picks/{position}", dependencies=[Depends(require_login)])
def remove_pick(position: int):
    db.delete("sf_picks", {"position": f"eq.{position}"})
    return {"ok": True}


@app.get("/api/export.pdf", dependencies=[Depends(require_login)])
def export_pdf():
    import fitz  # PyMuPDF, imported here so the function starts fast
    from PIL import Image  # MuPDF cannot decode WebP, so Pillow converts each slide first

    picks = get_picks()
    if not picks:
        raise HTTPException(400, "nothing picked")
    doc = fitz.open()
    for p in picks:
        im = Image.open(io.BytesIO(db.download(p["slide"]["image_path"]))).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        page = doc.new_page(width=im.width * 0.5, height=im.height * 0.5)  # 1600 px wide -> 800 pt
        page.insert_image(page.rect, stream=buf.getvalue())
    out = io.BytesIO(doc.tobytes(garbage=3, deflate=True))
    return StreamingResponse(out, media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="picked-slides.pdf"'})


# ---- add a deck from the browser ------------------------------------------

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "deck"


class DeckBegin(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    pages: int = Field(ge=1, le=300)


@app.post("/api/decks/begin", dependencies=[Depends(require_login)])
def deck_begin(body: DeckBegin):
    deck_id = f"{_slug(body.name)}-{secrets.token_hex(3)}"
    uploads = [{"page": n, "path": f"uploads/{deck_id}/{n:03d}.webp",
                **db.signed_upload_url(f"uploads/{deck_id}/{n:03d}.webp")} for n in range(1, body.pages + 1)]
    return {"deck_id": deck_id, "uploads": uploads}


class DeckPage(BaseModel):
    page: int
    title: str = Field(max_length=300, default="")
    text: str = Field(max_length=8000, default="")


class DeckFinish(BaseModel):
    deck_id: str = Field(max_length=80, pattern=r"^[a-z0-9-]+$")
    name: str = Field(max_length=120)
    course: str = Field(max_length=20, default="mine")
    course_name: str = Field(max_length=120, default="My decks")
    term: str = Field(max_length=40, default="added")
    pages: list[DeckPage]


@app.post("/api/decks/finish", dependencies=[Depends(require_login)])
def deck_finish(body: DeckFinish):
    session = int(time.time()) % 100000  # unique enough per upload; not a real session number
    slides, items = [], []
    for pg in body.pages:
        sid = f"{body.deck_id}-{pg.page:03d}"
        title = pg.title.strip() or (pg.text.strip().splitlines() or [""])[0][:120]
        path = f"uploads/{body.deck_id}/{pg.page:03d}.webp"
        slides.append({"slide_id": sid, "course": body.course, "course_name": body.course_name, "term": body.term,
                       "session": session, "session_title": body.name, "slide_number": pg.page, "title": title,
                       "text": pg.text, "image_path": path, "thumb_path": path, "used_in": [body.term]})
        items.append({"id": f"{sid}-card-0", "slide_id": sid, "kind": "card", "term": body.term,
                      "occurrence_id": None,
                      "text": card_text(body.course_name, body.name, pg.page, title, pg.text, "")})
    vecs = embed.embed_documents([i["text"] for i in items])
    for i, v in zip(items, vecs):
        i["embedding"] = v
    db.upsert("sf_slides", slides, on_conflict="slide_id")
    db.upsert("sf_items", items, batch=100)
    return {"ok": True, "slides": len(slides)}


# ---- static page (local dev; on Vercel, public/ is served before the function) -----------

app.mount("/", StaticFiles(directory=PUBLIC, html=True, check_dir=False), name="static")
