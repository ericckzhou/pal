import { useCallback, useEffect, useRef, useState } from "react";
import { api, DocProcessing as State } from "./api";
import { num } from "./format";

type Props = { onProgress: () => void; onAuthError: (err: unknown) => void };

const mb = (n: number) => `${Math.max(1, Math.round(n / 1024 / 1024))} MB`;

/**
 * What pal is doing with documents in the background, and the one slow job it only does when asked:
 * re-reading PDFs that an older PDF reader read.
 */
export default function DocProcessing({ onProgress, onAuthError }: Props) {
  const [s, setS] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [showFailed, setShowFailed] = useState(false);
  const lastDone = useRef<number | undefined>(undefined);

  const load = useCallback(() => {
    api
      .docProcessing()
      .then((next) => {
        setS(next);
        if (next.reread.done !== lastDone.current) {
          lastDone.current = next.reread.done;
          onProgress();
        }
      })
      .catch(onAuthError);
  }, [onAuthError, onProgress]);

  useEffect(() => load(), [load]);

  const active = !!s && (s.reread.running || s.indexing.pending > 0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, load]);

  const act = (fn: () => Promise<unknown>) => {
    setError("");
    fn()
      .then(load)
      .catch((err) => {
        onAuthError(err);
        setError(err instanceof Error ? err.message : "Something went wrong.");
      });
  };

  if (!s) return null;
  const job = s.reread;
  const { pending, total } = s.indexing;
  const stale = s.stale_pdfs;
  const finished = !job.running && !!job.finished_at;
  const failed = job.failed ?? [];
  if (!pending && !job.running && !finished && !stale.files && !stale.uploads) return null;

  return (
    <div className="panel doc-processing" aria-live="polite">
      <p className="panel-title">Background work</p>

      {pending > 0 && (
        <div className="proc-row">
          <span className="small">
            Indexing for meaning-based search: {num(total - pending)} of {num(total)} passages done. Keyword search works
            meanwhile.
          </span>
          <progress max={total} value={total - pending} />
        </div>
      )}

      {job.running && (
        <div className="proc-row">
          <span className="small">
            Re-reading PDFs with {s.pdf_reader === "docling" ? "Docling" : "pypdf"}: {job.done} of {job.total}
            {job.current ? ` · now ${job.current}` : ""}
          </span>
          <progress max={job.total} value={job.done} />
          {s.can_reread && (
            <button className="small" onClick={() => act(api.stopReread)}>
              Stop after this file
            </button>
          )}
        </div>
      )}

      {finished && (
        <p className="small">
          Re-read {job.stopped ? "stopped" : "finished"}: {job.done} of {job.total} PDFs
          {failed.length > 0 && (
            <>
              {" · "}
              <button className="link small" onClick={() => setShowFailed((v) => !v)}>
                {failed.length} skipped
              </button>
            </>
          )}
        </p>
      )}
      {showFailed && failed.length > 0 && (
        <ul className="small muted proc-failed">
          {failed.map((f) => (
            <li key={f.path}>
              {f.path}: {f.reason}
            </li>
          ))}
        </ul>
      )}

      {!job.running && stale.files > 0 && s.pdf_reader === "docling" && (
        <div className="proc-row">
          <span className="small">
            {stale.files} PDF{stale.files === 1 ? "" : "s"} ({mb(stale.bytes)}) were read with the older PDF reader.
            Re-reading them with Docling gives section headings, readable tables and text from scans. It runs only on this
            PC, takes a few seconds per page, and you can stop it any time. Until then they stay searchable as they are.
          </span>
          {s.can_reread ? (
            <button className="small primary" onClick={() => act(api.startReread)}>
              Re-read {stale.files} PDF{stale.files === 1 ? "" : "s"}
            </button>
          ) : (
            <span className="small muted">Start this from pal on the PC.</span>
          )}
        </div>
      )}
      {stale.uploads > 0 && s.pdf_reader === "docling" && (
        <p className="small muted">
          {stale.uploads} PDF{stale.uploads === 1 ? " was" : "s were"} uploaded before pal kept encrypted copies of
          uploads, so {stale.uploads === 1 ? "it" : "they"} can't be re-read. Upload {stale.uploads === 1 ? "it" : "them"}{" "}
          again to use the new reader.
        </p>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
