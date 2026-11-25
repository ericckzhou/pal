import { useCallback, useEffect, useState } from "react";
import { api, Attention as Data } from "./api";
import { relTime } from "./format";
import { Icon } from "./Icon";
import { View } from "./Sidebar";

type Props = { onView: (v: View) => void; onAuthError: (err: unknown) => void };

const day = (iso: string) => new Date(`${iso}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

// Neglected-task scan: long-overdue or untouched open tasks, and projects that have gone quiet.
// Nothing here changes on its own; "Still on it" restarts a task's clock, everything else is done on the task board.
export default function Attention({ onView, onAuthError }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.attention().then(setData).catch(onAuthError);
  }, [onAuthError]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (!data) return null;
  const empty = data.tasks.length === 0 && data.projects.length === 0;

  const keep = async (id: string) => {
    setError("");
    try {
      await api.stillOnIt(id);
      load();
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "That didn't work.");
    }
  };

  return (
    <div className="panel attention">
      <p className="panel-title">
        <Icon name="flag" size={14} /> Needs attention
      </p>
      {error && <p className="error small">{error}</p>}
      {empty ? (
        <p className="muted small">
          Nothing neglected: no task is {data.rules.overdue_days}+ days overdue or untouched for {data.rules.stale_days}+ days.
        </p>
      ) : (
        <ul className="plain">
          {data.tasks.map((t) => (
            <li key={t.id} className="attention-row">
              <span className="li-main">
                {t.title}
                {t.project && <span className="badge">{t.project}</span>}
              </span>
              <span className="li-meta">
                {t.reason === "overdue" && t.due_date ? `overdue since ${day(t.due_date)}` : `untouched since ${relTime(t.updated_at)}`}
              </span>
              {t.reason === "stale" ? (
                <button className="link small" onClick={() => keep(t.id)} title="Keep it; ask again in two weeks">
                  Still on it
                </button>
              ) : (
                <button className="link small" onClick={() => onView("tasks")}>
                  Reschedule
                </button>
              )}
            </li>
          ))}
          {data.projects.map((p) => (
            <li key={p.id} className="attention-row">
              <span className="li-main">
                {p.name}
                <span className="badge">project</span>
              </span>
              <span className="li-meta">
                quiet since {relTime(p.last_activity)} · {p.open_tasks} open {p.open_tasks === 1 ? "task" : "tasks"}
              </span>
              <button className="link small" onClick={() => onView("work")}>
                Open
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
