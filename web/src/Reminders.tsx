import { FormEvent, useEffect, useState } from "react";
import { api, Project, Reminder } from "./api";
import { formatWhen, localDateTime, REPEATS } from "./dates";
import { Act } from "./Work";

type Props = { version: number; projects: Project[]; scope: string | null; act: Act; onAuthError: (e: unknown) => void };

// Reminders for one project (scope) or all of them. Inside a project, new reminders belong to it.
export default function Reminders({ version, projects, scope, act, onAuthError }: Props) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [title, setTitle] = useState("");
  const [at, setAt] = useState(() => localDateTime(new Date(Date.now() + 3600_000)));
  const [rrule, setRrule] = useState("");
  const [projectId, setProjectId] = useState("");

  useEffect(() => {
    api.reminders().then(setItems).catch(onAuthError);
  }, [version, onAuthError]);
  const shown = scope ? items.filter((r) => r.project_id === scope) : items;
  const active = projects.filter((p) => p.status === "active");

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !at) return;
    act(async () => {
      await api.addReminder({ title: title.trim(), at, rrule: rrule || null, project_id: scope ?? (projectId || null) });
      setTitle("");
    });
  };
  return (
    <div className="tab-body">
      <form className="memory-add" onSubmit={add}>
        <input placeholder="Remind me to…" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} aria-label="Reminder" />
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} aria-label="When" className="date" />
        <select value={rrule} onChange={(e) => setRrule(e.target.value)} aria-label="Repeat">
          {REPEATS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        {!scope && active.length > 0 && (
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Project">
            <option value="">No project</option>
            {active.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        <button type="submit" className="primary" disabled={!title.trim() || !at}>
          Add
        </button>
      </form>
      <p className="muted small">
        Reminders appear in pal and, if you allow it, as desktop notifications while a pal tab is open. Or type /remind in a chat.
      </p>
      <ul className="memory-list">
        {shown.length === 0 && <li className="muted small empty-row">{scope ? "No reminders for this project." : "No reminders."}</li>}
        {shown.map((r) => (
          <li key={r.id} className="memory-item">
            <p className="memory-text">{r.title}</p>
            <div className="memory-meta small">
              <span className="badge stated">{formatWhen(r.due_at)}</span>
              {r.repeat && <span className="badge">{r.repeat}</span>}
              {r.project && !scope && <span className="badge">{r.project}</span>}
              <span className="spacer" />
              <button className="small" onClick={() => act(() => api.deleteReminder(r.id))}>
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
