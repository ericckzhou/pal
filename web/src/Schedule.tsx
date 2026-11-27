import { useEffect, useState } from "react";
import { api, CalendarEvent, Reminder } from "./api";
import { dateTime, relTime } from "./format";
import { Icon } from "./Icon";
import ScheduleAutomations from "./ScheduleAutomations";

type Props = { version: number; owner: boolean; onAuthError: (err: unknown) => void };

function state(r: Reminder): { label: string; cls: string } {
  if (!r.active) return { label: "Done", cls: "" };
  if (r.paused) return { label: "Paused", cls: "warn" };
  return { label: "Scheduled", cls: "ok" };
}

// Reminders as scheduled jobs: pause, resume, run now, delete. New ones come from chat (/remind) or Projects > Reminders.
export default function Schedule({ version, owner, onAuthError }: Props) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [events, setEvents] = useState<CalendarEvent[] | null>(null); // null: no calendar connected

  useEffect(() => {
    api.allReminders().then(setItems).catch(onAuthError);
  }, [version, tick, onAuthError]);

  useEffect(() => {
    api
      .calendarEvents()
      .then((r) => setEvents(r.connected ? r.events : null))
      .catch(() => setEvents(null)); // the calendar band is extra; reminders still show without it
  }, [version]);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setError("");
    try {
      await fn();
      setNotice(done);
      setConfirmId(null);
      setTick((t) => t + 1);
    } catch (err) {
      onAuthError(err);
      setError(err instanceof Error ? err.message : "That didn't work.");
    }
  };

  const shown = filter === "active" ? items.filter((r) => r.active) : items;

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>Schedule</h2>
          <p className="muted small">Reminders pal fires on time. Add new ones in chat with /remind, or in Projects &amp; tasks.</p>
        </div>
        <div className="tabs" role="tablist">
          {(["active", "all"] as const).map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>
              {f === "active" ? "Active" : "All"}
            </button>
          ))}
        </div>
      </header>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice small">{notice}</p>}
      {owner && <ScheduleAutomations onAuthError={onAuthError} />}

      {events && (
        <section className="calendar-band" aria-labelledby="calendar-heading">
          <h3 id="calendar-heading">Calendar</h3>
          <p className="muted small">The next two weeks from your connected Google Calendar. Read-only; change events in Google.</p>
          {events.length === 0 ? (
            <p className="muted">Nothing on your connected calendars in the next two weeks.</p>
          ) : (
            <ul className="calendar-list">
              {events.map((e) => (
                <li key={e.id} className={e.status === "tentative" ? "tentative" : undefined}>
                  <span className="calendar-when">{e.when}</span>
                  <span className="calendar-title">{e.title}</span>
                  {e.location && <span className="muted small">{e.location}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <h3>Reminders</h3>
      {shown.length === 0 ? (
        <p className="muted">Nothing scheduled.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Reminder</th>
                <th scope="col">Repeats</th>
                <th scope="col">Next run</th>
                <th scope="col">State</th>
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const st = state(r);
                return (
                  <tr key={r.id}>
                    <td className="cell-title">
                      <strong>{r.title}</strong>
                      {r.project && <span className="badge">{r.project}</span>}
                    </td>
                    <td className="small">{r.repeat || "Once"}</td>
                    <td className="small" title={dateTime(r.due_at)}>
                      {r.active ? `${dateTime(r.due_at)} (${relTime(r.due_at)})` : "—"}
                    </td>
                    <td>
                      <span className={`state ${st.cls}`}>
                        <span className={`dot ${st.cls}`} aria-hidden="true" />
                        {st.label}
                      </span>
                    </td>
                    <td className="row-actions">
                      {r.active &&
                        (r.paused ? (
                          <button className="small" onClick={() => act(() => api.resumeReminder(r.id), `Resumed “${r.title}”.`)}>
                            <Icon name="play" size={14} /> Resume
                          </button>
                        ) : (
                          <button className="small" onClick={() => act(() => api.pauseReminder(r.id), `Paused “${r.title}”.`)}>
                            <Icon name="pause" size={14} /> Pause
                          </button>
                        ))}
                      <button
                        className="small"
                        onClick={() => act(() => api.triggerReminder(r.id), `Sent “${r.title}” now. Its schedule hasn't changed.`)}
                        title="Send the notification now without changing the schedule"
                      >
                        <Icon name="bolt" size={14} /> Run now
                      </button>
                      {confirmId === r.id ? (
                        <>
                          <button className="danger small" onClick={() => act(() => api.deleteReminder(r.id), `Deleted “${r.title}”.`)}>
                            Delete
                          </button>
                          <button className="small" onClick={() => setConfirmId(null)}>Keep</button>
                        </>
                      ) : (
                        <button className="icon-btn" onClick={() => setConfirmId(r.id)} aria-label={`Delete ${r.title}`}>
                          <Icon name="trash" size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
