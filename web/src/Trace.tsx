import { useState } from "react";
import { api, RetrievalTrace, TraceCandidate, TraceDecision } from "./api";

const DECISION: Record<TraceDecision, string> = {
  used: "sent to model",
  trimmed: "cut for space",
  reranker_dropped: "dropped by reranker",
  ranked_lower: "ranked too low",
};
const KIND = { memory: "memory", document: "doc", conversation: "chat", note: "note", project: "project" } as const;

function rerankLine(r: RetrievalTrace["rerank"]): string {
  if (r.status === "ran") return `reranker kept ${r.kept} of ${r.pool}`;
  if (r.status === "fell_back") return "reranker failed, fused order kept";
  if (r.status === "no_candidates") return "reranker had nothing to read";
  return "reranker off";
}

function summary(t: RetrievalTrace): string {
  const cut = t.below_cutoff.keyword + t.below_cutoff.vector;
  return [
    t.vector_search ? "keyword + vector search" : "keyword search only (embedding failed)",
    `${t.fused} candidate${t.fused === 1 ? "" : "s"}`,
    rerankLine(t.rerank),
    cut ? `${cut} below the similarity cutoff` : null,
  ].filter(Boolean).join(" · ");
}

function Row({ c }: { c: TraceCandidate }) {
  return (
    <tr className={`trace-${c.decision}`}>
      <td>{c.handle ? <span className="cite">{c.handle}</span> : null}</td>
      <td>{c.pinned ? "pinned" : KIND[c.kind]}</td>
      <td className="trace-label">{c.label ?? <span className="muted">(deleted)</span>}</td>
      <td className="num">{c.keyword_rank ?? "–"}</td>
      <td className="num">{c.vector_rank ?? "–"}</td>
      <td className="num">{c.sim != null ? c.sim.toFixed(2) : "–"}</td>
      <td className="num">{c.score != null ? `${(c.score * 1000).toFixed(1)}${c.boosted ? "↑" : ""}` : "–"}</td>
      <td>{DECISION[c.decision]}</td>
    </tr>
  );
}

/** "Why these sources?": loads the reply's stored retrieval trace on first open. */
export function TracePanel({ messageId }: { messageId: string }) {
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    if (trace || error) return;
    api.trace(messageId).then(setTrace, (e: unknown) => setError(e instanceof Error ? e.message : "Couldn't load the trace."));
  };
  return (
    <details className="trace small" onToggle={(e) => (e.currentTarget.open ? load() : undefined)}>
      <summary>Why these sources?</summary>
      {error && <p className="error">{error}</p>}
      {!trace && !error && <p className="muted">Loading…</p>}
      {trace && (
        <>
          <p className="muted">{summary(trace)}</p>
          {trace.candidates.length ? (
            <div className="trace-table">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Kind</th>
                    <th>Item</th>
                    <th title="Rank in the keyword list">Kw</th>
                    <th title="Rank in the vector list">Vec</th>
                    <th title={`Cosine similarity (vector cutoff ${trace.cutoffs.vec_min}, keyword hits need ${trace.cutoffs.fts_vec_min})`}>Sim</th>
                    <th title="Fused rank score ×1000; ↑ = focus-project boost">Score</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.candidates.map((c) => <Row key={`${c.kind}-${c.id}`} c={c} />)}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Nothing matched well enough, so no sources were sent.</p>
          )}
        </>
      )}
    </details>
  );
}
