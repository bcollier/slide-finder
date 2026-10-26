# Prompt log: Slide Finder (15-114 HW6)

**Tools and models.** Everything was built in Claude Code (the terminal agent) on October 26, 2026.
The first half of the session ran on Claude Opus 5.5; I switched the model to Claude Fable 5.1
about two hours in, after a run of poor design recommendations (see the entry marked "switched
models"). No other AI tools were used. Voyage AI (`voyage-3.5`) is the embedding model inside
the app; it is not a coding tool.

**How I worked.** I described the project in my own words first and did not give the assignment
page to the agent until after the spec was drafted. Then I had it build one step at a time:
spec, install and five-line check, persistent smoke test, real data, look at the distances,
runtime adds, do something with the results, deploy, evaluate. I tested each deployed version in
my browser and sent screenshots back when something was wrong. Prompts below are verbatim; my
typos are left in. Passcodes and keys never appear in prompts or in this log.

Time on the project: roughly 12:20 PM to 7:30 PM, with breaks, about 6 hours.

---

## 1. The idea (12:30 PM)

> ok, I want to build something small, that is similar to faculty twin but better retrieval of
> slides and content. I want to pull in all of the classes I teach and all of the transcripts from
> all of the recordings. Break up the transcripts into chunks along with their associated slides.
> When a user submits a question such as how do we do segmentation or clustering or how do I use a
> random forest it will go to the best slides about that topic. I also want to eventually add slide
> decks from my old classes, and have text that describes exactly what is in each slide and its
> context, that way I can be building a new deck and find the slide where I talked about sprints
> and team design or working with AWS. let's start building a spec, and a new repo for this
> project. I realize it's like faculty twin, but this is a small proof of concept for me to find
> my own slides/content and to make sure it is doing it the best way possible. the retrieval in
> the faculty twin was poor. start spec for this. it is due at 7:30pm on Monday 26th

The agent inventoried my lecture archive (about 1,450 decks across 10 courses, 690 transcript
files) and drafted `SPEC.md`. I then gave it the assignment link:

> as you build here is the assignment text https://www.cs.cmu.edu/~113/hw6.html

## 2. Scoping (12:40 PM)

> for now spec to the core courses I teach, data mining, data visualization, ai methods, ai for
> business, ml for business applications. we can add old courses later.

> do you have a repo started

> go

Step 2: Python 3.12 venv, `chromadb` 1.5.9 installed, `scripts/check_chroma.py` creates a
persistent collection. Step 3: `scripts/smoke_test.py` adds 15 hand-written slide cards from my
Data Mining deck and finds them again in a second process. The agent's first test query ("how do
we do segmentation") turned out to share a word with a slide title, so we replaced it; the lesson
"everyday phrasing, not shared words" stuck for the rest of the project.

## 3. Design changes I asked for (1:00 PM)

> in the design I would like to be able to see what I said about a slide in past versions of the
> course. for example, looking at a data mining slide I want to be able to see the actual text I
> said over that slide for the past 4 times I taught it

> does the assignment say it has to run locally? i would rather run it in the cloud

> for the sake of speed, could we use the data from faculty twin for this assignment, then pull
> the rest in since this is due soon

> I still want this to run in the web, I won't want to have to use it on the mac mini vs laptop,
> just build for web now

The agent recommended Chroma Cloud. I already pay for Supabase:

> we are not required to use chroma cloud it is just recommended, I already have supabase so would
> prefer to use that if they are comparable. will this work for adding all of my content to?

It then proposed keeping Chroma as a second backend "for graders". That was the point where I
switched models:

> why do graders need to access the database, they don't just use supabase. you are doing a shitty
> job of giving me recommendations. I don't know why. I'm turning on fable, explain why you keep
> giving bad suggestions.

The new model's answer (summarized): it had invented a grading constraint, kept Chroma out of
sunk cost, kept adding scope instead of cutting, and proceeded on defaults I had not approved.
Design became one backend: Supabase Postgres with pgvector, Voyage embeddings, FastAPI on Vercel,
a passcode, and a README that tells anyone how to run their own copy on a free Supabase project.

## 4. Build (1:05 PM to 2:00 PM)

> ok to everything, start building until you have a working prototype for me to test

> I can't see any progress are you working

What was built, in order, each piece run and checked before the next:

1. `supabase/migrations/001_schema.sql`: `sf_slides`, `sf_items` (with `halfvec(1024)` and a
   generated `tsvector`), `sf_occurrences`, `sf_picks`, the `sf_search` function (vector top 50
   + full-text top 50, reciprocal rank fusion), and `sf_exec`. I ran it in the Supabase SQL
   editor; the agent could not run DDL through the REST API.
2. `slidefinder/load.py`: reads the 978 processed slides and 739 aligned transcripts from my
   Faculty Twin build (input data only; no code reused), builds one "card" per slide and ~120-word
   "said" chunks, embeds with Voyage, upserts. 50 slides took 4.2 s; all 942 (one case session
   skipped) took 57 s.
3. `slidefinder/search.py`: merges items per slide, cutoff for "I don't think I taught this".
4. `slidefinder/app.py` + `public/`: search, slide view, notes, picks, PDF export, deck upload.
5. Vercel deploy.

Prompts during this stretch:

> which SQL?

> you did not point me to the sql, tell me the file name or something

> in the future the sql link should be a CHROME link not something that opens in claude, and next
> time just give me these two direct links, take the file here and run it here. practice for me.
> keep this in memory

## 5. Where the AI got it wrong

- **PostgREST batch inserts.** The first load failed with "All object keys must match": card
  items had no `occurrence_id` key while transcript items did. Fixed by writing the key as null.
- **Vercel's `public/` folder.** The function crashed on import with "Directory '/var/task/public'
  does not exist". Vercel serves `public/` as static files and leaves it out of the function
  bundle. The first fix (rename to `web/`) worked but was the wrong lesson; the right one was to
  use Vercel's zero-config FastAPI entry (`main.py`) and let `public/` be static, with no rewrites.
- **PyMuPDF and WebP.** "Export PDF" returned 500: MuPDF cannot decode WebP, which is the slide
  image format. Fixed by decoding with Pillow first. The agent's own local test then failed
  twice for reasons unrelated to the bug (an async body iterator, then a `secure` cookie over
  plain http) before it tested correctly over https.
- **A test query that could not work.** "it aced the practice problems but bombed the real test"
  never reaches the bias-variance slide: the embedding reads it as being about exams. Replaced
  with "it fits the data it has seen perfectly and falls apart on anything new", which ranks it
  first. Written up in `eval/step5_distances.md`.
- **Duplicate grouping, first version.** Picture-only pages all get near-identical card text (just
  the header), so an embedding-similarity rule merged different images, and a loose 64-bit image
  hash merged any two slides that shared a template ("Natural Language Processing" with "Quiz
  Access Code"). 317 pages collapsed to 206. Rewritten with separate rules for text-bearing and
  picture-only pages and a 256-bit hash; 261 unique slides, and the merged pairs are real repeats.
- **Where to put the image description.** The first version appended the vision model's 80-word
  description to each slide's card before embedding. "too many numbers on one screen, what should
  I cut" then ranked "Tufte's Five Laws of Data-Ink" above "Identify and Eliminate Clutter", which
  had been first. Long cards dilute the slide's own words. Descriptions became their own item kind
  (`desc`), embedded separately; the slide takes whichever item is closest.
- **PostgREST's 1000-row cap.** The course dropdown showed one course after Data Mining loaded:
  Supabase caps a plain select at 1000 rows and `limit=` cannot raise it. Fixed with Range-header
  pagination (`db.select_all`). The same cap would have silently skipped 169 Data Mining slides in
  the describer.
- **A persistent 1Password-style mistake of mine, not the AI's:** the generated passcode was 14
  characters and I kept typing 10. I set my own.

## 6. Testing the prototype (2:00 PM onward)

> i keep typing the password and its not working

> i changed it

> for this slide, what is the text description of it? how was it matched?

> I want to be able to see the full text that is the descriptoin, I also want to see the matching
> scores for each slide. I also want to see 30 or so slides, adjustable by slider

> export didn't work

> give me 10 good questions for vector search on my material. i like "how to I group customers
> into meaningful segments? etc. how do I predict demand in the future?

> put those questions her

> import all content for data visualization, data mining, ml for business applications

The last prompt started phase 2: `slidefinder/archive.py` renders every deck page from the raw
archive (PyMuPDF, LibreOffice for pptx-only decks), pulls text and speaker notes, and
`slidefinder/load_archive.py` groups near-duplicate slides across terms before loading one copy
with a list of every term that used it.

## 6b. Attaching what I said to old decks (2:30 PM)

No video alignment existed for Data Mining, so the agent built the text fallback from the spec:
my lines only (the de-identified transcripts label speakers), 45-second windows, matched in order
to the deck. The first dry run on Clustering I (2023 Fall) was wrong in an instructive way: every
window-to-slide similarity sat between 0.63 and 0.91, one generic slide collected the first 40
minutes, and slides 1 to 29 got nothing. Two causes, two fixes. Document-to-document cosines are
uniformly high, so the matrix is double-centered (subtract each window's and each slide's mean).
And slides 1 to 29 were picture-only (the spaghetti-sauce story told over photos), so the slide
side now embeds the card plus its image description. A soft time prior (a window at 30% of the
lecture should be near 30% of the deck) settled the rest. Result on that session: 81 of 134
windows placed on 32 of 55 slides, in deck order. Windows that match nothing (admin, Q&A) are
dropped rather than forced onto a slide.

## 6c. Versions of the same slide (3:55 PM)

> I get multiple versions of the same slide, how do we change that so it's smart enough to
> understand these are different versions

A search for customer segmentation showed the same "Customer Segmentation" slide three times,
from three terms. The grouping rule had required a close whole-page image hash for short
identical text, and each term uses a different deck template, so the same slide hashed
differently every year. Relaxing to "identical text is the same slide" was measured and rejected:
it merged 22 different "What's Wrong with this Picture?" pages and 12 different charts credited
to the same author. The fix hashes the embedded pictures themselves (they survive a template
change), and compares those: Data Mining went from 1,168 to 879 unique slides, with the merges
checked by eye on contact sheets (the wrong merges that showed up along the way, a stock picture
under different titles and the steps of a build-up sequence, each got a rule). Three courses
regrouped and reloaded.

> the about in the repo says it uses chrom

The GitHub description was stale from Step 2 (Chroma); corrected.

> still a problem, I am fine with them being returned, but there should be some smart logic to
> say these top three slides are from the same course. put the cards on top of each other at
> the number one spot, and clearly label their difference sections as Fall 2026, Fall 2025, etc

The same slide also lives in three courses (AI for Business Leaders, Data Mining, Data
Visualization), and the index keeps one copy per course on purpose, so that what I said in each
course stays with that course. The fix is at search time: results whose slide text is identical
(five or more words) from different courses or terms are stacked into one slot, shown as a
stacked card with a tab per version ("Fall 2026 · AI for Business Leaders"), and clicking a tab
swaps in that version's scores and match. Two copies from the same course and term never stack
(those are different slides sharing a caption).

> when opening a slide it is centered vertically so I hvae to scroll down to see it

> why are the "what i said" boxes different here, they are from teh same slide same course

They were two consecutive ~120-word chunks of one recording; the page now labels chunks
"2024 Spring (M4) · part 1 of 2".

> for these slides rather than say "untitled" create a title for them, these two slides seem to
> obviously have a title

614 slides had no title: the heading was inside a picture, or the first text line was a page
number or a link. A first pass titled them from the extracted text and image description
(cheap, but it paraphrased: "Business Advantages of Customer Segmentation" for a slide that
says "Benefits of Customer Segmentation"). A second pass sends the slide image and asks for
the heading exactly as printed; 858 slides re-titled (the text-only pass's results were
replaced), cards and descriptions re-embedded. The deck reader now also skips page numbers
and links when picking a title.

> explain the score distance and meaning here, and make sure it is clear in the readme

Added "Reading the numbers on a card" to README section 3, worked through one card.

## 7. Evaluation

`eval/run.py` scores retrieval on 28 labeled questions (private file, not in the repo) in three
modes, with and without the image descriptions. Three runs during the day: the first showed my
"two kinds matched" bonus hurt and it came out; the second, after loading three more courses,
dropped because Data Mining now holds the same clustering material as the labeled course, so the
third run filters to the question's course; the fourth, after regrouping repeated slides by
their pictures, gained four points of hit@1. Results and what they mean are in the README.
