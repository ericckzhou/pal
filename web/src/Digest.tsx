import { useCallback, useEffect, useState } from "react";
import { api, Digest, DigestRow } from "./api";
import { dateTime, num } from "./format";
import { Icon } from "./Icon";
import { View } from "./Sidebar";

type Props = { onView: (v: View) => void; onAuthError: (err: unknown) => void };

const day = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

function List({ title, icon, empty, items }: {
  title: string; icon: string; empty: string; items: { key: string; main: string; meta?: string; badge?: string | null }[];
}) {
  return (
    <div className="panel">
      <p className="panel-title">
        <Icon name={icon} size={14} /> {title}
      </p>
      {items.length === 0 ? (
        <p className="muted small">{empty}</p>
      ) : (
        <ul className="plain">
          {items.map((i) => (
            <li key={i.key}>
              <span className="li-main">
                {i.main}
                {i.badge && <span className="badge">{i.badge}</span>}
              </span>
              {i.meta && <span className="li-meta">{i.meta}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The weekly digest: made every Monday from 08:00 from your tasks, reminders and activity. No model writes it.
export default function DigestPage({ onView, onAuthError }: Props) {
  const [rows, setRows] = useState<DigestRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");

  const loadList = useCallback(
    (pick?: string) =>
      api
        .digests()
        .then((r) => {
          setRows(r);
          setSelected((cur) => pick ?? cur ?? r[0]?.id ?? null);
        })
        .catch(onAuthError),
    [onAuthError],
  );

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selected) return setDigest(null);
    let alive = true;
    api
      .digest(selected)
      .then((d) => {
        if (!alive) return;
        setDigest(d);
        if (!d.read_at) api.readDigest(d.id).catch(onAuthError);
      })
      .catch(onAuthError);
    return () => {
      alive = false;
    };
  }, [selected, reload, onAuthError]);

  const rebuild = async () => {
    setBusy(true);
    setError("");
    try {
      const made = await api.generateDigest();
      await loadList(made.id);
      setSelected(made.id);
      setReload((n) => n + 1);
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "Couldn't build the digest.");
    } finally {
      setBusy(false);
    }
  };

  const b = digest?.body;
  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Weekly digest</h2>
          <p className="muted small">Made every Monday at 8:00 from your tasks, reminders and activity. pal's model doesn't write it.</p>
        </div>
        <div className="filters">
          {rows && rows.length > 0 && (
            <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)} aria-label="Week">
              {rows.map((r) => (
                <option key={r.id} value={r.id}>
                  Week of {day(r.week_start)}
                  {r.read_at ? "" : " (new)"}
                </option>
              ))}
            </select>
          )}
          <button className="primary" onClick={rebuild} disabled={busy} title="Rebuild this week's digest from current data">
            <Icon name="refresh" size={14} /> {busy ? "Building…" : "Build now"}
          </button>
        </div>
      </header>
      {error && <p className="error">{error}</p>}

      {rows && rows.length === 0 && !busy && (
        <p className="muted">No digest yet. The first one arrives Monday at 8:00, or press Build now.</p>
      )}

      {b && digest && (
        <div className="overview">
          <p className="muted small">
            {day(b.period.start)} – {day(b.period.end)} · made {dateTime(digest.created_at)}
          </p>
          <div className="tiles compact">
            <div className="tile">
              <span className="tile-label">Tasks done</span>
              <span className="tile-value">{num(b.counts.done)}</span>
              <span className="tile-sub">{num(b.counts.added)} added</span>
            </div>
            <div className="tile">
              <span className="tile-label">{b.overdue.length > 0 && <span className="dot bad" aria-hidden="true" />}Overdue now</span>
              <span className="tile-value">{b.overdue.length}</span>
              <span className="tile-sub">{b.due_this_week.length} due this week</span>
            </div>
            <div className="tile">
              <span className="tile-label">Chats</span>
              <span className="tile-value">{num(b.counts.chats)}</span>
              <span className="tile-sub">{num(b.counts.messages_sent)} messages from you</span>
            </div>
            <div className="tile">
              <span className="tile-label">Learned</span>
              <span className="tile-value">{num(b.counts.memories_added)}</span>
              <span className="tile-sub">memories · {num(b.counts.documents_added)} documents added</span>
            </div>
            <div className="tile">
              <span className="tile-label">Reminders</span>
              <span className="tile-value">{num(b.counts.reminders_fired)}</span>
              <span className="tile-sub">fired · {b.upcoming_reminders.length} coming up</span>
            </div>
          </div>
          <div className="overview-grid">
            <List title="Done last week" icon="check" empty="Nothing marked done."
              items={b.done.map((t) => ({ key: t.id, main: t.title, badge: t.project }))} />
            <List title="Due this week" icon="schedule" empty="Nothing due this week."
              items={b.due_this_week.map((t) => ({ key: t.id, main: t.title, badge: t.project, meta: t.due_date ? day(t.due_date) : undefined }))} />
            <List title="Overdue" icon="flag" empty="Nothing overdue."
              items={b.overdue.map((t) => ({ key: t.id, main: t.title, badge: t.project, meta: t.due_date ? `was due ${day(t.due_date)}` : undefined }))} />
            <List title="Reminders coming up" icon="bolt" empty="No reminders this week."
              items={b.upcoming_reminders.map((r) => ({ key: r.id, main: r.title, badge: r.project, meta: dateTime(r.due_at) }))} />
            <List title="Projects" icon="work" empty="No active projects."
              items={b.projects.map((p) => ({ key: p.name, main: p.name, meta: `${p.done} done · ${p.open} open` }))} />
            <List title="Neglected" icon="flag" empty="Nothing neglected."
              items={[
                ...b.neglected.tasks.map((t) => ({
                  key: t.id, main: t.title, badge: t.project,
                  meta: t.reason === "overdue" ? "long overdue" : "untouched for weeks",
                })),
                ...b.neglected.projects.map((p) => ({ key: p.id, main: p.name, badge: "project", meta: `${p.open_tasks} open, gone quiet` })),
              ]} />
          </div>
          <p className="small">
            <button className="link" onClick={() => onView("tasks")}>Open the task board</button>
          </p>
        </div>
      )}
    </section>
  );
}
