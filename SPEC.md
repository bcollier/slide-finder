# Slide Finder: spec

Status: draft, Oct 26, 2026. Due Mon Oct 26, 7:30 PM (my deadline; the course deadline is 8:00 PM).
Course: CMU 15-114 HW6, Build a Vector Database App.

## 1. What it is

A command-line tool for finding my own teaching material by meaning. I type a question like
"how do we do segmentation" or "how do I use a random forest", and it returns the slides where I
taught that, ranked, with what I said while each slide was on screen. Later, when I'm building a new
deck, I can search "sprints and team design" or "working with AWS" across every course I've taught
and pull the right slides into the new deck.

This is a small, separate proof of concept. Its job is to get retrieval right, and to prove it with
numbers, before any of it goes back into a bigger app.

## 2. Why a vector database earns its place

People (including me) never phrase the question with the words on the slide. Two test queries,
to confirm in Step 5 against the real index:

| Query | Should return | Shared words |
| --- | --- | --- |
| "it aced the practice problems but bombed the real test" | "Generalization and Overfitting" (70-445 s05, slide 70; also 45-851 in phase 2) | none |
| "which shoppers behave alike" | "Customer Segmentation" and the clustering slides (70-445 s05, slides 31 to 40; also 45-851 in phase 2) | none: the slides say "customers", "segmentation", "clusters" |

Backups: "the computer reads the photo" (computer vision / CNN slides in 45-884); "stop it from
memorizing" (cross-validation / regularization).

Dropped after checking the slide text in Step 3: "how do we do segmentation". The clustering deck
has a slide titled "Customer Segmentation", so keyword search would find it too. It is still a
good everyday query, just not proof. The same goes for "great on training data", since the
variance slide says "training data".

Step 3 result (15 hand-typed cards, default model): the overfitting query put the bias-variance
slide first with zero shared words.

Keyword search fails because the slide text uses the technical term and the question uses
the everyday one.

## 3. What one item is

The unit I want back is **one slide**. Every slide is stored as one or more items, all tagged
with the same `slide_id`, and search results are merged per slide.

| Item kind | Document text (what gets embedded) | Why |
| --- | --- | --- |
| `card` | Deck title + slide title + a written description of what the slide shows and teaches + the slide's own text and speaker notes | Finds the slide by what it is about, including picture-only slides that have almost no text |
| `said` | A ~120-word chunk of what I said while that slide was showing, prefixed with the slide title | Finds the slide by how I explained it in class, which is often closer to how a student asks |
| `note` | Free text I add at runtime ("used this in the Deloitte workshop", "good opener for AWS") | My own annotations become searchable |

All three kinds stay well under the default model's 256-word-piece limit. Text past that limit is
dropped without warning, so long transcript windows get split, never stored whole.

Metadata on every item: `slide_id`, `kind`, `course` ("45-851"), `course_name`, `term`
("2025 Fall (A2)"), `session` (number and title), `slide_number`, `deck_path`, `image_path`,
`content_hash`.

### Repeated slides

The same slide shows up in many terms (Data Mining has six with decks). One copy is indexed,
and every term that used it is kept as an **occurrence**. Otherwise the top 10 would be the same
k-means slide six times.

Exact hashes are too strict, because a slide that changed one word or the year between terms
should still count as the same slide. Two slides are the same when both are true:
- their card text embeddings are near-duplicates (cosine similarity ≥ 0.95, tuned in Step 5 by looking at pairs), and
- their images are close (difference hash, Hamming distance ≤ 10 of 64 bits).

This is a second use of the vector database: near-duplicate detection across terms.

### What I said about a slide, term by term

For any slide, I can see the words I actually said over it each time I taught it, newest first.
The default is the last 4 terms.

Example of the layout (the times and quotes below are placeholders, not real output):

```
> history 2
Generalization and Overfitting: The Bias-Variance Tradeoff
45-851 Data Mining · used in 5 terms · I have recordings for 3

2025 Fall (M2) · s04 Classification · 00:41:10–00:43:55 (2m45s) · matched by video
  "So here's the thing that gets everybody. Your model can look perfect on the data
   you trained it on, and then you hand it something new and it falls apart..."

2024 Spring (M4) · s15 Overview of classification · 00:18:02–00:19:30 · matched by video
  "..."

2023 Fall (AB2) · s03 Classification II · 00:52:44–00:55:10 · matched by text (lower confidence)
  "..."

2025 Spring (M4), 2024 Fall (AB2): used this slide, no transcript yet
```

How the words get attached to a slide, per recorded session:

1. **Load my lines only.** Read the de-identified timed transcript (`*.cleaned.timed.jsonl` /
   `transcript_clean.timed.jsonl`), keep cues with `speaker == "instructor"`, and drop `student`
   and `unclear`. Raw `.vtt` files are never read.
