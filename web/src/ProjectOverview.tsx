import { ReactNode, useEffect, useState } from "react";
import { api, ProjectOverview as Overview } from "./api";
import { formatDue, formatWhen, today } from "./dates";
import { relTime } from "./format";
import { Icon } from "./Icon";
import { Act, Section } from "./Work";

type Props = {
  projectId: string;
  version: number;
  act: Act;
  onTab: (s: Section) => void;
  onOpenConversation: (id: string) => void;
  onAuthError: (err: unknown) => void;
};

const UP_NEXT = 6;
const SCHEDULED = 5;
const NOTES = 4;

// One project at a glance: what's next, what's scheduled, what was decided, and where it was discussed.
// Each panel previews its tab; the full list (and editing) lives there.
export default function ProjectOverview({ projectId, version, act, onTab, onOpenConversation, onAuthError }: Props) {
  const [data, setData] = useState<Overview | null>(null);

  useEffect(() => {
    api.projectOverview(projectId).then(setData).catch(onAuthError);
  }, [projectId, version, onAuthError]);

  if (!data) return <p className="muted small">Loading project…</p>;
  const c = data.counts;
  const now = today();
  const nextReminder = data.scheduled.find((r) => !r.paused);

  return (
    <div className="overview">
      {c.suggested > 0 && (
        <div className="alert">
          <span>
            {c.suggested} memory suggestion{c.suggested === 1 ? "" : "s"} for this project waiting for you.
          </span>
          <button className="small" onClick={() => onTab("memory")}>
            Review
          </button>
        </div>
      )}

      <div className="tiles compact">
        <Tile label="Open tasks" value={c.open_tasks} dot={c.overdue > 0 ? "bad" : undefined} onClick={() => onTab("tasks")}
          sub={`${c.overdue ? `${c.overdue} overdue` : "none overdue"} · ${c.done_this_week} done this week`} />
        <Tile label="Scheduled" value={c.scheduled} onClick={() => onTab("reminders")}
          sub={nextReminder ? `next ${relTime(nextReminder.due_at)}` : "nothing upcoming"} />
        <Tile label="Memories" value={c.memories} dot={c.suggested > 0 ? "warn" : undefined} onClick={() => onTab("memory")}
          sub={c.suggested ? `${c.suggested} suggested` : "saved for this project"} />
        <Tile label="Documents" value={c.documents} onClick={() => onTab("documents")} sub="searched when you chat" />
      </div>

      <div className="overview-grid">
        <Panel icon="tasks" title="Up next" more={c.open_tasks > UP_NEXT ? `All ${c.open_tasks} tasks` : "Tasks"} onMore={() => onTab("tasks")}>
          {data.tasks.length === 0 ? (
            <p className="muted small">Nothing open. Add a task on the Tasks tab, or type /task in a chat.</p>
          ) : (
            <ul className="plain">
              {data.tasks.slice(0, UP_NEXT).map((t) => (
                <li key={t.id} className="check-row">
                  <input type="checkbox" onChange={() => act(() => api.editTask(t.id, { status: "done" }))} aria-label={`Mark ${t.title} done`} />
                  <span className="li-main">{t.title}</span>
                  {t.due_date && <span className={`badge ${t.due_date < now ? "overdue" : ""}`}>{formatDue(t.due_date, now)}</span>}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel icon="schedule" title="Scheduled" more="Reminders" onMore={() => onTab("reminders")}>
          {data.scheduled.length === 0 ? (
            <p className="muted small">No reminders. Type /remind in a chat, or add one on the Reminders tab.</p>
          ) : (
            <ul className="plain">
              {data.scheduled.slice(0, SCHEDULED).map((r) => (
                <li key={r.id} className={r.paused ? "dim" : ""}>
                  <span className="li-main">{r.title}</span>
                  <span className="li-meta">
                    {r.paused ? "Paused" : formatWhen(r.due_at)}
                    {r.repeat ? ` · ${r.repeat}` : ""}
                    {r.task ? ` · for “${r.task}”` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel icon="note" title="Decisions & notes" more="Notes" onMore={() => onTab("notes")}>
          {data.notes.length === 0 ? (
            <p className="muted small">Nothing written down yet. Record decisions on the Notes tab; pal remembers recent ones in chat.</p>
          ) : (
            <ul className="plain">
              {data.notes.slice(0, NOTES).map((n) => (
                <li key={n.id}>
                  <span className="li-main clamp-2">{n.text}</span>
                  <span className="li-meta">
                    {n.kind === "decision" ? "Decision" : "Note"} · {relTime(n.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel icon="chat" title={`Chats${c.chats ? ` (${c.chats})` : ""}`}>
          {data.chats.length === 0 ? (
            <p className="muted small">No chats focused on this project. Pick it in a chat's Project menu.</p>
          ) : (
            <ul className="plain">
              {data.chats.map((ch) => (
                <li key={ch.id}>
                  <button className="row-link" onClick={() => onOpenConversation(ch.id)}>
                    <span className="li-main">{ch.title}</span>
                    <span className="li-meta">
                      {relTime(ch.updated_at)} · {ch.message_count} msg
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Tile({ label, value, sub, dot, onClick }: { label: string; value: number; sub: string; dot?: "bad" | "warn"; onClick: () => void }) {
  return (
    <button className="tile" onClick={onClick}>
      <span className="tile-label">
        {dot && <span className={`dot ${dot}`} aria-hidden="true" />}
        {label}
      </span>
      <span className="tile-value">{value}</span>
      <span className="tile-sub">{sub}</span>
    </button>
  );
}

function Panel({ icon, title, more, onMore, children }: { icon: string; title: string; more?: string; onMore?: () => void; children: ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h3 className="panel-title">
          <Icon name={icon} size={14} /> {title}
        </h3>
        {more && onMore && (
          <button className="link small" onClick={onMore}>
            {more} →
          </button>
        )}
      </header>
      {children}
    </section>
  );
}
