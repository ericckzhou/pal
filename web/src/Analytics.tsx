import { useEffect, useState } from "react";
import { api, UsageDay, UsageReport } from "./api";
import { num } from "./format";

type Props = { onAuthError: (err: unknown) => void };
type Metric = "tokens" | "replies";
const RANGES = [7, 30, 90];

const value = (d: UsageDay, m: Metric) => (m === "tokens" ? d.prompt_tokens + d.completion_tokens : d.replies);
const dayLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

// One series, one axis: a bar per day. Hover or focus a bar for its numbers; the table shows them all.
function DailyBars({ daily, metric }: { daily: UsageDay[]; metric: Metric }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...daily.map((d) => value(d, metric)));
  const ticks = [max, max / 2, 0];
  const labelEvery = Math.ceil(daily.length / 8);
  const h = hover !== null ? daily[hover] : null;
  return (
    <div className="chart">
      <div className="chart-plot" onMouseLeave={() => setHover(null)}>
        <div className="chart-axis" aria-hidden="true">
          {ticks.map((t, i) => (
            <span key={i}>{num(Math.round(t))}</span>
          ))}
        </div>
        <div className="chart-bars" role="img" aria-label={`${metric === "tokens" ? "Tokens" : "Replies"} per day`}>
          {ticks.map((_, i) => (
            <span key={i} className="gridline" style={{ bottom: `${100 - i * 50}%` }} aria-hidden="true" />
          ))}
          {daily.map((d, i) => {
            const v = value(d, metric);
            return (
              <button
                key={d.day}
                className={`bar-slot ${hover === i ? "hot" : ""}`}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                aria-label={`${dayLabel(d.day)}: ${v} ${metric}`}
              >
                <span className="bar" style={{ height: v ? `max(2px, ${(v / max) * 100}%)` : "0" }} />
              </button>
            );
          })}
          {h && hover !== null && (
            <div className="tooltip" style={{ left: `${((hover + 0.5) / daily.length) * 100}%` }} role="status">
              <strong>{dayLabel(h.day)}</strong>
              <span>{num(h.replies)} replies</span>
              <span>{num(h.prompt_tokens)} tokens read</span>
              <span>{num(h.completion_tokens)} tokens written</span>
            </div>
          )}
        </div>
      </div>
      <div className="chart-x" aria-hidden="true">
        {daily.map((d, i) => (
          <span key={d.day}>{i % labelEvery === 0 || i === daily.length - 1 ? dayLabel(d.day) : ""}</span>
        ))}
      </div>
    </div>
  );
}

export default function Analytics({ onAuthError }: Props) {
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>("tokens");
  const [asTable, setAsTable] = useState(false);
  const [data, setData] = useState<UsageReport | null>(null);

  useEffect(() => {
    api.usage(days).then(setData).catch(onAuthError);
  }, [days, onAuthError]);

  const t = data?.totals;
  const perReply = t && t.replies ? Math.round((t.prompt_tokens + t.completion_tokens) / t.replies) : null;

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Analytics</h2>
          <p className="muted small">Token use by the local model. Nothing here leaves this PC.</p>
        </div>
      </header>
      <div className="filters">
        <div className="tabs" role="tablist" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r} role="tab" aria-selected={days === r} onClick={() => setDays(r)}>
              {r} days
            </button>
          ))}
        </div>
        <div className="tabs" role="tablist" aria-label="Measure">
          {(["tokens", "replies"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)}>
              {m === "tokens" ? "Tokens" : "Replies"}
            </button>
          ))}
        </div>
        <button className="small" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show table"}
        </button>
      </div>

      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="tiles">
            <div className="tile"><span className="tile-label">Replies</span><span className="tile-value">{num(t!.replies)}</span></div>
            <div className="tile"><span className="tile-label">Tokens read</span><span className="tile-value">{num(t!.prompt_tokens)}</span><span className="tile-sub">your messages + context</span></div>
            <div className="tile"><span className="tile-label">Tokens written</span><span className="tile-value">{num(t!.completion_tokens)}</span></div>
            <div className="tile"><span className="tile-label">Per reply</span><span className="tile-value">{num(perReply)}</span><span className="tile-sub">average tokens</span></div>
          </div>
          {t!.untracked_replies > 0 && (
            <p className="muted small">{t!.untracked_replies} older replies were made before pal counted tokens, so they aren't in the token totals.</p>
          )}

          <h3 className="section">{metric === "tokens" ? "Tokens" : "Replies"} per day</h3>
          {asTable ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Day</th>
                    <th scope="col" className="num">Replies</th>
                    <th scope="col" className="num">Tokens read</th>
                    <th scope="col" className="num">Tokens written</th>
                  </tr>
                </thead>
                <tbody>
                  {[...data.daily].reverse().map((d) => (
                    <tr key={d.day}>
                      <td>{dayLabel(d.day)}</td>
                      <td className="num">{d.replies}</td>
                      <td className="num">{d.prompt_tokens.toLocaleString()}</td>
                      <td className="num">{d.completion_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <DailyBars daily={data.daily} metric={metric} />
          )}

          <h3 className="section">By model</h3>
          {data.by_model.length === 0 ? (
            <p className="muted">No replies in this range.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col" className="num">Replies</th>
                    <th scope="col" className="num">Tokens read</th>
                    <th scope="col" className="num">Tokens written</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_model.map((m) => (
                    <tr key={m.model}>
                      <td className="mono">{m.model}</td>
                      <td className="num">{m.replies}</td>
                      <td className="num">{m.prompt_tokens.toLocaleString()}</td>
                      <td className="num">{m.completion_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