2. **Find when each slide was on screen.**
   - *By video (preferred):* take a frame every 2 seconds at low resolution with ffmpeg and
     compare it with that session's slide images (difference hash, plus a small grayscale
     correlation to break ties). Smooth the sequence so a single odd frame can't flip the
     slide, and allow going back to an earlier slide. The output is time windows per slide.
   - *By text (fallback when there is no video, or the video match is weak):* split the transcript
     into 30-second windows and embed them. Pick the best in-order assignment of windows to
     slides, by similarity to each slide's card, with a penalty for jumping around. Results are
     labeled lower confidence.
3. **Cut the words into windows.** Instructor cues that fall inside a slide's on-screen windows
   become that occurrence's transcript. Slides shown for less than 8 seconds get no text (I was
   clicking past them).
4. **Store it twice.**
   - `build/occurrences.jsonl` has one row per (slide, term, session): page number, time
     windows, method, confidence and full text. `history` reads this file.
   - `said` items in Chroma (~120-word chunks with `term` and `occurrence_id` metadata) make the
     words searchable, so a query can match how I said it in 2023 even if the slide text never
     changed.

Spot-check before trusting it: for 3 sessions, open the recording at 5 random windows and
confirm the slide on screen. Write the hit rate in the README.

Stretch: **`compare 2`**, one LLM call that reads the term-by-term text and says in three bullets
how my explanation changed: new examples, things I stopped saying, and how long I spent on it.

## 4. Data

### Two phases, for speed

**Phase 1 (graded tonight): Faculty Twin's processed slides as the input dataset.** These are
already built, de-identified and aligned:

| Course | Sessions | Slides | Slides with what I said |
| --- | --- | --- | --- |
| 45-884 AI Methods for Social and Visual Data, Fall 2026 | 7 | 402 | 319 |
| 70-445 AI for Business Leaders, Fall 2026 | 10 | 576 | 420 |
| **Total** | **17** | **978** | **739** |

The data is read from `~/Lecture Archive/_build`. Nothing from faculty-twin's code is imported.

| File | Gives |
| --- | --- |
| `slides/<course>/sNN/slides.json` | slide id, title, text, speaker notes, flags, image and thumbnail paths |
| `slides/<course>/sNN/deck.json` | session title and date |
| `slides/<course>/sNN/*.webp` | slide images |
| `align/<course>/sNN.json` | per slide: on-screen time windows (matched from video frames) and the de-identified transcript of those windows |

This dataset is from one term only, so phase 1 builds the per-slide transcript view, but with one term in it.

**Phase 2 (right after, starting with Data Mining): the raw archive.** Decks, transcripts and
video from every term, using the pipeline below. This is what makes "history across terms" real.
The loader writes the same record shape as phase 1, so search, `history` and the CLI don't change.

### Raw archive (phase 2)

Source: my lecture archive (`LECTURE_ARCHIVE`, default `~/Lecture Archive`), one folder per
course, term and session, each with `slides.pdf` and/or `slides.pptx`. Many slides repeat
across terms.

| Input | Used for | Notes |
| --- | --- | --- |
| `slides.pdf` | Slide images (rendered with PyMuPDF) and page text | Preferred when present |
| `slides.pptx` | Speaker notes, and the source when no PDF exists (converted with LibreOffice) | |
| De-identified timed transcripts (`*timed.jsonl`), instructor lines only | `said` items and `history` | Never raw `.vtt`: those have student names and voices |
| Session video (`video.mp4`) | When each slide was on screen | Frames only; nothing from the video is stored except slide time windows |
| My recorded scripts (`*.cleaned.md`, no timestamps) | `said` items, matched by text | Async module videos |

Coverage for "what I said" (sessions that have both a deck and a timed transcript):

| Course | Terms with transcripts | Sessions |
| --- | --- | --- |
| 45-851 Data Mining | 2023 Fall (11), 2024 Spring (5), 2025 Fall M2 (1) | 17 |
| 45-884 AI Methods | 2025 Fall (5), 2026 Summer (6), 2026 Fall (7) | 18 |
| 70-445 AI for Business Leaders | 2026 Fall (10) | 10 |
| 45-885 Data Visualization | 2024 Spring, 2026 Spring | 2 |
| 46-887 ML for Business Applications | 2026 Spring | 1 |

Later: Data Mining 2024 Fall (12 videos) and 2025 Spring (16 videos) have recordings but no
transcript. Transcribing them locally (Whisper) and de-identifying them would give Data Mining
five terms of history. That is not in this version.

**Scope for this version: the five core courses I teach now, every term in the archive.**
Older and occasional courses (Probability and Statistics, Data Exploration and Visualization,
Managing Tech Talent, ML Foundations with Python, Math Skills Workshop) come later through `add deck`.

