# Retrieval evaluation

Run 2026-11-26 by `eval/retrieval_eval.py` with the real embedding model. pal returns at most 6 items per question. Percentages show a 95% confidence interval in brackets; with a few dozen questions per set, those intervals are wide, so differences of a few points between variants are not meaningful.

## Held-out set (the honest numbers)

[Made-up data](retrieval/heldout.json) written after the cut-offs were tuned and never used to tune anything: 71 questions with a stored answer and 30 without. Strict scoring: a hit must be the expected item, and for documents the returned chunk must contain the answer. **Near miss** questions ask for a fact that isn't stored about a topic that is ("What's Dr. Haddad's phone number?" when only the name is stored); returning the related item there is defensible, since the model can then say what it does and doesn't know, so that column is a harder bar than off-topic.

| Variant | Recall@6 | MRR | Abstains: off-topic | Abstains: near miss | Noise | p50 ms |
|---|---|---|---|---|---|---|
| Keyword only | 86% (76%–92%) | 0.66 | 47% (25%–70%) | 13% (4%–38%) | 2.8 | 3 |
| Vector only | 97% (90%–99%) | 0.86 | 87% (62%–96%) | 33% (15%–58%) | 1.0 | 23 |
| Hybrid, no cut-off | 97% (90%–99%) | 0.85 | 0% (0%–20%) | 0% (0%–20%) | 6.0 | 26 |
| Hybrid | 97% (90%–99%) | 0.85 | 80% (55%–93%) | 13% (4%–38%) | 1.4 | 26 |
| Hybrid + rerank (pal's default) (×3) | 97% (90%–99%) | 0.96 | 100% (80%–100%) | 80% (55%–93%) | 0.1 | 379 |

## Tuning set (in-sample)

[Made-up data](retrieval/corpus.json) that pal's cut-offs were calibrated on: 50 questions with a stored answer and 10 without. Lenient scoring: a hit is any returned item containing an expected string. These numbers are optimistic by construction; they are kept to show what tuning bought, not how pal does on new data.

| Variant | Recall@6 | MRR | Abstains: no answer | Noise | p50 ms |
|---|---|---|---|---|---|
| Keyword only | 90% (79%–96%) | 0.76 | 30% (11%–60%) | 1.6 | 3 |
| Vector only | 100% (93%–100%) | 0.94 | 80% (49%–94%) | 0.5 | 21 |
| Hybrid, no cut-off | 98% (90%–100%) | 0.88 | 0% (0%–28%) | 6.0 | 20 |
| Hybrid | 100% (93%–100%) | 0.94 | 70% (40%–89%) | 0.7 | 24 |
| Hybrid + rerank (pal's default) (×3) | 100% (93%–100%) | 1.00 | 100% (72%–100%) | 0.0 | 383 |

**Recall@6**: a correct item is among those returned. **MRR**: how high it ranks (1.0 = always first). **Abstains**: for questions with no stored answer, pal sends the model nothing. **Noise**: items returned for those questions. **p50 ms**: median time per question on the development PC. **(×3)**: the variant ran three times because the reranker asks the local model; brackets after MRR and noise give the range across runs (none means the runs agreed).

## What this shows

- **The tuning set overstated things, mostly on abstention.** Plain hybrid search abstained on 70% of the tuning set's no-answer questions but 47% of the held-out ones. The cut-offs fit the tuning data better than new data, which is what in-sample calibration predicts.
- **The reranker does most of the work.** On the held-out set it lifts MRR from 0.85 to 0.96, and abstention on near misses from 13% to 80%, for about 0.35 s per message. Without it, a question like "What's the wifi password?" brings back the memory naming the network, and the model has to work out that the password isn't there.
- **Reranking can only remove items, not find missing ones.** Both held-out misses are gaps in the embedding model: "Which herb should I leave out?" doesn't reach "I can't stand cilantro", and "my nephew and niece in Calgary" needs two steps (Dev lives in Calgary; Dev's kids are Ravi and Leela).
- **Keyword search adds noise on this set, not recall.** Vector-only matched hybrid's recall with better abstention (60% vs 47%). The keyword list is kept for exact names, codes and numbers (licence plates, ticket numbers) that embed poorly, but this set doesn't show it earning its place. That is a question for the next set: changing pal based on this one would turn it into a second tuning set.
- **Remaining near-miss failures are defensible.** The three no-answer questions the default still answers return the closest stored fact ("What's Dr. Haddad's phone number?" returns the memory naming Dr. Haddad), which a careful model can use to say what it doesn't know.

## Limits

- Both sets are made up, and were written alongside the retrieval code by people who know how it works, which biases them toward questions it handles. Questions from real use would be a better test.
- The held-out set is small (71 + 30 questions), so the intervals above are wide: the reranker's 80% near-miss abstention is anywhere from 55% to 93%.
- Not covered: adversarial text inside documents, contradicting memories (handled separately by memory replacement), large corpora where many items compete, and pinned memories.

## Held-out set: what Hybrid + rerank (pal's default) got wrong (first run)

Missed (no correct item returned):

- Which herb should I leave out when I cook for myself?
- What are my nephew and niece in Calgary called?

Returned something for a no-answer question (first item shown):

- What's my Aeroplan number? → My frequent flyer program is Aeroplan.
- What's Dr. Haddad's phone number? → My GP is Dr. Haddad at Elm Street Clinic.
- When is Ada's school concert? → My niece Ada turns seven on October 19.

## Tuning set: what Hybrid + rerank (pal's default) got wrong (first run)

Missed (no correct item returned):

- none

Returned something for a no-answer question (first item shown):

- none
