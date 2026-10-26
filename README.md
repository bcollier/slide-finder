# Slide Finder

Find my own slides by meaning. Ask "how do I predict demand in the future" and get the slides
where I taught it, ranked, with what I said in class while each one was on screen.

Live (passcode): https://slide-finder.vercel.app

Built for CMU 15-114 HW6 (Build a Vector Database App), October 26, 2026.

<!-- Ben: sections 1 to 4 are yours to write in your own words. Notes in brackets are what the
assignment asks each one to cover; delete them when done. -->

## 1. What it does and why I built it

[1 to 2 sentences. Why: Faculty Twin's retrieval was poor; I want to find my own slides and
what I said about them, across every term, when building a new deck.]

## 2. What is in the database

[Where the data came from (my lecture archive; phase 1 used the slide images, text and
de-identified transcript alignments I had already processed for Faculty Twin, as input data
only). What one item is: a slide has several items, a "card" (deck, session, title, slide text,
speaker notes), a description of the image written by a vision model, ~120-word chunks of what I
said over it, and notes I add. How many: 3,171 unique
slides from 5,900 deck pages (five courses, every term since Fall 2023; 2,729 pages were repeats of a
slide from another term), 4,733 searchable items. Metadata: course, term, session, slide number, which terms
used the slide. Repeated slides across terms are grouped into one.]

## 3. Adding and searching

[Add: a note on any slide, searchable immediately; a whole PDF deck from the browser. Search: type
a question; meaning and keyword matches are fused; results are grouped per slide with scores;
weak matches are flagged. Then: open the slide, read what I said, pick slides, export a PDF.]

## 4. Two queries that keyword search would miss

[Required. Fill from the tested list:]

| Query | Slide it returns | Words in common |
| --- | --- | --- |
| it fits the data it has seen perfectly and falls apart on anything new | "The sweet spot, a balance of bias and variance" (70-445, Machine Learning Fundamentals, slide 67), matched through what I said over it | none |
| how does a computer read a photo | "Images are Numbers" (45-884, Computer Vision Fundamentals, slide 29) | none |
| too many numbers on one screen, what should I cut | "Identify and Eliminate Clutter" (45-885 Data Visualization, session 4; used in 2 terms) | none |

[One sentence on why keyword search fails: the question uses everyday words and the slide uses
the technical term; nothing overlaps.]

Others that work: "why does the chatbot make things up" → Hallucinations; "teach the machine by
rewarding it when it does well" → Reinforcement Learning with Human Feedback; "how are the words
in a sentence turned into numbers" → Tokens In, Vectors Inside, Tokens Out.

One that does not: "it aced the practice problems but bombed the real test". The embedding reads
it as being about exams. See `eval/step5_distances.md`.

## 5. Retrieval scores

Measured with `eval/run.py` on 28 labeled questions (private; aggregates only here):

| Mode | hit@1 | hit@5 | recall@10 | MRR | off-topic flagged |
| --- | --- | --- | --- | --- | --- |
| hybrid (meaning + keywords) | 0.68 | 0.96 | 0.80 | 0.78 | 4/6 |
| meaning only | 0.57 | 0.93 | 0.78 | 0.73 | 4/6 |
| keywords only | 0.07 | 0.07 | 0.04 | 0.07 | 6/6 |
| hybrid with a 15% bonus for slides matched by both card and transcript (the first version) | 0.57 | 0.93 | 0.78 | 0.72 | 4/6 |

What the numbers say: the bonus I had added by intuition made ranking worse, so it is off.
Keywords alone are nearly useless on these questions because Postgres requires every word of the
question to appear, and real questions are long; the keyword side only contributes on short
queries with exact terms, which is what it is for. Two off-topic questions slip past the 0.55
cutoff. The one question missed in every mode asks how to evaluate a RAG chatbot; the relevant
slides are code-heavy and will need the image descriptions.

## 6. Stack, and running your own copy

- **Vector database:** Supabase Postgres with the `pgvector` extension. Embeddings are stored as
  `halfvec(1024)` with an HNSW cosine index; a generated `tsvector` column gives keyword search.
  `sf_search` fuses the two by reciprocal rank in one SQL function.
- **Embedding model:** Voyage AI `voyage-3.5` (1024 dimensions), called over HTTP. Nothing
  downloads on first run; you need a Voyage key (free tier is plenty).
- **App:** FastAPI (`slidefinder/app.py`) and a static page (`public/`), deployed on Vercel. One
  passcode, set as `SF_PASSCODE`.
- **Slide images:** a private Supabase Storage bucket, served through short-lived signed links.

To run it yourself:

1. Create a free Supabase project. In the SQL editor, run `supabase/migrations/001_schema.sql`.
   In Storage, create a private bucket named `slide-finder`.
2. Copy `.env.example` to `.env` and fill in `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `VOYAGE_API_KEY`, and an `SF_PASSCODE`.
3. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. `.venv/bin/uvicorn slidefinder.app:app --port 8000` and open http://localhost:8000.
5. Add data: click **Add deck** and choose any PDF. The browser renders the pages and the
   server indexes them; they are searchable as soon as it finishes. My own archive is not in the
   repo (see `.gitignore`); `slidefinder/load.py` and `slidefinder/archive.py` are the loaders I
   use for it and expect my folder layout.

## 7. How I used AI

See `prompt_log.md`. Claude Code (Claude Opus 5.5, then Claude Fable 5.1) wrote the code from my
descriptions, one step at a time; I tested each deployed version in the browser and sent back what
was wrong. The "where it got it wrong" list is in the prompt log.

## Privacy

Slide images and transcripts are mine; transcripts were de-identified before any of this (only my
own lines are indexed). Nothing from the archive, the database, or `.env` is committed.

<!-- AI-generated documentation below this line -->

## Appendix: how search works (AI-generated)

1. The question is embedded once (Voyage, `input_type=query`).
2. `sf_search` returns the 50 closest items by cosine distance and the 50 best keyword matches
   (`websearch_to_tsquery`), each with a rank; the fused score is `1/(60+rank)` summed over both.
3. Items are merged per slide: the slide takes its best item's score and remembers which item
   matched best ("why"). Item kinds: card, desc (image description), said, note. Keeping the
   description as its own item mattered: folded into the card it diluted the slide's own words
   and changed which slide ranked first.
4. If the best cosine distance is above 0.55 the page says "I don't think I taught this" and shows
   the closest slides marked weak. The number comes from `eval/step5_distances.md`: on-topic best
   distances were 0.46 to 0.50, off-topic 0.58 to 0.60.