| Course | Terms with decks | Sessions | PDF pages (approx.) |
| --- | --- | --- | --- |
| 45-851 Data Mining | 6 (Fall 2023 to Fall 2025) | 63 | 1,990 |
| 45-885 Data Visualization | 5 (Spring 2024 to Spring 2026) | 35 | 1,390 |
| 45-884 AI Methods for Social and Visual Data | 3 (Fall 2025 to Fall 2026) | 28 | 1,230 |
| 70-445 AI for Business Leaders | 1 (Fall 2026) | 10 | 580 |
| 46-887 Machine Learning for Business Applications | 1 (Spring 2026) | 7 | 270 |
| **Total** | | **143** | **about 5,450** |

Pages are counted from PDFs only (sessions with only a pptx add a few more). Every term is
included, since repeated slides collapse to one copy (see "Repeated slides") with all of its
terms listed. The estimate is 2,500 to 3,500 unique slides after merging; Step 4 measures it.
Data Mining terms with no decks yet (Fall 2026) are skipped.

**Privacy rules (hard):**
- Nothing from the archive is committed: no slide images, transcripts, or `chroma_store/`.
- No student names, faces, or voices in the index, the README, or the video.
- Guest-speaker sessions (`_build/guest_sessions.json`) and the Tesla vs Waymo case are skipped.

**For graders:** the tool never requires my archive. `slides add <any.pdf>` works on any deck.
The README will name a public sample (to pick: a few of my own decks that contain no student
material, published as a release download, or an openly licensed course deck set) and give the
two commands to load it.

## 5. What it does with the results

Printing the top three raw would earn a B. The app does this instead:

1. **Merge per slide.** A slide's score combines its best `card`, `said`, and `note` hits
   (best hit, plus a small bonus when more than one kind matched). Return the top slides, not
   the top chunks.
2. **Group and explain.** Show results grouped by deck: course, term, session, slide number, a
   one-line "why it matched" (the item that matched best, quoted briefly), and the terms it was
   used in.
3. **Say when nothing fits.** If the best distance is past a cutoff picked from real numbers in
   Step 5, the app says "I don't think I taught this" and shows the closest one anyway, marked as weak.
4. **Open it.** `open 3` opens result 3's slide image (or the deck at that page).
5. **Collect for a new deck.** `pick 1 3 4` adds slides to a working set. `export new-deck.pdf`
   writes those pages, in order, into one PDF I can start a new deck from. The set survives
   restarts.
6. (Stretch) **Short answer.** One LLM call writes two or three sentences on how I taught this,
   using only the retrieved slides and transcript text, with slide references.

## 6. Adding items at runtime

| Command | What happens | Searchable |
| --- | --- | --- |
| `add deck <path.pdf\|.pptx> [--course X --term Y]` | Renders, extracts text, writes descriptions, merges duplicates, indexes | immediately |
| `add note <result#\|slide_id> "text"` | Stores a `note` item on that slide | immediately |
| `add transcript <file> --deck <deck>` | Chunks the transcript and matches the chunks to slides | immediately |

IDs are deterministic (`<content_hash>-<kind>-<n>`), and writes use `upsert`. Re-adding the same
deck updates it instead of silently skipping or duplicating it. (`add` with an existing ID is
ignored without any error.)

## 7. Retrieval: getting it right, and proving it

Faculty Twin's retrieval missed often. The main suspected causes, each one tested here instead of
assumed:

| Suspected cause | Change in this project | Test |
| --- | --- | --- |
| Picture-heavy slides had almost no text to embed | `card` text includes a written description of the image | Score with and without descriptions |
| One vector per slide mixed slide text and transcript | Separate `card` / `said` items, merged per slide | Score card-only vs said-only vs both |
| Long text cut off at the model limit | Chunks under 256 word pieces; length check in the indexer | Count the items that were truncated (should be 0) |
| Embedding model choice | Compare Chroma's default (all-MiniLM-L6-v2) with `BAAI/bge-small-en-v1.5`, plus Voyage if time allows | Same eval set, each model in its own collection |
| Exact terms like "AWS" or "SMOTE" get blurred by embeddings | Optional keyword pass fused with the vector ranks (reciprocal rank fusion) | Score with and without |

**Eval set (phase 1):** Faculty Twin's labeled course questions: 28 with their correct slides
(`expected_slides`) and 6 off-topic ones that should come back "not taught". They're read from
`FT_EVAL_PATH`. The eval questions stay private: they're never copied into this repo, and only
aggregate scores go in the README. This also gives a direct before/after against Faculty Twin's
retrieval on the same questions.

**Eval set (phase 2):** about 15 more of my own questions for Data Mining, labeled with the `judge`
command. It shows the top 10 for a question and lets me mark the good ones by number.

