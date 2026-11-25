import { useEffect, useRef, useState } from "react";
import { api, LogLine } from "./api";

type Props = { onAuthError: (err: unknown) => void };
const LEVELS = ["debug", "info", "warning", "error"] as const;

// Recent server log lines (kept in memory since pal started), coloured by severity.
export default function Logs({ onAuthError }: Props) {
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("info");
  const [query, setQuery] = useState("");
  const [lines, setLines] = useState<LogLine[]>([]);
  const [live, setLive] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.logs(level, query).then((l) => alive && setLines(l)).catch(onAuthError);
    const first = setTimeout(load, query ? 250 : 0);
    const t = live ? setInterval(load, 3000) : undefined;
    return () => {
      alive = false;
      clearTimeout(first);
      if (t) clearInterval(t);
    };
  }, [level, query, live, onAuthError]);

  useEffect(() => {
    if (live) endRef.current?.scrollIntoView({ block: "end" });
  }, [lines, live]);

  return (
    <section className="page page-fill">
      <header className="page-head">
        <div>
          <h2>Logs</h2>
          <p className="muted small">The last 2,000 lines since pal started. Only shown on this PC.</p>
        </div>
      </header>
      <div className="filters">
        <div className="tabs" role="tablist" aria-label="Lowest level shown">
          {LEVELS.map((l) => (
            <button key={l} role="tab" aria-selected={level === l} onClick={() => setLevel(l)}>
              {l[0].toUpperCase() + l.slice(1)}
            </button>
          ))}
        </div>
        <input className="search page-search" placeholder="Filter text" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="small check">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Follow
        </label>
      </div>
      <div className="logs" role="log" aria-live="off">
        {lines.length === 0 ? (
          <p className="muted">No lines at this level{query ? " match" : ""}.</p>
        ) : (
          lines.map((l) => (
            <div key={l.seq} className={`log-line lv-${l.level}`}>
              <span className="log-at">{l.at.slice(11, 19)}</span>
              <span className="log-level">{l.level.toUpperCase()}</span>
              <span className="log-src">{l.logger}</span>
              <span className="log-msg">{l.message}</span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}
