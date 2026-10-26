# Step 5: looking at the distances (Oct 26, 2026)

Index: 942 slides, 2,504 items (942 cards, 1,562 transcript chunks), voyage-3.5, cosine distance
(0 = identical). Hybrid search = vector top 50 + Postgres full-text top 50, reciprocal rank fusion.

## On-topic queries (no shared words with the slide that should come back)

| Query | Top slide | Distance | Matched through |
| --- | --- | --- | --- |
| it fits the data it has seen perfectly and falls apart on anything new | 70445-s05-067 "The sweet spot, a balance of bias and variance" | 0.502 | what I said over the slide |
| which shoppers behave alike | 70445-s05-023/028 "People like you" recommendation slides, then Market Segmentation | 0.487 | said + card |
| how does a computer read a photo | 45884-s06-029 "Images are Numbers" | 0.463 | said + card |
| finding the sweet spot between too simple and too complicated a model | 70445-s05-067 (shares "sweet spot", so not a README query) | 0.467 | said |

## Off-topic queries

| Query | Best distance | Top slide (wrong, as expected) |
| --- | --- | --- |
| who won the Stanley Cup in 2019 | 0.598 | a Nobel Prize slide |
| best pizza in Pittsburgh | 0.577 | "Life Outside CMU" (a bike trail) |

Gap: on-topic best 0.46 to 0.50, off-topic best 0.58 to 0.60. `WEAK_DISTANCE = 0.55` sits in the gap.

## A query that failed, and why

"it aced the practice problems but bombed the real test" never reaches the bias-variance slides
(not in the vector top 50). The embedding reads it as being about exams and arithmetic, and the
keyword side matches "test" to a slide about positive medical tests. The slide card is fine
("perform very well on training data but high error on test data"); the query is a metaphor two
steps away from the concept. Lesson: everyday phrasing works, metaphors don't. A query-rewrite
step (an LLM turning the metaphor into the technical statement before embedding) would likely
fix this; it is on the list to evaluate, not assumed.

## Things noticed

- Picture-only slides have OCR garbage as titles ("a0D OD", "O0ULO OD D0 00"). Treat titles with
  no real word as untitled.
- The two courses share slides (the same "People like you" slide is in 70-445 s04 and s05 and
  45-884 s01). Phase 2's near-duplicate grouping applies within a term too.
