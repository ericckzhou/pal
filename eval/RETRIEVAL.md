# Retrieval evaluation

Run 2026-11-26 by `eval/retrieval_eval.py` on [made-up data](retrieval/corpus.json): 50 questions with a known answer and 10 with none (general-knowledge questions and personal facts that were never stored). pal returns at most 6 items per question.

| Variant | Recall@6 | MRR | Abstains when nothing fits | Noise (items on no-answer questions) | p50 ms |
|---|---|---|---|---|---|
| Keyword only | 90% | 0.76 | 30% | 1.6 | 2 |
| Vector only | 100% | 0.94 | 80% | 0.5 | 16 |
| Hybrid, no cut-off | 98% | 0.88 | 0% | 6.0 | 16 |
| Hybrid | 100% | 0.94 | 70% | 0.7 | 18 |
| Hybrid + rerank (pal's default) | 100% | 1.00 | 100% | 0.0 | 309 |

**Recall@6**: the right passage is among the items returned. **MRR**: how high it ranks (1.0 = always first).
**Abstains**: for questions with no stored answer, pal sends the model nothing, so it can't cite something unrelated.
**p50 ms**: median time per question on the development PC, embedding the question included.

## What this shows, and its limits

- Keyword search alone misses paraphrases ("Who is my boss?" vs "My manager is..."). Vectors catch them.
- Without a similarity cut-off, every question returns six items, so the model always gets something to cite, related or not.
- The keyword floor (`FTS_VEC_MIN`) was raised from 0.45 to 0.55 after this evaluation: abstention went from 50% to 70% with no loss of recall. 0.60 scored slightly better still, but was not chosen, to leave margin for exact names that embed poorly; tuning hard against 60 questions would overfit.
- Reranking with the local model removes the remaining noise for about 0.3 s per message.
- The set is small, made up and fairly easy: most variants are near the ceiling on recall, so the useful differences are in ranking (MRR) and abstention. A larger set built from real use would separate them better.

## Questions missed by Hybrid + rerank (pal's default)

- none
