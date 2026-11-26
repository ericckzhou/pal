-- Per-reply retrieval decision trace: candidate ids, ranks, similarity, fused score, reranker outcome. No item text.
ALTER TABLE messages ADD COLUMN retrieval_trace jsonb;