**Metrics:** hit@5 (a good slide in the top 5), recall@10, and MRR. `slides eval` prints one row per
configuration, and the winning configuration becomes the default. The table goes in the README.

Distance: Chroma defaults to squared L2. Collections here are created with cosine distance
(`configuration={"hnsw": {"space": "cosine"}}`), so 0 means identical and the cutoff in
section 5 reads as 1 minus cosine similarity.

Two collections can't share embeddings from different models, so each configuration is its own
collection (`slides__minilm`, `slides__bge`, ...).

## 8. Slide descriptions

For each unique slide, a vision model gets the slide image, the slide text and notes, the deck and
session title, and the titles of the slides before and after it. It returns about 60 to 90 words: what the
slide shows (chart type, diagram, code, photo), what concept it teaches, and where it sits in the
lesson. Descriptions are cached by `content_hash` in `cache/descriptions.jsonl`, so a re-run
costs nothing.

- Model: Claude Haiku 4.5 (fast and cheap for images). Key in `.env`, never committed.
- Cost guard: count the unique slides first and print the estimate; stop if it is over $15.
- Transcript text sent to the model must already be de-identified.
- Without a key, `card` falls back to slide text + notes, so graders can run without one.

## 9. Stack

- Python 3.11+, `chromadb` (1.5.x, `PersistentClient(path="chroma_store")`). No `Settings(...)`
  client and no `.persist()`: both are from old versions.
- PyMuPDF for rendering and text, python-pptx for notes, LibreOffice for pptx-only decks.
- `sentence-transformers` for the bge comparison. Models download on first run.
- `anthropic` for descriptions (optional).
- CLI: a menu loop (`python -m slidefinder`) plus one-shot subcommands
  (`slides ask "..."`) so it can be scripted and timed.

```
slidefinder/
  ingest.py     # archive walk, render, text + notes, dedup
  describe.py   # vision descriptions + cache
  chunk.py      # transcript chunks, slide matching
  store.py      # Chroma collections, add/upsert, query
  search.py     # merge per slide, cutoff, grouping
  collect.py    # pick / export PDF
  cli.py
eval/
  questions.jsonl, run.py
```

## 10. Build order (mapped to the HW steps)

Built one step at a time, checking each one before the next. The times are budgets.

| # | Step | Done when | Budget |
| --- | --- | --- | --- |
| 1 | Idea, data, two queries | This spec | done |
| 2 | Install Chroma, five-line check | Imports, creates a collection | 15 min |
| 3 | Persistent smoke test | 15 hand-typed slide cards survive a restart | 15 min |
| 4 | Load phase 1 (Faculty Twin build: 978 slides, 739 with transcript) as `card` + `said` items | Counts match, timing for 50 printed, 0 truncated items | 30 min |
| 5 | Query and look at the distances | Both test queries checked; on-topic and off-topic distance ranges written down | 25 min |
| 6 | Runtime add: `add deck`, `add note` | Added item shows up in the very next search | 20 min |
| 7 | Do something: merge per slide, group, cutoff, open, pick, export | A new PDF comes out of three picks | 45 min |
| 8 | Descriptions for the 978 slides (background run, texts me when done) and the `history` command on phase 1 | Every slide has a description; `history` shows what I said | 30 min |
| 9 | Eval on the 28 + 6 questions, compare configs, Faculty Twin as baseline | Table of hit@5 / MRR per configuration | 40 min |
| 12 | Phase 2: Data Mining raw decks, near-duplicate grouping, instructor lines, video alignment, text fallback, then the other courses | `history` shows 3 Data Mining terms for the overfitting slide; spot-check written down | after submission, or tonight if time allows |
| 10 | README (first four sections in my own words), prompt log | Two separate files | 30 min |
| 11 | Video, push, portfolio, form | Link works in an incognito window | 30 min |

Cut line if time runs short: drop Voyage, keyword fusion, the LLM answer, `compare`, and the
text-matching fallback (history then covers sessions with video only). Steps 1 through 7, step 10 and step 11 are the graded core.

## 11. Open decisions

| Decision | Recommendation |
| --- | --- |
| Faculty Twin was turned in for 15-113. The HW says new standalone project. | **Decided:** phase 1 uses Faculty Twin's processed slides and alignments as **input data only**. All code is new, nothing is imported, and the README says plainly where the data came from. |
| Public sample for graders | Two or three of my own decks with no student material, as a release download |
| API key for descriptions | Anthropic key in this repo's `.env`, or the course's OpenAI credits |
| Repo visibility | Private until submission, then public (or add the TAs) |

## 12. Not in this version

A web UI or cloud hosting (planned next: Chroma Cloud plus a small web page), Faculty Twin
integration, editing or deleting slides, and video clips.
